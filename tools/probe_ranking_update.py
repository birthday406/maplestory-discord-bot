"""GMS 공식 랭킹의 일일 갱신 시각을 하루 동안 측정합니다."""

import argparse
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp


RANKING_API_URL = "https://www.nexon.com/api/maplestory/no-auth/ranking/v2/na"
KST = ZoneInfo("Asia/Seoul")


def select_candidates(database: Path, limit: int) -> list[str]:
    """최근 실제 경험치 변경이 확인된 캐릭터부터 감시 대상으로 고릅니다."""
    if not database.exists():
        raise FileNotFoundError(f"랭킹 DB가 없습니다: {database}")
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            WITH history AS (
                SELECT
                    name_key,
                    snapshot_date,
                    level,
                    exp,
                    LAG(level) OVER (
                        PARTITION BY name_key ORDER BY snapshot_date
                    ) AS previous_level,
                    LAG(exp) OVER (
                        PARTITION BY name_key ORDER BY snapshot_date
                    ) AS previous_exp
                FROM ranking_snapshots
            ),
            changed AS (
                SELECT name_key, MAX(snapshot_date) AS last_change
                FROM history
                WHERE previous_level IS NOT NULL
                  AND (level != previous_level OR exp != previous_exp)
                GROUP BY name_key
            )
            SELECT character.name
            FROM characters AS character
            LEFT JOIN changed ON changed.name_key = character.name_key
            ORDER BY changed.last_change IS NULL, changed.last_change DESC,
                     character.updated_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    if not rows:
        raise RuntimeError("ranking.db에 감시할 캐릭터 기록이 없습니다.")
    return [row[0] for row in rows]


async def fetch_character(session: aiohttp.ClientSession, name: str) -> dict | None:
    async with session.get(
        RANKING_API_URL,
        params={
            "type": "overall",
            "id": "weekly",
            "reboot_index": "0",
            "page_index": "1",
            "character_name": name,
        },
        timeout=aiohttp.ClientTimeout(total=20),
    ) as response:
        response.raise_for_status()
        payload = await response.json()
    return next(
        (
            rank
            for rank in payload.get("ranks", [])
            if rank.get("characterName", "").casefold() == name.casefold()
        ),
        None,
    )


def rank_value(character: dict) -> dict:
    return {
        "level": character.get("level", 0),
        "exp": character.get("exp", 0),
        "rank": character.get("rank", 0),
    }


def write_result(path: Path, result: dict) -> None:
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


async def probe(database: Path, output: Path, interval: int, hours: int, limit: int) -> int:
    names = select_candidates(database, limit)
    started = datetime.now(timezone.utc)
    deadline = started + timedelta(hours=hours)
    baseline: dict[str, dict] = {}
    checks = 0
    print(f"감시 대상: {', '.join(names)}")

    async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"}) as session:
        while datetime.now(timezone.utc) < deadline:
            checked_at = datetime.now(timezone.utc)
            current: dict[str, dict] = {}
            for name in names:
                try:
                    character = await fetch_character(session, name)
                    if character is not None:
                        current[name] = rank_value(character)
                except (aiohttp.ClientError, TimeoutError, ValueError) as error:
                    print(f"{name} 확인 실패: {error}")
                await asyncio.sleep(1)
            checks += 1

            if not baseline:
                baseline = current
                print(f"기준값 저장: {checked_at.astimezone(KST):%Y-%m-%d %H:%M:%S KST}")
            else:
                changes = [
                    {"name": name, "before": baseline[name], "after": value}
                    for name, value in current.items()
                    if name in baseline and value != baseline[name]
                ]
                if changes:
                    result = {
                        "status": "detected",
                        "started_at_kst": started.astimezone(KST).isoformat(),
                        "detected_at_kst": checked_at.astimezone(KST).isoformat(),
                        "poll_interval_minutes": interval / 60,
                        "checks": checks,
                        "changes": changes,
                    }
                    write_result(output, result)
                    print(f"공홈 랭킹 변경 감지: {checked_at.astimezone(KST):%Y-%m-%d %H:%M:%S KST}")
                    return 0

            write_result(
                output,
                {
                    "status": "watching",
                    "started_at_kst": started.astimezone(KST).isoformat(),
                    "last_checked_at_kst": checked_at.astimezone(KST).isoformat(),
                    "poll_interval_minutes": interval / 60,
                    "checks": checks,
                    "candidates": names,
                },
            )
            await asyncio.sleep(interval)

    write_result(
        output,
        {
            "status": "not_detected",
            "started_at_kst": started.astimezone(KST).isoformat(),
            "finished_at_kst": datetime.now(timezone.utc).astimezone(KST).isoformat(),
            "checks": checks,
            "candidates": names,
        },
    )
    print("설정한 시간 안에 랭킹 변경을 감지하지 못했습니다.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("ranking.db"))
    parser.add_argument("--output", type=Path, default=Path("ranking-update-probe.json"))
    parser.add_argument("--interval", type=int, default=600, help="확인 간격(초)")
    parser.add_argument("--hours", type=int, default=24, help="최대 감시 시간")
    parser.add_argument("--candidates", type=int, default=5, help="감시 캐릭터 수")
    args = parser.parse_args()
    return asyncio.run(
        probe(args.database, args.output, args.interval, args.hours, args.candidates)
    )


if __name__ == "__main__":
    raise SystemExit(main())
