import asyncio
import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from maple_bot import (
    RANKING_API_URL,
    RANKING_FORBIDDEN_BACKOFF_STEPS,
    RANKING_SCAN_INTERVAL_SECONDS,
    RankingRateLimited,
    configured_ranking_world_ids,
    current_ranking_scan_date,
    ranking_backoff_seconds,
)
from ranking_store import MIN_TRACKED_LEVEL, RANKING_PAGE_SIZE, RankingStore


REPRESENTATIVE_RANKING_TYPES = ("legion", "achievement")


def normalize_representative(character: dict, ranking_type: str) -> dict:
    saved = {"characterName": character["characterName"]}
    if ranking_type == "legion":
        saved.update(
            legionLevel=int(character.get("legionLevel", 0)),
            legionRank=int(character["rank"]),
        )
    elif ranking_type == "achievement":
        saved.update(
            achievementScore=int(character.get("starSum", 0)),
            achievementRank=int(character["rank"]),
        )
    else:
        raise ValueError(f"Unsupported ranking type: {ranking_type}")
    return saved


def eligible_representatives(characters: list[dict], ranking_type: str) -> list[dict]:
    return [
        normalize_representative(character, ranking_type)
        for character in characters
        if character.get("level", 0) >= MIN_TRACKED_LEVEL
    ]


