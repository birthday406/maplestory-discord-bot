"""수집 배치의 페이지 연속성과 날짜별 인원을 검사합니다."""

import json
import logging
from datetime import date, datetime, timedelta, timezone


WORLDS = {45: "Kronos", 19: "Scania", 1: "Bera", 70: "Hyperion"}
KINDS = {"world": "경험치", "legion": "유니온", "achievement": "업적"}
ACHIEVEMENT_AUDIT_WORLD = 45


def initialize_audit(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS ranking_audit_pages (
            day TEXT, kind TEXT, world INTEGER, page INTEGER, names TEXT NOT NULL,
            PRIMARY KEY(day, kind, world, page)
        );
        CREATE TABLE IF NOT EXISTS ranking_audit_ends (
            day TEXT, kind TEXT, world INTEGER, shard INTEGER, page INTEGER,
            PRIMARY KEY(day, kind, world, shard)
        );
        CREATE TABLE IF NOT EXISTS ranking_audit_reports (
            day TEXT, kind TEXT, counts TEXT NOT NULL, issues TEXT NOT NULL,
            checked_at TEXT NOT NULL, notified INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(day, kind)
        );
    """)


def record_audit(connection, records):
    # 재전송한 같은 페이지는 덮어써서 재시작을 중복 캐릭터로 오인하지 않습니다.
    for record in records:
        if record.get("audit_version") != 1:
            continue
        day = record["scan_date"]
        kind = record.get("ranking_type", "world")
        world, page = int(record["world_id"]), int(record["page_index"])
        if kind not in KINDS or world not in WORLDS or page < 1 or (page - 1) % 10:
            raise ValueError("Invalid audit ranking type/world/page")
        if record.get("completed"):
            shard = int(record["shard_index"])
            if shard not in range(4) or page < 1 or (page - 1) % 40 != shard * 10:
                raise ValueError("Invalid audit completion shard/page")
            connection.execute(
                "INSERT OR IGNORE INTO ranking_audit_ends VALUES (?, ?, ?, ?, ?)",
                (day, kind, world, shard, page),
            )
        else:
            names = [item["characterName"].casefold() for item in record["characters"]]
            completed = connection.execute(
                "SELECT 1 FROM ranking_audit_ends WHERE day=? AND kind=? AND world=? AND shard=?",
                (day, kind, world, ((page - 1) // 10) % 4),
            ).fetchone()
            # 대표 랭킹의 다음 순회가 첫 완료분의 검사 근거를 바꾸지 않게 합니다.
            action = "IGNORE" if completed else "REPLACE"
            connection.execute(
                f"INSERT OR {action} INTO ranking_audit_pages VALUES (?, ?, ?, ?, ?)",
                (day, kind, world, page, json.dumps(names)),
            )


def check_collection(store, now=None):
    """완료된 단계는 한 번 검사하고, 미완료 단계는 다음 UTC 기준일에 보고합니다."""
    now = now or datetime.now(timezone.utc)
    with store._connect() as connection:
        first = connection.execute(
            "SELECT MIN(day) FROM (SELECT MIN(day) AS day FROM ranking_audit_pages "
            "UNION ALL SELECT MIN(day) FROM ranking_audit_ends)"
        ).fetchone()[0]
        current_day = (now - timedelta(hours=17, minutes=10)).date()
        first_day = date.fromisoformat(first) if first else current_day + timedelta(days=1)
        # 전체 페이지 목록을 매번 훑지 않고 날짜만 순회합니다. 배치가 끊긴 날도 검사합니다.
        for offset in range((current_day - first_day).days + 1):
            day = (first_day + timedelta(days=offset)).isoformat()
            deadline = datetime.combine(date.fromisoformat(day) + timedelta(days=1),
                                        datetime.min.time(), timezone.utc) + timedelta(hours=17, minutes=10)
            for kind in KINDS:
                if connection.execute(
                    "SELECT 1 FROM ranking_audit_reports WHERE day=? AND kind=?", (day, kind)
                ).fetchone():
                    continue
                ends = {(r[0], r[1]): r[2] for r in connection.execute(
                    "SELECT world, shard, page FROM ranking_audit_ends WHERE day=? AND kind=?",
                    (day, kind),
                )}
                audit_worlds = (
                    (ACHIEVEMENT_AUDIT_WORLD,)
                    if kind == "achievement"
                    else WORLDS
                )
                expected = {
                    (world, shard) for world in audit_worlds for shard in range(4)
                }
                if not expected.issubset(ends) and now < deadline:
                    continue
                issues, counts = [], {}
                previous = connection.execute(
                    "SELECT counts FROM ranking_audit_reports WHERE day<? AND kind=? "
                    "AND issues='[]' ORDER BY day DESC LIMIT 1", (day, kind),
                ).fetchone()
                baseline = json.loads(previous[0]) if previous else {}
                # 당일 경험치가 없는 대표 값은 별도 대기 테이블에 정상 보관될 수 있습니다.
                snapshot_worlds = dict(connection.execute(
                    "SELECT name_key, world_id FROM ranking_snapshots WHERE snapshot_date=?",
                    (day,),
                )) if kind != "world" else {}
                retained = {r[0] for r in connection.execute(
                    "SELECT name_key FROM ranking_pending_representatives "
                    "WHERE snapshot_date=? AND ranking_type=? AND value>0 AND ranking>0",
                    (day, kind),
                )} - snapshot_worlds.keys() if kind != "world" else set()
                unknown_world = set()
                achievement_names = None
                if kind == "achievement":
                    pages = {r[0]: json.loads(r[1]) for r in connection.execute(
                        "SELECT page, names FROM ranking_audit_pages "
                        "WHERE day=? AND kind=? AND world=?",
                        (day, kind, ACHIEVEMENT_AUDIT_WORLD),
                    )}
                    missing, pending = [], []
                    for shard in range(4):
                        end = ends.get((ACHIEVEMENT_AUDIT_WORLD, shard))
                        if end is None:
                            pending.append(str(shard + 1))
                        else:
                            missing.extend(
                                page for page in range(1 + shard * 10, end, 40)
                                if page not in pages
                            )
                    if pending:
                        issues.append(
                            f"업적: 완료 표식 없음 (샤드 {', '.join(pending)})"
                        )
                    if missing:
                        sample = ', '.join(map(str, sorted(missing)[:5]))
                        issues.append(
                            f"업적: 누락 페이지 {len(missing):,}개 (시작 순위 {sample})"
                        )
                    names = [nickname for items in pages.values() for nickname in items]
                    achievement_names = set(names)
                    duplicate = len(names) - len(achievement_names)
                    if duplicate:
                        logging.getLogger(__name__).info(
                            "ranking_audit_overlap day=%s kind=%s world=global duplicates=%s",
                            day, kind, duplicate,
                        )
                    unknown_world = achievement_names - snapshot_worlds.keys()
                for world, name in WORLDS.items():
                    if kind == "achievement":
                        # 전역 업적 목록은 당일 경험치 스냅샷의 실제 월드로 나눕니다.
                        unique = {
                            nickname for nickname in achievement_names
                            if snapshot_worlds.get(nickname) == world
                        }
                    else:
                        pages = {r[0]: json.loads(r[1]) for r in connection.execute(
                            "SELECT page, names FROM ranking_audit_pages "
                            "WHERE day=? AND kind=? AND world=?",
                            (day, kind, world),
                        )}
                        missing, pending = [], []
                        for shard in range(4):
                            end = ends.get((world, shard))
                            if end is None:
                                pending.append(str(shard + 1))
                            else:
                                missing.extend(
                                    page for page in range(1 + shard * 10, end, 40)
                                    if page not in pages
                                )
                        if pending:
                            issues.append(
                                f"{name}: 완료 표식 없음 (샤드 {', '.join(pending)})"
                            )
                        if missing:
                            sample = ', '.join(map(str, sorted(missing)[:5]))
                            issues.append(
                                f"{name}: 누락 페이지 {len(missing):,}개 (시작 순위 {sample})"
                            )
                        names = [
                            nickname for items in pages.values() for nickname in items
                        ]
                        unique = set(names)
                        duplicate = len(names) - len(unique)
                        if duplicate:
                            # 공식 응답의 겹침은 집계에서 합치고 원본과 로그만 보존합니다.
                            logging.getLogger(__name__).info(
                                "ranking_audit_overlap day=%s kind=%s world=%s duplicates=%s",
                                day, kind, world, duplicate,
                            )
                    counts[str(world)] = len(unique)
                    old = baseline.get(str(world), 0)
                    if old and len(unique) * 10 <= old * 9:
                        issues.append(f"{name}: 인원 {old:,} → {len(unique):,}명 (10% 이상 감소)")
                    condition = {"world": "1=1", "legion": "legion_level>0 AND legion_rank IS NOT NULL",
                                 "achievement": "achievement_score>0 AND achievement_rank IS NOT NULL"}[kind]
                    saved = {r[0] for r in connection.execute(
                        "SELECT name_key FROM ranking_snapshots WHERE snapshot_date=? "
                        f"AND world_id=? AND level>=260 AND {condition}", (day, world),
                    )}
                    waiting = unique & retained
                    if waiting:
                        logging.getLogger(__name__).info(
                            "ranking_audit_pending day=%s kind=%s world=%s retained=%s",
                            day, kind, world, len(waiting),
                        )
                    absent = unique - saved - waiting
                    if absent:
                        issues.append(f"{name}: 배치에 있지만 DB에 없는 캐릭터 {len(absent):,}명")
                    if kind == "world" and not unique:
                        issues.append(f"{name}: 경험치 수집 인원 0명")
                if unknown_world:
                    waiting = unknown_world & retained
                    absent = unknown_world - retained
                    if waiting:
                        logging.getLogger(__name__).info(
                            "ranking_audit_pending day=%s kind=%s world=unknown retained=%s",
                            day, kind, len(waiting),
                        )
                    if absent:
                        issues.append(f"업적: 월드 미확인·DB 저장 누락 {len(absent):,}명")
                connection.execute(
                    "INSERT INTO ranking_audit_reports(day,kind,counts,issues,checked_at) VALUES(?,?,?,?,?)",
                    (day, kind, json.dumps(counts), json.dumps(issues, ensure_ascii=False), now.isoformat()),
                )
        return [dict(row) for row in connection.execute(
            "SELECT * FROM ranking_audit_reports WHERE notified=0 ORDER BY day,kind"
        )]


def acknowledge_report(store, day, kind):
    with store._connect() as connection:
        connection.execute("UPDATE ranking_audit_reports SET notified=1 WHERE day=? AND kind=?", (day, kind))
