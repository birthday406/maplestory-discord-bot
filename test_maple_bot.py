import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import maple_bot
from maple_bot import (
    ALERT_NEWS,
    ALERT_SUNNY_DAY,
    ALERT_SUNNY_LIST,
    HEXA_CORE_COSTS,
    calculate_hexa_cost,
    current_sunny_sunday_entry,
    extract_sunny_sunday,
    format_sunny_sunday_date,
    html_to_text,
    is_patch_notes,
    known_sunny_sunday_translation,
    load_state,
    migrate_sunny_sunday_state,
    news_alert_command,
    normalize_alert_channels,
    post_url,
    save_state,
    sunny_day_alert_command,
    sunny_list_alert_command,
    sunny_sunday_entry_action,
    sunny_sunday_timestamp,
    thumbnail_url,
    update_alert_channel,
    visible_sunny_sunday_entries,
    watched_posts,
)


class NewsFilteringTests(unittest.TestCase):
    def test_legacy_channels_are_migrated_only_when_no_saved_setting_exists(self) -> None:
        self.assertEqual(
            normalize_alert_channels(None, 111, 222),
            {
                ALERT_NEWS: {111},
                ALERT_SUNNY_DAY: {222},
                ALERT_SUNNY_LIST: {222},
            },
        )
        self.assertEqual(
            normalize_alert_channels({}, 111, 222),
            {ALERT_NEWS: set(), ALERT_SUNNY_DAY: set(), ALERT_SUNNY_LIST: set()},
        )

    def test_alert_channels_support_multiple_channels_and_idempotent_updates(self) -> None:
        channels = {ALERT_NEWS: set(), ALERT_SUNNY_DAY: set(), ALERT_SUNNY_LIST: set()}

        self.assertTrue(update_alert_channel(channels, ALERT_SUNNY_DAY, 111, True))
        self.assertTrue(update_alert_channel(channels, ALERT_SUNNY_DAY, 222, True))
        self.assertFalse(update_alert_channel(channels, ALERT_SUNNY_DAY, 222, True))
        self.assertTrue(update_alert_channel(channels, ALERT_SUNNY_DAY, 111, False))
        self.assertEqual(channels[ALERT_SUNNY_DAY], {222})

    def test_alert_setting_commands_are_guild_only_and_admin_only(self) -> None:
        for command in (
            news_alert_command,
            sunny_day_alert_command,
            sunny_list_alert_command,
        ):
            self.assertTrue(command.guild_only)
            self.assertTrue(command.default_permissions.administrator)
            self.assertEqual(
                {choice.value for choice in command.parameters[1].choices},
                {"on", "off"},
            )

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

            sent_ids, categories, sunny_sunday, alert_channels = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"maintenance", "sale", "general", "update"})
        self.assertIsNone(sunny_sunday)
        self.assertIsNone(alert_channels)

    def test_legacy_weekly_message_id_is_migrated_to_its_channel(self) -> None:
        schedule = {
            "announcement_channel_id": 222,
            "entries": [{"message_id": 333}],
        }

        self.assertTrue(migrate_sunny_sunday_state(schedule, 222))
        self.assertEqual(schedule, {"entries": [{"message_ids": {"222": 333}}]})

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
                    "message_ids": {"222": 123},
                }
            ],
        }
        alert_channels = {
            ALERT_NEWS: {111},
            ALERT_SUNNY_DAY: {222, 333},
            ALERT_SUNNY_LIST: {222},
        }
        with tempfile.TemporaryDirectory() as directory:
            maple_bot.STATE_PATH = Path(directory) / "state.json"
            save_state({1}, {"update"}, schedule, alert_channels)
            sent_ids, categories, loaded_schedule, loaded_channels = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"update"})
        self.assertEqual(loaded_schedule, schedule)
        self.assertEqual(
            normalize_alert_channels(loaded_channels, 999, 999), alert_channels
        )

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

    def test_sunny_sunday_command_selects_only_the_nearest_valid_entry(self) -> None:
        start = sunny_sunday_timestamp("August 02, 2026")
        entries = [
            {"timestamp": start + 1_209_600, "name": "later"},
            {"timestamp": start, "name": "expired"},
            {"timestamp": start + 604_800, "name": "this week"},
        ]

        self.assertEqual(
            current_sunny_sunday_entry(entries, start + 86_400),
            entries[2],
        )

    def test_weekly_sunny_sunday_message_lifecycle(self) -> None:
        start = sunny_sunday_timestamp("August 02, 2026")
        entry = {"timestamp": start, "message_ids": {}}

        self.assertIsNone(sunny_sunday_entry_action(entry, 111, start - 1))
        self.assertEqual(sunny_sunday_entry_action(entry, 111, start), "send")
        entry["message_ids"]["111"] = 123
        self.assertIsNone(sunny_sunday_entry_action(entry, 111, start + 86399))
        self.assertEqual(
            sunny_sunday_entry_action(entry, 111, start + 86400), "delete"
        )
        self.assertEqual(
            sunny_sunday_entry_action(entry, 222, start + 1), "send"
        )


class AlertDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_translated_embed_is_sent_to_every_registered_channel(self) -> None:
        first_channel = SimpleNamespace(
            id=111, send=AsyncMock(return_value=SimpleNamespace(id=1001))
        )
        second_channel = SimpleNamespace(
            id=222, send=AsyncMock(return_value=SimpleNamespace(id=1002))
        )
        bot = SimpleNamespace(
            alert_text_channels=lambda alert_type: [first_channel, second_channel]
        )
        embed = maple_bot.discord.Embed(title="translated announcement")

        sent_message_ids = await maple_bot.MapleNewsBot.send_alert_embed(
            bot, ALERT_NEWS, embed
        )

        self.assertEqual(sent_message_ids, {111: 1001, 222: 1002})
        first_channel.send.assert_awaited_once_with(embed=embed, file=None)
        second_channel.send.assert_awaited_once_with(embed=embed, file=None)

    async def test_enabling_new_list_channel_sends_saved_schedule_once(self) -> None:
        guild = SimpleNamespace(id=10, me=object())
        permissions = SimpleNamespace(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            attach_files=True,
        )
        channel = SimpleNamespace(
            id=222,
            guild=guild,
            mention="#sunny",
            permissions_for=Mock(return_value=permissions),
        )
        interaction = SimpleNamespace(
            guild=guild,
            permissions=SimpleNamespace(administrator=True),
            response=SimpleNamespace(
                send_message=AsyncMock(), defer=AsyncMock()
            ),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        schedule = {
            "title": "v.270",
            "entries": [{"timestamp": 1, "message_ids": {}}],
        }
        bot = SimpleNamespace(
            alert_channels={
                ALERT_NEWS: set(),
                ALERT_SUNNY_DAY: set(),
                ALERT_SUNNY_LIST: set(),
            },
            sunny_sunday=schedule,
            send_sunny_sunday_to_channel=AsyncMock(
                return_value=SimpleNamespace(id=1001)
            ),
            delete_sunny_day_message=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.configure_alert_channel(
            bot, interaction, channel, True, ALERT_SUNNY_LIST, "썬데이 목록 알림"
        )

        self.assertEqual(bot.alert_channels[ALERT_SUNNY_LIST], {222})
        bot.send_sunny_sunday_to_channel.assert_awaited_once_with(
            channel, "☀️ v.270 ☀️", schedule["entries"]
        )
        bot.persist_state.assert_called_once_with()


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
