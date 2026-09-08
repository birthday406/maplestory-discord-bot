"""오래된 랭킹 스냅샷을 검증된 두 압축본으로 보관하고 별도 DB로 복원합니다."""

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime, timezone
from itertools import groupby
from pathlib import Path
from uuid import uuid4


COLUMNS = (
    "name_key", "snapshot_date", "level", "exp", "ranking", "world_id",
    "job_name", "legion_level", "legion_rank", "achievement_score", "achievement_rank",
)
BATCH_SIZE = 10_000
SCHEMA = """CREATE TABLE ranking_snapshots (
    name_key TEXT NOT NULL, snapshot_date TEXT NOT NULL,
    level INTEGER NOT NULL, exp INTEGER NOT NULL, ranking INTEGER NOT NULL,
    world_id INTEGER, job_name TEXT, legion_level INTEGER, legion_rank INTEGER,
    achievement_score INTEGER, achievement_rank INTEGER,
    PRIMARY KEY (name_key, snapshot_date)
)"""


def require_separate_storage(db_path: Path, archive_dir: Path, replica_dir: Path) -> None:
    # 복사 위치는 미리 마운트되어 있어야 하며, 같은 디스크의 다른 폴더로 대체하지 않습니다.
    replica_device = replica_dir.stat().st_dev
    if not replica_dir.is_dir() or replica_device in {
        db_path.stat().st_dev, archive_dir.stat().st_dev
    }:
        raise ValueError("Archive replica must be on a separate mounted filesystem.")


