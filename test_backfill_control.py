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
                "[7/100] Done: ok\n", encoding="utf-8"
            )

            result = status_text(
                root,
                [(10, "python tools/backfill_maplebot.py --level 295")],
            )

        self.assertIn("백필 상태: 실행 중", result)
        self.assertIn("현재 레벨 저장: 2명", result)
        self.assertIn("최근 실행 진행: 7/100", result)


if __name__ == "__main__":
    unittest.main()