class RankingBatchWriter:
    """가져온 페이지를 전송 가능한 JSONL 묶음으로 안전하게 저장합니다."""

    def __init__(self, outbox: Path, worker_name: str, pages_per_batch: int) -> None:
        self.outbox = outbox
        self.worker_name = "".join(
            character if character.isalnum() else "-" for character in worker_name
        ).strip("-") or "worker"
        self.pages_per_batch = pages_per_batch
        self.outbox.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._path: Path | None = None
        self._pages = 0
        for partial in self.outbox.glob("*.part"):
            partial.replace(partial.with_suffix(".jsonl"))

    def write(
        self,
        scan_date,
        world_id: int,
        page_index: int,
        characters: list[dict],
        ranking_type: str = "world",
    ) -> None:
        if not characters:
            return
        if self._file is None:
            name = f"{scan_date}-{self.worker_name}-{time.time_ns()}.part"
            self._path = self.outbox / name
            self._file = self._path.open("a", encoding="utf-8")
        json.dump(
            {
                "scan_date": scan_date.isoformat(),
                "world_id": world_id,
                "page_index": page_index,
                "ranking_type": ranking_type,
                "characters": characters,
            },
            self._file,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._file.write("\n")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._pages += 1
        if self._pages >= self.pages_per_batch:
            self.finalize()

    def finalize(self) -> None:
        if self._file is None or self._path is None:
            return
        self._file.close()
        self._path.replace(self._path.with_suffix(".jsonl"))
        self._file = None
        self._path = None
        self._pages = 0


async def sync_ready_batches(outbox: Path) -> int:
    """완성된 묶음을 SSH로 메인 서버에 보내고 로컬 보관 폴더로 옮깁니다."""
    target = os.getenv("RANKING_SYNC_TARGET")
    if not target:
        return 0
    try:
        remote_host, remote_directory = target.rsplit(":", 1)
    except ValueError as error:
        raise ValueError("RANKING_SYNC_TARGET must be HOST:DIRECTORY") from error
    sent = outbox / "sent"
    synced = 0
    for batch_path in sorted(outbox.glob("*.jsonl")):
        remote_final = f"{remote_directory.rstrip('/')}/{batch_path.name}"
        remote_partial = f"{remote_final}.part"
        command = ["scp", "-q", "-o", "BatchMode=yes"]
        ssh_key = os.getenv("RANKING_SYNC_SSH_KEY")
        if ssh_key:
            command.extend(("-i", ssh_key))
        command.extend((str(batch_path), f"{remote_host}:{remote_partial}"))
        process = await asyncio.create_subprocess_exec(
            *command,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logging.error(
                "ranking_worker_error phase=sync batch=%s returncode=%s error=%s",
                batch_path.name,
                process.returncode,
                stderr.decode(errors="replace").strip() or "-",
            )
            break
        command = ["ssh", "-o", "BatchMode=yes"]
        if ssh_key:
            command.extend(("-i", ssh_key))
        command.extend(
            (remote_host, "mv", "--", remote_partial, remote_final)
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logging.error(
                "ranking_worker_error phase=publish batch=%s returncode=%s error=%s",
                batch_path.name,
                process.returncode,
                stderr.decode(errors="replace").strip() or "-",
            )
            break
        sent.mkdir(exist_ok=True)
        destination = sent / batch_path.name
        if destination.exists():
            suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            destination = sent / f"{batch_path.stem}-{suffix}.jsonl"
        batch_path.replace(destination)
        logging.info("ranking_worker_sync phase=sync batch=%s", batch_path.name)
        synced += 1
    return synced


async def run_worker() -> None:
    world_ids = configured_ranking_world_ids(
        os.environ["RANKING_WORKER_WORLD_IDS"]
    )
    store = RankingStore(
        Path(os.getenv("RANKING_WORKER_DB_PATH", "ranking-worker.db"))
    )
    writer = RankingBatchWriter(
        Path(os.getenv("RANKING_OUTBOX_PATH", "ranking-outbox")),
        os.getenv("RANKING_WORKER_NAME", socket.gethostname()),
        max(1, int(os.getenv("RANKING_BATCH_PAGES", "60"))),
    )
    failures, retry_until = store.get_collector_backoff()
    completed: set[int] = set()
    scan_date = None
    world_offset = 0
    next_request_at = 0.0
    request_lock = asyncio.Lock()
    concurrency = max(1, int(os.getenv("RANKING_WORKER_CONCURRENCY", "3")))
    shard_count = max(1, int(os.getenv("RANKING_WORKER_SHARD_COUNT", "1")))
    shard_index = int(os.getenv("RANKING_WORKER_SHARD_INDEX", "0"))
    if not 0 <= shard_index < shard_count:
        raise ValueError("RANKING_WORKER_SHARD_INDEX must be smaller than shard count.")
    shard_step = RANKING_PAGE_SIZE * shard_count
    current_page_index: int | None = None
    current_ranking_type = "world"
    representative_offset = 0

    async with aiohttp.ClientSession() as session:
        async def request_page(
            ranking_type: str, world_id: int, page_index: int
        ) -> dict:
            nonlocal current_page_index, current_ranking_type, next_request_at
            current_page_index = page_index
            current_ranking_type = ranking_type
            async with request_lock:
                loop = asyncio.get_running_loop()
                if next_request_at > loop.time():
                    await asyncio.sleep(next_request_at - loop.time())
                next_request_at = loop.time() + RANKING_SCAN_INTERVAL_SECONDS
            async with session.get(
                RANKING_API_URL.format(region="na"),
                params={
                    "type": ranking_type,
                    "id": str(world_id),
                    "reboot_index": "0",
                    "page_index": str(page_index),
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                if response.status in {403, 429}:
                    try:
                        retry_after = int(response.headers.get("Retry-After", ""))
                    except ValueError:
                        retry_after = None
                    raise RankingRateLimited(
                        f"{ranking_type}:{world_id}", response.status, retry_after
                    )
                response.raise_for_status()
                return await response.json()

        async def fetch_page(world_id: int, page_index: int) -> dict:
            payload = await request_page("world", world_id, page_index)
            writer.write(
                scan_date,
                world_id,
                page_index,
                [
                    character
                    for character in payload.get("ranks", [])
                    if character.get("level", 0) >= MIN_TRACKED_LEVEL
                ],
            )
            return payload

        async def scan_representative_chunk(
            world_id: int, ranking_type: str
        ) -> None:
            state_type = f"{ranking_type}-shard-{shard_index}-of-{shard_count}"
            cursor = store.representative_cursor(world_id, state_type)
            if cursor == 1 and shard_index:
                cursor += RANKING_PAGE_SIZE * shard_index
                store.advance_representative_scan(world_id, state_type, cursor)
            page_indices = [cursor + shard_step * offset for offset in range(concurrency)]
            payloads = await asyncio.gather(
                *(request_page(ranking_type, world_id, index) for index in page_indices)
            )
            for page_index, payload in zip(page_indices, payloads):
                ranks = payload.get("ranks", [])
                if not ranks:
                    store.finish_representative_scan(world_id, state_type)
                    break
                matches = eligible_representatives(ranks, ranking_type)
                writer.write(
                    current_ranking_scan_date(),
                    world_id,
                    page_index,
                    matches,
                    ranking_type,
                )
                next_index = page_index + len(ranks)
                total_count = int(payload.get("totalCount", 0))
                if total_count and next_index > total_count:
                    store.finish_representative_scan(world_id, state_type)
                    logging.warning(
                        "ranking_worker_complete phase=representative type=%s world=%s",
                        ranking_type,
                        world_id,
                    )
                    break
                store.advance_representative_scan(
                    world_id, state_type, page_index + shard_step
                )

        async def scan_world_shard_chunk(world_id: int) -> bool:
            scan_id = 100_000 + world_id * 100 + shard_index
            cursor = store.start_scan(scan_date, world_id=scan_id)
            if cursor is None:
                return True
            if cursor == 1 and shard_index:
                cursor += RANKING_PAGE_SIZE * shard_index
            page_indices = [cursor + shard_step * offset for offset in range(concurrency)]
            payloads = await asyncio.gather(
                *(fetch_page(world_id, index) for index in page_indices)
            )
            for page_index, payload in zip(page_indices, payloads):
                ranks = payload.get("ranks", [])
                if not ranks:
                    store.finish_scan(scan_date, world_id=scan_id)
                    return True
                eligible = [
                    character
                    for character in ranks
                    if character.get("level", 0) >= MIN_TRACKED_LEVEL
                ]
                store.save_page(
                    eligible,
                    scan_date,
                    page_index + shard_step,
                    world_id=scan_id,
                    source_page_index=page_index,
                )
                if len(eligible) != len(ranks):
                    store.finish_scan(scan_date, world_id=scan_id)
                    return True
            return False

        try:
            while True:
                today = current_ranking_scan_date()
                if scan_date != today:
                    writer.finalize()
                    scan_date = today
                    completed.clear()
                    world_offset = 0
                    logging.warning(
                        "ranking_worker_start phase=cycle date=%s worlds=%s",
                        scan_date,
                        world_ids,
                    )

                now = int(datetime.now(timezone.utc).timestamp())
                if now < retry_until:
                    await asyncio.sleep(min(retry_until - now, 60))
                    continue

                remaining = [world for world in world_ids if world not in completed]
                if not remaining:
                    jobs = [
                        (world_id, ranking_type)
                        for world_id in world_ids
                        for ranking_type in REPRESENTATIVE_RANKING_TYPES
                    ]
                    world_id, ranking_type = jobs[representative_offset % len(jobs)]
                    representative_offset += 1
                    try:
                        await scan_representative_chunk(world_id, ranking_type)
                        if failures:
                            failures = 0
                            retry_until = 0
                            store.clear_collector_backoff()
                        await sync_ready_batches(writer.outbox)
                    except RankingRateLimited as error:
                        writer.finalize()
                        await sync_ready_batches(writer.outbox)
                        failures += 1
                        wait_seconds = ranking_backoff_seconds(
                            error.status, error.retry_after, failures
                        )
                        retry_until = (
                            int(datetime.now(timezone.utc).timestamp()) + wait_seconds
                        )
                        store.set_collector_backoff(failures, retry_until)
                        logging.warning(
                            "ranking_worker_backoff phase=fetch target=%s page=%s "
                            "type=%s status=%s failures=%s wait_seconds=%s retry_at=%s",
                            error.target,
                            current_page_index,
                            current_ranking_type,
                            error.status,
                            failures,
                            wait_seconds,
                            datetime.fromtimestamp(
                                retry_until, timezone.utc
                            ).isoformat(),
                        )
                    except (aiohttp.ClientError, TimeoutError, ValueError, OSError):
                        logging.exception(
                            "ranking_worker_error phase=collect world=%s page=%s "
                            "type=%s",
                            world_id,
                            current_page_index,
                            current_ranking_type,
                        )
                        await asyncio.sleep(RANKING_FORBIDDEN_BACKOFF_STEPS[0])
                    continue
                world_id = remaining[0]
                try:
                    world_completed = await scan_world_shard_chunk(world_id)
                    if failures:
                        failures = 0
                        retry_until = 0
                        store.clear_collector_backoff()
                    if world_completed:
                        completed.add(world_id)
                        scan_id = 100_000 + world_id * 100 + shard_index
                        timing = store.get_scan_timing(scan_date, scan_id)
                        logging.warning(
                            "ranking_worker_complete phase=world world=%s shard=%s/%s date=%s "
                            "started_at=%sZ completed_at=%sZ elapsed_seconds=%s "
                            "elapsed_hours=%.2f",
                            world_id,
                            shard_index + 1,
                            shard_count,
                            scan_date,
                            datetime.fromtimestamp(
                                timing["started_at"], timezone.utc
                            ).strftime("%Y-%m-%dT%H:%M:%S"),
                            datetime.fromtimestamp(
                                timing["completed_at"], timezone.utc
                            ).strftime("%Y-%m-%dT%H:%M:%S"),
                            timing["elapsed_seconds"],
                            timing["elapsed_seconds"] / 3600,
                        )
                    await sync_ready_batches(writer.outbox)
                except RankingRateLimited as error:
                    writer.finalize()
                    await sync_ready_batches(writer.outbox)
                    failures += 1
                    wait_seconds = ranking_backoff_seconds(
                        error.status,
                        error.retry_after,
                        failures,
                    )
                    retry_until = int(datetime.now(timezone.utc).timestamp()) + wait_seconds
                    store.set_collector_backoff(failures, retry_until)
                    logging.warning(
                        "ranking_worker_backoff phase=fetch world=%s page=%s "
                        "type=%s status=%s failures=%s wait_seconds=%s retry_at=%s",
                        error.target,
                        current_page_index,
                        current_ranking_type,
                        error.status,
                        failures,
                        wait_seconds,
                        datetime.fromtimestamp(retry_until, timezone.utc).isoformat(),
                    )
                except (
                    aiohttp.ClientError,
                    TimeoutError,
                    ValueError,
                    OSError,
                ) as error:
                    logging.exception(
                        "ranking_worker_error phase=collect world=%s page=%s "
                        "type=%s error_type=%s",
                        world_id,
                        current_page_index,
                        current_ranking_type,
                        type(error).__name__,
                    )
                    await asyncio.sleep(RANKING_FORBIDDEN_BACKOFF_STEPS[0])
        finally:
            writer.finalize()


def main() -> None:
    load_dotenv()
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
    )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
