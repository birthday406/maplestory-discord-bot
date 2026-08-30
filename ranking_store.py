import asyncio
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Awaitable, Callable

from maple_data import LEVEL_EXP


KRONOS_WORLD_ID = 45
MIN_TRACKED_LEVEL = 260
RANKING_PAGE_SIZE = 10


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
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
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
                    updated_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ranking_snapshots (
                    name_key TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    exp INTEGER NOT NULL,
                    ranking INTEGER NOT NULL,
                    PRIMARY KEY (name_key, snapshot_date)
                );

                CREATE TABLE IF NOT EXISTS ranking_scan_state (
                    world_id INTEGER PRIMARY KEY,
                    scan_date TEXT NOT NULL,
                    next_index INTEGER NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS ranking_collector_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    consecutive_limit_failures INTEGER NOT NULL DEFAULT 0,
                    retry_until INTEGER NOT NULL DEFAULT 0,
                    active_pages_date TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS ranking_preferences (
                    discord_user_id INTEGER PRIMARY KEY,
                    character_name TEXT NOT NULL
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
            # 기존 ranking.db도 지우지 않고 메창력에 필요한 열만 안전하게 추가합니다.
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
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE characters ADD COLUMN {name} {definition}")
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

    def get_default_character(self, discord_user_id: int) -> str | None:
        """사용자가 이름 없이 /랭킹을 실행했을 때 사용할 캐릭터를 반환합니다."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT character_name FROM ranking_preferences WHERE discord_user_id = ?",
                (discord_user_id,),
            ).fetchone()
        return row["character_name"] if row is not None else None

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
                "SELECT scan_date, next_index, completed FROM ranking_scan_state WHERE world_id = ?",
                (world_id,),
            ).fetchone()
            if row is not None:
                if row["scan_date"] != day:
                    # 매일 높은 레벨 캐릭터부터 먼저 갱신하고 남는 시간에 하위 순위를 읽습니다.
                    connection.execute(
                        """
                        UPDATE ranking_scan_state
                        SET scan_date = ?, next_index = 1, completed = 0
                        WHERE world_id = ?
                        """,
                        (day, world_id),
                    )
                    return 1
                if not row["completed"]:
                    return row["next_index"]
                return None
            connection.execute(
                """
                INSERT INTO ranking_scan_state (world_id, scan_date, next_index, completed)
                VALUES (?, ?, 1, 0)
                ON CONFLICT(world_id) DO UPDATE SET
                    scan_date = excluded.scan_date,
                    next_index = 1,
                    completed = 0
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

    def prepare_active_pages(self, scan_date: date) -> None:
        """최근 7일 내 경험치가 변했거나 아직 판별 자료가 부족한 페이지를 준비합니다."""
        day = scan_date.isoformat()
        cutoff = (scan_date - timedelta(days=7)).isoformat()
        with self._connect() as connection:
            prepared = connection.execute(
                "SELECT active_pages_date FROM ranking_collector_state WHERE id = 1"
            ).fetchone()["active_pages_date"]
            if prepared == day:
                return
            connection.execute("DELETE FROM ranking_active_pages")
            connection.execute(
                """
                WITH activity AS (
                    SELECT
                        character.world_id,
                        character.scan_page_index,
                        MIN(snapshot.snapshot_date) AS first_snapshot,
                        MAX(CASE
                            WHEN snapshot.level != character.level
                              OR snapshot.exp != character.exp
                            THEN snapshot.snapshot_date
                        END) AS last_change
                    FROM characters AS character
                    LEFT JOIN ranking_snapshots AS snapshot
                        ON snapshot.name_key = character.name_key
                    WHERE character.scan_page_index IS NOT NULL
                      AND character.level >= ?
                    GROUP BY character.name_key
                )
                INSERT OR IGNORE INTO ranking_active_pages
                    (scan_date, world_id, page_index, refreshed)
                SELECT ?, world_id, scan_page_index, 0
                FROM activity
                WHERE first_snapshot IS NULL
                   OR first_snapshot > ?
                   OR last_change >= ?
                """,
                (MIN_TRACKED_LEVEL, day, cutoff, cutoff),
            )
            connection.execute(
                "UPDATE ranking_collector_state SET active_pages_date = ? WHERE id = 1",
                (day,),
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
            refreshed = {
                row["page_index"]
                for row in connection.execute(
                    """
                    SELECT page_index
                    FROM ranking_active_pages
                    WHERE scan_date = ? AND world_id = ? AND refreshed = 1
                      AND page_index >= ?
                    """,
                    (scan_date.isoformat(), world_id, page_index),
                ).fetchall()
            }
            next_index = page_index
            while next_index in refreshed:
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
    ) -> None:
        """한 페이지와 다음 시작 순번을 같은 작업으로 저장합니다."""
        day = scan_date.isoformat()
        with self._connect() as connection:
            for character in characters:
                name_key = character["characterName"].casefold()
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
                    source_page_index,
                    day,
                )
                connection.execute(
                    """
                    INSERT INTO characters
                        (name_key, name, world_id, job_name, level, exp, ranking, image_url,
                         legion_level, legion_rank, achievement_score, achievement_rank,
                         scan_page_index, updated_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        updated_date = excluded.updated_date
                    """,
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO ranking_snapshots
                        (name_key, snapshot_date, level, exp, ranking)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(name_key, snapshot_date) DO UPDATE SET
                        level = excluded.level,
                        exp = excluded.exp,
                        ranking = excluded.ranking
                    """,
                    (
                        name_key,
                        day,
                        character["level"],
                        character.get("exp", 0),
                        character["rank"],
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
        return self.get_gains(character["characterName"])

    def finish_scan(self, scan_date: date, world_id: int = KRONOS_WORLD_ID) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE ranking_scan_state
                SET completed = 1
                WHERE world_id = ?
                """,
                (world_id,),
            )

    def get_gains(self, character_name: str, limit: int = 14) -> list[dict]:
        """최근 15개 기록을 비교해 날짜별 경험치 증가량을 반환합니다."""
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
        with self._connect() as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        with sqlite3.connect(backup_path) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("Ranking backup integrity check failed.")
            return connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0]


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
                store.finish_scan(scan_date, world_id=scan_id)
                return {"saved": saved, "next_index": cursor, "reason": "end"}

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
                store.finish_scan(scan_date, world_id=scan_id)
                return {"saved": saved, "next_index": cursor, "reason": "level_boundary"}
            if max_characters is not None and processed >= max_characters:
                store.finish_scan(scan_date, world_id=scan_id)
                return {"saved": saved, "next_index": cursor, "reason": "limit"}

        if max_pages is not None:
            return {"saved": saved, "next_index": cursor, "reason": "batch"}
        if delay_seconds > 0 and (max_characters is None or processed < max_characters):
            await asyncio.sleep(delay_seconds)

    store.finish_scan(scan_date, world_id=scan_id)
    return {"saved": saved, "next_index": cursor, "reason": reason}
