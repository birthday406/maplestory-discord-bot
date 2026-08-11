import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import maple_bot
from maple_calculators import (
    calculate_arcane_symbol_completion,
    calculate_symbol,
    simulate_extreme_growth_potions,
)
from maple_data import (
    ARCANE_SYMBOL_GROWTH,
    AUTHENTIC_SYMBOL_GROWTH,
    BOSS_TRAFFIC_LIGHTS,
    EXTREME_GROWTH_POTION_RATES,
    SYMBOL_REGIONS,
)
from maple_bot import (
    ALERT_CASH_TRANSFER,
    ALERT_CUBE_SALE,
    ALERT_MIRACLE_TIME,
    ALERT_NEWS,
    ALERT_SERVER,
    ALERT_SUNNY_DAY,
    ALERT_SUNNY_LIST,
    ALERT_URSUS,
    EPIC_DUNGEON_BONUSES,
    EPIC_DUNGEONS,
    EXP_COUPON_BURNING_OPTIONS,
    EXP_COUPONS,
    GROWTH_POTION_EMOJIS,
    GROWTH_POTIONS,
    HEXA_CORE_COSTS,
    LEVEL_EXP,
    build_miracle_time_embed,
    build_server_status_embed,
    build_ursus_embed,
    calculate_epic_dungeon,
    calculate_exp_coupons,
    calculate_growth_potions,
    calculate_hexa_cost,
    cash_shop_command,
    cash_shop_transfer_alert_command,
    cash_shop_transfer_command,
    channel_recommend_command,
    cube_sale_alert_command,
    cube_sale_command,
    current_sunny_sunday_entry,
    current_ursus_window,
    epic_dungeon_command,
    exp_coupon_command,
    extreme_growth_potion_command,
    extract_cash_shop_transfer,
    extract_miracle_time,
    extract_sunny_sunday,
    format_sunny_sunday_date,
    format_boss_hp_as_k,
    growth_potion_command,
    help_command,
    hot_week_command,
    html_to_text,
    is_cash_shop_update,
    is_patch_notes,
    known_sunny_sunday_translation,
    localize_sunny_sunday_text,
    load_state,
    merge_patch_events,
    migrate_sunny_sunday_state,
    miracle_time_alert_command,
    miracle_time_command,
    news_alert_command,
    normalize_alert_channels,
    parse_pssb_rates,
    parse_server_status,
    post_url,
    pssb_command,
    save_state,
    server_status_alert_command,
    server_status_command,
    should_send_cash_shop_transfer,
    should_send_miracle_time,
    sunny_day_alert_command,
    sunny_list_alert_command,
    sunny_sunday_command,
    sunny_sunday_entry_action,
    sunny_sunday_list_command,
    sunny_sunday_timestamp,
    symbol_calculator_command,
    thumbnail_url,
    traffic_light_command,
    update_alert_channel,
    utc_event_timestamp,
    ursus_alert_command,
    ursus_boundary_event,
    ursus_command,
    visible_sunny_sunday_entries,
    watched_posts,
)


