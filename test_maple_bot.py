import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import maple_bot
from maple_bot import (
    HEXA_CORE_COSTS,
    calculate_hexa_cost,
    configured_sunny_sunday_channel_id,
    extract_sunny_sunday,
    format_sunny_sunday_date,
    html_to_text,
    is_patch_notes,
    known_sunny_sunday_translation,
    load_state,
    post_url,
    save_state,
    should_post_sunny_sunday_schedule,
    sunny_sunday_entry_action,
    sunny_sunday_timestamp,
    thumbnail_url,
    visible_sunny_sunday_entries,
    watched_posts,
)


class NewsFilteringTests(unittest.TestCase):
    def test_sunny_sunday_channel_uses_dedicated_setting_with_existing_fallback(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(configured_sunny_sunday_channel_id(111), 111)

        with patch.dict("os.environ", {"SUNNY_SUNDAY_CHANNEL_ID": "222"}):
            self.assertEqual(configured_sunny_sunday_channel_id(111), 222)

    def test_sunny_sunday_schedule_is_posted_once_per_dedicated_channel(self) -> None:
        schedule = {"announcement_channel_id": 111}

        self.assertFalse(should_post_sunny_sunday_schedule(schedule, 111))
        self.assertTrue(should_post_sunny_sunday_schedule(schedule, 222))
        self.assertTrue(should_post_sunny_sunday_schedule({}, 111))

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

            sent_ids, categories, sunny_sunday = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"maintenance", "sale", "general", "update"})
        self.assertIsNone(sunny_sunday)

    def test_sunny_sunday_schedule_is_saved_and_loaded(self) -> None:
        original_state_path = maple_bot.STATE_PATH
        schedule = {
            "post_id": 42415,
            "title": "v.270 - Ride the Lightning",
            "url": "https://example.com/patch-notes",
            "entries": [
                {
                    "timestamp": 1785628800,
                    "name": "· __<t:1785628800:F> (<t:1785628800:R>)__",
                    "value": "- 스타포스 강화 비용 30% 할인",
                    "message_id": 123,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            maple_bot.STATE_PATH = Path(directory) / "state.json"
            save_state({1}, {"update"}, schedule)
            sent_ids, categories, loaded_schedule = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"update"})
        self.assertEqual(loaded_schedule, schedule)

    def test_html_to_text_removes_tags_and_script(self) -> None:
        source = "<h1>Patch</h1><script>ignore()</script><p>Notes</p>"
        self.assertEqual(html_to_text(source), "Patch Notes")

    def test_only_real_update_patch_notes_are_detected(self) -> None:
        self.assertTrue(
            is_patch_notes(
                {"category": "update", "name": "v.270 - Ride the Lightning Patch Notes"}
            )
        )
        self.assertFalse(
            is_patch_notes(
                {"category": "update", "name": "v.270 Ride the Lightning Update Preview"}
            )
        )
        self.assertFalse(
            is_patch_notes({"category": "general", "name": "Patch Notes"})
        )

    def test_extract_sunny_sunday_reads_dates_special_label_and_perks(self) -> None:
        source = """
        <a id="SunnySunday"></a><h2>Sunny Sunday</h2>
        <table><tr><th>Date</th><th>Perks</th></tr><tr>
          <td><strong>August 02, 2026</strong></td>
          <td><strong>Special Sunny Sunday</strong><ul>
            <li>30% off Star Force enhancements<ul><li>Excludes Superior Equipment</li></ul></li>
            <li>Combo Kill EXP + 300%</li>
          </ul></td>
        </tr></table>
        """

        self.assertEqual(
            extract_sunny_sunday(source),
            [
                (
                    "August 02, 2026",
                    True,
                    [
                        "30% off Star Force enhancements Excludes Superior Equipment",
                        "Combo Kill EXP + 300%",
                    ],
                )
            ],
        )

    def test_known_sunny_sunday_translations_follow_requested_wording(self) -> None:
        expected = {
            "+250% Monster Park Clear EXP Excludes Monster Park Extreme": "몬스터 파크 클리어 경험치 250% 증가 (익몬 제외)",
            "30% reduced chance of item destruction when enhancing items below 21 Stars": "21성 이하에서 스타포스 강화 시 파괴 확률 30% 감소",
            "30% off Star Force enhancements": "스타포스 강화 비용 30% 할인",
            "Elite monster appearance increase": "앨리트 몬스터 증가 (1마리 → 3마리)",
            "[HEXA Matrix] When Main Stat is Lv. 5+, the enhancement chance is increased 20%": "[헥사 매트릭스] 헥사 스탯의 메인 레벨 5이상 강화 확률 20% 증가",
            "50% off Ability resets": "어빌리티 재설정 비용 50% 할인",
            "1+1 Star Force upon a successful enhancement at 10-Star or below": "10성 이하에서 스타포스 강화시 1+1",
            "Treasure Hunter EXP ×3": "트레져 헌터 경험치 3배",
            "2x Sol Erda when hunting": "사냥을 통해 획득할 수 있는 솔 에르다 2배 증가",
            "Rune Appearance Cooldown reduction (from 15 min to 10 min)": "룬 재등장 및 재사용 대기시간 감소 (15분 → 10분)",
            "Combo Kill EXP +300%": "콤보킬 경험치 획득량 300% 증가",
            "+100% Rune EXP buff effect (3× normal EXP)": "룬 경험치 버프 효과 100% 증가",
            "5x Magnificent Soul chance from soul shards": "소울 조각 사용 시 위대한 소울 획득 확률 5배",
            "+100% chance to register a new monster in Monster Collection": "몬스터 컬렉션 신규 몬스터 등록 확률 추가 100%",
            "Mysterious Monsterbloom (x3): Untradable, Permanent": "의문의 모몽 (x3): 교환불가, 영구",
            "50% off Spell Trace Enhancements": "",
        }

        for source, translation in expected.items():
            self.assertEqual(known_sunny_sunday_translation(source), translation)

    def test_sunny_sunday_date_uses_discord_timestamp(self) -> None:
        self.assertEqual(
            format_sunny_sunday_date("August 02, 2026"),
            "<t:1785628800:F> (<t:1785628800:R>)",
        )

    def test_sunny_sunday_list_excludes_entries_after_24_hours(self) -> None:
        start = sunny_sunday_timestamp("August 02, 2026")
        entries = [
            {"timestamp": start, "name": "expired"},
            {"timestamp": start + 604800, "name": "upcoming"},
        ]

        self.assertEqual(
            visible_sunny_sunday_entries(entries, start + 86400),
            [entries[1]],
        )

    def test_weekly_sunny_sunday_message_lifecycle(self) -> None:
        start = sunny_sunday_timestamp("August 02, 2026")
        entry = {"timestamp": start, "message_id": None}

        self.assertIsNone(sunny_sunday_entry_action(entry, start - 1))
        self.assertEqual(sunny_sunday_entry_action(entry, start), "send")
        entry["message_id"] = 123
        self.assertIsNone(sunny_sunday_entry_action(entry, start + 86399))
        self.assertEqual(
            sunny_sunday_entry_action(entry, start + 86400), "delete"
        )


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
