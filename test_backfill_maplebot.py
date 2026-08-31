import sys
import tempfile
import types
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from tools.backfill_maplebot import collect, reconstruction_plan


class MapleBotBackfillTests(unittest.TestCase):
    def test_transient_failures_retry_and_success_resets_error_streak(self):
        failures = {"Retry": 2, "FailA": 3, "FailB": 3, "Good": 0}
        attempts = {name: 0 for name in failures}
        gains = [
            {"date": (date(2026, 8, 1) + timedelta(days=day)).isoformat(), "exp": 0}
            for day in range(30)
        ]

        class Page:
            def on(self, *_args):
                pass

            def goto(self, url, **_kwargs):
                self.name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])

            def get_by_text(self, *_args, **_kwargs):
                return self

            def wait_for(self, **_kwargs):
                attempts[self.name] += 1
                if attempts[self.name] <= failures[self.name]:
                    raise TimeoutError("temporary render timeout")

            def count(self):
                return 0

            def title(self):
                return self.name

            def evaluate(self, _script):
                return gains

            def close(self):
                pass

        class Browser:
            def new_page(self):
                return Page()

            def close(self):
                pass

        class Playwright:
            chromium = SimpleNamespace(launch=lambda **_kwargs: Browser())

        class Manager:
            def __enter__(self):
                return Playwright()

            def __exit__(self, *_args):
                pass

        fake_sync_api = types.ModuleType("playwright.sync_api")
        fake_sync_api.sync_playwright = Manager
        args = SimpleNamespace(
            checkpoint=None,
            limit=None,
            delay=0,
            edge=None,
            retries=2,
            max_errors=2,
            recovery_delay=0,
            max_recoveries=3,
        )
        characters = [{"name": name, "region": "GMS"} for name in failures]

        with tempfile.TemporaryDirectory() as directory:
            args.checkpoint = f"{directory}/checkpoint.jsonl"
            with patch.dict(sys.modules, {"playwright.sync_api": fake_sync_api}), patch(
                "tools.backfill_maplebot.time.sleep"
            ):
                results = collect(args, characters)

        self.assertEqual(
            [item["name"] for item in results],
            ["Retry", "Good", "FailA", "FailB"],
        )
        self.assertEqual(attempts, {"Retry": 3, "FailA": 4, "Good": 1, "FailB": 4})

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
