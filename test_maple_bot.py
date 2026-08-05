import json
import tempfile
import unittest
from pathlib import Path

import maple_bot
from maple_bot import (
    HEXA_CORE_COSTS,
    calculate_hexa_cost,
    html_to_text,
    load_state,
    post_url,
    thumbnail_url,
    watched_posts,
)


class NewsFilteringTests(unittest.TestCase):
    def test_watched_posts_includes_events_and_excludes_other_categories(self) -> None:
        posts = [
            {"id": 1, "category": "update"},
            {"id": 2, "category": "events"},
            {"id": 3, "category": "maintenance"},
            {"id": 4, "category": "community"},
        ]

        self.assertEqual([post["id"] for post in watched_posts(posts)], [1, 2, 3])

    def test_post_url_uses_category_id_and_title_slug(self) -> None:
        post = {"id": 123, "category": "sale", "name": "Summer Sale: It's Here!"}

        self.assertEqual(
            post_url(post),
            "https://www.nexon.com/maplestory/news/sale/123/summer-sale-it-s-here",
        )

    def test_thumbnail_url_uses_nexon_origin(self) -> None:
        post = {"imageThumbnail": "/media/example/thumbnail.png"}

        self.assertEqual(
            thumbnail_url(post),
            "https://g.nexonstatic.com/media/example/thumbnail.png",
        )

    def test_load_state_treats_legacy_state_as_pre_events_categories(self) -> None:
        original_state_path = maple_bot.STATE_PATH
        with tempfile.TemporaryDirectory() as directory:
            maple_bot.STATE_PATH = Path(directory) / "state.json"
            maple_bot.STATE_PATH.write_text(json.dumps({"sent_ids": [1]}), encoding="utf-8")

            sent_ids, categories = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"maintenance", "sale", "general", "update"})

    def test_html_to_text_removes_tags_and_script(self) -> None:
        source = "<h1>Patch</h1><script>ignore()</script><p>Notes</p>"
        self.assertEqual(html_to_text(source), "Patch Notes")


class HexaCostTests(unittest.TestCase):
    def test_enhancement_core_level_7_to_20_matches_reference(self) -> None:
        self.assertEqual(calculate_hexa_cost("강화 코어", 7, 20), (54, 1319))

    def test_level_0_to_30_totals_match_reference(self) -> None:
        expected_totals = {
            "스킬 코어": (150, 4500),
            "3rd 스킬 코어": (117, 3442),
            "마스터리 코어": (83, 2252),
            "강화 코어": (123, 3383),
            "공용 코어": (208, 6268),
            "직업군 공용 코어": (137, 4035),
        }

        self.assertEqual(set(HEXA_CORE_COSTS), set(expected_totals))
        for core_type, expected in expected_totals.items():
            self.assertEqual(calculate_hexa_cost(core_type, 0, 30), expected)

    def test_target_level_must_be_higher_than_current_level(self) -> None:
        with self.assertRaises(ValueError):
            calculate_hexa_cost("강화 코어", 20, 20)
