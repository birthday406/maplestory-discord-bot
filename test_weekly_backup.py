import gzip
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tools.backup_ranking_db import create_local_backup, prune_local_backups


class WeeklyRankingBackupTests(unittest.TestCase):
    def test_backup_is_compressed_and_can_be_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "ranking.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE characters (name TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO characters VALUES ('Home')")
                connection.commit()

            archive = create_local_backup(
                database,
                root / "backups",
                now=datetime(2026, 9, 13, 23, 30, tzinfo=timezone.utc),
            )

            self.assertEqual(archive.name, "ranking-20260913T233000Z.db.gz")
            restored = root / "restored.db"
            with gzip.open(archive, "rb") as source:
                restored.write_bytes(source.read())
            with closing(sqlite3.connect(restored)) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA integrity_check").fetchone()[0],
                    "ok",
                )
                self.assertEqual(
                    connection.execute("SELECT name FROM characters").fetchone()[0],
                    "Home",
                )
            expected_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(
                archive.with_name(f"{archive.name}.sha256").read_text().split()[0],
                expected_hash,
            )

    def test_prune_keeps_four_newest_complete_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = [
                f"ranking-202609{day:02d}T233000Z.db.gz"
                for day in range(1, 7)
            ]
            for name in names:
                (root / name).write_bytes(b"archive")
                (root / f"{name}.sha256").write_text("hash\n")
            unrelated = root / "keep-me.txt"
            unrelated.write_text("keep")

            removed = prune_local_backups(root, retain=4)

            self.assertEqual([path.name for path in removed], names[:2])
            self.assertEqual(
                sorted(path.name for path in root.glob("*.db.gz")),
                names[2:],
            )
            self.assertTrue(unrelated.exists())

    def test_failed_backup_does_not_leave_temporary_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with self.assertRaises(sqlite3.OperationalError):
                create_local_backup(root / "missing.db", root / "backups")

            self.assertEqual(list((root / "backups").glob(".*.tmp*")), [])


if __name__ == "__main__":
    unittest.main()
