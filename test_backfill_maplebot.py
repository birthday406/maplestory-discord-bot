import unittest

from tools.backfill_maplebot import reconstruction_plan


class MapleBotBackfillTests(unittest.TestCase):
    def test_conflicting_newer_snapshot_only_fills_before_first_anchor(self):
        gains = [
            {"date": f"2026-08-{day:02d}", "exp": 10}
            for day in range(1, 31)
        ]
        plan, mode = reconstruction_plan(
            gains,
            {"2026-08-27": 270, "2026-08-30": 2_000_500},
        )

        self.assertEqual(mode, "before_first_anchor")
        self.assertEqual(plan["2026-07-31"], 0)
        self.assertNotIn("2026-08-28", plan)


if __name__ == "__main__":
    unittest.main()