class NewsFilteringTests(unittest.TestCase):
    def test_pssb_rates_keep_gender_pair_in_one_reward_slot(self) -> None:
        source = """
        <table><tbody>
        <tr><th>Item Name</th><th>Gender</th><th>Rate</th></tr>
        <tr><td>Shared Hat</td><td>All</td><td>2.00%</td></tr>
        <tr><td>Captain (M)</td><td>Male</td><td rowspan="2">4.00%</td></tr>
        <tr><td>Captain (F)</td><td>Female</td></tr>
        <tr><td></td><td></td><td>100.00%</td></tr>
        </tbody></table>
        """

        self.assertEqual(
            parse_pssb_rates(source),
            [("Shared Hat", 2.0), ("Captain (M) / Captain (F)", 4.0)],
        )

    def test_pssb_command_only_offers_one_or_five_draws(self) -> None:
        self.assertEqual(
            {choice.value for choice in pssb_command.parameters[0].choices},
            {1, 5},
        )

    def test_legacy_channels_are_migrated_only_when_no_saved_setting_exists(self) -> None:
        self.assertEqual(
            normalize_alert_channels(None, 111, 222),
            {
                ALERT_NEWS: {111},
                ALERT_SUNNY_DAY: {222},
                ALERT_SUNNY_LIST: {222},
                ALERT_MIRACLE_TIME: set(),
                ALERT_CASH_TRANSFER: set(),
                ALERT_CUBE_SALE: set(),
                ALERT_URSUS: set(),
                ALERT_SERVER: set(),
            },
        )
        self.assertEqual(
            normalize_alert_channels({}, 111, 222),
            {
                ALERT_NEWS: set(),
                ALERT_SUNNY_DAY: set(),
                ALERT_SUNNY_LIST: set(),
                ALERT_MIRACLE_TIME: set(),
                ALERT_CASH_TRANSFER: set(),
                ALERT_CUBE_SALE: set(),
                ALERT_URSUS: set(),
                ALERT_SERVER: set(),
            },
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
            miracle_time_alert_command,
            cash_shop_transfer_alert_command,
            cube_sale_alert_command,
            ursus_alert_command,
            server_status_alert_command,
        ):
            self.assertTrue(command.guild_only)
            self.assertTrue(command.default_permissions.administrator)
            self.assertEqual(
                {choice.value for choice in command.parameters[1].choices},
                {"on", "off"},
            )

    def test_server_status_requires_all_logins_and_one_game_channel(self) -> None:
        payload = {
            "servers": [
                {
                    "worldName": world,
                    "Login00": 1,
                    "Login01": 1,
                    "Game00": 1 if world != "Bera" else 0,
                    "Game01": -1,
                }
                for world in ("Scania", "Bera", "Kronos", "Hyperion")
            ]
        }

        self.assertEqual(
            parse_server_status(payload),
            {"Scania": True, "Bera": False, "Kronos": True, "Hyperion": True},
        )

    def test_server_status_rejects_missing_world_data(self) -> None:
        with self.assertRaises(ValueError):
            parse_server_status({"servers": []})

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

    def test_cash_shop_update_only_matches_sale_update_posts(self) -> None:
        self.assertTrue(
            is_cash_shop_update(
                {
                    "category": "sale",
                    "name": "Cash Shop Update for August 11",
                }
            )
        )
        self.assertFalse(
            is_cash_shop_update(
                {"category": "sale", "name": "Premium Surprise Style Box"}
            )
        )
        self.assertFalse(
            is_cash_shop_update(
                {"category": "events", "name": "Cash Shop Update Preview"}
            )
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

            (
                sent_ids,
                categories,
                sunny_sunday,
                patch_events,
                alert_channels,
                exp_coupon_preferences,
                symbol_preferences,
                ursus_alert_events,
                latest_cash_shop,
                server_status,
            ) = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"maintenance", "sale", "general", "update"})
        self.assertIsNone(sunny_sunday)
        self.assertIsNone(patch_events)
        self.assertIsNone(alert_channels)
        self.assertEqual(exp_coupon_preferences, {})
        self.assertEqual(symbol_preferences, {})
        self.assertEqual(ursus_alert_events, {})
        self.assertIsNone(latest_cash_shop)
        self.assertIsNone(server_status)

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
            ALERT_MIRACLE_TIME: set(),
            ALERT_CASH_TRANSFER: set(),
            ALERT_CUBE_SALE: set(),
            ALERT_URSUS: set(),
            ALERT_SERVER: set(),
        }
        patch_events = {"post_id": 42415, "miracle_time": []}
        exp_coupon_preferences = {"123": "하이퍼버닝"}
        symbol_preferences = {
            "123": {"potion_level": 3, "elanos": "적용"}
        }
        ursus_alert_events = {"111": "start:1786467600"}
        latest_cash_shop = {
            "post_id": 42853,
            "title": "Cash Shop Update for August 11",
            "url": "https://example.com/cash-shop-update",
        }
        with tempfile.TemporaryDirectory() as directory:
            maple_bot.STATE_PATH = Path(directory) / "state.json"
            save_state(
                {1},
                {"update"},
                schedule,
                alert_channels,
                patch_events,
                exp_coupon_preferences,
                symbol_preferences,
                ursus_alert_events,
                latest_cash_shop,
                "down",
            )
            (
                sent_ids,
                categories,
                loaded_schedule,
                loaded_patch_events,
                loaded_channels,
                loaded_exp_coupon_preferences,
                loaded_symbol_preferences,
                loaded_ursus_alert_events,
                loaded_latest_cash_shop,
                loaded_server_status,
            ) = load_state()

        maple_bot.STATE_PATH = original_state_path
        self.assertEqual(sent_ids, {1})
        self.assertEqual(categories, {"update"})
        self.assertEqual(loaded_schedule, schedule)
        self.assertEqual(loaded_patch_events, patch_events)
        self.assertEqual(
            normalize_alert_channels(loaded_channels, 999, 999), alert_channels
        )
        self.assertEqual(loaded_exp_coupon_preferences, exp_coupon_preferences)
        self.assertEqual(loaded_symbol_preferences, symbol_preferences)
        self.assertEqual(loaded_ursus_alert_events, ursus_alert_events)
        self.assertEqual(loaded_latest_cash_shop, latest_cash_shop)
        self.assertEqual(loaded_server_status, "down")

    def test_ursus_schedule_follows_pacific_daylight_saving_time(self) -> None:
        summer_start = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
        summer_window = current_ursus_window(summer_start)
        self.assertIsNotNone(summer_window)
        self.assertEqual((summer_window[0].hour, summer_window[1].hour), (10, 14))
        self.assertEqual(ursus_boundary_event(summer_start)[0], "start")
        self.assertEqual(
            ursus_boundary_event(datetime(2026, 8, 11, 21, 0, tzinfo=timezone.utc))[0],
            "end",
        )
        self.assertIsNone(
            current_ursus_window(datetime(2026, 8, 11, 21, 0, tzinfo=timezone.utc))
        )
        self.assertIsNone(
            ursus_boundary_event(datetime(2026, 8, 11, 17, 1, tzinfo=timezone.utc))
        )

        winter_start = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
        winter_window = current_ursus_window(winter_start)
        self.assertIsNotNone(winter_window)
        self.assertEqual((winter_window[0].hour, winter_window[1].hour), (9, 13))

    def test_ursus_embed_uses_discord_timestamps_and_state_image(self) -> None:
        now = datetime(2026, 8, 11, 17, 0, tzinfo=timezone.utc)
        window = current_ursus_window(now)
        embed, image_path = build_ursus_embed("active", window, now)

        self.assertEqual(image_path, maple_bot.URSUS_ACTIVE_IMAGE_PATH)
        self.assertIn("진행 중입니다", embed.description)
        self.assertIn(f"<t:{int(window[0].timestamp())}:T>", embed.description)
        self.assertEqual(embed.image.url, "attachment://ursus-golden-time.jpg")

        inactive_embed, inactive_path = build_ursus_embed("inactive", now=now)
        self.assertEqual(inactive_path, maple_bot.URSUS_INACTIVE_IMAGE_PATH)
        self.assertIn("진행 중이지 않습니다", inactive_embed.description)
        self.assertEqual(
            inactive_embed.image.url,
            "attachment://ursus-golden-time-inactive.jpg",
        )
        ended_embed, ended_path = build_ursus_embed("ended", window, now)
        self.assertEqual(ended_path, maple_bot.URSUS_INACTIVE_IMAGE_PATH)
        self.assertIn("끝났습니다", ended_embed.description)

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

    def test_patch_event_sections_are_extracted_from_updated_patch_notes(self) -> None:
        source = """
        <a id="MiracleTime"></a><h2>Miracle Time</h2>
        <table><tr><th>Equipment</th><th>Date</th></tr><tr>
          <td>Emblem, Mechanical Heart, Ring, Accessory, Shoulder Accessory</td>
          <td>August 14, 2026<br>12:00 AM UTC - 11:59 PM UTC</td>
        </tr></table>
        <a id="CashShopTransfer"></a><h2>Cash Shop Transfer</h2>
        <p><strong>September 2, 2026 12:00 AM UTC - September 9, 2026 2:00 PM UTC</strong></p>
        """

        self.assertEqual(
            extract_cash_shop_transfer(source),
            {
                "start_timestamp": utc_event_timestamp(
                    "September 2, 2026 12:00 AM UTC"
                ),
                "end_timestamp": utc_event_timestamp(
                    "September 9, 2026 2:00 PM UTC"
                ),
            },
        )
        self.assertEqual(
            extract_miracle_time(source),
            [
                {
                    "equipment": "엠블렘, 기계 심장, 반지, 장신구, 어깨장식",
                    "start_timestamp": utc_event_timestamp(
                        "August 14, 2026 12:00 AM UTC"
                    ),
                    "end_timestamp": utc_event_timestamp(
                        "August 14, 2026 11:59 PM UTC"
                    ),
                    "notified_channel_ids": [],
                }
            ],
        )

    def test_miracle_time_embed_uses_requested_cube_and_equipment_lines(self) -> None:
        embed = build_miracle_time_embed(
            {"url": "https://example.com/patch"},
            [{"start_timestamp": 123, "equipment": "엠블렘, 기계 심장, 반지, 장신구, 어깨장식"}],
        )

        self.assertEqual(
            embed.description.splitlines()[1],
            "사용 가능: Glowing Cube (레드 큐브)·Bright Cube (블랙 큐브)",
        )
        self.assertEqual(embed.fields[0].name, "· __<t:123:F> (<t:123:R>)__")
        self.assertEqual(
            embed.fields[0].value,
            "대상 장비　엠블렘, 기계 심장, 반지, 장신구, 어깨장식",
        )

    def test_patch_note_refresh_keeps_sent_miracle_alerts(self) -> None:
        current = {
            "post_id": 42415,
            "cash_shop_transfer": {
                "start_timestamp": 50,
                "notified_channel_ids": [222],
            },
            "miracle_time": [
                {"start_timestamp": 100, "notified_channel_ids": [111]}
            ],
        }
        updated = {
            "post_id": 42415,
            "cash_shop_transfer": {
                "start_timestamp": 50,
                "notified_channel_ids": [],
            },
            "miracle_time": [
                {"start_timestamp": 100, "notified_channel_ids": []}
            ],
        }

        self.assertEqual(
            merge_patch_events(current, updated)["miracle_time"][0][
                "notified_channel_ids"
            ],
            [111],
        )
        self.assertEqual(
            merge_patch_events(current, updated)["cash_shop_transfer"][
                "notified_channel_ids"
            ],
            [222],
        )

    def test_miracle_alert_is_sent_once_during_its_utc_day(self) -> None:
        entry = {
            "start_timestamp": 100,
            "end_timestamp": 200,
            "notified_channel_ids": [],
        }
        self.assertFalse(should_send_miracle_time(entry, 111, 99))
        self.assertTrue(should_send_miracle_time(entry, 111, 100))
        entry["notified_channel_ids"].append(111)
        self.assertFalse(should_send_miracle_time(entry, 111, 101))
        self.assertFalse(should_send_miracle_time(entry, 222, 201))

    def test_patch_event_commands_have_requested_names(self) -> None:
        self.assertEqual(cash_shop_transfer_command.name, "캐시이동")
        self.assertEqual(miracle_time_command.name, "미라클큐브")
        self.assertEqual(hot_week_command.name, "핫위크")
        self.assertEqual(cube_sale_command.name, "큐브세일")

    def test_cash_transfer_alert_is_sent_only_during_first_24_hours(self) -> None:
        event = {
            "start_timestamp": 100,
            "end_timestamp": 1_000_000,
            "notified_channel_ids": [],
        }
        self.assertFalse(should_send_cash_shop_transfer(event, 111, 99))
        self.assertTrue(should_send_cash_shop_transfer(event, 111, 100))
        event["notified_channel_ids"].append(111)
        self.assertFalse(should_send_cash_shop_transfer(event, 111, 101))
        self.assertFalse(
            should_send_cash_shop_transfer(event, 222, 100 + 86_400)
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

    def test_sunny_sunday_names_use_game_localization(self) -> None:
        self.assertEqual(
            localize_sunny_sunday_text(
                "슈피겔레트의 가속 열풍 시간 부스터와 Spiegelette"
            ),
            "슈피겔라의 헤이스트 피버 타임 부스터와 슈피겔라",
        )

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
    async def test_server_command_shows_all_four_main_worlds(self) -> None:
        statuses = {
            "Scania": True,
            "Bera": True,
            "Kronos": False,
            "Hyperion": True,
        }
        interaction = SimpleNamespace(
            client=SimpleNamespace(fetch_server_status=AsyncMock(return_value=statuses)),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await server_status_command.callback(interaction)

        interaction.response.defer.assert_awaited_once_with()
        embed = interaction.followup.send.await_args.kwargs["embed"]
        self.assertEqual({field.name for field in embed.fields}, set(statuses))
        self.assertIn("점검 중", next(field.value for field in embed.fields if field.name == "Kronos"))

    async def test_server_open_alert_is_sent_once_after_down_to_up_transition(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        bot = SimpleNamespace(
            server_status="down",
            fetch_server_status=AsyncMock(return_value=statuses),
            send_alert_embed=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)
        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        self.assertEqual(bot.server_status, "up")
        bot.send_alert_embed.assert_awaited_once()
        self.assertEqual(bot.send_alert_embed.await_args.args[0], ALERT_SERVER)
        bot.persist_state.assert_called_once_with()

    async def test_server_api_error_does_not_change_saved_status(self) -> None:
        bot = SimpleNamespace(
            server_status="up",
            fetch_server_status=AsyncMock(side_effect=ValueError("bad response")),
            send_alert_embed=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        self.assertEqual(bot.server_status, "up")
        bot.send_alert_embed.assert_not_awaited()
        bot.persist_state.assert_not_called()

    def test_server_open_embed_uses_green_color(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        embed = build_server_status_embed(statuses, opened=True)

        self.assertEqual(embed.color.value, 0x57F287)
        self.assertIn("모두 열렸습니다", embed.description)

    async def test_ursus_command_attaches_active_image(self) -> None:
        window = (
            datetime(2026, 8, 11, 10, 0, tzinfo=maple_bot.URSUS_TIMEZONE),
            datetime(2026, 8, 11, 14, 0, tzinfo=maple_bot.URSUS_TIMEZONE),
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        image_file = object()

        with patch("maple_bot.current_ursus_window", return_value=window), patch(
            "maple_bot.discord.File", return_value=image_file
        ) as file_class:
            await ursus_command.callback(interaction)

        file_class.assert_called_once_with(maple_bot.URSUS_ACTIVE_IMAGE_PATH)
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIs(send_kwargs["file"], image_file)
        self.assertIn("진행 중입니다", send_kwargs["embed"].description)

    async def test_ursus_start_and_end_alerts_are_each_sent_once(self) -> None:
        class DummyTextChannel:
            def __init__(self) -> None:
                self.id = 111
                self.send = AsyncMock()

        channel = DummyTextChannel()
        start = datetime(2026, 8, 11, 10, 0, tzinfo=maple_bot.URSUS_TIMEZONE)
        end = datetime(2026, 8, 11, 14, 0, tzinfo=maple_bot.URSUS_TIMEZONE)
        bot = SimpleNamespace(
            alert_channels={ALERT_URSUS: {111}},
            ursus_alert_events={},
            get_channel=lambda channel_id: channel,
            persist_state=Mock(),
        )
        image_file = object()

        with patch.object(maple_bot.discord, "TextChannel", DummyTextChannel), patch(
            "maple_bot.ursus_boundary_event", return_value=("start", start, end)
        ) as boundary_event, patch(
            "maple_bot.discord.File", return_value=image_file
        ):
            await maple_bot.MapleNewsBot.check_ursus.coro(bot)
            await maple_bot.MapleNewsBot.check_ursus.coro(bot)
            boundary_event.return_value = ("end", start, end)
            await maple_bot.MapleNewsBot.check_ursus.coro(bot)

        self.assertEqual(channel.send.await_count, 2)
        self.assertEqual(
            bot.ursus_alert_events,
            {"111": f"end:{int(end.timestamp())}"},
        )
        self.assertEqual(bot.persist_state.call_count, 2)

    async def test_cash_transfer_command_attaches_embed_image(self) -> None:
        schedule = {
            "url": "https://example.com/patch",
            "cash_shop_transfer": {"start_timestamp": 100, "end_timestamp": 200},
        }
        interaction = SimpleNamespace(
            client=SimpleNamespace(patch_events=schedule),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        image_file = object()

        with patch("maple_bot.discord.File", return_value=image_file) as file_class:
            await cash_shop_transfer_command.callback(interaction)

        file_class.assert_called_once_with(maple_bot.CASH_SHOP_TRANSFER_IMAGE_PATH)
        send_kwargs = interaction.response.send_message.await_args.kwargs
        self.assertIs(send_kwargs["file"], image_file)
        self.assertEqual(
            send_kwargs["embed"].image.url,
            "attachment://cash-shop-transfer.png",
        )

    async def test_miracle_time_alert_is_sent_and_recorded_once(self) -> None:
        class DummyTextChannel:
            def __init__(self) -> None:
                self.id = 111
                self.send = AsyncMock()

        channel = DummyTextChannel()
        entry = {
            "equipment": "모자",
            "start_timestamp": 0,
            "end_timestamp": 9_999_999_999,
            "notified_channel_ids": [],
        }
        bot = SimpleNamespace(
            patch_events={
                "url": "https://example.com/patch",
                "miracle_time": [entry],
            },
            sent_ids={1},
            alert_channels={ALERT_MIRACLE_TIME: {111}},
            get_channel=lambda channel_id: channel,
            persist_state=Mock(),
        )

        with patch.object(maple_bot.discord, "TextChannel", DummyTextChannel):
            await maple_bot.MapleNewsBot.check_miracle_time.coro(bot)

        channel.send.assert_awaited_once()
        self.assertEqual(entry["notified_channel_ids"], [111])
        bot.persist_state.assert_called_once_with()

    async def test_cash_transfer_alert_is_sent_and_recorded_once(self) -> None:
        class DummyTextChannel:
            def __init__(self) -> None:
                self.id = 111
                self.send = AsyncMock()

        channel = DummyTextChannel()
        event = {
            "start_timestamp": 0,
            "end_timestamp": 9_999_999_999,
            "notified_channel_ids": [],
        }
        bot = SimpleNamespace(
            patch_events={
                "url": "https://example.com/patch",
                "cash_shop_transfer": event,
            },
            sent_ids={1},
            alert_channels={ALERT_CASH_TRANSFER: {111}},
            get_channel=lambda channel_id: channel,
            persist_state=Mock(),
        )

        image_file = object()
        with patch.object(maple_bot.discord, "TextChannel", DummyTextChannel), patch(
            "maple_bot.discord.File", return_value=image_file
        ) as file_class, patch("maple_bot.datetime") as mocked_datetime:
            mocked_datetime.now.return_value.timestamp.return_value = 1
            await maple_bot.MapleNewsBot.check_cash_shop_transfer.coro(bot)

        channel.send.assert_awaited_once()
        file_class.assert_called_once_with(maple_bot.CASH_SHOP_TRANSFER_IMAGE_PATH)
        send_kwargs = channel.send.await_args.kwargs
        self.assertIs(send_kwargs["file"], image_file)
        self.assertEqual(
            send_kwargs["embed"].image.url,
            "attachment://cash-shop-transfer.png",
        )
        self.assertEqual(event["notified_channel_ids"], [111])
        bot.persist_state.assert_called_once_with()

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


class TrafficLightTests(unittest.IsolatedAsyncioTestCase):
    def test_boss_hp_units_are_converted_to_ingame_k_unit(self) -> None:
        self.assertEqual(format_boss_hp_as_k("38.5B"), "38,500,000K")
        self.assertEqual(format_boss_hp_as_k("24.175T"), "24,175,000,000K")
        self.assertEqual(format_boss_hp_as_k("1.01Q"), "1,010,000,000,000K")

    def test_boss_choices_match_all_provided_health_values(self) -> None:
        self.assertEqual(len(BOSS_TRAFFIC_LIGHTS), 18)
        self.assertEqual(
            {choice.value for choice in traffic_light_command.parameters[0].choices},
            set(BOSS_TRAFFIC_LIGHTS),
        )
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["칼로스"]["카오스"], ("5.12Q", "256T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["칼로스"]["익스트림"], ("21.57Q", "1.08Q"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["세렌"]["익스트림"], ("6.48Q", "324T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["카링"]["익스트림"], ("55.10Q", "2.76Q"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["림보"]["하드"], ("12.55Q", "627.65T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["발드릭스"]["노말"], ("8.90Q", "445T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["발드릭스"]["하드"], ("20.27Q", "1.01Q"))

    async def test_command_shows_selected_boss_five_percent_requirement(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await traffic_light_command.callback(
            interaction,
            SimpleNamespace(value="발드릭스"),
            SimpleNamespace(value="하드"),
        )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("**총 체력**　20,270,000,000,000K", embed.description)
        self.assertIn("**5% 최소 피해량**　1,010,000,000,000K", embed.description)

    async def test_command_explains_invalid_boss_difficulty_combination(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await traffic_light_command.callback(
            interaction,
            SimpleNamespace(value="림보"),
            SimpleNamespace(value="카오스"),
        )

        interaction.response.send_message.assert_awaited_once_with(
            "림보에서 선택 가능한 난이도: **노말, 하드**",
            ephemeral=True,
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


class ExtremeGrowthPotionTests(unittest.TestCase):
    def test_probability_table_covers_every_level_and_each_row_totals_100(self) -> None:
        self.assertEqual(set(EXTREME_GROWTH_POTION_RATES), set(range(130, 200)))
        for rates in EXTREME_GROWTH_POTION_RATES.values():
            self.assertEqual(len(rates), 10)
            self.assertEqual(sum(rates), 100)

    def test_known_probability_rows_match_supplied_images(self) -> None:
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[130],
            (0, 0, 0, 0, 0, 0, 0, 5, 5, 90),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[131],
            (0, 0, 0, 0, 0, 0, 5, 5, 10, 80),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[134],
            (0, 0, 0, 5, 5, 5, 5, 10, 15, 55),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[135],
            (0, 0, 0, 5, 5, 5, 5, 15, 20, 45),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[137],
            (0, 0, 5, 5, 5, 5, 10, 15, 20, 35),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[139],
            (0, 5, 5, 5, 5, 10, 10, 15, 20, 25),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[140],
            (5, 5, 5, 5, 5, 5, 5, 20, 20, 25),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[141],
            (5, 5, 5, 5, 5, 5, 10, 20, 20, 20),
        )
        self.assertEqual(
            EXTREME_GROWTH_POTION_RATES[199],
            (100, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        )

    def test_simulation_stops_using_potions_at_level_200(self) -> None:
        with patch("maple_calculators.random.choices", return_value=[10]) as choices:
            self.assertEqual(simulate_extreme_growth_potions(195, 10), (200, [10]))

        choices.assert_called_once()

    def test_start_level_is_limited_to_130_through_199(self) -> None:
        for invalid_level in (129, 200):
            with self.assertRaises(ValueError):
                simulate_extreme_growth_potions(invalid_level, 1)

    def test_count_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            simulate_extreme_growth_potions(130, 0)

    def test_command_is_named_extreme_growth_potion(self) -> None:
        self.assertEqual(extreme_growth_potion_command.name, "익성비")


class ExtremeGrowthPotionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_uses_custom_egp_emoji_without_table_footer(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        with patch(
            "maple_bot.simulate_extreme_growth_potions", return_value=(200, [1])
        ):
            await extreme_growth_potion_command.callback(interaction, 199, 1)

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("<:EGP:1536685490789679104>", embed.title)
        self.assertNotIn("EGP 엑셀 표", str(embed.to_dict()))


class GrowthPotionTests(unittest.TestCase):
    def test_each_potion_has_its_requested_custom_emoji(self) -> None:
        self.assertEqual(
            GROWTH_POTION_EMOJIS,
            {
                "익성비 · 익스트림 성장의 비약": "<:EGP:1536685490789679104>",
                "궁성비 · 궁극의 유니온 성장의 비약": "<:UGP:1536686894434488471>",
                "극성비 · 극한 성장의 비약": "<:MGP:1536686939049168967>",
                "초성비 · 초월 성장의 비약": "<:TGP:1536686905238749245>",
                "전성비 · 전설 성장의 비약": "<:LGP:1536686920707342407>",
            },
        )

    def test_level_exp_table_covers_level_200_through_299(self) -> None:
        self.assertEqual(len(LEVEL_EXP), 100)
        self.assertEqual(LEVEL_EXP[0], 2_207_026_470)
        self.assertEqual(LEVEL_EXP[-1], 1_737_759_854_037_637)

    def test_existing_exp_carries_over_after_level_up(self) -> None:
        self.assertEqual(
            calculate_growth_potions("극성비 · 극한 성장의 비약", 200, 50, 1),
            (201, 1_103_513_235, 2_207_026_470, 1),
        )

    def test_potion_above_its_range_gives_fixed_exp(self) -> None:
        self.assertEqual(
            calculate_growth_potions("극성비 · 극한 성장의 비약", 250, 0, 1),
            (250, 156_334_978_019, 156_334_978_019, 1),
        )

    def test_extreme_potion_uses_its_fixed_exp_at_level_200(self) -> None:
        self.assertEqual(
            calculate_growth_potions("익성비 · 익스트림 성장의 비약", 200, 0, 1),
            (200, 571_115_568, 571_115_568, 1),
        )

    def test_hyper_burning_stops_exactly_at_level_260(self) -> None:
        self.assertEqual(
            calculate_growth_potions(
                "초성비 · 초월 성장의 비약", 257, 0, 1, hyper_burning=True
            ),
            (260, 0, LEVEL_EXP[57], 1),
        )

    def test_beyond_burning_stops_exactly_at_level_270(self) -> None:
        self.assertEqual(
            calculate_growth_potions(
                "초성비 · 초월 성장의 비약", 269, 0, 1, beyond_burning=True
            ),
            (270, 0, LEVEL_EXP[69], 1),
        )

    def test_beyond_burning_does_not_apply_below_level_260(self) -> None:
        self.assertEqual(
            calculate_growth_potions(
                "초성비 · 초월 성장의 비약", 259, 0, 1, beyond_burning=True
            ),
            (260, 0, LEVEL_EXP[59], 1),
        )

    def test_start_level_is_limited_to_200_through_299(self) -> None:
        for invalid_level in (199, 300):
            with self.assertRaises(ValueError):
                calculate_growth_potions(
                    "초성비 · 초월 성장의 비약", invalid_level, 0, 1
                )

    def test_remaining_potions_are_not_used_after_level_300(self) -> None:
        result = calculate_growth_potions(
            "전성비 · 전설 성장의 비약", 299, 99.999, 100
        )

        self.assertEqual(result[0], 300)
        self.assertEqual(result[3], 1)

    def test_command_offers_only_requested_potions(self) -> None:
        self.assertEqual(
            [parameter.display_name for parameter in growth_potion_command.parameters],
            ["비약종류", "시작레벨", "경험치", "개수", "하이퍼버닝", "비욘드버닝"],
        )
        self.assertEqual(
            {choice.value for choice in growth_potion_command.parameters[0].choices},
            set(GROWTH_POTIONS),
        )
        for parameter_index in (4, 5):
            self.assertFalse(growth_potion_command.parameters[parameter_index].required)
            self.assertEqual(
                {
                    choice.value
                    for choice in growth_potion_command.parameters[
                        parameter_index
                    ].choices
                },
                {"적용", "미적용"},
            )

    def test_command_is_named_growth_potion(self) -> None:
        self.assertEqual(growth_potion_command.name, "성장의비약")


class GrowthPotionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_burning_choices_default_to_disabled(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        potion = SimpleNamespace(
            name="극성비 · 극한 성장의 비약",
            value="극성비 · 극한 성장의 비약",
        )

        with patch(
            "maple_bot.calculate_growth_potions",
            return_value=(245, 0, 0, 1),
        ) as calculator:
            await growth_potion_command.callback(interaction, potion, 245, 0, 1)

        calculator.assert_called_once_with(potion.value, 245, 0, 1, False, False)
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("하이퍼 버닝**　미적용", embed.description)
        self.assertIn("비욘드 버닝**　미적용", embed.description)

    async def test_korean_burning_choices_are_converted_to_boolean(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        potion = SimpleNamespace(
            name="극성비 · 극한 성장의 비약",
            value="극성비 · 극한 성장의 비약",
        )

        with patch(
            "maple_bot.calculate_growth_potions",
            return_value=(245, 0, 0, 1),
        ) as calculator:
            await growth_potion_command.callback(
                interaction,
                potion,
                245,
                0,
                1,
                SimpleNamespace(name="적용", value="적용"),
                SimpleNamespace(name="미적용", value="미적용"),
            )

        calculator.assert_called_once_with(
            potion.value, 245, 0, 1, True, False
        )
        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("<:MGP:1536686939049168967>", embed.title)
        self.assertIn("하이퍼 버닝**　적용", embed.description)
        self.assertIn("비욘드 버닝**　미적용", embed.description)


class ExpCouponTests(unittest.TestCase):
    def test_coupon_tables_match_supplied_html_boundaries(self) -> None:
        normal_start, normal_exp = EXP_COUPONS["EXP 교환권"]
        advanced_start, advanced_exp = EXP_COUPONS["상급 EXP 교환권"]

        self.assertEqual((normal_start, len(normal_exp)), (200, 61))
        self.assertEqual((normal_exp[0], normal_exp[-1]), (7_404_000, 76_572_000))
        self.assertEqual((advanced_start, len(advanced_exp)), (260, 40))
        self.assertEqual(
            (advanced_exp[0], advanced_exp[-1]),
            (388_229_000, 1_078_497_000),
        )

    def test_normal_coupon_levels_up_with_expected_remainder(self) -> None:
        self.assertEqual(
            calculate_exp_coupons("EXP 교환권", 200, 0, 299),
            (201, 6_769_530, 2_213_796_000, 299),
        )

    def test_normal_coupon_stops_after_level_260(self) -> None:
        result = calculate_exp_coupons("EXP 교환권", 260, 0, 100_000_000)

        self.assertEqual(result[0], 261)
        self.assertEqual(result[3], 22_619)

    def test_advanced_coupon_can_reach_level_300(self) -> None:
        result = calculate_exp_coupons("상급 EXP 교환권", 299, 0, 2_000_000)

        self.assertEqual(result[0], 300)
        self.assertEqual(result[3], 1_611_280)

    def test_hyper_burning_stops_exactly_at_level_260(self) -> None:
        coupon_exp = EXP_COUPONS["EXP 교환권"][1][59]
        required_count = (LEVEL_EXP[59] + coupon_exp - 1) // coupon_exp
        result = calculate_exp_coupons(
            "EXP 교환권", 259, 0, required_count, "하이퍼버닝"
        )

        self.assertEqual(result[0], 260)

    def test_beyond_burning_stops_exactly_at_level_270(self) -> None:
        required_count = (
            LEVEL_EXP[69] + EXP_COUPONS["상급 EXP 교환권"][1][9] - 1
        ) // EXP_COUPONS["상급 EXP 교환권"][1][9]
        result = calculate_exp_coupons(
            "상급 EXP 교환권", 269, 0, required_count, "비욘드버닝"
        )

        self.assertEqual(result[0], 270)

    def test_coupon_rejects_level_outside_its_table(self) -> None:
        for coupon_name, level in (("EXP 교환권", 261), ("상급 EXP 교환권", 259)):
            with self.assertRaises(ValueError):
                calculate_exp_coupons(coupon_name, level, 0, 1)

    def test_command_offers_both_coupon_types(self) -> None:
        self.assertEqual(
            {choice.value for choice in exp_coupon_command.parameters[0].choices},
            set(EXP_COUPONS),
        )

    def test_command_offers_requested_burning_types(self) -> None:
        burning_parameter = next(
            parameter
            for parameter in exp_coupon_command.parameters
            if parameter.name == "burning"
        )
        self.assertEqual(
            {choice.value for choice in burning_parameter.choices},
            set(EXP_COUPON_BURNING_OPTIONS),
        )
        self.assertFalse(burning_parameter.required)


class ExpCouponCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_uses_selected_coupon_emoji(self) -> None:
        expected_emojis = {
            "EXP 교환권": "<:EV:1536691867293323274>",
            "상급 EXP 교환권": "<:AEV:1536691857692565554>",
        }
        for coupon_name, emoji in expected_emojis.items():
            client = SimpleNamespace(
                exp_coupon_burning_preferences={}, persist_state=Mock()
            )
            interaction = SimpleNamespace(
                client=client,
                user=SimpleNamespace(id=123),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            with patch(
                "maple_bot.calculate_exp_coupons",
                return_value=(300, 0, 1, 1),
            ):
                await exp_coupon_command.callback(
                    interaction,
                    SimpleNamespace(name=coupon_name, value=coupon_name),
                    260,
                    0,
                    1,
                    SimpleNamespace(name="X", value="X"),
                )

            embed = interaction.response.send_message.await_args.kwargs["embed"]
            self.assertEqual(embed.title, f"{emoji} {coupon_name} 계산기")

    async def test_burning_defaults_to_x_then_saves_and_reuses_selection(self) -> None:
        client = SimpleNamespace(
            exp_coupon_burning_preferences={}, persist_state=Mock()
        )
        interactions = [
            SimpleNamespace(
                client=client,
                user=SimpleNamespace(id=123),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            for _ in range(3)
        ]
        coupon = SimpleNamespace(name="상급 EXP 교환권", value="상급 EXP 교환권")
        with patch(
            "maple_bot.calculate_exp_coupons",
            return_value=(270, 0, 1, 1),
        ) as calculate:
            await exp_coupon_command.callback(
                interactions[0], coupon, 269, 0, 1, None
            )
            await exp_coupon_command.callback(
                interactions[1],
                coupon,
                269,
                0,
                1,
                SimpleNamespace(name="비욘드버닝", value="비욘드버닝"),
            )
            await exp_coupon_command.callback(
                interactions[2], coupon, 269, 0, 1, None
            )

        self.assertEqual(client.exp_coupon_burning_preferences, {"123": "비욘드버닝"})
        client.persist_state.assert_called_once_with()
        self.assertEqual(
            [call.args[-1] for call in calculate.call_args_list],
            ["X", "비욘드버닝", "비욘드버닝"],
        )
        first_embed = interactions[0].response.send_message.await_args.kwargs["embed"]
        last_embed = interactions[2].response.send_message.await_args.kwargs["embed"]
        self.assertIn("**버닝**　X", first_embed.description)
        self.assertIn("**버닝**　비욘드버닝", last_embed.description)


class EpicDungeonTests(unittest.TestCase):
    def test_supplied_tables_cover_each_minimum_level_through_294(self) -> None:
        expected = {
            "하이마운틴": (260, 35, 260_900_000_000, 759_600_000_000),
            "앵글러컴퍼니": (270, 25, 554_700_000_000, 1_139_400_000_000),
            "악몽선경": (280, 15, 1_039_200_000_000, 1_519_200_000_000),
        }
        for dungeon_name, (minimum, length, first, last) in expected.items():
            dungeon = EPIC_DUNGEONS[dungeon_name]
            self.assertEqual(dungeon["minimum_level"], minimum)
            self.assertEqual(len(dungeon["experience"]), length)
            self.assertEqual(
                (dungeon["experience"][0], dungeon["experience"][-1]),
                (first, last),
            )

    def test_bonus_is_applied_to_selected_dungeon_experience(self) -> None:
        self.assertEqual(
            calculate_epic_dungeon("하이마운틴", 260, 0, 1.5),
            (260, 391_350_000_000, 260_900_000_000, 391_350_000_000),
        )

    def test_existing_experience_carries_over_after_level_up(self) -> None:
        current_exp = int(LEVEL_EXP[60] * 90 / 100)
        gained_exp = 260_900_000_000 * 2.5
        self.assertEqual(
            calculate_epic_dungeon("하이마운틴", 260, 90, 2.5),
            (
                261,
                int(current_exp + gained_exp - LEVEL_EXP[60]),
                260_900_000_000,
                int(gained_exp),
            ),
        )

    def test_dungeon_rejects_character_below_entry_level(self) -> None:
        with self.assertRaisesRegex(ValueError, "Lv.270"):
            calculate_epic_dungeon("앵글러컴퍼니", 269, 0, 1.5)
        with self.assertRaisesRegex(ValueError, "Lv.280"):
            calculate_epic_dungeon("악몽선경", 279, 0, 1.5)

    def test_level_294_and_above_use_last_table_value(self) -> None:
        for level in (294, 299):
            self.assertEqual(
                calculate_epic_dungeon("악몽선경", level, 0, 2.0)[2],
                1_519_200_000_000,
            )

    def test_command_offers_requested_dungeons_and_bonuses(self) -> None:
        self.assertEqual(
            {choice.value for choice in epic_dungeon_command.parameters[0].choices},
            set(EPIC_DUNGEONS),
        )
        self.assertEqual(
            {choice.value for choice in epic_dungeon_command.parameters[3].choices},
            set(EPIC_DUNGEON_BONUSES),
        )


class EpicDungeonCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_uses_selected_dungeon_emoji(self) -> None:
        expected_emojis = {
            "하이마운틴": "<:HMountain:1536686575558205540>",
            "앵글러컴퍼니": "<:Angler:1536686640045756446>",
            "악몽선경": "<:Nightmare:1536686565210722324>",
        }
        for dungeon_name, emoji in expected_emojis.items():
            interaction = SimpleNamespace(
                response=SimpleNamespace(send_message=AsyncMock())
            )
            with patch(
                "maple_bot.calculate_epic_dungeon",
                return_value=(300, 0, 1, 2),
            ):
                await epic_dungeon_command.callback(
                    interaction,
                    SimpleNamespace(name=dungeon_name, value=dungeon_name),
                    280,
                    0,
                    SimpleNamespace(name="1.5배", value=1.5),
                )

            embed = interaction.response.send_message.await_args.kwargs["embed"]
            self.assertIn(emoji, embed.title)


class SymbolCalculatorTests(unittest.TestCase):
    def test_growth_and_meso_tables_match_supplied_totals(self) -> None:
        self.assertEqual((sum(ARCANE_SYMBOL_GROWTH), sum(AUTHENTIC_SYMBOL_GROWTH)), (2679, 4565))
        expected_meso_totals = {
            "소멸의 여로": 252_470_000,
            "츄츄 아일랜드": 306_050_000,
            "레헬른": 359_630_000,
            "아르카나": 413_210_000,
            "모라스": 466_790_000,
            "에스페라": 520_370_000,
            "세르니움": 3_930_100_000,
            "호텔 아르크스": 4_751_600_000,
            "오디움": 5_573_300_000,
            "도원경": 6_395_000_000,
            "아르테리아": 7_216_900_000,
            "카르시온": 8_038_600_000,
            "탈라하트": 16_072_800_000,
            "기어드락": 20_181_300_000,
        }
        self.assertEqual(
            {
                region: sum(info["meso_costs"])
                for region, info in SYMBOL_REGIONS.items()
            },
            expected_meso_totals,
        )

    def test_arcane_calculation_applies_event_then_normal_daily_reward(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "아르카나", 1, 0, 20, 0, True, date(2026, 8, 10)
            ),
            (2679, 413_210_000, 20, 24, 128, date(2026, 12, 15)),
        )

    def test_authentic_potion_bonus_is_included_before_twenty_percent(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "세르니움", 1, 0, 11, 6, True, date(2026, 8, 10)
            ),
            (4565, 3_930_100_000, 10, 19, 430, date(2027, 10, 13)),
        )

    def test_current_growth_reduces_only_required_symbol_count(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "소멸의 여로", 1, 10, 2, 0, True, date(2026, 9, 9)
            ),
            (2, 970_000, 20, 24, 1, date(2026, 9, 9)),
        )

    def test_region_determines_symbol_type(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "세르니움", 1, 0, 2, 0, True, date(2026, 8, 10)
            )[:4],
            (29, 36_500_000, 10, 12),
        )

    def test_potion_bonus_applies_without_elanos_twenty_percent(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "세르니움", 1, 0, 2, 6, False, date(2026, 8, 10)
            ),
            (29, 36_500_000, 10, 16, 2, date(2026, 8, 11)),
        )

    def test_arcane_weekly_scenarios_do_not_increase_weekly_reward(self) -> None:
        self.assertEqual(
            calculate_arcane_symbol_completion(
                145, 20, 24, date(2026, 8, 10), True
            ),
            (2, date(2026, 8, 11)),
        )
        self.assertEqual(
            calculate_arcane_symbol_completion(
                145, 20, 24, date(2026, 8, 10), False
            ),
            (7, date(2026, 8, 16)),
        )

    def test_command_offers_regions_potion_levels_and_elanos_options(self) -> None:
        potion_parameter = next(
            parameter
            for parameter in symbol_calculator_command.parameters
            if parameter.name == "potion_level"
        )
        elanos_parameter = next(
            parameter
            for parameter in symbol_calculator_command.parameters
            if parameter.name == "elanos"
        )
        self.assertEqual(symbol_calculator_command.name, "심볼계산기")
        self.assertEqual(
            {choice.value for choice in symbol_calculator_command.parameters[0].choices},
            set(SYMBOL_REGIONS),
        )
        self.assertEqual(
            {choice.value for choice in potion_parameter.choices},
            set(range(7)),
        )
        self.assertEqual(
            {choice.value for choice in elanos_parameter.choices},
            {"적용", "미적용"},
        )
        self.assertFalse(potion_parameter.required)
        self.assertFalse(elanos_parameter.required)


class SymbolCalculatorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_infers_type_and_shows_current_growth(self) -> None:
        region = next(
            choice
            for choice in symbol_calculator_command.parameters[0].choices
            if choice.value == "소멸의 여로"
        )
        potion = symbol_calculator_command.parameters[4].choices[0]
        elanos = symbol_calculator_command.parameters[5].choices[0]
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                symbol_calculator_preferences={}, persist_state=Mock()
            ),
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await symbol_calculator_command.callback(
            interaction, region, 1, 10, 2, potion, elanos
        )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("아케인 심볼 · 소멸의 여로", embed.description)
        self.assertIn("현재 성장치**　10 / 12", embed.description)
        self.assertIn("엘라노스**　적용", embed.description)
        self.assertIn("이번 주 주간퀘 함", embed.description)
        self.assertIn("이번 주 주간퀘 안 함", embed.description)

    async def test_authentic_result_does_not_show_arcane_weekly_quest(self) -> None:
        region = next(
            choice
            for choice in symbol_calculator_command.parameters[0].choices
            if choice.value == "세르니움"
        )
        potion = symbol_calculator_command.parameters[4].choices[0]
        elanos = symbol_calculator_command.parameters[5].choices[0]
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                symbol_calculator_preferences={}, persist_state=Mock()
            ),
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await symbol_calculator_command.callback(
            interaction, region, 1, 0, 2, potion, elanos
        )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertNotIn("주간퀘", embed.description)

    async def test_options_default_then_save_and_reuse_user_selection(self) -> None:
        region = next(
            choice
            for choice in symbol_calculator_command.parameters[0].choices
            if choice.value == "세르니움"
        )
        client = SimpleNamespace(
            symbol_calculator_preferences={}, persist_state=Mock()
        )
        interactions = [
            SimpleNamespace(
                client=client,
                user=SimpleNamespace(id=123),
                response=SimpleNamespace(send_message=AsyncMock()),
            )
            for _ in range(3)
        ]
        with patch(
            "maple_bot.calculate_symbol",
            return_value=(10, 1_000_000, 10, 18, 1, date(2026, 8, 12)),
        ) as calculate:
            await symbol_calculator_command.callback(
                interactions[0], region, 1, 0, 2, None, None
            )
            await symbol_calculator_command.callback(
                interactions[1],
                region,
                1,
                0,
                2,
                SimpleNamespace(name="4레벨", value=4),
                SimpleNamespace(name="적용", value="적용"),
            )
            await symbol_calculator_command.callback(
                interactions[2], region, 1, 0, 2, None, None
            )

        self.assertEqual(
            client.symbol_calculator_preferences,
            {"123": {"potion_level": 4, "elanos": "적용"}},
        )
        client.persist_state.assert_called_once_with()
        self.assertEqual(
            [(call.args[4], call.args[5]) for call in calculate.call_args_list],
            [(0, False), (4, True), (4, True)],
        )
        first_embed = interactions[0].response.send_message.await_args.kwargs["embed"]
        last_embed = interactions[2].response.send_message.await_args.kwargs["embed"]
        self.assertIn("**보약**　없음", first_embed.description)
        self.assertIn("**엘라노스**　미적용", first_embed.description)
        self.assertIn("**보약**　4레벨", last_embed.description)
        self.assertIn("**엘라노스**　적용", last_embed.description)


class NewsPollingTests(unittest.IsolatedAsyncioTestCase):
    async def test_polling_saves_latest_cash_shop_update_without_resending_post(self) -> None:
        post = {
            "id": 42853,
            "category": "sale",
            "name": "Cash Shop Update for August 11",
            "liveDate": "2026-08-11T00:00:00Z",
        }
        bot = SimpleNamespace(
            fetch_posts=AsyncMock(return_value=[post]),
            sent_ids={post["id"]},
            latest_cash_shop=None,
            sunny_sunday={},
            saved_categories=set(maple_bot.WATCHED_CATEGORIES),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_news.coro(bot)

        self.assertEqual(
            bot.latest_cash_shop,
            {
                "post_id": 42853,
                "title": "Cash Shop Update for August 11",
                "url": (
                    "https://www.nexon.com/maplestory/news/sale/42853/"
                    "cash-shop-update-for-august-11"
                ),
            },
        )
        bot.persist_state.assert_called_once_with()


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_guide_is_private_and_lists_only_user_commands(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await help_command.callback(interaction)

        arguments = interaction.response.send_message.await_args
        self.assertEqual(help_command.name, "명령어")
        self.assertTrue(arguments.kwargs["ephemeral"])
        field_text = "\n".join(field.value for field in arguments.kwargs["embed"].fields)
        self.assertIn("/심볼계산기", field_text)
        self.assertIn("/5퍼", field_text)
        self.assertIn("/우르스", field_text)
        self.assertIn("/서버", field_text)
        self.assertIn("/캐샵", field_text)
        self.assertNotIn("/공지알림", field_text)


class ScheduleCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_cash_shop_command_uses_saved_latest_link_and_thumbnail(self) -> None:
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                latest_cash_shop={
                    "post_id": 42853,
                    "title": "Cash Shop Update for August 11",
                    "url": "https://example.com/latest-cash-shop",
                }
            ),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with patch("maple_bot.discord.File", return_value=Mock()) as discord_file:
            await cash_shop_command.callback(interaction)

        arguments = interaction.response.send_message.await_args
        embed = arguments.kwargs["embed"]
        self.assertEqual(embed.title, "[ 캐시샵 업데이트 ]")
        self.assertIn("https://example.com/latest-cash-shop", embed.description)
        self.assertIn("https://masonym.dev/cash-shop", embed.description)
        self.assertEqual(embed.thumbnail.url, "attachment://cash-shop-update.png")
        discord_file.assert_called_once_with(
            maple_bot.CASH_SHOP_UPDATE_IMAGE_PATH,
            filename="cash-shop-update.png",
        )

    async def test_sunny_commands_hide_past_entries_and_use_korean_title(self) -> None:
        schedule = {
            "title": "v.270 Sunny Sunday",
            "url": "https://example.com/patch",
            "entries": [
                {"timestamp": 0, "name": "지난 일정", "value": "지난 보상"},
                {
                    "timestamp": 9_999_999_999,
                    "name": "남은 일정",
                    "value": "남은 보상",
                },
            ],
        }
        list_interaction = SimpleNamespace(
            client=SimpleNamespace(sunny_sunday=schedule),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        current_interaction = SimpleNamespace(
            client=SimpleNamespace(sunny_sunday=schedule),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        with patch("maple_bot.discord.File", return_value=Mock()):
            await sunny_sunday_list_command.callback(list_interaction)
            await sunny_sunday_command.callback(current_interaction)

        list_embed = list_interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual([field.name for field in list_embed.fields], ["남은 일정"])
        current_embed = current_interaction.response.send_message.await_args.kwargs[
            "embed"
        ]
        self.assertEqual(current_embed.title, "☀️ 이번 주 썬데이 메이플 ☀️")

    async def test_placeholder_events_report_no_active_event(self) -> None:
        for command, event_name in (
            (hot_week_command, "핫위크"),
            (cube_sale_command, "큐브세일"),
        ):
            interaction = SimpleNamespace(
                response=SimpleNamespace(send_message=AsyncMock())
            )
            await command.callback(interaction)
            embed = interaction.response.send_message.await_args.kwargs["embed"]
            self.assertEqual(
                embed.description,
                f"현재 진행 중인 {event_name} 이벤트가 없습니다.",
            )


class ChannelRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_uses_display_name_and_channel_between_1_and_40(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(display_name="류*게이"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        # 무작위 결과를 27로 고정해 닉네임과 추천 채널이 메시지에 들어가는지 확인합니다.
        with patch("maple_bot.random.randint", return_value=27) as randint:
            await channel_recommend_command.callback(interaction)

        randint.assert_called_once_with(1, 40)
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("류\\*게이", message)
        self.assertIn("27채널", message)
        self.assertIn("광휘나 칠흑 잘뜨는 채널", message)
        self.assertIn("나 보스 캐리해줘야돼 ㅋㅋ", message)
