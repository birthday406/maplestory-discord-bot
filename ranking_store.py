import asyncio
import sqlite3
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable

from maple_data import LEVEL_EXP


KRONOS_WORLD_ID = 45
MIN_TRACKED_LEVEL = 260


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

                CREATE TABLE IF NOT EXISTS ranking_preferences (
                    discord_user_id INTEGER PRIMARY KEY,
                    character_name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS guild_ranking_members (
                    guild_id INTEGER NOT NULL,
                    discord_user_id INTEGER NOT NULL,
                    discord_display_name TEXT NOT NULL,
                    character_name TEXT NOT NULL,
                    character_name_key TEXT NOT NULL,
                    PRIMARY KEY (guild_id, discord_user_id)
                );
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
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE characters ADD COLUMN {name} {definition}")

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
        restart_completed: bool = False,
    ) -> int | None:
        """중단된 다음 순번부터 이어가고, 한 바퀴를 마치면 필요할 때 처음부터 시작합니다."""
        day = scan_date.isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scan_date, next_index, completed FROM ranking_scan_state WHERE world_id = ?",
                (world_id,),
            ).fetchone()
            if row is not None:
                if not row["completed"]:
                    # 날짜가 바뀌어도 아직 끝나지 않은 전체 수집은 같은 순번에서 이어갑니다.
                    return row["next_index"]
                if not restart_completed:
                    return None
                connection.execute(
                    """
                    UPDATE ranking_scan_state
                    SET scan_date = ?, next_index = 1, completed = 0
                    WHERE world_id = ?
                    """,
                    (day, world_id),
                )
                return 1
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

    def save_page(
        self,
        characters: list[dict],
        scan_date: date,
        next_index: int,
        world_id: int = KRONOS_WORLD_ID,
        update_checkpoint: bool = True,
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
                    day,
                )
                connection.execute(
                    """
                    INSERT INTO characters
                        (name_key, name, world_id, job_name, level, exp, ranking, image_url,
                         legion_level, legion_rank, achievement_score, achievement_rank, updated_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


async def scan_kronos_rankings(
    fetch_page: Callable[[int], Awaitable[dict]],
    store: RankingStore,
    scan_date: date,
    max_characters: int | None,
    delay_seconds: float = 1.0,
    max_pages: int | None = None,
    restart_completed: bool = False,
) -> dict:
    """상위 랭커부터 내려가며 지정한 분량 또는 Lv.260 경계에서 멈춥니다."""
    cursor = store.start_scan(scan_date, restart_completed=restart_completed)
    if cursor is None:
        return {"saved": 0, "next_index": None, "reason": "already_completed"}

    saved = 0
    # page_index는 해당 순위부터 시작하므로 재시작 전 처리량도 시험 한도에 포함합니다.
    processed = max(cursor - 1, 0)
    reason = "limit"
    pages = 0
    while max_characters is None or processed < max_characters:
        payload = await fetch_page(cursor)
        ranks = payload.get("ranks", [])
        if not ranks:
            reason = "end"
            break

        eligible = [item for item in ranks if item.get("level", 0) >= MIN_TRACKED_LEVEL]
        remaining = len(eligible) if max_characters is None else max_characters - processed
        page_to_save = eligible[:remaining]
        next_cursor = cursor + len(ranks)
        store.save_page(page_to_save, scan_date, next_cursor)
        saved += len(page_to_save)
        processed += len(ranks)
        cursor = next_cursor
        pages += 1

        if len(eligible) < len(ranks):
            reason = "level_boundary"
            break
        if max_pages is not None and pages >= max_pages:
            return {"saved": saved, "next_index": cursor, "reason": "batch"}
        if delay_seconds > 0 and (max_characters is None or processed < max_characters):
            await asyncio.sleep(delay_seconds)

    store.finish_scan(scan_date)
    return {"saved": saved, "next_index": cursor, "reason": reason}
