import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from maple_bot import configured_ranking_world_ids, import_ready_ranking_batches
from ranking_store import RankingStore
from ranking_worker import (
    RankingBatchWriter,
    eligible_representatives,
    normalize_representative,
    current_ranking_scan_date,
    run_worker,
    sync_ready_batches,
)


class RankingWorkerTests(unittest.TestCase):
    @staticmethod
    def character(name: str, rank: int, exp: int) -> dict:
        return {
            "characterName": name,
            "characterImgURL": None,
            "exp": exp,
            "jobName": "Hero",
            "level": 295,
            "rank": rank,
            "worldID": 45,
        }

    def test_batch_is_imported_once_without_replacing_newer_current_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outbox = root / "outbox"
            writer = RankingBatchWriter(outbox, "worker-2", pages_per_batch=2)
            older = self.character("AkaneLize", 10, 100)
            writer.write(date(2026, 8, 30), 45, 1, [older])

            store = RankingStore(root / "ranking.db")
            newer = self.character("AkaneLize", 9, 200)
            store.save_page(
                [newer],
                date(2026, 8, 31),
                next_index=1,
                update_checkpoint=False,
                source_page_index=1,
            )
            same_day_but_older = self.character("AkaneLize", 10, 150)
            writer.write(date(2026, 8, 31), 45, 1, [same_day_but_older])
            batch = next(outbox.glob("*.jsonl"))
            inbox = root / "inbox"
            inbox.mkdir()
            batch.replace(inbox / batch.name)

            self.assertEqual(
                import_ready_ranking_batches(store, inbox),
                (2, 1, 0),
            )
            with store._connect() as connection:
                current_exp = connection.execute(
                    "SELECT exp FROM characters WHERE name_key = ?",
                    ("akanelize",),
                ).fetchone()["exp"]
                snapshots = connection.execute(
                    """
                    SELECT snapshot_date, exp
                    FROM ranking_snapshots
                    WHERE name_key = ?
                    ORDER BY snapshot_date
                    """,
                    ("akanelize",),
                ).fetchall()
            self.assertEqual(current_exp, 200)
            self.assertEqual(
                [(row["snapshot_date"], row["exp"]) for row in snapshots],
                [("2026-08-30", 100), ("2026-08-31", 200)],
            )
            self.assertTrue((inbox / "processed" / batch.name).exists())

    def test_sync_publishes_batch_only_after_scp_finishes(self) -> None:
        class Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        calls = []

        async def create_process(*args, **_kwargs):
            calls.append(args)
            return Process()

        with tempfile.TemporaryDirectory() as directory:
            outbox = Path(directory)
            batch = outbox / "batch.jsonl"
            batch.write_text("{}\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {
                    "RANKING_SYNC_TARGET": "ubuntu@example:/srv/ranking-inbox/",
                    "RANKING_SYNC_SSH_KEY": "/key",
                },
            ), patch(
                "ranking_worker.asyncio.create_subprocess_exec",
                side_effect=create_process,
            ):
                self.assertEqual(asyncio.run(sync_ready_batches(outbox)), 1)

            self.assertEqual(calls[0][-1], "ubuntu@example:/srv/ranking-inbox/batch.jsonl.part")
            self.assertEqual(
                calls[1][-6:],
                (
                    "/key",
                    "ubuntu@example",
                    "mv",
                    "--",
                    "/srv/ranking-inbox/batch.jsonl.part",
                    "/srv/ranking-inbox/batch.jsonl",
                ),
            )
            self.assertTrue((outbox / "sent" / batch.name).exists())

    def test_world_configuration_rejects_untracked_worlds(self) -> None:
        self.assertEqual(configured_ranking_world_ids("45,70,45"), (45, 70))
        with self.assertRaises(ValueError):
            configured_ranking_world_ids("30")

    def test_representative_batches_only_enrich_existing_character(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RankingStore(root / "ranking.db")
            day = date(2026, 8, 31)
            original = self.character("AkaneLize", 25, 123)
            store.save_page(
                [original], day, next_index=1, update_checkpoint=False
            )

            writer = RankingBatchWriter(root / "outbox", "worker", pages_per_batch=2)
            legion = normalize_representative(
                {"characterName": "AkaneLize", "legionLevel": 10_535, "rank": 877},
                "legion",
            )
            achievement = normalize_representative(
                {"characterName": "AkaneLize", "starSum": 33_370, "rank": 113},
                "achievement",
            )
            writer.write(day, 45, 1, [legion], "legion")
            writer.write(day, 45, 1, [achievement], "achievement")
            batch = next((root / "outbox").glob("*.jsonl"))

            self.assertEqual(store.import_batch(batch), 2)
            with store._connect() as connection:
                current = connection.execute(
                    """SELECT ranking, exp, legion_level, legion_rank,
                              achievement_score, achievement_rank
                       FROM characters WHERE name_key = 'akanelize'"""
                ).fetchone()
                snapshot = connection.execute(
                    """SELECT legion_level, legion_rank,
                              achievement_score, achievement_rank
                       FROM ranking_snapshots
                       WHERE name_key = 'akanelize' AND snapshot_date = ?""",
                    (day.isoformat(),),
                ).fetchone()
            self.assertEqual((current["ranking"], current["exp"]), (25, 123))
            self.assertEqual(tuple(current)[2:], (10_535, 877, 33_370, 113))
            self.assertEqual(tuple(snapshot), (10_535, 877, 33_370, 113))

    def test_representative_checkpoint_resumes_and_restarts_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.assertEqual(store.representative_cursor(45, "legion"), 1)
            store.advance_representative_scan(45, "legion", 31)
            self.assertEqual(store.representative_cursor(45, "legion"), 31)
            store.finish_representative_scan(45, "legion")
            self.assertEqual(store.representative_cursor(45, "legion"), 1)

    def test_representative_checkpoint_resets_on_new_day_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.db"
            store = RankingStore(path)
            first, second = date(2026, 9, 8), date(2026, 9, 9)
            for kind in ("legion-shard-0-of-4", "achievement-shard-0-of-4"):
                self.assertEqual(store.representative_cursor(45, kind, first), 1)
                store.advance_representative_scan(45, kind, 89881)
                store = RankingStore(path)
                self.assertEqual(store.representative_cursor(45, kind, first), 89881)
                self.assertEqual(store.representative_cursor(45, kind, second), 1)

    def test_early_representatives_survive_restart_until_experience_arrives(self):
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ranking.db"
                store = RankingStore(path)
                day = date(2026, 9, 8)
                character = self.character("Early", 100, 123)
                if existing:
                    store.save_page([character], date(2026, 9, 7), 1, update_checkpoint=False)
                store.save_representative_page(
                    [{"characterName": "Early", "legionLevel": 4541, "legionRank": 89965}],
                    day, "legion",
                )
                store.save_representative_page(
                    [{"characterName": "Early", "achievementScore": 33000, "achievementRank": 113}],
                    day, "achievement",
                )
                with store._connect() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM ranking_snapshots WHERE snapshot_date=?",
                        (day.isoformat(),),
                    ).fetchone()[0], 0)
                store = RankingStore(path)
                store.save_page([character], day, 1, update_checkpoint=False, preserve_newer=True)
                with store._connect() as connection:
                    row = connection.execute(
                        "SELECT exp,legion_level,legion_rank,achievement_score,achievement_rank "
                        "FROM ranking_snapshots WHERE snapshot_date=?", (day.isoformat(),),
                    ).fetchone()
                self.assertEqual(tuple(row), (123, 4541, 89965, 33000, 113))
                with store._connect() as connection:
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM ranking_pending_representatives"
                    ).fetchone()[0], 0)

    def test_legacy_checkpoint_migrates_without_reusing_undated_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.db"
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "CREATE TABLE representative_scan_state (world_id INTEGER, ranking_type TEXT, "
                    "next_index INTEGER, started_at INTEGER, completed_at INTEGER, "
                    "PRIMARY KEY(world_id,ranking_type))"
                )
                connection.execute("INSERT INTO representative_scan_state VALUES (45,'legion',89881,1,NULL)")
            store = RankingStore(path)
            self.assertEqual(store.representative_cursor(45, "legion", date(2026, 9, 8)), 1)
            store.advance_representative_scan(45, "legion", 41)
            self.assertEqual(RankingStore(path).representative_cursor(45, "legion", date(2026, 9, 8)), 41)

    def test_pending_values_stay_on_original_day_and_invalid_values_do_not_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            first, second = date(2026, 9, 8), date(2026, 9, 9)
            store.save_representative_page(
                [{"characterName": "Early", "legionLevel": 4541, "legionRank": 89965}], first, "legion")
            store.save_representative_page(
                [{"characterName": "Early", "legionLevel": 0, "legionRank": None}], first, "legion")
            store.save_page([dict(self.character("Early", 100, 123), legionLevel=5000, legionRank=100)],
                            second, 1, update_checkpoint=False)
            store.save_page([self.character("Early", 200, 50)], first, 1,
                            update_checkpoint=False, preserve_newer=True)
            with store._connect() as connection:
                self.assertEqual(tuple(connection.execute(
                    "SELECT legion_level,legion_rank FROM characters").fetchone()), (5000, 100))
                self.assertEqual(tuple(connection.execute(
                    "SELECT legion_level,legion_rank FROM ranking_snapshots WHERE snapshot_date=?",
                    (first.isoformat(),)).fetchone()), (4541, 89965))

    def test_old_representative_batch_does_not_overwrite_newer_current(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            for day, score in ((date(2026, 9, 8), 4541), (date(2026, 9, 9), 5000)):
                store.save_page([dict(self.character("Kept", 100, 123),
                                     legionLevel=score, legionRank=900)], day, 1,
                                update_checkpoint=False)
            store.save_representative_page(
                [{"characterName": "Kept", "legionLevel": 4600, "legionRank": 899}],
                date(2026, 9, 8), "legion",
            )
            with store._connect() as connection:
                self.assertEqual(tuple(connection.execute(
                    "SELECT legion_level,legion_rank FROM characters"
                ).fetchone()), (5000, 900))
                self.assertEqual(tuple(connection.execute(
                    "SELECT legion_level,legion_rank FROM ranking_snapshots "
                    "WHERE snapshot_date='2026-09-08'"
                ).fetchone()), (4600, 899))

    def test_worker_discards_boundary_response_and_starts_new_day_at_first_page(self):
        first = current_ranking_scan_date()
        second = first + timedelta(days=1)
        clock = [first]
        legion_requests = []

        class Response:
            status = 200

            def __init__(self, params):
                self.params = params

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def raise_for_status(self):
                pass

            async def json(self):
                if self.params["type"] == "legion":
                    page = int(self.params["page_index"])
                    legion_requests.append(page)
                    if len(legion_requests) == 1:
                        clock[0] = second
                    return {"ranks": [{"characterName": "Boundary", "level": 280,
                                       "legionLevel": 9000, "rank": page}], "totalCount": page}
                if len(legion_requests) >= 2:
                    raise asyncio.CancelledError
                return {"ranks": []}

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def get(self, _url, *, params, timeout):
                return Response(params)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RankingStore(root / "worker.db")
            store.representative_cursor(45, "legion-shard-0-of-4", first)
            store.advance_representative_scan(45, "legion-shard-0-of-4", 89881)
            env = {"RANKING_WORKER_WORLD_IDS": "45", "RANKING_WORKER_SHARD_COUNT": "4",
                   "RANKING_WORKER_SHARD_INDEX": "0", "RANKING_WORKER_CONCURRENCY": "1",
                   "RANKING_WORKER_DB_PATH": str(store.path),
                   "RANKING_OUTBOX_PATH": str(root / "outbox"), "RANKING_SYNC_TARGET": ""}
            with patch.dict(os.environ, env), patch("ranking_worker.aiohttp.ClientSession", Session), \
                    patch("ranking_worker.current_ranking_scan_date", side_effect=lambda: clock[0]), \
                    patch("ranking_worker.RANKING_SCAN_INTERVAL_SECONDS", 0):
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(run_worker())
            records = [json.loads(line) for batch in (root / "outbox").glob("*.jsonl")
                       for line in batch.read_text(encoding="utf-8").splitlines()]
            pages = [r["page_index"] for r in records if r["scan_date"] == second.isoformat()
                     and r["ranking_type"] == "legion" and not r.get("completed")]
            self.assertEqual(pages, [1])

    def test_representative_collection_keeps_only_level_260_plus(self) -> None:
        ranks = [
            {"characterName": "Kept", "level": 260, "legionLevel": 9000, "rank": 1},
            {"characterName": "Skipped", "level": 259, "legionLevel": 9000, "rank": 2},
        ]
        self.assertEqual(
            eligible_representatives(ranks, "legion"),
            [{"characterName": "Kept", "legionLevel": 9000, "legionRank": 1}],
        )

    def test_active_pages_follow_changed_world_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            kronos = self.character("KronosCharacter", 1, 100)
            hyperion = dict(
                self.character("HyperionCharacter", 1, 100),
                worldID=70,
            )
            for character, page in ((kronos, 1), (hyperion, 11)):
                store.save_page(
                    [character],
                    date(2026, 8, 31),
                    next_index=1,
                    update_checkpoint=False,
                    source_page_index=page,
                )

            store.prepare_active_pages(date(2026, 8, 31), (45,))
            self.assertEqual(store.next_active_page(date(2026, 8, 31)), (45, 1))
            store.prepare_active_pages(date(2026, 8, 31), (70,))
            self.assertEqual(store.next_active_page(date(2026, 8, 31)), (70, 11))


if __name__ == "__main__":
    unittest.main()
