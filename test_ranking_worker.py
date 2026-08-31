import tempfile
import unittest
from datetime import date
from pathlib import Path

from maple_bot import configured_ranking_world_ids, import_ready_ranking_batches
from ranking_store import RankingStore
from ranking_worker import RankingBatchWriter


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

    def test_world_configuration_rejects_untracked_worlds(self) -> None:
        self.assertEqual(configured_ranking_world_ids("45,70,45"), (45, 70))
        with self.assertRaises(ValueError):
            configured_ranking_world_ids("30")

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