def sync_directory(directory: Path) -> None:
    # Linux에서는 파일 이름 변경도 디스크에 반영한 뒤 DB 정리를 허용합니다.
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish_archive(path: Path, compressed: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sync_directory(path.parent.parent)
    if path.exists():
        if path.read_bytes() != compressed:
            raise ValueError(f"Existing archive differs: {path.name}")
        return
    # 쓰다 중단된 파일은 .partial로 남기고 완성된 파일과 구분합니다. 자동 삭제하지 않습니다.
    partial = path.with_name(path.name + ".partial-" + uuid4().hex)
    with partial.open("xb") as output:
        output.write(compressed)
        output.flush()
        os.fsync(output.fileno())
    os.replace(partial, path)
    sync_directory(path.parent)


def read_archive(path: Path) -> tuple[dict, list[tuple]]:
    """gzip 오류·해시·형식·행 수를 검증해 실제 복원할 데이터를 반환합니다."""
    raw = gzip.decompress(path.read_bytes())
    digest = hashlib.sha256(raw).hexdigest()
    lines = raw.splitlines()
    header = json.loads(lines[0])
    if (
        header.get("format") != "ranking-snapshots"
        or header.get("version") != 1
        or header.get("columns") != list(COLUMNS)
        or path.name != f"{header['created_at']}-{digest}.jsonl.gz"
    ):
        raise ValueError(f"Invalid archive header or checksum: {path.name}")
    rows = [tuple(json.loads(line)) for line in lines[1:]]
    if not rows or len(rows) != header["rows"]:
        raise ValueError(f"Archive row count mismatch: {path.name}")
    for row in rows:
        if len(row) != len(COLUMNS) or date.fromisoformat(row[1]).strftime("%Y-%m") != header["month"]:
            raise ValueError(f"Invalid snapshot row: {path.name}")
    if len({row[:2] for row in rows}) != len(rows):
        raise ValueError(f"Duplicate snapshot keys: {path.name}")
    # 실제 SQLite 테이블에 복원해 제약조건과 값까지 확인합니다. 운영 DB는 건드리지 않습니다.
    with closing(sqlite3.connect(":memory:")) as restored:
        restored.execute(SCHEMA)
        restored.executemany(
            f"INSERT INTO ranking_snapshots VALUES ({', '.join('?' for _ in COLUMNS)})", rows
        )
        if restored.execute("SELECT * FROM ranking_snapshots ORDER BY rowid").fetchall() != rows:
            raise ValueError(f"Archive restore content mismatch: {path.name}")
    return header, rows


def archive_snapshots(store, keep_since: date, archive_dir: Path, replica_dir: Path) -> int:
    """기준일 이전 기록만 월별 묶음으로 보관하고, 검증한 값과 동일한 DB 행만 정리합니다."""
    archive_dir = archive_dir.resolve()
    replica_dir = replica_dir.resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    sync_directory(archive_dir.parent)
    require_separate_storage(store.path, archive_dir, replica_dir)
    with store._connect() as connection:
        columns = tuple(row["name"] for row in connection.execute("PRAGMA table_info(ranking_snapshots)"))
    if columns != COLUMNS:
        raise ValueError("Snapshot schema changed; archive format must be updated first.")

    deleted = 0
    while True:
        with store._connect() as connection:
            rows = [tuple(row) for row in connection.execute(
                f"SELECT {', '.join(COLUMNS)} FROM ranking_snapshots "
                "WHERE snapshot_date < ? ORDER BY snapshot_date LIMIT ?",
                (keep_since.isoformat(), BATCH_SIZE),
            )]
        if not rows:
            return deleted

        for month, group in groupby(rows, key=lambda row: row[1][:7]):
            batch = list(group)
            created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            header = {
                "format": "ranking-snapshots", "version": 1, "columns": COLUMNS,
                "month": month, "created_at": created_at, "rows": len(batch),
            }
            raw = ("\n".join(json.dumps(item, ensure_ascii=False) for item in [header, *batch]) + "\n").encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            filename = f"{created_at}-{digest}.jsonl.gz"
            primary = archive_dir / month / filename
            replica = replica_dir / month / filename
            publish_archive(primary, gzip.compress(raw, mtime=0))
            if read_archive(primary)[1] != batch:
                raise ValueError("Primary archive content mismatch.")
            publish_archive(replica, primary.read_bytes())
            if read_archive(replica)[1] != batch:
                raise ValueError("Replica archive content mismatch.")

            # 압축 도중 변경된 행은 지우지 않습니다. 다음 묶음에 변경된 내용도 보관합니다.
            require_separate_storage(store.path, archive_dir, replica_dir)
            with store._connect() as connection:
                # 원본을 보관소로 옮겨도 /닉네임추적의 최초 관측일은 바뀌지 않게 남깁니다.
                connection.executemany(
                    "INSERT INTO ranking_archive_first_seen VALUES (?, ?) "
                    "ON CONFLICT(name_key) DO UPDATE SET first_seen_date = "
                    "MIN(first_seen_date, excluded.first_seen_date)",
                    [row[:2] for row in batch],
                )
                before_delete = connection.total_changes
                connection.executemany(
                    "DELETE FROM ranking_snapshots WHERE "
                    + " AND ".join(f"{column} IS ?" for column in COLUMNS),
                    batch,
                )
                deleted += connection.total_changes - before_delete


def restore_archives(archive_dir: Path, destination: Path) -> int:
    """보관본만으로 새 SQLite DB에 복원합니다. 기존 DB는 덮어쓰지 않습니다."""
    paths = sorted(archive_dir.rglob("*.jsonl.gz"), key=lambda path: path.name)
    if not paths:
        raise ValueError("No completed archives found.")
    # 기존 운영 DB를 잘못 지정해도 수정하지 않도록 새 파일만 허용합니다.
    with destination.open("xb"):
        pass
    with closing(sqlite3.connect(destination)) as connection, connection:
        connection.execute(SCHEMA)
        for path in paths:
            _, rows = read_archive(path)
            # 늦게 재수집된 같은 날짜의 정정 기록은 나중 보관본의 값으로 복원합니다.
            connection.executemany(
                f"INSERT INTO ranking_snapshots VALUES ({', '.join('?' for _ in COLUMNS)}) "
                "ON CONFLICT(name_key, snapshot_date) DO UPDATE SET "
                + ", ".join(f"{column} = excluded.{column}" for column in COLUMNS[2:]),
                rows,
            )
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("Restored archive integrity check failed.")
        return connection.execute("SELECT COUNT(*) FROM ranking_snapshots").fetchone()[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="월별 보관 스냅샷을 새 조회용 SQLite DB로 복원")
    parser.add_argument("archive_dir", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    print(f"Restored {restore_archives(arguments.archive_dir, arguments.destination):,} snapshots.")
