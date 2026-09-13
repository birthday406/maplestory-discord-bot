import argparse
import base64
import gzip
import hashlib
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ModuleNotFoundError:  # Windows에서는 백업 생성 단위 테스트만 실행합니다.
    fcntl = None


BACKUP_NAME = re.compile(r"^ranking-\d{8}T\d{6}Z\.db\.gz$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_local_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """실행 중인 SQLite DB를 검사하고 gzip 백업으로 원자적으로 저장합니다."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    name = f"ranking-{timestamp:%Y%m%dT%H%M%SZ}.db.gz"
    archive = backup_dir / name
    temporary_database = backup_dir / f".{name}.tmp.db"
    temporary_archive = backup_dir / f".{name}.tmp.gz"
    try:
        # 원본이 없어도 빈 DB를 만들지 않도록 읽기 전용으로 엽니다.
        source_uri = f"file:{database_path.resolve().as_posix()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as source:
            with closing(sqlite3.connect(temporary_database)) as target:
                source.backup(target)
        with closing(sqlite3.connect(temporary_database)) as snapshot:
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Ranking backup integrity check failed.")

        # 압축 파일도 완전히 디스크에 기록한 뒤 최종 이름으로 바꿉니다.
        with temporary_database.open("rb") as source, temporary_archive.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1) as compressed:
                shutil.copyfileobj(source, compressed, length=1024 * 1024)
            raw.flush()
            os.fsync(raw.fileno())
        temporary_archive.replace(archive)
        digest = sha256_file(archive)
        checksum = archive.with_name(f"{archive.name}.sha256")
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        return archive
    finally:
        temporary_database.unlink(missing_ok=True)
        temporary_archive.unlink(missing_ok=True)


def prune_local_backups(backup_dir: Path, retain: int = 4) -> list[Path]:
    """정해진 주간 백업 이름만 골라 오래된 파일과 체크섬을 정리합니다."""
    if retain < 1:
        raise ValueError("retain must be at least 1")
    archives = sorted(
        (path for path in backup_dir.iterdir() if BACKUP_NAME.fullmatch(path.name)),
        key=lambda path: path.name,
    )
    removed = archives[:-retain]
    for archive in removed:
        archive.unlink()
        archive.with_name(f"{archive.name}.sha256").unlink(missing_ok=True)
    return removed


def _ssh_command(identity: Path, target: str, arguments: list[str]) -> subprocess.CompletedProcess:
    remote_command = " ".join(shlex.quote(argument) for argument in arguments)
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            target,
            remote_command,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def replicate_backup(
    archive: Path,
    *,
    target: str,
    remote_dir: str,
    identity: Path,
    retain: int = 4,
) -> None:
    """보조 서버로 전송한 파일의 해시를 확인한 뒤 최근 백업만 남깁니다."""
    digest = sha256_file(archive)
    _ssh_command(identity, target, ["mkdir", "-p", remote_dir])
    uploading_name = f".{archive.name}.uploading"
    subprocess.run(
        [
            "scp",
            "-i",
            str(identity),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            str(archive),
            f"{target}:{remote_dir}/{uploading_name}",
        ],
        check=True,
    )

    # 보조 서버에서 해시 확인·최종 이름 변경·4개 순환 보관을 한 번에 처리합니다.
    remote_code = """
import hashlib
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
upload = root / sys.argv[2]
name = sys.argv[3]
expected = sys.argv[4]
retain = int(sys.argv[5])
hash_state = hashlib.sha256()
with upload.open("rb") as source:
    for chunk in iter(lambda: source.read(1024 * 1024), b""):
        hash_state.update(chunk)
digest = hash_state.hexdigest()
if digest != expected:
    upload.unlink(missing_ok=True)
    raise SystemExit("replica checksum mismatch")
final = root / name
upload.replace(final)
checksum = root / f"{name}.sha256"
temporary_checksum = root / f".{name}.sha256.tmp"
temporary_checksum.write_text(f"{digest}  {name}\\n", encoding="utf-8")
temporary_checksum.replace(checksum)
pattern = re.compile(r"^ranking-\\d{8}T\\d{6}Z\\.db\\.gz$")
archives = sorted(
    (path for path in root.iterdir() if pattern.fullmatch(path.name)),
    key=lambda path: path.name,
)
for old in archives[:-retain]:
    old.unlink()
    (root / f"{old.name}.sha256").unlink(missing_ok=True)
print(final)
"""
    encoded = base64.b64encode(remote_code.encode("utf-8")).decode("ascii")
    loader = f"import base64;exec(base64.b64decode({encoded!r}))"
    _ssh_command(
        identity,
        target,
        [
            "python3",
            "-c",
            loader,
            remote_dir,
            uploading_name,
            archive.name,
            digest,
            str(retain),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="주간 랭킹 DB 백업")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--retain", type=int, default=4)
    parser.add_argument("--replica-target", required=True)
    parser.add_argument("--replica-dir", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    args = parser.parse_args()
    if fcntl is None:
        raise RuntimeError("Weekly backup locking requires Linux.")

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.backup_dir / ".weekly-backup.lock"
    with lock_path.open("w") as lock:
        # 타이머가 겹쳐 실행돼도 백업은 한 번만 수행합니다.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archive = create_local_backup(args.database, args.backup_dir)
        replicate_backup(
            archive,
            target=args.replica_target,
            remote_dir=args.replica_dir,
            identity=args.identity,
            retain=args.retain,
        )
        removed = prune_local_backups(args.backup_dir, args.retain)
    print(json.dumps({
        "backup": str(archive),
        "sha256": sha256_file(archive),
        "local_removed": [path.name for path in removed],
        "retain": args.retain,
        "replica": args.replica_target,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
