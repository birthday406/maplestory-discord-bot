import asyncio
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from maple_bot import configured_ranking_world_ids, import_ready_ranking_batches
from ranking_store import RankingStore
from ranking_worker import (
    RankingBatchWriter,
    eligible_representatives,
    normalize_representative,
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

    def test_representative_checkpoint_survives_day_change_and_restarts_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.assertEqual(store.representative_cursor(45, "legion"), 1)
            store.advance_representative_scan(45, "legion", 31)
            self.assertEqual(store.representative_cursor(45, "legion"), 31)
            store.finish_representative_scan(45, "legion")
            self.assertEqual(store.representative_cursor(45, "legion"), 1)

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
