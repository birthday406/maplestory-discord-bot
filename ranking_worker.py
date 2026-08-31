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
from ranking_store import MIN_TRACKED_LEVEL, RankingStore, scan_rankings


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
    sent = outbox / "sent"
    synced = 0
    for batch_path in sorted(outbox.glob("*.jsonl")):
        command = ["scp", "-q", "-o", "BatchMode=yes"]
        ssh_key = os.getenv("RANKING_SYNC_SSH_KEY")
        if ssh_key:
            command.extend(("-i", ssh_key))
        command.extend((str(batch_path), target))
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
    current_page_index: int | None = None

    async with aiohttp.ClientSession() as session:
        async def fetch_page(world_id: int, page_index: int) -> dict:
            nonlocal current_page_index, next_request_at
            current_page_index = page_index
            loop = asyncio.get_running_loop()
            if next_request_at > loop.time():
                await asyncio.sleep(next_request_at - loop.time())
            next_request_at = loop.time() + RANKING_SCAN_INTERVAL_SECONDS
            async with session.get(
                RANKING_API_URL.format(region="na"),
                params={
                    "type": "world",
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
                    raise RankingRateLimited(world_id, response.status, retry_after)
                response.raise_for_status()
                payload = await response.json()
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
                    writer.finalize()
                    await sync_ready_batches(writer.outbox)
                    await asyncio.sleep(60)
                    continue
                world_id = remaining[world_offset % len(remaining)]
                world_offset += 1
                try:
                    result = await scan_rankings(
                        lambda page_index: fetch_page(world_id, page_index),
                        store,
                        scan_date,
                        max_characters=None,
                        max_pages=1,
                        scan_id=world_id,
                    )
                    if failures:
                        failures = 0
                        retry_until = 0
                        store.clear_collector_backoff()
                    if result["reason"] in {
                        "already_completed",
                        "level_boundary",
                        "end",
                    }:
                        completed.add(world_id)
                        logging.warning(
                            "ranking_worker_complete phase=world world=%s date=%s",
                            world_id,
                            scan_date,
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
                        "status=%s failures=%s wait_seconds=%s retry_at=%s",
                        error.target,
                        current_page_index,
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
                        "error_type=%s",
                        world_id,
                        current_page_index,
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
