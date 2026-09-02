import json
import tempfile
import unittest
from pathlib import Path

from tools.backfill_control import status_text


class BackfillControlTests(unittest.TestCase):
    def test_status_uses_unique_successful_checkpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "maplebot-backfill-level-295.jsonl"
            checkpoint.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {"name": "Akane", "gains": [1]},
                        {"name": "AKANE", "gains": [1]},
                        {"name": "Retry", "error": "timeout"},
                        {"name": "Done", "gains": [1]},
                    )
                ),
                encoding="utf-8",
            )
            (root / "maplebot-backfill-280-plus.log").write_text(
                "[99/999] Old: ok\n"
                "[2026-09-01T00:00:00Z] level 295 start\n"
                "[7/100] Done: ok\n",
                encoding="utf-8",
            )
            (root / "maplebot-backfill-current-level").write_text(
                "295\n", encoding="utf-8"
            )

            result = status_text(
                root,
                [(10, "python tools/backfill_maplebot.py --level 295")],
            )
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write(
                    "\n"
                    + json.dumps({"name": "akane", "gains": [1]})
                    + "\n"
                    + json.dumps({"name": "New", "gains": [1]})
                )
            updated = status_text(
                root,
                [(10, "python tools/backfill_maplebot.py --level 295")],
            )

        self.assertIn("백필 상태: 실행 중", result)
        self.assertIn("현재 레벨 저장: 2명", result)
        self.assertIn("최근 실행 진행: 7/100", result)
        self.assertIn("현재 레벨 저장: 3명", updated)

    def test_progress_does_not_cross_into_another_level(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "maplebot-backfill-current-level").write_text("299\n")
            (root / "maplebot-backfill-280-plus.log").write_text(
                "[2026-09-01T00:00:00Z] level 299 start\n"
                "[2026-09-01T00:01:00Z] level 298 start\n"
                "[1/2560] Other: ok\n",
                encoding="utf-8",
            )

            result = status_text(root, [(10, "bash run_maplebot_backfill_aux.sh")])

        self.assertIn("현재 레벨: Lv.299", result)
        self.assertNotIn("1/2560", result)


if __name__ == "__main__":
    unittest.main()
