import gzip
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import ranking_archive as archive
from ranking_store import RankingStore


class RankingArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = RankingStore(self.root / "ranking.db")
        self.primary = self.root / "archive"
        self.replica = self.root / "replica"
        self.primary.mkdir()
        self.replica.mkdir()
        self.cutoff = date(2026, 9, 7) - timedelta(days=89)

    def save(self, day, exp=123):
        self.store.save_page([
            {"characterName": "테스트Hero", "level": 270, "exp": exp,
             "worldID": 45, "jobName": "Hero", "rank": 12,
             "legionLevel": 9999, "legionRank": 4,
             "achievementScore": 12345, "achievementRank": 9}
        ], day, 1, update_checkpoint=False)

    def rows(self):
        with self.store._connect() as connection:
            return [tuple(row) for row in connection.execute(
                "SELECT * FROM ranking_snapshots ORDER BY snapshot_date"
            )]

    def run_archive(self):
        # 테스트 임시 폴더는 같은 디스크이므로 저장소 분리 검사만 대체합니다.
        with patch.object(archive, "require_separate_storage"):
            return archive.archive_snapshots(self.store, self.cutoff, self.primary, self.replica)

    def test_boundary_months_and_roundtrip_from_either_copy(self):
        for day in (date(2026, 4, 30), date(2026, 5, 1), self.cutoff - timedelta(days=1), self.cutoff):
            self.save(day)
        before = self.rows()
        self.assertEqual(self.run_archive(), 3)
        self.assertEqual(self.rows(), before[-1:])
        self.assertEqual(self.store.get_first_seen_date("테스트Hero"), date(2026, 4, 30))
        self.assertEqual(self.store.character_count(), 1)
        self.assertEqual({p.name for p in self.primary.iterdir()}, {"2026-04", "2026-05", "2026-06"})
        for index, source in enumerate((self.primary, self.replica)):
            restored = self.root / f"restored-{index}.db"
            self.assertEqual(archive.restore_archives(source, restored), 3)
            with sqlite3.connect(restored) as connection:
                self.assertEqual(connection.execute(
                    "SELECT * FROM ranking_snapshots ORDER BY snapshot_date"
                ).fetchall(), before[:-1])
        count = len(list(self.primary.rglob("*.jsonl.gz")))
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(len(list(self.primary.rglob("*.jsonl.gz"))), count)

    def test_copy_failure_keeps_original(self):
        self.save(date(2026, 5, 1))
        before = self.rows()
        publish = archive.publish_archive

        def fail_replica(path, content):
            if self.replica in path.parents:
                raise OSError("backup storage unavailable")
            publish(path, content)

        with patch.object(archive, "publish_archive", side_effect=fail_replica):
            with self.assertRaises(OSError):
                self.run_archive()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.run_archive(), 1)

    def test_corrupted_replica_is_not_accepted(self):
        self.save(date(2026, 5, 1))
        publish = archive.publish_archive

        def corrupt_replica(path, content):
            publish(path, content[:-5] if self.replica in path.parents else content)

        with patch.object(archive, "publish_archive", side_effect=corrupt_replica):
            with self.assertRaises((EOFError, gzip.BadGzipFile)):
                self.run_archive()
        self.assertEqual(len(self.rows()), 1)

    def test_changed_row_and_late_correction_are_preserved(self):
        old_day = date(2026, 5, 1)
        self.save(old_day)
        publish = archive.publish_archive
        changed = False

        def update_during_copy(path, content):
            nonlocal changed
            publish(path, content)
            if self.replica in path.parents and not changed:
                self.save(old_day, exp=456)
                changed = True

        with patch.object(archive, "publish_archive", side_effect=update_during_copy):
            self.assertEqual(self.run_archive(), 1)
        versions = [archive.read_archive(p)[1][0][3] for p in sorted(self.primary.rglob("*.jsonl.gz"))]
        self.assertEqual(versions, [123, 456])
        # 보관이 끝난 과거 날짜에 정정 데이터가 나중에 들어와도 새 묶음에 남깁니다.
        self.save(old_day, exp=789)
        self.assertEqual(self.run_archive(), 1)
        destination = self.root / "restored.db"
        self.assertEqual(archive.restore_archives(self.replica, destination), 1)
        with sqlite3.connect(destination) as connection:
            self.assertEqual(connection.execute("SELECT exp FROM ranking_snapshots").fetchone()[0], 789)

    def test_interruption_after_copies_keeps_source_and_retry_restores(self):
        self.save(date(2026, 5, 1))
        with patch.object(archive, "require_separate_storage", side_effect=[None, OSError("mount lost")]):
            with self.assertRaises(OSError):
                archive.archive_snapshots(self.store, self.cutoff, self.primary, self.replica)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.run_archive(), 1)
        self.assertEqual(archive.restore_archives(self.primary, self.root / "restored.db"), 1)

    def test_same_filesystem_or_missing_replica_cannot_prune(self):
        self.save(date(2026, 5, 1))
        with self.assertRaises(ValueError):
            archive.archive_snapshots(self.store, self.cutoff, self.primary, self.replica)
        with self.assertRaises(FileNotFoundError):
            archive.archive_snapshots(self.store, self.cutoff, self.primary, self.root / "missing")
        self.assertEqual(len(self.rows()), 1)

    def test_bounded_batches_and_unknown_schema(self):
        for offset in range(3):
            self.save(date(2026, 5, 1) + timedelta(days=offset))
        with patch.object(archive, "BATCH_SIZE", 1):
            self.assertEqual(self.run_archive(), 3)
        self.assertEqual(len(list(self.primary.rglob("*.jsonl.gz"))), 3)
        self.save(date(2026, 5, 5))
        with self.store._connect() as connection:
            connection.execute("ALTER TABLE ranking_snapshots ADD COLUMN future_field TEXT")
        with self.assertRaises(ValueError):
            self.run_archive()
        self.assertEqual(len(self.rows()), 1)

    def test_checksum_and_existing_restore_target(self):
        self.save(date(2026, 5, 1))
        self.run_archive()
        with self.assertRaises(FileExistsError):
            archive.restore_archives(self.primary, self.store.path)
        path = next(self.primary.rglob("*.jsonl.gz"))
        raw = gzip.decompress(path.read_bytes()).replace(b"123", b"124")
        path.write_bytes(gzip.compress(raw))
        with self.assertRaises(ValueError):
            archive.read_archive(path)


class ArchiveSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def test_import_loop_schedules_once_and_does_not_delete_old_history(self):
        import maple_bot
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "test.db")
            old_day = date(2025, 1, 1)
            store.save_page([
                {"characterName": "OldHero", "worldID": 45, "jobName": "Hero",
                 "level": 270, "exp": 100, "rank": 1}
            ], old_day, 1, update_checkpoint=False)
            bot = object.__new__(maple_bot.MapleNewsBot)
            bot.ranking_store = store
            bot._ranking_import_only = True
            bot._ranking_retry_until = 0
            bot._ranking_scan_date = None
            bot._completed_ranking_world_ids = set()
            bot.archive_ranking_history = AsyncMock()
            with patch.object(maple_bot, "import_ready_ranking_batches", return_value=(0, 0, 0)):
                await bot.collect_rankings.coro(bot)
                await bot._ranking_archive_task
                await bot.collect_rankings.coro(bot)
            bot.archive_ranking_history.assert_awaited_once()
            self.assertEqual(store.get_first_seen_date("OldHero"), old_day)

    async def test_missing_configuration_never_archives(self):
        import maple_bot
        bot = object.__new__(maple_bot.MapleNewsBot)
        with patch.object(maple_bot, "archive_snapshots") as job:
            await bot.archive_ranking_history(date(2026, 9, 7))
        job.assert_not_called()

    async def test_ninety_day_cutoff_and_failure_is_reported(self):
        import maple_bot
        bot = object.__new__(maple_bot.MapleNewsBot)
        bot._ranking_archive_replica_path = Path("replica")
        bot._ranking_archive_path = Path("archive")
        bot.ranking_store = object()
        with patch.object(maple_bot, "archive_snapshots", return_value=5) as job:
            await bot.archive_ranking_history(date(2026, 9, 7))
        self.assertEqual(job.call_args.args[1], date(2026, 6, 10))
        with patch.object(maple_bot, "archive_snapshots", side_effect=OSError("unavailable")):
            with self.assertLogs(level="ERROR"):
                await bot.archive_ranking_history(date(2026, 9, 7))
