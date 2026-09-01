import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from maple_data import LEVEL_EXP


KRONOS_WORLD_ID = 45
MIN_TRACKED_LEVEL = 260
RANKING_PAGE_SIZE = 10


class _ClosingConnection(sqlite3.Connection):
    """트랜잭션 종료와 함께 파일 핸들도 닫는 SQLite 연결입니다."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def ranking_total_exp(level: int, current_exp: int) -> int | None:
    """Lv.200 이상 캐릭터의 현재 위치를 누적 경험치로 바꿉니다."""
    if not 200 <= level <= 300:
        return None
    return sum(LEVEL_EXP[: level - 200]) + current_exp


class RankingStore:
    """랭킹 현재값·날짜별 기록·중단 지점을 한 SQLite 파일에 저장합니다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=30,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            # 수집기의 쓰기 작업이 /랭킹 읽기를 막지 않도록 WAL을 사용합니다.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    name_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    world_id INTEGER NOT NULL,
                    job_name TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    exp INTEGER NOT NULL,
                    ranking INTEGER NOT NULL,
                    image_url TEXT,
                    legion_level INTEGER NOT NULL DEFAULT 0,
                    legion_rank INTEGER,
                    achievement_score INTEGER NOT NULL DEFAULT 0,
                    achievement_rank INTEGER,
                    scan_page_index INTEGER,
                    last_changed_date TEXT,
                    updated_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ranking_snapshots (
                    name_key TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    exp INTEGER NOT NULL,
                    ranking INTEGER NOT NULL,
                    world_id INTEGER,
                    job_name TEXT,
                    legion_level INTEGER,
                    legion_rank INTEGER,
                    achievement_score INTEGER,
                    achievement_rank INTEGER,
                    PRIMARY KEY (name_key, snapshot_date)
                );

                CREATE INDEX IF NOT EXISTS idx_ranking_snapshots_date
                ON ranking_snapshots (snapshot_date);

                CREATE TABLE IF NOT EXISTS nickname_changes (
                    old_name_key TEXT NOT NULL,
                    old_name TEXT NOT NULL,
                    new_name_key TEXT NOT NULL,
                    new_name TEXT NOT NULL,
                    old_snapshot_date TEXT NOT NULL,
                    new_snapshot_date TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (old_name_key, new_name_key, new_snapshot_date)
                );

                CREATE TABLE IF NOT EXISTS ranking_scan_state (
                    world_id INTEGER PRIMARY KEY,
                    scan_date TEXT NOT NULL,
                    next_index INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    started_at INTEGER,
                    completed_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS ranking_collector_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    consecutive_limit_failures INTEGER NOT NULL DEFAULT 0,
                    retry_until INTEGER NOT NULL DEFAULT 0,
                    active_pages_date TEXT NOT NULL DEFAULT '',
                    active_pages_worlds TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS ranking_preferences (
                    discord_user_id INTEGER PRIMARY KEY,
                    character_name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ranking_profiles (
                    name_key TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ranking_populations (
                    snapshot_date TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    world_id INTEGER NOT NULL,
                    population INTEGER NOT NULL,
                    PRIMARY KEY (snapshot_date, metric, world_id)
                );

                CREATE TABLE IF NOT EXISTS ranking_priority_characters (
                    name_key TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    last_requested_at INTEGER NOT NULL,
                    last_refreshed_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ranking_active_pages (
                    scan_date TEXT NOT NULL,
                    world_id INTEGER NOT NULL,
                    page_index INTEGER NOT NULL,
                    refreshed INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scan_date, world_id, page_index)
                );

                CREATE INDEX IF NOT EXISTS idx_active_pages_queue
                ON ranking_active_pages (scan_date, refreshed, page_index, world_id);

                CREATE TABLE IF NOT EXISTS guild_ranking_members (
                    guild_id INTEGER NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    discord_display_name TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    character_name_key TEXT NOT NULL,
                    PRIMARY KEY (guild_id, discord_user_id)
                );

                INSERT OR IGNORE INTO ranking_collector_state
                    (id, consecutive_limit_failures, retry_until)
                VALUES (1, 0, 0);
                """
            )
            # 기존 ranking.db도 지우지 않고 종합 지수에 필요한 열만 안전하게 추가합니다.
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(characters)").fetchall()
            }
            for name, definition in (
                ("legion_level", "INTEGER NOT NULL DEFAULT 0"),
                ("legion_rank", "INTEGER"),
                ("achievement_score", "INTEGER NOT NULL DEFAULT 0"),
                ("achievement_rank", "INTEGER"),
                ("scan_page_index", "INTEGER"),
                ("last_changed_date", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE characters ADD COLUMN {name} {definition}")
            snapshot_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(ranking_snapshots)"
                ).fetchall()
            }
            for name, definition in (
                ("world_id", "INTEGER"),
                ("job_name", "TEXT"),
                ("legion_level", "INTEGER"),
                ("legion_rank", "INTEGER"),
                ("achievement_score", "INTEGER"),
                ("achievement_rank", "INTEGER"),
            ):
                if name not in snapshot_columns:
                    connection.execute(
                        f"ALTER TABLE ranking_snapshots ADD COLUMN {name} {definition}"
                    )
            # 기존 캐릭터는 일단 마지막 정상 수집일을 기준으로 잡습니다. 이후부터는
            # 레벨/경험치가 실제로 바뀐 날만 갱신되므로 과거 전체 조인이 필요 없습니다.
            connection.execute(
                """
                UPDATE characters
                SET last_changed_date = updated_date
                WHERE last_changed_date IS NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_characters_active_scan
                ON characters (last_changed_date, world_id, scan_page_index)
                """
            )
            scan_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(ranking_scan_state)"
                ).fetchall()
            }
            for name, definition in (
                ("started_at", "INTEGER"),
                ("completed_at", "INTEGER"),
            ):
                if name not in scan_columns:
                    connection.execute(
                        f"ALTER TABLE ranking_scan_state ADD COLUMN {name} {definition}"
                    )
            collector_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(ranking_collector_state)"
                ).fetchall()
            }
            if "active_pages_date" not in collector_columns:
                connection.execute(
                    "ALTER TABLE ranking_collector_state "
                    "ADD COLUMN active_pages_date TEXT NOT NULL DEFAULT ''"
                )
            if "active_pages_worlds" not in collector_columns:
                connection.execute(
                    "ALTER TABLE ranking_collector_state "
                    "ADD COLUMN active_pages_worlds TEXT NOT NULL DEFAULT ''"
                )

    def save_default_character(self, discord_user_id: int, character_name: str) -> None:
        """사용자가 마지막으로 직접 조회한 캐릭터 이름을 기억합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ranking_preferences (discord_user_id, character_name)
                VALUES (?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET
                    character_name = excluded.character_name
                """,
                (discord_user_id, character_name),
            )

    def detect_nickname_changes(
        self,
        old_date: date,
        new_date: date,
        min_snapshot_count: int = 700_000,
        min_size_ratio: float = 0.95,
    ) -> dict:
        """연속된 완전 수집일만 비교해 닉네임 변경 후보를 저장합니다."""
        if new_date - old_date != timedelta(days=1):
            return {"saved": 0, "reason": "dates_not_consecutive"}
        old_day, new_day = old_date.isoformat(), new_date.isoformat()
        with self._connect() as connection:
            old_count = connection.execute(
                "SELECT COUNT(*) FROM ranking_snapshots WHERE snapshot_date = ?",
                (old_day,),
            ).fetchone()[0]
            new_count = connection.execute(
                "SELECT COUNT(*) FROM ranking_snapshots WHERE snapshot_date = ?",
                (new_day,),
            ).fetchone()[0]
            if (
                min(old_count, new_count) < min_snapshot_count
                or min(old_count, new_count) / max(old_count, new_count, 1)
                < min_size_ratio
            ):
                return {
                    "saved": 0,
                    "reason": "incomplete_snapshot",
                    "counts": [old_count, new_count],
                }
            disappeared = connection.execute(
                """SELECT old.name_key, character.name,
                          COALESCE(old.world_id, character.world_id) AS world_id,
                          COALESCE(old.job_name, character.job_name) AS job_name,
                          old.level, old.exp, old.ranking
                     FROM ranking_snapshots AS old
                     JOIN characters AS character ON character.name_key = old.name_key
                    WHERE old.snapshot_date = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM ranking_snapshots AS current
                           WHERE current.snapshot_date = ?
                             AND current.name_key = old.name_key
                      )""",
                (old_day, new_day),
            ).fetchall()
            appeared = connection.execute(
                """SELECT current.name_key, character.name,
                          COALESCE(current.world_id, character.world_id) AS world_id,
                          COALESCE(current.job_name, character.job_name) AS job_name,
                          current.level, current.exp, current.ranking
                     FROM ranking_snapshots AS current
                     JOIN characters AS character ON character.name_key = current.name_key
                    WHERE current.snapshot_date = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM ranking_snapshots AS old
                           WHERE old.snapshot_date = ?
                             AND old.name_key = current.name_key
                      )""",
                (new_day, old_day),
            ).fetchall()
            appeared_by_identity: dict[tuple[int, str, int], list] = {}
            for item in appeared:
                appeared_by_identity.setdefault(
                    (item["world_id"], item["job_name"], item["level"]), []
                ).append(item)

            proposals = []
            for old in disappeared:
                matches = []
                for level in (old["level"], old["level"] + 1):
                    for new in appeared_by_identity.get(
                        (old["world_id"], old["job_name"], level), []
                    ):
                        old_total = ranking_total_exp(old["level"], old["exp"])
                        new_total = ranking_total_exp(new["level"], new["exp"])
                        if old_total is None or new_total is None or new_total < old_total:
                            continue
                        score = 20 if new["level"] == old["level"] else 15
                        gain = new_total - old_total
                        level_exp = LEVEL_EXP[min(old["level"] - 200, len(LEVEL_EXP) - 1)]
                        if gain == 0:
                            score += 40
                        elif gain <= level_exp // 10:
                            score += 30
                        elif gain <= level_exp:
                            score += 20
                        else:
                            continue
                        rank_gap = abs(new["ranking"] - old["ranking"])
                        score += 10 if rank_gap <= 100 else 5 if rank_gap <= 500 else 0
                        if score >= 50:
                            matches.append((score, new))
                matches.sort(key=lambda item: item[0], reverse=True)
                if matches:
                    ambiguous = len(matches) > 1 and matches[0][0] - matches[1][0] < 10
                    proposals.append((matches[0][0], old, matches[0][1], ambiguous))

            used_old, used_new, saved = set(), set(), 0
            for score, old, new, ambiguous in sorted(proposals, reverse=True, key=lambda x: x[0]):
                if old["name_key"] in used_old or new["name_key"] in used_new:
                    continue
                confidence = "HIGH" if score >= 65 and not ambiguous else "POSSIBLE"
                status = "AMBIGUOUS" if ambiguous else "PENDING"
                connection.execute(
                    """INSERT INTO nickname_changes
                        (old_name_key, old_name, new_name_key, new_name,
                         old_snapshot_date, new_snapshot_date, score, confidence, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(old_name_key, new_name_key, new_snapshot_date)
                       DO UPDATE SET score=excluded.score,
                                     confidence=excluded.confidence,
                                     status=excluded.status""",
                    (
                        old["name_key"], old["name"], new["name_key"], new["name"],
                        old_day, new_day, score, confidence, status,
                    ),
                )
                used_old.add(old["name_key"])
                used_new.add(new["name_key"])
                saved += 1
        return {"saved": saved, "reason": "ok", "counts": [old_count, new_count]}

    def get_nickname_trace(self, nickname: str) -> list[dict]:
        """과거 이름이나 현재 이름 어느 쪽으로 조회해도 저장된 변경 연결을 반환합니다."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM nickname_changes WHERE status != 'REJECTED'"
            ).fetchall()
        previous = {row["new_name_key"]: row for row in rows}
        following = {row["old_name_key"]: row for row in rows}
        key = nickname.casefold()
        seen = set()
        while key in previous and key not in seen:
            seen.add(key)
            key = previous[key]["old_name_key"]
        result = []
        seen.clear()
        while key in following and key not in seen:
            seen.add(key)
            row = following[key]
            result.append(dict(row))
            key = row["new_name_key"]
        return result

    def get_default_character(self, discord_user_id: int) -> str | None:
        """사용자가 이름 없이 /랭킹을 실행했을 때 사용할 캐릭터를 반환합니다."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT character_name FROM ranking_preferences WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchone()
        return row["character_name"] if row is not None else None

    def save_ranking_profile(self, character_name: str, profile: tuple) -> None:
        """명령어 카드에 필요한 공식 랭킹 응답을 다음 조회용으로 저장합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ranking_profiles (name_key, profile_json, updated_at)
                VALUES (?, ?, CAST(strftime('%s', 'now') AS INTEGER))
                ON CONFLICT(name_key) DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (
                    character_name.casefold(),
                    json.dumps(profile[:5], ensure_ascii=False),
                ),
            )

    def get_ranking_profile(self, character_name: str) -> tuple | None:
        """마지막으로 정상 조회한 전체 프로필을 반환합니다."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT profile_json FROM ranking_profiles WHERE name_key = ?",
                (character_name.casefold(),),
            ).fetchone()
        if row is None:
            return None
        try:
            profile = json.loads(row["profile_json"])
        except (json.JSONDecodeError, TypeError):
            return None
        return tuple(profile) if len(profile) == 5 else None

    def save_population(
        self,
        scan_date: date,
        metric: str,
        population: int,
        world_id: int = 0,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ranking_populations
                    (snapshot_date, metric, world_id, population)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(snapshot_date, metric, world_id) DO UPDATE SET
                    population = excluded.population
                """,
                (scan_date.isoformat(), metric, world_id, population),
            )

    def get_population(
        self,
        metric: str,
        world_id: int = 0,
        scan_date: date | None = None,
    ) -> int | None:
        conditions = "metric = ? AND world_id = ?"
        parameters: list = [metric, world_id]
        if scan_date is not None:
            conditions += " AND snapshot_date = ?"
            parameters.append(scan_date.isoformat())
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT population
                FROM ranking_populations
                WHERE {conditions}
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return row["population"] if row is not None else None

    def get_ai_score_populations(self, world_id: int) -> dict[str, int | None]:
        return {
            "level_population": self.get_population("level_260_plus"),
            "legion_population": self.get_population("legion", world_id),
            "achievement_population": self.get_population("achievement", world_id),
        }

    def register_guild_character(
        self,
        guild_id: int,
        discord_user_id: int,
        discord_display_name: str,
        character_name: str,
    ) -> None:
        """한 Discord 서버 안에서만 사용자의 현재 기본 캐릭터를 등록합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO guild_ranking_members
                    (guild_id, discord_user_id, discord_display_name, character_name, character_name_key)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, discord_user_id) DO UPDATE SET
                    discord_display_name = excluded.discord_display_name,
                    character_name = excluded.character_name,
                    character_name_key = excluded.character_name_key
                """,
                (
                    guild_id,
                    discord_user_id,
                    discord_display_name,
                    character_name,
                    character_name.casefold(),
                ),
            )

    def unregister_guild_character(self, guild_id: int, discord_user_id: int) -> bool:
        """현재 서버에서만 사용자의 랭킹 등록을 제거합니다."""
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM guild_ranking_members WHERE guild_id = ? AND discord_user_id = ?",
                (guild_id, discord_user_id),
            )
        return result.rowcount > 0

    def get_guild_rankings(self, guild_id: int) -> list[dict]:
        """현재 서버에 등록된 캐릭터의 마지막 저장 정보를 반환합니다."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    member.discord_display_name,
                    member.character_name,
                    character.world_id,
                    character.level,
                    character.exp,
                    character.ranking,
                    character.legion_level,
                    character.legion_rank,
                    character.achievement_score,
                    character.achievement_rank,
                    character.updated_date
                FROM guild_ranking_members AS member
                LEFT JOIN characters AS character
                    ON character.name_key = member.character_name_key
                WHERE member.guild_id = ?
                """,
                (guild_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def start_scan(
        self,
        scan_date: date,
        world_id: int = KRONOS_WORLD_ID,
    ) -> int | None:
        """중단 지점부터 이어가고, 완료한 수집은 다음 날짜에만 다시 시작합니다."""
        day = scan_date.isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT scan_date, next_index, completed, started_at
                FROM ranking_scan_state
                WHERE world_id = ?
                """,
                (world_id,),
            ).fetchone()
            if row is not None:
                if row["scan_date"] != day:
                    # 매일 높은 레벨 캐릭터부터 먼저 갱신하고 남는 시간에 하위 순위를 읽습니다.
                    connection.execute(
                        """
                        UPDATE ranking_scan_state
                        SET scan_date = ?, next_index = 1, completed = 0,
                            started_at = CAST(strftime('%s', 'now') AS INTEGER),
                            completed_at = NULL
                        WHERE world_id = ?
                        """,
                        (day, world_id),
                    )
                    return 1
                if not row["completed"]:
                    if row["started_at"] is None:
                        connection.execute(
                            """
                            UPDATE ranking_scan_state
                            SET started_at = CAST(strftime('%s', 'now') AS INTEGER)
                            WHERE world_id = ?
                            """,
                            (world_id,),
                        )
                    return row["next_index"]
                return None
            connection.execute(
                """
                INSERT INTO ranking_scan_state
                    (world_id, scan_date, next_index, completed, started_at, completed_at)
                VALUES (?, ?, 1, 0, CAST(strftime('%s', 'now') AS INTEGER), NULL)
                ON CONFLICT(world_id) DO UPDATE SET
                    scan_date = excluded.scan_date,
                    next_index = 1,
                    completed = 0,
                    started_at = excluded.started_at,
                    completed_at = NULL
                """,
                (world_id, day),
            )
        return 1

    def get_collector_backoff(self) -> tuple[int, int]:
        """재시작 뒤에도 차단 직후 요청을 반복하지 않도록 대기 상태를 읽습니다."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT consecutive_limit_failures, retry_until
                FROM ranking_collector_state
                WHERE id = 1
                """
            ).fetchone()
        return row["consecutive_limit_failures"], row["retry_until"]

    def set_collector_backoff(self, failures: int, retry_until: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ranking_collector_state
                SET consecutive_limit_failures = ?, retry_until = ?
                WHERE id = 1
                """,
                (failures, retry_until),
            )

    def clear_collector_backoff(self) -> None:
        self.set_collector_backoff(0, 0)

    def prioritize_character(self, character_name: str, refreshed_date: date) -> None:
        """Discord 명령어로 조회한 캐릭터를 다음 날 최우선 갱신 대상으로 기억합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ranking_priority_characters
                    (name_key, name, last_requested_at, last_refreshed_date)
                VALUES (?, ?, CAST(strftime('%s', 'now') AS INTEGER), ?)
                ON CONFLICT(name_key) DO UPDATE SET
                    name = excluded.name,
                    last_requested_at = excluded.last_requested_at,
                    last_refreshed_date = excluded.last_refreshed_date
                """,
                (character_name.casefold(), character_name, refreshed_date.isoformat()),
            )

    def queue_priority_refresh(self, character_name: str) -> None:
        """저장값은 바로 보여주되 공식 프로필은 수집 루프에서 다시 읽게 합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ranking_priority_characters
                    (name_key, name, last_requested_at, last_refreshed_date)
                VALUES (?, ?, CAST(strftime('%s', 'now') AS INTEGER), '')
                ON CONFLICT(name_key) DO UPDATE SET
                    name = excluded.name,
                    last_requested_at = excluded.last_requested_at,
                    last_refreshed_date = ''
                """,
                (character_name.casefold(), character_name),
            )

    def next_priority_character(self, scan_date: date) -> str | None:
        """오늘 아직 갱신하지 않은 명령어 조회 캐릭터 중 최근 조회 대상을 반환합니다."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT priority.name
                FROM ranking_priority_characters AS priority
                JOIN characters AS character ON character.name_key = priority.name_key
                WHERE priority.last_refreshed_date < ?
                  AND character.level >= ?
                ORDER BY priority.last_requested_at DESC
                LIMIT 1
                """,
                (scan_date.isoformat(), MIN_TRACKED_LEVEL),
            ).fetchone()
        return row["name"] if row is not None else None

    def mark_priority_refreshed(self, character_name: str, scan_date: date) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ranking_priority_characters
                SET last_refreshed_date = ?
                WHERE name_key = ?
                """,
                (scan_date.isoformat(), character_name.casefold()),
            )

    def prepare_active_pages(
        self,
        scan_date: date,
        world_ids: tuple[int, ...] | None = None,
    ) -> None:
        """최근 7일 내 경험치가 변한 캐릭터가 있는 페이지를 준비합니다."""
        day = scan_date.isoformat()
        cutoff = (scan_date - timedelta(days=7)).isoformat()
        world_signature = ",".join(map(str, world_ids)) if world_ids else "*"
        world_filter = ""
        parameters: list[str | int] = [day, MIN_TRACKED_LEVEL, cutoff]
        if world_ids:
            world_filter = f"AND world_id IN ({','.join('?' for _ in world_ids)})"
            parameters.extend(world_ids)
        with self._connect() as connection:
            prepared = connection.execute(
                """
                SELECT active_pages_date, active_pages_worlds
                FROM ranking_collector_state
                WHERE id = 1
                """
            ).fetchone()
            if (
                prepared["active_pages_date"] == day
                and prepared["active_pages_worlds"] == world_signature
            ):
                return
            connection.execute("DELETE FROM ranking_active_pages")
            connection.execute(
                """
                INSERT OR IGNORE INTO ranking_active_pages
                    (scan_date, world_id, page_index, refreshed)
                SELECT ?, world_id, scan_page_index, 0
                FROM characters
                WHERE scan_page_index IS NOT NULL
                  AND level >= ?
                  AND last_changed_date >= ?
                  {world_filter}
                GROUP BY world_id, scan_page_index
                """.format(world_filter=world_filter),
                parameters,
            )
            connection.execute(
                """
                UPDATE ranking_collector_state
                SET active_pages_date = ?, active_pages_worlds = ?
                WHERE id = 1
                """,
                (day, world_signature),
            )

    def next_active_page(self, scan_date: date) -> tuple[int, int] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT world_id, page_index
                FROM ranking_active_pages
                WHERE scan_date = ? AND refreshed = 0
                ORDER BY page_index, world_id
                LIMIT 1
                """,
                (scan_date.isoformat(),),
            ).fetchone()
        return (row["world_id"], row["page_index"]) if row is not None else None

    def mark_active_page_refreshed(
        self, scan_date: date, world_id: int, page_index: int
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ranking_active_pages
                SET refreshed = 1
                WHERE scan_date = ? AND world_id = ? AND page_index = ?
                """,
                (scan_date.isoformat(), world_id, page_index),
            )

    def skip_refreshed_active_pages(
        self, scan_date: date, world_id: int, page_index: int
    ) -> int:
        """우선 수집을 마친 연속 페이지를 순차 수집에서 다시 요청하지 않습니다."""
        with self._connect() as connection:
            next_index = page_index
            while connection.execute(
                """SELECT 1 FROM ranking_active_pages
                    WHERE scan_date = ? AND world_id = ?
                      AND page_index = ? AND refreshed = 1""",
                (scan_date.isoformat(), world_id, next_index),
            ).fetchone() is not None:
                next_index += RANKING_PAGE_SIZE
            if next_index != page_index:
                connection.execute(
                    "UPDATE ranking_scan_state SET next_index = ? WHERE world_id = ?",
                    (next_index, world_id),
                )
        return next_index

    def save_page(
        self,
        characters: list[dict],
        scan_date: date,
        next_index: int,
        world_id: int = KRONOS_WORLD_ID,
        update_checkpoint: bool = True,
        source_page_index: int | None = None,
        preserve_newer: bool = False,
    ) -> None:
        """한 페이지와 다음 시작 순번을 같은 작업으로 저장합니다."""
        day = scan_date.isoformat()
        with self._connect() as connection:
            for character in characters:
                name_key = character["characterName"].casefold()
                if not preserve_newer:
                    connection.execute(
                        "DELETE FROM ranking_snapshots WHERE name_key = ? AND snapshot_date > ?",
                        (name_key, day),
                    )
                values = (
                    name_key,
                    character["characterName"],
                    character["worldID"],
                    character["jobName"],
                    character["level"],
                    character.get("exp", 0),
                    character["rank"],
                    character.get("characterImgURL"),
                    character.get("legionLevel", 0),
                    character.get("legionRank"),
                    character.get("achievementScore", 0),
                    character.get("achievementRank"),
                    character.get("_scanPageIndex", source_page_index),
                    day,
                    day,
                )
                connection.execute(
                    """
                    INSERT INTO characters
                        (name_key, name, world_id, job_name, level, exp, ranking, image_url,
                         legion_level, legion_rank, achievement_score, achievement_rank,
                         scan_page_index, last_changed_date, updated_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name_key) DO UPDATE SET
                        name = excluded.name,
                        world_id = excluded.world_id,
                        job_name = excluded.job_name,
                        level = excluded.level,
                        exp = excluded.exp,
                        ranking = excluded.ranking,
                        image_url = excluded.image_url,
                        legion_level = CASE
                            WHEN excluded.legion_level > 0 THEN excluded.legion_level
                            ELSE characters.legion_level
                        END,
                        legion_rank = COALESCE(excluded.legion_rank, characters.legion_rank),
                        achievement_score = CASE
                            WHEN excluded.achievement_score > 0 THEN excluded.achievement_score
                            ELSE characters.achievement_score
                        END,
                        achievement_rank = COALESCE(
                            excluded.achievement_rank, characters.achievement_rank
                        ),
                        scan_page_index = COALESCE(
                            excluded.scan_page_index, characters.scan_page_index
                        ),
                        last_changed_date = CASE
                            WHEN excluded.level != characters.level
                              OR excluded.exp != characters.exp
                            THEN excluded.updated_date
                            ELSE COALESCE(
                                characters.last_changed_date,
                                excluded.updated_date
                            )
                        END,
                        updated_date = excluded.updated_date
                    {newer_guard}
                    """.format(
                        newer_guard=(
                            "WHERE excluded.updated_date > characters.updated_date"
                            if preserve_newer
                            else ""
                        )
                    ),
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO ranking_snapshots
                        (name_key, snapshot_date, level, exp, ranking, world_id, job_name,
                         legion_level, legion_rank, achievement_score, achievement_rank)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    {snapshot_conflict}
                    """.format(
                        snapshot_conflict=(
                            "ON CONFLICT(name_key, snapshot_date) DO NOTHING"
                            if preserve_newer
                            else """
                            ON CONFLICT(name_key, snapshot_date) DO UPDATE SET
                                level = excluded.level,
                                exp = excluded.exp,
                                ranking = excluded.ranking,
                                world_id = excluded.world_id,
                                job_name = excluded.job_name,
                                legion_level = COALESCE(
                                    excluded.legion_level, ranking_snapshots.legion_level
                                ),
                                legion_rank = COALESCE(
                                    excluded.legion_rank, ranking_snapshots.legion_rank
                                ),
                                achievement_score = COALESCE(
                                    excluded.achievement_score,
                                    ranking_snapshots.achievement_score
                                ),
                                achievement_rank = COALESCE(
                                    excluded.achievement_rank,
                                    ranking_snapshots.achievement_rank
                                )
                            """
                        )
                    ),
                    (
                        name_key,
                        day,
                        character["level"],
                        character.get("exp", 0),
                        character["rank"],
                        character["worldID"],
                        character["jobName"],
                        character.get("legionLevel"),
                        character.get("legionRank"),
                        character.get("achievementScore"),
                        character.get("achievementRank"),
                    ),
                )
            if update_checkpoint:
                connection.execute(
                    """
                    UPDATE ranking_scan_state
                    SET next_index = ?
                    WHERE world_id = ?
                    """,
                    (next_index, world_id),
                )

    def import_batch(self, batch_path: Path) -> int:
        """보조 수집기의 JSONL 묶음을 운영 DB에 중복 없이 합칩니다."""
        grouped: dict[date, list[dict]] = {}
        with batch_path.open(encoding="utf-8") as batch:
            for line in batch:
                if not line.strip():
                    continue
                record = json.loads(line)
                snapshot_date = date.fromisoformat(record["scan_date"])
                page_index = int(record["page_index"])
                for character in record["characters"]:
                    saved = dict(character)
                    saved["_scanPageIndex"] = page_index
                    grouped.setdefault(snapshot_date, []).append(saved)

        imported = 0
        for snapshot_date, characters in grouped.items():
            self.save_page(
                characters,
                snapshot_date,
                next_index=1,
                update_checkpoint=False,
                preserve_newer=True,
            )
            imported += len(characters)
        return imported

    def save_snapshot(self, character: dict, scan_date: date) -> list[dict]:
        """명령어로 조회한 한 캐릭터도 자동 수집과 같은 표에 기록합니다."""
        self.save_page(
            [character],
            scan_date,
            next_index=1,
            world_id=character["worldID"],
            update_checkpoint=False,
        )
        if character["level"] >= MIN_TRACKED_LEVEL:
            self.prioritize_character(character["characterName"], scan_date)
        return self.get_gains(character["characterName"], limit=30)

    def finish_scan(
        self, scan_date: date, world_id: int = KRONOS_WORLD_ID
    ) -> int | None:
        """수집 완료를 기록하고 시작부터 걸린 초를 반환합니다."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ranking_scan_state
                SET completed = 1,
                    completed_at = CAST(strftime('%s', 'now') AS INTEGER)
                WHERE world_id = ? AND scan_date = ?
                """,
                (world_id, scan_date.isoformat()),
            )
            row = connection.execute(
                """
                SELECT started_at, completed_at
                FROM ranking_scan_state
                WHERE world_id = ? AND scan_date = ?
                """,
                (world_id, scan_date.isoformat()),
            ).fetchone()
        if row is None or row["started_at"] is None or row["completed_at"] is None:
            return None
        return max(row["completed_at"] - row["started_at"], 0)

    def get_gains(self, character_name: str, limit: int = 14) -> list[dict]:
        """최근 15개 일별 기록을 비교해 최대 14일치 경험치 증가량을 반환합니다."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT snapshot_date, level, exp
                FROM ranking_snapshots
                WHERE name_key = ?
                ORDER BY snapshot_date DESC
                LIMIT ?
                """,
                (character_name.casefold(), limit + 1),
            ).fetchall()
        snapshots = list(reversed(rows))
        gains = []
        for previous, current in zip(snapshots, snapshots[1:]):
            previous_total = ranking_total_exp(previous["level"], previous["exp"])
            current_total = ranking_total_exp(current["level"], current["exp"])
            if previous_total is None or current_total is None or current_total < previous_total:
                continue
            gains.append(
                {
                    "date": current["snapshot_date"],
                    "exp": current_total - previous_total,
                }
            )
        return gains[-limit:]

    def remove_old_snapshots(self, keep_since: date) -> None:
        """그래프에 쓰지 않는 오래된 일별 기록이 계속 쌓이지 않게 정리합니다."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM ranking_snapshots WHERE snapshot_date < ?",
                (keep_since.isoformat(),),
            )

    def character_count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0]

    def backup_to(self, backup_path: Path) -> int:
        """실행 중에도 안전한 SQLite 백업을 만들고 저장 행 수를 확인합니다."""
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, closing(sqlite3.connect(backup_path)) as target:
            source.backup(target)
        with closing(sqlite3.connect(backup_path)) as backup:
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Ranking backup integrity check failed.")
            return backup.execute("SELECT COUNT(*) FROM characters").fetchone()[0]


async def scan_rankings(
    fetch_page: Callable[[int], Awaitable[dict]],
    store: RankingStore,
    scan_date: date,
    max_characters: int | None,
    delay_seconds: float = 1.0,
    max_pages: int | None = None,
    scan_id: int = KRONOS_WORLD_ID,
) -> dict:
    """한 월드의 상위 랭커부터 수집하고 Lv.260 경계에서 멈춥니다."""
    cursor = store.start_scan(scan_date, world_id=scan_id)
    if cursor is None:
        return {"saved": 0, "next_index": None, "reason": "already_completed"}

    cursor = store.skip_refreshed_active_pages(scan_date, scan_id, cursor)
    saved = 0
    # page_index는 해당 순위부터 시작하므로 재시작 전 처리량도 시험 한도에 포함합니다.
    processed = max(cursor - 1, 0)
    reason = "limit"
    while max_characters is None or processed < max_characters:
        # 공식 API는 페이지당 10명을 반환합니다. 실행당 여러 페이지를 동시에 읽어
        # 요청 간격은 유지하면서 한 번에 저장하는 인원을 늘립니다.
        batch_size = max_pages or 1
        page_indices = [
            cursor + RANKING_PAGE_SIZE * offset for offset in range(batch_size)
        ]
        payloads = await asyncio.gather(*(fetch_page(index) for index in page_indices))

        for page_index, payload in zip(page_indices, payloads):
            ranks = payload.get("ranks", [])
            if not ranks:
                elapsed = store.finish_scan(scan_date, world_id=scan_id)
                return {
                    "saved": saved,
                    "next_index": cursor,
                    "reason": "end",
                    "elapsed_seconds": elapsed,
                }

            eligible = [
                item for item in ranks if item.get("level", 0) >= MIN_TRACKED_LEVEL
            ]
            remaining = len(ranks) if max_characters is None else max_characters - processed
            page_to_save = eligible[:remaining]
            next_cursor = page_index + len(ranks)
            store.save_page(
                page_to_save,
                scan_date,
                next_cursor,
                world_id=scan_id,
                source_page_index=page_index,
            )
            saved += len(page_to_save)
            processed += len(ranks)
            cursor = next_cursor
            cursor = store.skip_refreshed_active_pages(scan_date, scan_id, cursor)

            if any(item.get("level", 0) < MIN_TRACKED_LEVEL for item in ranks):
                elapsed = store.finish_scan(scan_date, world_id=scan_id)
                return {
                    "saved": saved,
                    "next_index": cursor,
                    "reason": "level_boundary",
                    "elapsed_seconds": elapsed,
                }
            if max_characters is not None and processed >= max_characters:
                elapsed = store.finish_scan(scan_date, world_id=scan_id)
                return {
                    "saved": saved,
                    "next_index": cursor,
                    "reason": "limit",
                    "elapsed_seconds": elapsed,
                }

        if max_pages is not None:
            return {"saved": saved, "next_index": cursor, "reason": "batch"}
        if delay_seconds > 0 and (max_characters is None or processed < max_characters):
            await asyncio.sleep(delay_seconds)

    elapsed = store.finish_scan(scan_date, world_id=scan_id)
    return {
        "saved": saved,
        "next_index": cursor,
        "reason": reason,
        "elapsed_seconds": elapsed,
    }
