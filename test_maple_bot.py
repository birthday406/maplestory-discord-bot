import asyncio
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, patch

import maple_bot
from PIL import Image
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
from familiar_store import FamiliarExpectationStore
from tools.probe_ranking_update import rank_value, select_candidates
from maple_bot import (
    ALERT_CASH_TRANSFER,
    ALERT_CUBE_SALE,
    ALERT_EXCHANGE_LOG,
    ALERT_MIRACLE_TIME,
    ALERT_NEWS,
    ALERT_SERVER,
    ALERT_SUNNY_DAY,
    ALERT_SUNNY_LIST,
    ALERT_URSUS,
    INFO_EXCHANGE,
    INFO_TIME,
    INFO_UTC,
    EPIC_DUNGEON_BONUSES,
    EPIC_DUNGEONS,
    EXP_COUPON_BURNING_OPTIONS,
    EXP_COUPONS,
    GROWTH_POTION_EMOJIS,
    GROWTH_POTIONS,
    HEXA_CORE_COSTS,
    LEVEL_EXP,
    RANKING_SCAN_INTERVAL_SECONDS,
    RANKING_PAGES_PER_BATCH,
    MapleNewsBot,
    RankingRateLimited,
    allocate_ranking_pages,
    ranking_backoff_seconds,
    build_miracle_time_embed,
    build_command_stats_embed,
    build_exchange_rate_log_embed,
    build_server_status_embed,
    build_ursus_embed,
    appearance_search_autocomplete,
    appearance_search_command,
    calculate_epic_dungeon,
    calculate_exp_coupons,
    calculate_growth_potions,
    calculate_hexa_cost,
    cash_shop_command,
    cash_shop_transfer_alert_command,
    cash_shop_transfer_command,
    channel_recommend_command,
    count_eligible_ranking_characters,
    command_stats_command,
    cube_sale_alert_command,
    cube_sale_command,
    current_sunny_sunday_entry,
    current_ursus_window,
    create_ranking_history_image,
    epic_dungeon_command,
    exp_coupon_command,
    exchange_log_alert_command,
    exp_coupon_autocomplete,
    extreme_growth_potion_command,
    extract_cash_shop_transfer,
    extract_cash_shop_sections,
    extract_miracle_time,
    extract_maintenance_watch,
    extract_sunny_sunday,
    fetch_cached_ranking_profile,
    format_exchange_channel_name,
    format_time_channel_name,
    format_utc_channel_name,
    format_sunny_sunday_date,
    format_boss_hp_as_k,
    find_ranking_character,
    growth_potion_command,
    help_command,
    hot_week_command,
    html_to_text,
    info_channel_command,
    utc_channel_command,
    is_cash_shop_update,
    is_patch_notes,
    is_server_maintenance_post,
    item_search_autocomplete,
    item_search_command,
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
    parse_usd_exchange_rate,
    post_url,
    pssb_cash_item,
    pssb_command,
    quick_copy_command,
    quick_copy_symbol_command,
    quick_copy_symbol_prefix_command,
    ranking_command,
    maple_addict_power,
    record_command_usage,
    record_exchange_rate,
    save_state,
    search_cash_items,
    seed_ring_command,
    SeedRingSimulatorView,
    server_status_alert_command,
    server_status_command,
    should_check_server_status,
    should_send_cash_shop_transfer,
    should_send_miracle_time,
    sunny_day_alert_command,
    sunny_list_alert_command,
    sunny_sunday_command,
    sunny_sunday_entry_action,
    sunny_sunday_list_command,
    sunny_sunday_timestamp,
    symbol_calculator_command,
    simulate_seed_ring,
    thumbnail_url,
    traffic_light_command,
    traffic_light_difficulty_autocomplete,
    update_alert_channel,
    utc_event_timestamp,
    ursus_alert_command,
    ursus_boundary_event,
    ursus_command,
    visible_sunny_sunday_entries,
    watched_posts,
)
from ranking_store import RankingStore, ranking_scan_started_at, scan_rankings


class NewsFilteringTests(unittest.TestCase):
    def test_ranking_collection_keeps_one_rps_with_three_in_flight(self) -> None:
        self.assertEqual(RANKING_SCAN_INTERVAL_SECONDS, 1)
        self.assertEqual(RANKING_PAGES_PER_BATCH, 3)

    def test_ranking_pages_are_shared_between_active_worlds(self) -> None:
        allocation, offset = allocate_ranking_pages([19, 1, 45, 70], 0)
        self.assertEqual(allocation, {19: 1, 1: 1, 45: 1})
        self.assertEqual(offset, 3)

        allocation, offset = allocate_ranking_pages([45, 70], offset)
        self.assertEqual(allocation, {70: 2, 45: 1})
        self.assertEqual(offset, 0)

        allocation, offset = allocate_ranking_pages([45], offset)
        self.assertEqual(allocation, {45: 3})
        self.assertEqual(offset, 0)

    def test_ranking_scan_day_uses_utc(self) -> None:
        self.assertEqual(
            maple_bot.current_ranking_scan_date(datetime(2026, 8, 30, 17, 9, tzinfo=timezone.utc)),
            date(2026, 8, 29),
        )
        self.assertEqual(
            maple_bot.current_ranking_scan_date(datetime(2026, 8, 30, 17, 10, tzinfo=timezone.utc)),
            date(2026, 8, 30),
        )
        self.assertEqual(
            maple_bot.current_ranking_scan_date(datetime(2026, 8, 31, 17, 9, tzinfo=timezone.utc)),
            date(2026, 8, 30),
        )
        self.assertEqual(
            maple_bot.current_ranking_scan_date(datetime(2026, 8, 31, 17, 10, tzinfo=timezone.utc)),
            date(2026, 8, 31),
        )

    def test_ranking_collection_status_uses_latest_worker_batches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            processed = inbox / "processed"
            failed = inbox / "failed"
            processed.mkdir()
            failed.mkdir()
            (processed / "2026-09-01-oracle-worker-main-1.jsonl").write_text(
                json.dumps(
                    {
                        "world_id": 45,
                        "page_index": 1201,
                        "ranking_type": "world",
                    }
                ),
                encoding="utf-8",
            )
            (processed / "2026-09-01-oracle-worker-e2-2.jsonl").write_text(
                json.dumps(
                    {
                        "world_id": 45,
                        "page_index": 931,
                        "ranking_type": "achievement",
                    }
                ),
                encoding="utf-8",
            )
            (failed / "2026-09-01-broken-3.jsonl").touch()

            status = maple_bot.ranking_collection_status_text(
                inbox, date(2026, 9, 1)
            )

        self.assertIn("메인: Kronos 1,201위까지", status)
        self.assertIn("보조 E2: 경험치 완료 · Kronos 업적 931위", status)
        self.assertIn("실패 배치: 최근 10분 1개 · 누적 1개", status)

    def test_ranking_rate_limit_returns_without_sleeping_inside_fetch(self) -> None:
        async def run() -> RankingRateLimited:
            response = SimpleNamespace(status=403, headers={})
            context = AsyncMock()
            context.__aenter__.return_value = response
            bot = object.__new__(MapleNewsBot)
            bot.session = SimpleNamespace(get=Mock(return_value=context))
            bot._ranking_request_lock = asyncio.Lock()
            bot._next_ranking_request_at = 0.0

            with self.assertRaises(RankingRateLimited) as caught:
                await bot.fetch_ranking_page(19, 1)
            return caught.exception

        self.assertIsNone(asyncio.run(run()).retry_after)

    def test_slow_ranking_responses_can_overlap(self) -> None:
        async def run() -> None:
            first_started = asyncio.Event()
            second_started = asyncio.Event()
            release_first = asyncio.Event()

            class ResponseContext:
                def __init__(self, first: bool) -> None:
                    self.first = first

                async def __aenter__(self):
                    (first_started if self.first else second_started).set()
                    if self.first:
                        await release_first.wait()
                    return SimpleNamespace(
                        status=200,
                        headers={},
                        raise_for_status=Mock(),
                        json=AsyncMock(return_value={"ranks": []}),
                    )

                async def __aexit__(self, *_args) -> None:
                    return None

            bot = object.__new__(MapleNewsBot)
            bot.session = SimpleNamespace(
                get=Mock(side_effect=[ResponseContext(True), ResponseContext(False)])
            )
            bot._ranking_request_lock = asyncio.Lock()
            bot._next_ranking_request_at = 0.0
            with patch.object(maple_bot, "RANKING_SCAN_INTERVAL_SECONDS", 0):
                first = asyncio.create_task(bot.fetch_ranking_page(19, 1))
                await first_started.wait()
                second = asyncio.create_task(bot.fetch_ranking_page(19, 11))
                await asyncio.wait_for(second_started.wait(), timeout=0.1)
                release_first.set()
                await asyncio.gather(first, second)

        asyncio.run(run())

    def test_ranking_backoff_separates_403_and_429(self) -> None:
        self.assertEqual(ranking_backoff_seconds(403, None, 1), 5 * 60)
        self.assertEqual(ranking_backoff_seconds(403, None, 2), 15 * 60)
        self.assertEqual(ranking_backoff_seconds(403, None, 3), 60 * 60)
        self.assertEqual(ranking_backoff_seconds(403, None, 4), 6 * 60 * 60)
        self.assertEqual(ranking_backoff_seconds(403, None, 99), 6 * 60 * 60)
        self.assertEqual(ranking_backoff_seconds(429, None, 1), 60)
        self.assertEqual(ranking_backoff_seconds(429, 600, 1), 600)

    def test_ranking_collection_skips_api_during_backoff(self) -> None:
        async def run() -> None:
            bot = object.__new__(MapleNewsBot)
            bot._ranking_retry_until = int(datetime.now(timezone.utc).timestamp()) + 60

            await MapleNewsBot.collect_rankings.coro(bot)

        asyncio.run(run())

    def test_ranking_collection_yields_to_interactive_lookup(self) -> None:
        async def run() -> None:
            bot = object.__new__(MapleNewsBot)
            bot._ranking_interactive_requests = 1

            await MapleNewsBot.collect_rankings.coro(bot)

        asyncio.run(run())

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
                ALERT_EXCHANGE_LOG: set(),
                INFO_TIME: set(),
                INFO_UTC: set(),
                INFO_EXCHANGE: set(),
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
                ALERT_EXCHANGE_LOG: set(),
                INFO_TIME: set(),
                INFO_UTC: set(),
                INFO_EXCHANGE: set(),
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
            exchange_log_alert_command,
        ):
            self.assertTrue(command.guild_only)
            self.assertTrue(command.default_permissions.administrator)
            self.assertEqual(
                {choice.value for choice in command.parameters[1].choices},
                {"on", "off"},
            )

        self.assertTrue(info_channel_command.guild_only)
        self.assertTrue(info_channel_command.default_permissions.administrator)
        self.assertEqual(
            {choice.value for choice in info_channel_command.parameters[0].choices},
            {INFO_TIME, INFO_EXCHANGE},
        )
        self.assertEqual(
            {choice.value for choice in info_channel_command.parameters[2].choices},
            {"on", "off"},
        )
        self.assertTrue(utc_channel_command.guild_only)
        self.assertTrue(utc_channel_command.default_permissions.administrator)
        self.assertEqual(
            {choice.value for choice in utc_channel_command.parameters[1].choices},
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

    def test_server_status_treats_empty_world_list_as_maintenance(self) -> None:
        self.assertEqual(
            parse_server_status({"servers": []}),
            {"Scania": False, "Bera": False, "Kronos": False, "Hyperion": False},
        )

    def test_info_channel_names_use_korea_and_utc_time_and_naver_usd_rate(self) -> None:
        self.assertEqual(
            format_time_channel_name(
                datetime(2026, 8, 13, 23, 2, tzinfo=timezone.utc)
            ),
            "08월 14일 08시 00분",
        )
        self.assertEqual(
            format_time_channel_name(
                datetime(2026, 8, 13, 23, 7, tzinfo=timezone.utc)
            ),
            "08월 14일 08시 05분",
        )
        self.assertEqual(
            format_utc_channel_name(
                datetime(2026, 8, 13, 23, 7, tzinfo=timezone.utc)
            ),
            "UTC: 23:05",
        )
        source = """
        <table class="tbl_exchange"><tbody>
          <tr><td class="tit"><a>일본 JPY</a></td><td class="sale">9.50</td></tr>
          <tr><td class="tit"><a>미국 USD</a></td><td class="sale">1,423.80</td></tr>
        </tbody></table>
        """
        rate = parse_usd_exchange_rate(source)
        self.assertEqual(rate, Decimal("1423.80"))
        self.assertEqual(format_exchange_channel_name(rate), "USD-1,423.80")

    def test_time_channel_rename_interval_avoids_discord_rate_limit(self) -> None:
        self.assertEqual(maple_bot.MapleNewsBot.update_time_channels.minutes, 10.0)

    def test_server_time_embed_uses_automatic_dst_for_each_region(self) -> None:
        embed = maple_bot.build_server_time_embed(
            datetime(2026, 9, 3, 6, 32, tzinfo=timezone.utc)
        )

        self.assertEqual(embed.title, "· 서버시간")
        self.assertIn("9월 3일 AM 06:32", embed.description)
        self.assertEqual(
            [field.name for field in embed.fields],
            [
                "PDT [서부시간]",
                "CDT [중부시간]",
                "EDT [동부시간]",
                "CEST [유럽시간]",
                "KST [한국시간]",
                "AEST [호주시간]",
            ],
        )
        self.assertIn("9월 2일 PM 11:32", embed.fields[0].value)
        self.assertIn("9월 3일 PM 03:32", embed.fields[4].value)

    def test_missing_naver_usd_rate_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_usd_exchange_rate("<table><tr><td>일본 JPY</td></tr></table>")

    def test_exchange_log_keeps_only_five_latest_changes(self) -> None:
        exchange_log = None
        rates = ["1415.80", "1415.50", "1415.00", "1418.20", "1423.80", "1420.00"]
        for index, rate in enumerate(rates):
            exchange_log, changed = record_exchange_rate(
                exchange_log,
                Decimal(rate),
                datetime(2026, 8, 14, index, 0, tzinfo=timezone.utc),
            )
            self.assertTrue(changed)

        self.assertEqual(len(exchange_log["entries"]), 5)
        self.assertEqual(exchange_log["entries"][0]["rate"], "1415.50")
        self.assertEqual(exchange_log["entries"][-1]["change"], "-3.80")
        embed = build_exchange_rate_log_embed(exchange_log)
        self.assertIn("🔵 ▼ 3.80원", embed.description)
        self.assertIn("🔴 ▲ 5.60원", embed.description)

        same_log, changed = record_exchange_rate(
            exchange_log,
            Decimal("1420.00"),
            datetime(2026, 8, 14, 7, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(changed)
        self.assertIs(same_log, exchange_log)

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

    def test_scheduled_maintenance_starts_checking_one_hour_before_end(self) -> None:
        post = {
            "id": 43961,
            "name": "Scheduled Minor Patch Maintenance - August 13, 2026",
            "category": "maintenance",
            "isMSCW": False,
            "liveDate": "2026-08-11T12:00:00Z",
        }
        body = (
            "<p>Times:</p><p>Thursday, August 13, 2026<br>"
            "PDT (UTC -7): 6:00 AM - 12:00 PM</p>"
        )

        watch = extract_maintenance_watch(post, body)

        self.assertTrue(is_server_maintenance_post(post))
        self.assertIsNotNone(watch)
        self.assertEqual(watch["end_timestamp"] - watch["monitor_from_timestamp"], 3600)
        self.assertFalse(
            should_check_server_status(watch, watch["monitor_from_timestamp"] - 1)
        )
        self.assertTrue(
            should_check_server_status(watch, watch["monitor_from_timestamp"])
        )

    def test_emergency_maintenance_without_end_starts_at_publish_time(self) -> None:
        post = {
            "id": 42252,
            "name": "Emergency Maintenance - June 20, 2026",
            "category": "maintenance",
            "isMSCW": False,
            "liveDate": "2026-06-20T18:30:00Z",
        }
        body = "<p>We are experiencing an unexpected issue and will provide updates.</p>"

        watch = extract_maintenance_watch(post, body)

        self.assertIsNotNone(watch)
        self.assertIsNone(watch["end_timestamp"])
        self.assertEqual(watch["monitor_from_timestamp"], 1781980200)

    def test_completed_and_classic_world_maintenance_are_not_watched(self) -> None:
        completed = {
            "id": 1,
            "name": "[Completed] Unscheduled Maintenance",
            "category": "maintenance",
            "isMSCW": False,
            "liveDate": "2026-08-10T14:00:00Z",
        }
        classic = {**completed, "id": 2, "name": "Unscheduled Maintenance", "isMSCW": True}

        self.assertIsNone(extract_maintenance_watch(completed, "<p>maintenance</p>"))
        self.assertFalse(is_server_maintenance_post(classic))

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
                exchange_log,
                server_alert_roles,
                maintenance_watch,
                command_stats,
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
        self.assertIsNone(exchange_log)
        self.assertEqual(server_alert_roles, {})
        self.assertIsNone(maintenance_watch)
        self.assertEqual(command_stats, {"total": 0, "commands": {}, "users": {}})

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
            ALERT_EXCHANGE_LOG: set(),
            INFO_TIME: set(),
            INFO_UTC: set(),
            INFO_EXCHANGE: set(),
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
        exchange_log = {
            "date": "2026-08-14",
            "opening_rate": "1415.80",
            "current_rate": "1423.80",
            "entries": [],
            "message_ids": {"111": 444},
        }
        server_alert_roles = {"111": 555}
        maintenance_watch = {
            "post_id": 43961,
            "monitor_from_timestamp": 1_786_639_600,
            "saw_down": True,
            "completed": False,
        }
        command_stats = {
            "total": 3,
            "commands": {"ㅁ": 2, "캐샵": 1},
            "users": {"123": {"name": "테스터", "count": 3}},
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
                exchange_log,
                server_alert_roles,
                maintenance_watch,
                command_stats,
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
                loaded_exchange_log,
                loaded_server_alert_roles,
                loaded_maintenance_watch,
                loaded_command_stats,
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
        self.assertEqual(loaded_exchange_log, exchange_log)
        self.assertEqual(loaded_server_alert_roles, server_alert_roles)
        self.assertEqual(loaded_maintenance_watch, maintenance_watch)
        self.assertEqual(loaded_command_stats, command_stats)

    def test_ursus_schedule_matches_fixed_utc_windows(self) -> None:
        summer_start = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
        summer_window = current_ursus_window(summer_start)
        self.assertIsNotNone(summer_window)
        self.assertEqual((summer_window[0].hour, summer_window[1].hour), (11, 15))
        self.assertEqual(ursus_boundary_event(summer_start)[0], "start")
        self.assertEqual(
            ursus_boundary_event(datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc))[0],
            "end",
        )
        self.assertIsNone(
            current_ursus_window(datetime(2026, 8, 11, 22, 0, tzinfo=timezone.utc))
        )
        self.assertIsNone(
            ursus_boundary_event(datetime(2026, 8, 11, 18, 1, tzinfo=timezone.utc))
        )

        summer_evening = current_ursus_window(
            datetime(2026, 8, 12, 1, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            (
                summer_evening[0].astimezone(timezone.utc).hour,
                summer_evening[1].astimezone(timezone.utc).hour,
            ),
            (1, 5),
        )

        winter_start = datetime(2026, 1, 15, 18, 0, tzinfo=timezone.utc)
        winter_window = current_ursus_window(winter_start)
        self.assertIsNotNone(winter_window)
        self.assertEqual((winter_window[0].hour, winter_window[1].hour), (10, 14))

    def test_ursus_embed_uses_discord_timestamps_and_state_image(self) -> None:
        now = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
        window = current_ursus_window(now)
        embed, image_path = build_ursus_embed("active", window, now)

        self.assertEqual(image_path, maple_bot.URSUS_ACTIVE_IMAGE_PATH)
        self.assertIn("진행 중입니다", embed.description)
        self.assertIn(f"<t:{int(window[0].timestamp())}:T>", embed.description)
        self.assertEqual(embed.image.url, "attachment://ursus-golden-time.jpg")
        self.assertIsNone(embed.footer.text)

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

    def test_patch_display_title_removes_update_date_and_suffix(self) -> None:
        self.assertEqual(
            maple_bot.patch_display_title(
                {
                    "name": (
                        "[Updated 7/22] v.270 - Ride the Lightning Patch Notes"
                    )
                }
            ),
            "v.270 - Ride the Lightning",
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


class RankingCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def character(**changes) -> dict:
        character = {
            "characterName": "Home",
            "characterImgURL": "https://example.com/home.png",
            "exp": LEVEL_EXP[95] // 2,
            "jobName": "Dawn Warrior",
            "level": 295,
            "rank": 8926,
            "worldID": 45,
        }
        character.update(changes)
        return character

    def test_exact_character_is_selected_from_official_response(self) -> None:
        payload = {
            "totalCount": 1_000_000,
            "ranks": [self.character(characterName="Other"), self.character()],
        }

        self.assertEqual(find_ranking_character(payload, "home")["rank"], 8926)
        self.assertEqual(find_ranking_character(payload, "home")["totalCount"], 1_000_000)
        self.assertIsNone(find_ranking_character(payload, "Missing"))

    async def test_world_total_count_uses_unfiltered_ranking_page(self) -> None:
        bot = object.__new__(MapleNewsBot)
        bot.fetch_ranking_payload = AsyncMock(return_value={"totalCount": 654_321})

        result = await bot.fetch_ranking_total_count("na", "world", 45)

        self.assertEqual(result, 654_321)
        bot.fetch_ranking_payload.assert_awaited_once_with(
            "na",
            {
                "type": "world",
                "id": "45",
                "reboot_index": "0",
                "page_index": "1",
            },
        )

    async def test_profile_cache_reuses_recent_official_responses(self) -> None:
        character = self.character()
        client = SimpleNamespace(
            _ranking_profile_cache={},
            fetch_ranking_character=AsyncMock(
                side_effect=[
                    character,
                    self.character(rank=1309),
                ]
            ),
            fetch_ranking_total_count=AsyncMock(return_value=1_000_000),
            fetch_character_image=AsyncMock(return_value=b"image"),
        )

        first = await fetch_cached_ranking_profile(client, "Home")
        second = await fetch_cached_ranking_profile(client, "home")

        self.assertIs(first, second)
        self.assertEqual(client.fetch_ranking_character.await_count, 2)
        client.fetch_ranking_total_count.assert_awaited_once()
        client.fetch_character_image.assert_awaited_once()
        self.assertIsNone(first[3])
        self.assertIsNone(first[4])
        self.assertEqual(client._ranking_profile_refresh_requests, {"home"})

    async def test_saved_profile_skips_official_ranking_requests(self) -> None:
        character = self.character()
        store = SimpleNamespace(
            get_ranking_profile=Mock(
                return_value=(
                    character,
                    self.character(rank=1309),
                    1_000_000,
                    self.character(rank=2923, legionLevel=10221),
                    self.character(rank=810, score=33370),
                )
            ),
            queue_priority_refresh=Mock(),
        )
        client = SimpleNamespace(
            ranking_store=store,
            _ranking_profile_cache={},
            fetch_ranking_character=AsyncMock(),
            fetch_character_image=AsyncMock(return_value=b"image"),
        )

        profile = await fetch_cached_ranking_profile(client, "Home")

        self.assertEqual(profile[0]["characterName"], "Home")
        client.fetch_ranking_character.assert_not_awaited()
        store.queue_priority_refresh.assert_not_called()
        self.assertEqual(client._ranking_profile_refresh_requests, {"home"})

    async def test_saved_representative_data_replaces_incomplete_cache(self) -> None:
        character = self.character()
        saved = (
            character,
            self.character(rank=1309),
            1_000_000,
            self.character(rank=2923, legionLevel=10221),
            self.character(rank=810, score=33370),
        )
        now = asyncio.get_running_loop().time()
        client = SimpleNamespace(
            ranking_store=SimpleNamespace(get_ranking_profile=Mock(return_value=saved)),
            _ranking_profile_cache={
                "home": (
                    now,
                    (
                        character,
                        self.character(rank=1309),
                        1_000_000,
                        None,
                        None,
                        b"old",
                    ),
                )
            },
            fetch_ranking_character=AsyncMock(),
            fetch_character_image=AsyncMock(return_value=b"new"),
        )

        profile = await fetch_cached_ranking_profile(client, "Home")

        self.assertEqual(profile[3]["legionLevel"], 10221)
        self.assertEqual(profile[4]["score"], 33370)
        self.assertEqual(profile[5], b"new")
        client.fetch_ranking_character.assert_not_awaited()

    async def test_command_loads_rankings_and_unfiltered_world_total(self) -> None:
        character = self.character()
        world_character = self.character(rank=1309)
        client = SimpleNamespace(
            fetch_ranking_character=AsyncMock(
                side_effect=[character, world_character]
            ),
            fetch_ranking_total_count=AsyncMock(return_value=1_000_000),
            ranking_store=SimpleNamespace(
                save_snapshot=Mock(return_value=[]),
                save_default_character=Mock(),
                queue_priority_refresh=Mock(),
                get_nickname_trace=Mock(
                    return_value=[
                        {"old_name": "OldHome", "new_name": "Home"}
                    ]
                ),
            ),
        )
        interaction = SimpleNamespace(
            client=client,
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        with patch(
            "maple_bot.create_ranking_history_image",
            return_value=io.BytesIO(b"image"),
        ) as create_image:
            await ranking_command.callback(interaction, " Home ")

        interaction.response.defer.assert_awaited_once_with()
        self.assertEqual(
            [call.args for call in client.fetch_ranking_character.await_args_list],
            [
                ("na", "overall", "legendary", "Home"),
                ("na", "world", 45, "Home"),
            ],
        )
        client.fetch_ranking_total_count.assert_awaited_once_with("na", "world", 45)
        sent = interaction.followup.send.await_args.kwargs
        self.assertNotIn("embed", sent)
        self.assertEqual(sent["file"].filename, "ranking-card.png")
        self.assertNotIn(
            "achievementScore", client.ranking_store.save_snapshot.call_args.args[0]
        )
        client.ranking_store.save_snapshot.assert_called_once()
        client.ranking_store.save_default_character.assert_called_once_with(123, "Home")
        client.ranking_store.queue_priority_refresh.assert_called_once_with("Home")
        self.assertEqual(create_image.call_args.kwargs["previous_name"], "OldHome")

    async def test_command_reports_no_data_below_level_260(self) -> None:
        client = SimpleNamespace(
            fetch_ranking_character=AsyncMock(return_value=self.character(level=259)),
            ranking_store=SimpleNamespace(
                save_snapshot=Mock(),
                save_default_character=Mock(),
            ),
        )
        interaction = SimpleNamespace(
            client=client,
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await ranking_command.callback(interaction, "Home")

        self.assertEqual(
            interaction.followup.send.await_args.args[0],
            "**Home** 캐릭터의 기록 데이터가 없습니다.",
        )
        self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
        client.fetch_ranking_character.assert_awaited_once_with(
            "na", "overall", "legendary", "Home"
        )
        client.ranking_store.save_default_character.assert_not_called()
        client.ranking_store.save_snapshot.assert_not_called()

    async def test_command_uses_saved_character_when_nickname_is_empty(self) -> None:
        character = self.character()
        client = SimpleNamespace(
            fetch_ranking_character=AsyncMock(
                side_effect=[
                    character,
                    self.character(rank=1309),
                ]
            ),
            fetch_ranking_total_count=AsyncMock(return_value=1_000_000),
            ranking_store=SimpleNamespace(
                get_default_character=Mock(return_value="Home"),
                save_default_character=Mock(),
                save_snapshot=Mock(return_value=[]),
                queue_priority_refresh=Mock(),
            ),
        )
        interaction = SimpleNamespace(
            client=client,
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await ranking_command.callback(interaction)

        client.ranking_store.get_default_character.assert_called_once_with(123)
        self.assertEqual(client.fetch_ranking_character.await_count, 2)

    async def test_command_without_saved_character_explains_first_lookup(self) -> None:
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                ranking_store=SimpleNamespace(get_default_character=Mock(return_value=None))
            ),
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        )

        await ranking_command.callback(interaction)

        self.assertIn("처음에는", interaction.response.send_message.await_args.args[0])

    def test_snapshot_calculates_gain_across_a_level_up(self) -> None:
        first = self.character(level=295, exp=LEVEL_EXP[95] - 100)
        second = self.character(level=296, exp=50)

        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.assertEqual(store.save_snapshot(first, date(2026, 8, 15)), [])
            gains = store.save_snapshot(second, date(2026, 8, 16))

        self.assertEqual(gains, [{"date": "2026-08-16", "exp": 150}])

    def test_snapshot_repairs_a_future_dated_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(self.character(exp=100), date(2026, 8, 29))
            store.save_snapshot(self.character(exp=100), date(2026, 8, 30))
            store.save_snapshot(self.character(exp=200), date(2026, 8, 31))
            gains = store.save_snapshot(self.character(exp=200), date(2026, 8, 30))

        self.assertEqual(gains, [{"date": "2026-08-30", "exp": 100}])

    def test_snapshot_keeps_representative_legion_and_achievement(self) -> None:
        representative = self.character(
            legionLevel=10_221,
            legionRank=2_923,
            achievementScore=33_370,
            achievementRank=810,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            scan_date = date(2026, 8, 30)
            store.save_snapshot(representative, scan_date)
            # 같은 날 일반 랭킹만 다시 수집해도 이미 확인한 대표 정보는 유지합니다.
            store.save_snapshot(self.character(), scan_date)
            with store._connect() as connection:
                row = connection.execute(
                    """SELECT legion_level, legion_rank,
                              achievement_score, achievement_rank
                         FROM ranking_snapshots
                        WHERE name_key = ? AND snapshot_date = ?""",
                    ("home", scan_date.isoformat()),
                ).fetchone()

        self.assertEqual(tuple(row), (10_221, 2_923, 33_370, 810))

    def test_nickname_change_detector_links_a_strong_match(self) -> None:
        old = self.character(characterName="OldName", rank=1_000, exp=123_456)
        new = self.character(characterName="NewName", rank=1_020, exp=123_456)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(old, date(2026, 8, 30))
            store.save_snapshot(new, date(2026, 8, 31))
            preview = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
                save=False,
            )
            self.assertEqual(store.get_nickname_trace("newname"), [])
            result = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )
            repeated = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )
            trace = store.get_nickname_trace("newname")

        self.assertEqual(preview["saved"], 1)
        self.assertEqual(preview["preview"][0]["old_name"], "OldName")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(repeated["reason"], "already_processed")
        self.assertEqual(
            [(item["old_name"], item["new_name"]) for item in trace],
            [("OldName", "NewName")],
        )

    def test_nickname_change_detector_rejects_incomplete_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(self.character(), date(2026, 8, 30))
            result = store.detect_nickname_changes(
                date(2026, 8, 30), date(2026, 8, 31)
            )

        self.assertEqual(result["reason"], "incomplete_snapshot")

    def test_nickname_change_detector_rejects_zero_exp_boundary_match(self) -> None:
        old = self.character(characterName="OldName", rank=535_404, exp=0)
        new = self.character(characterName="NewName", rank=535_433, exp=0)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(old, date(2026, 8, 30))
            store.save_snapshot(new, date(2026, 8, 31))
            result = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )

        self.assertEqual(result["saved"], 0)

    def test_nickname_change_detector_observes_level_300_with_account_signals(self) -> None:
        old = self.character(
            characterName="OldName",
            level=300,
            rank=15,
            exp=0,
            legionLevel=10_500,
            achievementScore=33_000,
        )
        new = self.character(
            characterName="NewName",
            level=300,
            rank=18,
            exp=0,
            legionLevel=10_500,
            achievementScore=33_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(old, date(2026, 8, 30))
            store.save_snapshot(new, date(2026, 8, 31))
            result = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )

            with store._connect() as connection:
                observation = connection.execute(
                    "SELECT old_rank, new_rank, rank_gap FROM nickname_change_observations"
                ).fetchone()

        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["observed"], 1)
        self.assertEqual(tuple(observation), (15, 18, 3))

    def test_nickname_change_detector_rejects_mismatched_level_300_account_values(self) -> None:
        old = self.character(
            characterName="OldName",
            level=300,
            rank=15,
            exp=0,
            legionLevel=10_500,
            achievementScore=33_000,
        )
        new = self.character(
            characterName="NewName",
            level=300,
            rank=15,
            exp=0,
            legionLevel=10_499,
            achievementScore=33_000,
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(old, date(2026, 8, 30))
            store.save_snapshot(new, date(2026, 8, 31))
            result = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )

        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["observed"], 0)

    def test_nickname_change_detector_rejects_weak_rank_match(self) -> None:
        old = self.character(characterName="OldName", rank=10_000, exp=123_456)
        new = self.character(characterName="NewName", rank=11_000, exp=123_456)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(old, date(2026, 8, 30))
            store.save_snapshot(new, date(2026, 8, 31))
            result = store.detect_nickname_changes(
                date(2026, 8, 30),
                date(2026, 8, 31),
                min_snapshot_count=1,
            )

        self.assertEqual(result["saved"], 0)

    def test_default_character_is_saved_per_discord_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_default_character(1, "Akanelize")
            store.save_default_character(2, "Home")

            self.assertEqual(store.get_default_character(1), "Akanelize")
            self.assertEqual(store.get_default_character(2), "Home")
            self.assertIsNone(store.get_default_character(3))

    def test_full_ranking_profile_survives_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.db"
            store = RankingStore(path)
            profile = (
                self.character(),
                self.character(rank=1309),
                1_000_000,
                self.character(rank=2923, legionLevel=10221),
                self.character(rank=810, score=33370),
                b"not stored",
            )
            store.save_ranking_profile("Home", profile)

            restored = RankingStore(path).get_ranking_profile("home")

        self.assertEqual(restored, profile[:5])

    def test_all_first_place_rankings_have_max_maple_addict_power(self) -> None:
        score, tags = maple_addict_power(
            {
                "level": 250,
                "exp": 0,
                "ranking": 1,
                "legion_level": 1,
                "legion_rank": 1,
                "achievement_score": 1,
                "achievement_rank": 1,
            }
        )

        self.assertEqual(score, 99.9)
        self.assertEqual(tags, ["메이플 마스터", "레벨 장인"])

    def test_complete_population_data_uses_new_ai_score(self) -> None:
        score, tags = maple_addict_power(
            {
                "level": 300,
                "exp": 0,
                "ranking": 1,
                "level_population": 786_171,
                "legion_level": 12_000,
                "legion_rank": 1,
                "legion_population": 245_359,
                "achievement_score": 40_000,
                "achievement_rank": 1,
                "achievement_population": 1_768_531,
            }
        )

        self.assertEqual(score, 99.9)
        self.assertEqual(tags, ["메이플 마스터", "균형의 달인", "레벨 장인"])

    def test_character_without_legion_and_achievement_is_tagged_as_alt(self) -> None:
        _, tags = maple_addict_power(
            {
                "level": 295,
                "exp": 0,
                "ranking": 8_926,
            }
        )

        self.assertEqual(tags[-1], "부캐")

    def test_character_with_representative_ranking_is_not_tagged_as_alt(self) -> None:
        _, tags = maple_addict_power(
            {
                "level": 295,
                "exp": 0,
                "ranking": 8_926,
                "legion_level": 10_221,
                "legion_rank": 2_923,
            }
        )

        self.assertNotIn("부캐", tags)

    def test_population_snapshots_are_saved_by_date_and_world(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            first_day = date(2026, 8, 30)
            second_day = date(2026, 8, 31)
            store.save_population(first_day, "level_260_plus", 786_171)
            store.save_population(first_day, "legion", 245_359, 45)
            store.save_population(first_day, "achievement", 1_768_531, 45)
            store.save_population(second_day, "level_260_plus", 786_500)

            self.assertEqual(
                store.get_population("level_260_plus", scan_date=first_day), 786_171
            )
            self.assertEqual(store.get_population("level_260_plus"), 786_500)
            self.assertEqual(
                store.get_ai_score_populations(45),
                {
                    "level_population": 786_500,
                    "legion_population": 245_359,
                    "achievement_population": 1_768_531,
                },
            )

    def test_history_graph_is_rendered_as_png(self) -> None:
        result = create_ranking_history_image(
            self.character(),
            [
                {"date": "2026-08-15", "exp": 1_200_000_000_000},
                {"date": "2026-08-16", "exp": 2_500_000_000_000},
            ],
            world_rank=1309,
            legion={"legionLevel": 10221, "rank": 2923},
            achievement={"score": 33370, "rank": 810},
            world_total_count=1_000_000,
            updated_date=date(2026, 8, 30),
        )

        with Image.open(result) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1800, 1528))
            self.assertEqual(image.getpixel((0, 0)), (32, 40, 48))

    def test_next_level_estimate_uses_current_level_and_seven_day_average(self) -> None:
        remaining = LEVEL_EXP[95] - 100

        self.assertEqual(
            maple_bot.estimate_next_level(295, 100, 50),
            (remaining, remaining / 50),
        )
        self.assertIsNone(maple_bot.estimate_next_level(300, 0, 50))
        self.assertIsNone(maple_bot.estimate_next_level(295, 100, 0))

    def test_history_graph_ignores_faint_character_image_padding(self) -> None:
        source = Image.new("RGBA", (100, 100), (255, 255, 255, 1))
        source.paste((255, 0, 0, 255), (40, 30, 60, 70))
        character_image = io.BytesIO()
        source.save(character_image, format="PNG")

        result = create_ranking_history_image(
            self.character(),
            [],
            character_image=character_image.getvalue(),
        )

        with Image.open(result) as image:
            red_pixels = [
                (x, y)
                for y in range(84, 354)
                for x in range(84, 334)
                if image.getpixel((x, y))[0] > 180
                and image.getpixel((x, y))[1] < 100
                and image.getpixel((x, y))[2] < 100
            ]
        self.assertGreater(max(y for _, y in red_pixels) - min(y for _, y in red_pixels), 200)

    def test_exp_summary_uses_requested_recent_period(self) -> None:
        gains = [{"exp": value} for value in range(1, 31)]

        self.assertEqual(maple_bot.summarize_exp_gains(gains, 7), (27, 189))
        self.assertEqual(maple_bot.summarize_exp_gains(gains, 30), (16, 465))

    def test_history_graph_axis_uses_readable_t_steps(self) -> None:
        self.assertEqual(
            maple_bot.ranking_axis_scale(15_600_000_000_000),
            (2_000_000_000_000, 16_000_000_000_000),
        )
        self.assertEqual(
            maple_bot.ranking_axis_scale(86_360_000_000_000),
            (10_000_000_000_000, 90_000_000_000_000),
        )

    async def test_missing_character_returns_short_private_message(self) -> None:
        interaction = SimpleNamespace(
            client=SimpleNamespace(fetch_ranking_character=AsyncMock(return_value=None)),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await ranking_command.callback(interaction, "Missing")

        message = interaction.followup.send.await_args
        self.assertIn("찾지 못했습니다", message.args[0])
        self.assertTrue(message.kwargs["ephemeral"])


class RankingCollectionTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def ranks(start: int, count: int = 10, level: int = 295) -> list[dict]:
        return [
            {
                "characterName": f"Rank{rank}",
                "characterImgURL": f"https://example.com/{rank}.png",
                "exp": rank,
                "jobName": "Hero",
                "level": level,
                "rank": rank,
                "worldID": 45,
            }
            for rank in range(start, start + count)
        ]

    @staticmethod
    def save_rank_history(
        store: RankingStore,
        character: dict,
        start: date,
        days: int,
        page_index: int,
        changed_on: int | None = None,
    ) -> None:
        for offset in range(days):
            saved = dict(character)
            saved["exp"] = 200 if changed_on is not None and offset >= changed_on else 100
            store.save_page(
                [saved],
                start + timedelta(days=offset),
                next_index=1,
                update_checkpoint=False,
                source_page_index=page_index,
            )

    async def test_level_population_counts_tied_boundary_rows(self) -> None:
        rows = [
            {"level": 260, "rank": rank if rank <= 10 else 11}
            for rank in range(1, 18)
        ] + [
            {"level": 259, "rank": rank}
            for rank in range(18, 31)
        ]

        async def fetch_page(start_index: int) -> dict:
            return {"ranks": rows[start_index - 1 : start_index + 9]}

        self.assertEqual(
            await count_eligible_ranking_characters(fetch_page, len(rows)),
            17,
        )

    async def test_daily_population_refresh_saves_exact_level_count(self) -> None:
        rows = [
            {"level": 260, "rank": rank if rank <= 10 else 11}
            for rank in range(1, 18)
        ] + [
            {"level": 259, "rank": rank}
            for rank in range(18, 31)
        ]

        async def fetch_payload(region, params, target):
            start_index = int(params["page_index"])
            return {
                "totalCount": len(rows),
                "ranks": rows[start_index - 1 : start_index + 9],
            }

        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            bot = object.__new__(MapleNewsBot)
            bot.ranking_store = store
            bot.fetch_ranking_payload = AsyncMock(side_effect=fetch_payload)
            scan_date = date(2026, 8, 31)

            self.assertTrue(await bot.refresh_next_ranking_population(scan_date))
            self.assertEqual(
                store.get_population("level_260_plus", scan_date=scan_date), 17
            )

    async def test_trial_scans_from_top_and_stops_at_limit(self) -> None:
        fetch_page = AsyncMock(
            side_effect=[{"ranks": self.ranks(1)}, {"ranks": self.ranks(11)}]
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")

            result = await scan_rankings(
                fetch_page,
                store,
                date(2026, 8, 16),
                max_characters=15,
                delay_seconds=0,
            )

            self.assertEqual(store.character_count(), 15)
        self.assertEqual(result["reason"], "limit")
        self.assertEqual([call.args[0] for call in fetch_page.await_args_list], [1, 11])

    async def test_import_only_bot_refreshes_searched_character(self) -> None:
        today = maple_bot.current_ranking_scan_date()
        yesterday = date.fromordinal(today.toordinal() - 1)
        character = self.ranks(1, count=1)[0]
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_snapshot(character, yesterday)
            bot = object.__new__(MapleNewsBot)
            bot.ranking_store = store
            bot._ranking_import_only = True
            bot._ranking_retry_until = 0
            bot._ranking_limit_failures = 0
            bot._ranking_scan_date = today
            bot._ranking_world_offset = 0
            bot._completed_ranking_world_ids = set()
            bot._ranking_profile_cache = {}
            bot.fetch_ranking_character = AsyncMock(
                side_effect=[
                    character,
                    self.ranks(1, count=1)[0],
                    {**character, "legionLevel": 10_000},
                    {**character, "starSum": 30_000},
                ]
            )
            bot.fetch_ranking_total_count = AsyncMock(return_value=1_000_000)
            bot.fetch_character_image = AsyncMock(return_value=b"image")
            bot.fetch_ranking_page = AsyncMock()

            await MapleNewsBot.collect_rankings.coro(bot)

            self.assertIsNone(store.next_priority_character(today))
        self.assertEqual(bot.fetch_ranking_character.await_count, 4)
        bot.fetch_ranking_total_count.assert_awaited_once_with(
            "na", "world", character["worldID"], character["characterName"]
        )
        bot.fetch_ranking_page.assert_not_awaited()

    def test_active_pages_are_prepared_before_seven_day_unchanged_pages(self) -> None:
        today = date(2026, 8, 10)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.save_rank_history(store, self.ranks(1, 1)[0], date(2026, 8, 1), 9, 1)
            self.save_rank_history(
                store,
                self.ranks(11, 1)[0],
                date(2026, 8, 1),
                9,
                11,
                changed_on=7,
            )
            self.save_rank_history(store, self.ranks(21, 1)[0], date(2026, 8, 9), 1, 21)

            store.prepare_active_pages(today)

            self.assertEqual(store.next_active_page(today), (45, 11))
            store.mark_active_page_refreshed(today, 45, 11)
            self.assertEqual(store.next_active_page(today), (45, 21))
            store.mark_active_page_refreshed(today, 45, 21)
            self.assertIsNone(store.next_active_page(today))

    async def test_active_page_is_collected_before_sequential_pages(self) -> None:
        today = datetime.now(maple_bot.URSUS_TIMEZONE).date()
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.save_rank_history(
                store,
                self.ranks(11, 1)[0],
                today - timedelta(days=8),
                9,
                11,
                changed_on=7,
            )
            bot = object.__new__(MapleNewsBot)
            bot.ranking_store = store
            bot._ranking_retry_until = 0
            bot._ranking_limit_failures = 0
            bot._ranking_scan_date = today
            bot._ranking_world_offset = 0
            bot._completed_ranking_world_ids = set()
            bot.fetch_ranking_character = AsyncMock()
            bot.fetch_ranking_page = AsyncMock(return_value={"ranks": self.ranks(11)})
            bot.refresh_next_ranking_population = AsyncMock(return_value=False)

            await MapleNewsBot.collect_rankings.coro(bot)

            self.assertIsNone(store.next_active_page(today))
            self.assertEqual(store.start_scan(today, world_id=45), 1)
        bot.fetch_ranking_character.assert_not_awaited()
        bot.fetch_ranking_page.assert_awaited_once_with(45, 11)

    async def test_sequential_scan_skips_an_active_page_already_refreshed(self) -> None:
        today = date(2026, 8, 10)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            self.save_rank_history(
                store,
                self.ranks(1, 1)[0],
                date(2026, 8, 1),
                9,
                1,
                changed_on=7,
            )
            store.prepare_active_pages(today)
            store.mark_active_page_refreshed(today, 45, 1)
            fetch_page = AsyncMock(return_value={"ranks": self.ranks(11)})

            result = await scan_rankings(
                fetch_page,
                store,
                today,
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
            )

            self.assertEqual(result["next_index"], 21)
        fetch_page.assert_awaited_once_with(11)

    def test_command_refresh_keeps_the_last_known_page(self) -> None:
        character = self.ranks(31, 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.save_page(
                [character],
                date(2026, 8, 9),
                next_index=41,
                update_checkpoint=False,
                source_page_index=31,
            )
            store.save_snapshot(character, date(2026, 8, 10))

            connection = store._connect()
            try:
                page_index = connection.execute(
                    "SELECT scan_page_index FROM characters WHERE name_key = ?",
                    (character["characterName"].casefold(),),
                ).fetchone()["scan_page_index"]
            finally:
                connection.close()

        self.assertEqual(page_index, 31)

    def test_update_probe_prefers_a_recently_changed_character(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.db"
            store = RankingStore(path)
            inactive, active = self.ranks(1, 2)
            store.save_page(
                [inactive, active],
                date(2026, 8, 28),
                next_index=3,
                update_checkpoint=False,
            )
            active = dict(active, exp=999)
            store.save_page(
                [inactive, active],
                date(2026, 8, 29),
                next_index=3,
                update_checkpoint=False,
            )

            self.assertEqual(select_candidates(path, 1), [active["characterName"]])
            self.assertEqual(
                rank_value(active),
                {"level": active["level"], "exp": 999, "rank": active["rank"]},
            )

    async def test_interrupted_scan_resumes_from_saved_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            failed_fetch = AsyncMock(
                side_effect=[{"ranks": self.ranks(1)}, TimeoutError()]
            )
            with self.assertRaises(TimeoutError):
                await scan_rankings(
                    failed_fetch,
                    store,
                    date(2026, 8, 16),
                    max_characters=20,
                    delay_seconds=0,
                )

            # 사용자가 /랭킹을 조회해도 자동 수집의 11위 재개 지점은 바뀌지 않습니다.
            store.save_snapshot(self.ranks(99, count=1)[0], date(2026, 8, 16))
            resumed_fetch = AsyncMock(return_value={"ranks": self.ranks(11)})
            result = await scan_rankings(
                resumed_fetch,
                store,
                date(2026, 8, 16),
                max_characters=20,
                delay_seconds=0,
            )

            self.assertEqual(store.character_count(), 21)
        resumed_fetch.assert_awaited_once_with(11)
        self.assertEqual(result["reason"], "limit")

    async def test_batch_scan_restarts_from_top_after_a_day_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            first_fetch = AsyncMock(return_value={"ranks": self.ranks(1)})
            first = await scan_rankings(
                first_fetch,
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
            )
            second_fetch = AsyncMock(return_value={"ranks": self.ranks(1)})
            second = await scan_rankings(
                second_fetch,
                store,
                date(2026, 8, 17),
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
            )

        self.assertEqual(first["reason"], "batch")
        self.assertEqual(second["reason"], "batch")
        first_fetch.assert_awaited_once_with(1)
        second_fetch.assert_awaited_once_with(1)

    async def test_batch_fetches_three_pages_together(self) -> None:
        started: list[int] = []
        all_started = asyncio.Event()

        async def fetch_page(page_index: int) -> dict:
            started.append(page_index)
            if len(started) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=0.1)
            return {"ranks": self.ranks(page_index)}

        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            result = await scan_rankings(
                fetch_page,
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
                max_pages=3,
            )

            self.assertEqual(store.character_count(), 30)
        self.assertEqual(started, [1, 11, 21])
        self.assertEqual(result, {"saved": 30, "next_index": 31, "reason": "batch"})

    async def test_worlds_keep_independent_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            kronos_fetch = AsyncMock(return_value={"ranks": self.ranks(1)})
            bera_ranks = self.ranks(1)
            for character in bera_ranks:
                character["worldID"] = 1
            bera_fetch = AsyncMock(return_value={"ranks": bera_ranks})

            await scan_rankings(
                kronos_fetch,
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
                scan_id=45,
            )
            await scan_rankings(
                bera_fetch,
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
                scan_id=1,
            )

            self.assertEqual(store.start_scan(date(2026, 8, 16), world_id=45), 11)
            self.assertEqual(store.start_scan(date(2026, 8, 16), world_id=1), 11)
        kronos_fetch.assert_awaited_once_with(1)
        bera_fetch.assert_awaited_once_with(1)

    async def test_completed_scan_restarts_only_on_the_next_day(self) -> None:
        boundary = self.ranks(1, count=5, level=260) + self.ranks(
            6, count=5, level=259
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            await scan_rankings(
                AsyncMock(return_value={"ranks": boundary}),
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
            )

            same_day_fetch = AsyncMock()
            same_day = await scan_rankings(
                same_day_fetch,
                store,
                date(2026, 8, 16),
                max_characters=None,
                delay_seconds=0,
            )
            next_day_fetch = AsyncMock(return_value={"ranks": self.ranks(1)})
            next_day = await scan_rankings(
                next_day_fetch,
                store,
                date(2026, 8, 17),
                max_characters=None,
                delay_seconds=0,
                max_pages=1,
            )

        same_day_fetch.assert_not_awaited()
        self.assertEqual(same_day["reason"], "already_completed")
        next_day_fetch.assert_awaited_once_with(1)
        self.assertEqual(next_day["reason"], "batch")

    def test_scan_start_time_stays_at_1710_utc_after_restart(self) -> None:
        scan_date = date(2026, 8, 31)
        expected = int(
            datetime(2026, 8, 31, 17, 10, tzinfo=timezone.utc).timestamp()
        )
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            store.start_scan(scan_date, world_id=45)
            with store._connect() as connection:
                connection.execute(
                    "UPDATE ranking_scan_state SET started_at = ? WHERE world_id = 45",
                    (expected + 12_345,),
                )
            store.start_scan(scan_date, world_id=45)
            with store._connect() as connection:
                actual = connection.execute(
                    "SELECT started_at FROM ranking_scan_state WHERE world_id = 45"
                ).fetchone()[0]

        self.assertEqual(ranking_scan_started_at(scan_date), expected)
        self.assertEqual(actual, expected)

    async def test_scan_stops_when_level_falls_below_260(self) -> None:
        ranks = self.ranks(1, count=5, level=260) + self.ranks(6, count=5, level=259)
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            result = await scan_rankings(
                AsyncMock(return_value={"ranks": ranks}),
                store,
                date(2026, 8, 16),
                max_characters=100,
                delay_seconds=0,
            )

            self.assertEqual(store.character_count(), 5)
        self.assertEqual(result["reason"], "level_boundary")

    def test_backup_is_readable_and_keeps_saved_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = RankingStore(Path(directory) / "ranking.db")
            source.save_snapshot(self.ranks(1, count=1)[0], date(2026, 8, 16))
            backup_path = Path(directory) / "backup" / "ranking.db"

            self.assertEqual(source.backup_to(backup_path), 1)
            restored = RankingStore(backup_path)

            self.assertEqual(restored.character_count(), 1)

    def test_collector_backoff_survives_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ranking.db"
            store = RankingStore(path)
            store.set_collector_backoff(2, 123456)

            self.assertEqual(RankingStore(path).get_collector_backoff(), (2, 123456))

            store.clear_collector_backoff()
            self.assertEqual(RankingStore(path).get_collector_backoff(), (0, 0))

    def test_command_searched_character_becomes_next_day_priority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            first_day = date(2026, 8, 28)
            second_day = date(2026, 8, 29)
            character = self.ranks(1, count=1)[0]

            store.save_snapshot(character, first_day)

            self.assertIsNone(store.next_priority_character(first_day))
            self.assertEqual(
                store.next_priority_character(second_day), character["characterName"]
            )
            store.mark_priority_refreshed(character["characterName"], second_day)
            self.assertIsNone(store.next_priority_character(second_day))

    def test_searched_character_below_260_is_not_prioritized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RankingStore(Path(directory) / "ranking.db")
            character = self.ranks(1, count=1, level=259)[0]
            first_day = date(2026, 8, 28)

            store.save_snapshot(character, first_day)

            self.assertIsNone(
                store.next_priority_character(first_day + timedelta(days=1))
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

    async def test_server_status_is_not_requested_outside_maintenance_window(self) -> None:
        bot = SimpleNamespace(
            maintenance_watch=None,
            fetch_server_status=AsyncMock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        bot.fetch_server_status.assert_not_awaited()

    async def test_server_open_alert_is_sent_once_after_down_to_up_transition(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        bot = SimpleNamespace(
            server_status="down",
            maintenance_watch={
                "monitor_from_timestamp": 0,
                "saw_down": True,
                "completed": False,
            },
            fetch_server_status=AsyncMock(return_value=statuses),
            send_server_open_alert=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)
        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        self.assertEqual(bot.server_status, "up")
        self.assertTrue(bot.maintenance_watch["completed"])
        bot.send_server_open_alert.assert_awaited_once()
        bot.persist_state.assert_called_once_with()

    async def test_scheduled_maintenance_does_not_alert_before_down_or_end(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        bot = SimpleNamespace(
            server_status=None,
            maintenance_watch={
                "monitor_from_timestamp": 0,
                "end_timestamp": 9_999_999_999,
                "saw_down": False,
                "completed": False,
            },
            fetch_server_status=AsyncMock(return_value=statuses),
            send_server_open_alert=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        bot.send_server_open_alert.assert_not_awaited()
        self.assertFalse(bot.maintenance_watch["completed"])

    async def test_scheduled_maintenance_alerts_after_planned_end(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        bot = SimpleNamespace(
            server_status=None,
            maintenance_watch={
                "monitor_from_timestamp": 0,
                "end_timestamp": 0,
                "saw_down": False,
                "completed": False,
            },
            fetch_server_status=AsyncMock(return_value=statuses),
            send_server_open_alert=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        bot.send_server_open_alert.assert_awaited_once()
        self.assertTrue(bot.maintenance_watch["completed"])

    async def test_emergency_maintenance_waits_until_down_was_observed(self) -> None:
        statuses = {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")}
        bot = SimpleNamespace(
            server_status=None,
            maintenance_watch={
                "monitor_from_timestamp": 0,
                "end_timestamp": None,
                "saw_down": False,
                "completed": False,
            },
            fetch_server_status=AsyncMock(return_value=statuses),
            send_server_open_alert=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        bot.send_server_open_alert.assert_not_awaited()
        self.assertFalse(bot.maintenance_watch["completed"])

    async def test_server_api_error_does_not_change_saved_status(self) -> None:
        bot = SimpleNamespace(
            server_status="up",
            maintenance_watch={
                "monitor_from_timestamp": 0,
                "saw_down": False,
                "completed": False,
            },
            fetch_server_status=AsyncMock(side_effect=ValueError("bad response")),
            send_server_open_alert=AsyncMock(),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_server_status.coro(bot)

        self.assertEqual(bot.server_status, "up")
        bot.send_server_open_alert.assert_not_awaited()
        bot.persist_state.assert_not_called()

    async def test_server_alert_mentions_configured_role_per_channel(self) -> None:
        first = SimpleNamespace(id=111, send=AsyncMock())
        second = SimpleNamespace(id=222, send=AsyncMock())
        bot = SimpleNamespace(
            server_alert_roles={"111": 555, "222": 666},
            alert_text_channels=lambda alert_type: [first, second],
        )
        embed = build_server_status_embed(
            {world: True for world in ("Scania", "Bera", "Kronos", "Hyperion")},
            opened=True,
        )

        await maple_bot.MapleNewsBot.send_server_open_alert(bot, embed)

        self.assertEqual(first.send.await_args.kwargs["content"], "<@&555>")
        self.assertEqual(second.send.await_args.kwargs["content"], "<@&666>")

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
    def test_supplied_boss_thumbnail_files_exist(self) -> None:
        self.assertEqual(
            set(maple_bot.BOSS_THUMBNAIL_PATHS),
            set(BOSS_TRAFFIC_LIGHTS),
        )
        self.assertTrue(
            all(path.is_file() for path in maple_bot.BOSS_THUMBNAIL_PATHS.values())
        )

    def test_boss_hp_units_are_converted_to_ingame_k_unit(self) -> None:
        self.assertEqual(format_boss_hp_as_k("38.5B"), "38,500,000K")
        self.assertEqual(format_boss_hp_as_k("24.175T"), "24,175,000,000K")
        self.assertEqual(format_boss_hp_as_k("1.01Q"), "1,010,000,000,000K")

    def test_boss_choices_match_all_provided_health_values(self) -> None:
        self.assertEqual(len(BOSS_TRAFFIC_LIGHTS), 18)
        self.assertEqual(
            [choice.value for choice in traffic_light_command.parameters[0].choices][-7:],
            [
                "칼로스",
                "최초의 대적자",
                "카링",
                "찬란한 흉성",
                "림보",
                "발드릭스",
                "유피테르",
            ],
        )
        boss_choices = {
            choice.value for choice in traffic_light_command.parameters[0].choices
        }
        self.assertIn("검밑", boss_choices)
        self.assertTrue(
            {"스우", "데미안", "루시드", "윌", "더스크", "진 힐라", "듄켈"}.isdisjoint(
                boss_choices
            )
        )
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["칼로스"]["카오스"], ("5.12Q", "256T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["칼로스"]["익스트림"], ("21.57Q", "1.08Q"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["세렌"]["익스트림"], ("6.48Q", "324T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["카링"]["익스트림"], ("55.10Q", "2.76Q"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["림보"]["하드"], ("12.55Q", "627.65T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["발드릭스"]["노말"], ("8.90Q", "445T"))
        self.assertEqual(BOSS_TRAFFIC_LIGHTS["발드릭스"]["하드"], ("20.27Q", "1.01Q"))
        self.assertIn("찬란한 흉성", BOSS_TRAFFIC_LIGHTS)
        self.assertNotIn("말레픽 스타", BOSS_TRAFFIC_LIGHTS)

    async def test_difficulty_autocomplete_only_shows_selected_boss_modes(self) -> None:
        interaction = SimpleNamespace(namespace=SimpleNamespace(**{"보스": "유피테르"}))

        choices = await traffic_light_difficulty_autocomplete(interaction, "")

        self.assertEqual([choice.value for choice in choices], ["노말", "하드"])

    async def test_all_boss_difficulties_are_available_with_choice_object(self) -> None:
        for boss, difficulties in BOSS_TRAFFIC_LIGHTS.items():
            interaction = SimpleNamespace(
                namespace=SimpleNamespace(**{"보스": SimpleNamespace(value=boss)})
            )

            choices = await traffic_light_difficulty_autocomplete(interaction, "")

            self.assertEqual(
                [choice.value for choice in choices],
                list(difficulties),
                boss,
            )

    async def test_command_shows_selected_boss_five_percent_requirement(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        with patch("maple_bot.discord.File", return_value=Mock()):
            await traffic_light_command.callback(
                interaction,
                SimpleNamespace(value="발드릭스"),
                "하드",
            )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "🚦 하드 발드릭스 5%")
        self.assertNotIn("**하드 발드릭스**", embed.description)
        self.assertIn("**총 체력**　20,270,000,000,000K", embed.description)
        self.assertIn("**5% 최소 피해량**　1,010,000,000,000K", embed.description)
        self.assertNotIn("전투력 분석", embed.description)
        self.assertIsNone(embed.footer.text)

    async def test_black_mage_below_group_shows_all_seven_bosses(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await traffic_light_command.callback(
            interaction,
            SimpleNamespace(value="검밑"),
            None,
        )

        message = interaction.response.send_message.await_args.kwargs
        embed = message["embed"]
        self.assertEqual(embed.title, "🚦 검밑 보스 5%")
        self.assertEqual(
            [name for name in ("스우", "데미안", "루시드", "윌", "더스크", "진 힐라", "듄켈") if name in embed.description],
            ["스우", "데미안", "루시드", "윌", "더스크", "진 힐라", "듄켈"],
        )
        self.assertNotIn("file", message)

    async def test_command_title_omits_missing_difficulty(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        with patch("maple_bot.discord.File", return_value=Mock()):
            await traffic_light_command.callback(
                interaction,
                SimpleNamespace(value="헬럭스"),
                "일반",
            )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "🚦 일반 헬럭스 5%")

    async def test_lucid_result_attaches_boss_thumbnail(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        image_file = Mock()

        with patch("maple_bot.discord.File", return_value=image_file) as file_class:
            await traffic_light_command.callback(
                interaction,
                SimpleNamespace(value="루시드"),
                "하드",
            )

        message = interaction.response.send_message.await_args.kwargs
        self.assertEqual(message["embed"].thumbnail.url, "attachment://boss-lucid.webp")
        self.assertIs(message["file"], image_file)
        file_class.assert_called_once_with(maple_bot.BOSS_THUMBNAIL_PATHS["루시드"])

    async def test_command_explains_invalid_boss_difficulty_combination(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await traffic_light_command.callback(
            interaction,
            SimpleNamespace(value="림보"),
            "카오스",
        )

        interaction.response.send_message.assert_awaited_once_with(
            "림보에서 선택 가능한 난이도: **노말, 하드**",
            ephemeral=True,
        )


class SeedRingSimulatorTests(unittest.IsolatedAsyncioTestCase):
    def test_level_four_and_five_probability_boundaries(self) -> None:
        self.assertTrue(simulate_seed_ring(4, 5, roll=50)["success"])
        self.assertFalse(simulate_seed_ring(4, 5, roll=51)["success"])
        self.assertTrue(simulate_seed_ring(5, 5, roll=25)["success"])
        self.assertFalse(simulate_seed_ring(5, 5, roll=26)["success"])

    def test_repeat_view_keeps_attempt_and_stone_totals(self) -> None:
        view = SeedRingSimulatorView(user_id=123, level=4, stone_count=3)
        with patch("maple_bot.random.randint", side_effect=[100, 1]):
            first = view.draw()
            second = view.draw()

        self.assertIn("강화에 실패", first.description)
        self.assertIn("강화에 성공", second.description)
        fields = {field.name: field.value for field in second.fields}
        self.assertEqual(fields["시도 횟수"], "2회")
        self.assertEqual(fields["성공 / 실패"], "1회 / 1회")
        self.assertEqual(fields["누적 사용 연마석"], "6개")

    async def test_other_user_cannot_press_repeat_button(self) -> None:
        view = SeedRingSimulatorView(user_id=123, level=5, stone_count=1)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=456),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertFalse(await view.interaction_check(interaction))
        interaction.response.send_message.assert_awaited_once_with(
            "이 버튼은 명령어를 실행한 사용자만 누를 수 있습니다.", ephemeral=True
        )

    async def test_command_sends_first_result_with_repeat_button(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        await seed_ring_command.callback(
            interaction,
            SimpleNamespace(value=4),
            SimpleNamespace(value=5),
        )

        arguments = interaction.response.send_message.await_args.kwargs
        self.assertEqual(arguments["view"].attempts, 1)
        self.assertEqual(arguments["view"].children[0].label, "같은 조건으로 다시 시도")
        self.assertIn("성공 확률: **50%**", arguments["embed"].description)


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


class ExpCouponTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_coupon_autocomplete_filters_advanced_coupon_by_level(self) -> None:
        missing_level = SimpleNamespace(namespace=SimpleNamespace(current_level=None))
        low_level = SimpleNamespace(namespace=SimpleNamespace(current_level=259))
        high_level = SimpleNamespace(namespace=SimpleNamespace(current_level=260))

        missing_choices = await exp_coupon_autocomplete(missing_level, "")
        low_choices = await exp_coupon_autocomplete(low_level, "")
        high_choices = await exp_coupon_autocomplete(high_level, "")

        self.assertEqual(missing_choices, [])
        self.assertEqual([choice.value for choice in low_choices], ["EXP 교환권"])
        self.assertEqual({choice.value for choice in high_choices}, set(EXP_COUPONS))

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
                    260,
                    coupon_name,
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
        coupon = "상급 EXP 교환권"
        with patch(
            "maple_bot.calculate_exp_coupons",
            return_value=(270, 0, 1, 1),
        ) as calculate:
            await exp_coupon_command.callback(
                interactions[0], 269, coupon, 0, 1, None
            )
            await exp_coupon_command.callback(
                interactions[1],
                269,
                coupon,
                0,
                1,
                SimpleNamespace(name="비욘드버닝", value="비욘드버닝"),
            )
            await exp_coupon_command.callback(
                interactions[2], 269, coupon, 0, 1, None
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

    def test_elanos_applies_only_to_base_daily_reward(self) -> None:
        self.assertEqual(
            calculate_symbol(
                "아르카나", 1, 0, 2, 6, True, date(2026, 8, 10)
            )[:4],
            (12, 1_690_000, 20, 34),
        )
        self.assertEqual(
            calculate_symbol(
                "세르니움", 1, 0, 11, 6, True, date(2026, 8, 10)
            )[:4],
            (4565, 3_930_100_000, 10, 18),
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
    async def test_patch_commands_attach_thumbnail_without_link_embed(self) -> None:
        post = {
            "id": 42415,
            "category": "update",
            "name": "[Updated 7/22] v.270 - Ride the Lightning Patch Notes",
            "imageThumbnail": "/maplestory/news/ride-the-lightning.jpg",
        }
        client = SimpleNamespace(
            latest_patch=post,
            fetch_posts=AsyncMock(),
            fetch_character_image=AsyncMock(return_value=b"thumbnail"),
        )
        interaction = SimpleNamespace(
            client=client,
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        context = SimpleNamespace(bot=client, send=AsyncMock())
        expected = (
            "v.270 - Ride the Lightning\n"
            "https://www.nexon.com/maplestory/news/update/42415/"
            "updated-7-22-v-270-ride-the-lightning-patch-notes"
        )

        files = [Mock(), Mock()]
        with patch("maple_bot.discord.File", side_effect=files) as file_class:
            await maple_bot.patch_command.callback(interaction)
            await maple_bot.patch_prefix_command.callback(context)

        interaction.response.send_message.assert_awaited_once_with(
            expected, file=files[0], suppress_embeds=True
        )
        context.send.assert_awaited_once_with(
            expected, file=files[1], suppress_embeds=True
        )
        self.assertEqual(file_class.call_count, 2)
        self.assertEqual(file_class.call_args_list[0].kwargs["filename"], "patch-thumbnail.jpg")
        self.assertEqual(file_class.call_args_list[1].kwargs["filename"], "patch-thumbnail.jpg")
        self.assertEqual(client.fetch_character_image.await_count, 2)
        client.fetch_posts.assert_not_awaited()

    async def test_time_commands_send_the_same_embed_layout(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        context = SimpleNamespace(send=AsyncMock())
        now = datetime(2026, 9, 3, 6, 32, tzinfo=timezone.utc)

        with patch("maple_bot.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            await maple_bot.time_command.callback(interaction)
            await maple_bot.time_prefix_command.callback(context)

        slash_embed = interaction.response.send_message.await_args.kwargs["embed"]
        prefix_embed = context.send.await_args.kwargs["embed"]
        self.assertEqual(slash_embed.to_dict(), prefix_embed.to_dict())

    async def test_voyage_commands_send_the_guide_image_without_embed(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        context = SimpleNamespace(send=AsyncMock())
        image_file = Mock(filename="gms-voyage-guide.png")

        with patch("maple_bot.discord.File", return_value=image_file) as file_class:
            await maple_bot.voyage_command.callback(interaction)
            await maple_bot.voyage_prefix_command.callback(context)

        self.assertEqual(file_class.call_count, 2)
        interaction.response.send_message.assert_awaited_once_with(file=image_file)
        context.send.assert_awaited_once_with(file=image_file)

    async def test_existing_patch_detail_is_refreshed_only_every_five_minutes(self) -> None:
        post = {
            "id": 42853,
            "category": "update",
            "name": "v.270 - Ride the Lightning Patch Notes",
            "liveDate": "2026-08-11T00:00:00Z",
        }
        bot = SimpleNamespace(
            fetch_posts=AsyncMock(return_value=[post]),
            fetch_post_detail=AsyncMock(return_value={"body": "<p>Patch notes</p>"}),
            create_patch_event_schedule=Mock(return_value=None),
            sent_ids={post["id"]},
            patch_events={"post_id": post["id"]},
            latest_cash_shop=None,
            maintenance_watch=None,
            sunny_sunday={},
            saved_categories=set(maple_bot.WATCHED_CATEGORIES),
            persist_state=Mock(),
        )

        await maple_bot.MapleNewsBot.check_news.coro(bot)
        await maple_bot.MapleNewsBot.check_news.coro(bot)

        self.assertEqual(maple_bot.MapleNewsBot.check_news.minutes, 1.0)
        self.assertEqual(bot.fetch_posts.await_count, 2)
        bot.fetch_post_detail.assert_awaited_once_with(post["id"])

    async def test_polling_saves_latest_cash_shop_update_without_resending_post(self) -> None:
        post = {
            "id": 42853,
            "category": "sale",
            "name": "Cash Shop Update for August 11",
            "liveDate": "2026-08-11T00:00:00Z",
        }
        bot = SimpleNamespace(
            fetch_posts=AsyncMock(return_value=[post]),
            fetch_post_detail=AsyncMock(
                return_value={"body": "<h1>Premium Surprise Style Box</h1><h1>ONGOING SALES</h1>"}
            ),
            translate_texts=AsyncMock(return_value=["프리미엄 서프라이즈 스타일 박스"]),
            sent_ids={post["id"]},
            latest_cash_shop=None,
            maintenance_watch=None,
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
                "items": ["프리미엄 서프라이즈 스타일 박스"],
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
        self.assertIn("/패치", field_text)
        self.assertIn("!패치", field_text)
        self.assertIn("/시간", field_text)
        self.assertIn("!시간", field_text)
        self.assertIn("/항해", field_text)
        self.assertIn("!항해", field_text)
        self.assertIn("/아이템검색", field_text)
        self.assertIn("/외형검색", field_text)
        self.assertIn("/랭킹", field_text)
        self.assertIn("/시드링", field_text)
        self.assertIn("/ㅁ", field_text)
        self.assertIn("/심볼", field_text)
        self.assertNotIn("/서버랭킹", field_text)
        self.assertNotIn("/공지알림", field_text)

    async def test_quick_copy_shows_four_separate_code_blocks(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await quick_copy_command.callback(interaction)

        arguments = interaction.response.send_message.await_args
        message = arguments.args[0]
        self.assertTrue(arguments.kwargs["ephemeral"])
        self.assertEqual(message.count("```text"), 4)
        self.assertIn("Sacred Symbol/claim", message)
        self.assertIn("Arcane Symbol/claim", message)
        self.assertIn("Sol Erda Fragment", message)
        self.assertIn("/partyleave", message)

    async def test_quick_copy_aliases_show_the_same_message(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        context = SimpleNamespace(send=AsyncMock())

        await quick_copy_symbol_command.callback(interaction)
        await quick_copy_symbol_prefix_command.callback(context)

        slash_message = interaction.response.send_message.await_args.args[0]
        prefix_message = context.send.await_args.args[0]
        self.assertEqual(slash_message, prefix_message)
        self.assertEqual(slash_message.count("```text"), 4)


class CommandStatsTests(unittest.IsolatedAsyncioTestCase):
    def test_usage_is_counted_by_command_and_user(self) -> None:
        stats = {"total": 0, "commands": {}, "users": {}}

        record_command_usage(stats, "ㅁ", 123, "테스터")
        record_command_usage(stats, "ㅁ", 123, "새 닉네임")

        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["commands"], {"ㅁ": 2})
        self.assertEqual(
            stats["users"]["123"], {"name": "새 닉네임", "count": 2}
        )
        self.assertIn("/ㅁ", build_command_stats_embed(stats).fields[0].value)

    async def test_application_command_interaction_is_persisted_once(self) -> None:
        bot = SimpleNamespace(
            command_stats={"total": 0, "commands": {}, "users": {}},
            persist_state=Mock(),
        )
        interaction = SimpleNamespace(
            type=maple_bot.discord.InteractionType.application_command,
            data={"name": "ㅁ"},
            user=SimpleNamespace(id=123, display_name="테스터"),
        )

        await maple_bot.MapleNewsBot.on_interaction(bot, interaction)

        self.assertEqual(bot.command_stats["commands"], {"ㅁ": 1})
        bot.persist_state.assert_called_once_with()

    async def test_only_bot_owner_can_view_stats(self) -> None:
        denied = SimpleNamespace(
            user=SimpleNamespace(),
            client=SimpleNamespace(is_owner=AsyncMock(return_value=False)),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await command_stats_command.callback(denied)
        self.assertIn(
            "봇 소유자만", denied.response.send_message.await_args.args[0]
        )
        self.assertTrue(denied.response.send_message.await_args.kwargs["ephemeral"])

        allowed = SimpleNamespace(
            user=SimpleNamespace(),
            client=SimpleNamespace(
                is_owner=AsyncMock(return_value=True),
                command_stats={"total": 0, "commands": {}, "users": {}},
            ),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await command_stats_command.callback(allowed)
        self.assertEqual(
            allowed.response.send_message.await_args.kwargs["embed"].title,
            "명령어 사용 통계",
        )
        self.assertTrue(allowed.response.send_message.await_args.kwargs["ephemeral"])

    async def test_new_backfill_alert_is_sent_to_owner_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            alert_path = Path(directory) / "alert.txt"
            alert_path.write_text("RuntimeError: blocked by MapleBot", encoding="utf-8")
            bot = SimpleNamespace(
                _last_backfill_alert=None,
                send_owner_dm=AsyncMock(),
            )
            with patch.object(maple_bot, "BACKFILL_ALERT_PATH", alert_path):
                await MapleNewsBot.check_backfill_alert.coro(bot)
                await MapleNewsBot.check_backfill_alert.coro(bot)

        bot.send_owner_dm.assert_awaited_once()
        self.assertIn("blocked by MapleBot", bot.send_owner_dm.await_args.args[0])

    async def test_owner_can_restart_backfill_by_dm(self) -> None:
        bot = SimpleNamespace(
            is_owner=AsyncMock(return_value=True),
            run_backfill_control=AsyncMock(return_value="백필을 재시작했습니다."),
            process_commands=AsyncMock(),
        )
        message = SimpleNamespace(
            content="백필재시작",
            guild=None,
            author=SimpleNamespace(bot=False),
            channel=SimpleNamespace(send=AsyncMock()),
        )

        await MapleNewsBot.on_message(bot, message)

        bot.run_backfill_control.assert_awaited_once_with("restart")
        message.channel.send.assert_awaited_once_with("백필을 재시작했습니다.")
        bot.process_commands.assert_not_awaited()

    async def test_owner_can_check_ranking_collection_by_dm(self) -> None:
        bot = SimpleNamespace(
            is_owner=AsyncMock(return_value=True),
            ranking_collection_status=AsyncMock(return_value="랭킹 수집 상태"),
            process_commands=AsyncMock(),
        )
        message = SimpleNamespace(
            content="랭킹 상태",
            guild=None,
            author=SimpleNamespace(bot=False),
            channel=SimpleNamespace(send=AsyncMock()),
        )

        await MapleNewsBot.on_message(bot, message)

        bot.ranking_collection_status.assert_awaited_once_with()
        message.channel.send.assert_awaited_once_with("랭킹 수집 상태")
        bot.process_commands.assert_not_awaited()


class ItemSearchTests(unittest.IsolatedAsyncioTestCase):
    def test_search_finds_same_item_by_english_and_korean_name(self) -> None:
        english_result = search_cash_items("Red Steed Mask", limit=1)
        korean_result = search_cash_items("붉은 말의 탈", limit=1)

        self.assertEqual(english_result[0]["id"], "1007104")
        self.assertEqual(korean_result[0]["id"], "1007104")

    async def test_autocomplete_shows_both_names(self) -> None:
        choices = await item_search_autocomplete(None, "붉은 말")

        self.assertEqual(choices[0].value, "1007104")
        self.assertIn("Red Steed Mask", choices[0].name)
        self.assertIn("붉은 말의 탈", choices[0].name)

    async def test_item_command_attaches_available_icon(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await item_search_command.callback(interaction, "1007104")

        arguments = interaction.response.send_message.await_args
        self.assertIn("Red Steed Mask", arguments.kwargs["embed"].description)
        self.assertIn("붉은 말의 탈", arguments.kwargs["embed"].description)
        self.assertEqual(arguments.kwargs["file"].filename, "cash-item-1007104.png")

    async def test_item_command_keeps_name_only_items_searchable(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await item_search_command.callback(interaction, "20000")

        arguments = interaction.response.send_message.await_args
        self.assertNotIn("file", arguments.kwargs)
        self.assertIn("독립 아이콘", arguments.kwargs["embed"].footer.text)

    async def test_gms_only_item_explains_missing_kms_id(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await item_search_command.callback(interaction, "1007254")

        description = interaction.response.send_message.await_args.kwargs["embed"].description
        self.assertIn("Sweet Apple Fox Mask", description)
        self.assertIn("KMS 동일 ID 없음", description)


class AppearanceSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_registered_during_setup(self) -> None:
        bot = SimpleNamespace(
            tree=SimpleNamespace(
                set_translator=AsyncMock(),
                add_command=Mock(),
                sync=AsyncMock(),
            ),
            add_command=Mock(),
            persist_state=Mock(),
        )

        with patch("maple_bot.aiohttp.ClientSession", return_value=Mock()):
            await maple_bot.MapleNewsBot.setup_hook(bot)

        bot.tree.add_command.assert_any_call(appearance_search_command)
        bot.tree.add_command.assert_any_call(quick_copy_symbol_command)
        bot.tree.add_command.assert_any_call(maple_bot.voyage_command)
        bot.add_command.assert_any_call(quick_copy_symbol_prefix_command)
        bot.add_command.assert_any_call(maple_bot.patch_prefix_command)
        bot.add_command.assert_any_call(maple_bot.time_prefix_command)
        bot.add_command.assert_any_call(maple_bot.voyage_prefix_command)
        self.assertEqual(bot.add_command.call_count, 4)

    def test_search_filters_hair_and_face(self) -> None:
        hair = search_cash_items("30000", category="Hair", limit=1)
        wrong_category = search_cash_items("30000", category="Face", limit=1)

        self.assertEqual(hair[0]["gms_name"], "Toben Hair")
        self.assertEqual(hair[0]["kms_name"], "검은색 토벤 머리")
        self.assertEqual(wrong_category, [])

    async def test_autocomplete_shows_only_selected_type(self) -> None:
        interaction = SimpleNamespace(namespace=SimpleNamespace(종류="Face"))

        choices = await appearance_search_autocomplete(interaction, "도전적인 얼굴")

        self.assertEqual(choices[0].value, "20000")
        self.assertIn("Defiant Face", choices[0].name)
        self.assertIn("도전적인 얼굴", choices[0].name)

    async def test_command_shows_gms_and_kms_names(self) -> None:
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )
        appearance_type = SimpleNamespace(name="헤어", value="Hair")

        await appearance_search_command.callback(
            interaction, appearance_type, "30000"
        )

        embed = interaction.response.send_message.await_args.kwargs["embed"]
        self.assertIn("Toben Hair", embed.description)
        self.assertIn("검은색 토벤 머리", embed.description)
        self.assertIn("30000", embed.description)


class FamiliarSimulatorTests(unittest.IsolatedAsyncioTestCase):
    def test_normal_result_uses_unique_then_epic_pool(self) -> None:
        unique = ("공격력 +6%", 3.42)
        epic = ("보스 몬스터 공격 시 데미지 +30%", 0.24)

        with (
            patch("maple_bot.random.random", return_value=0.5),
            patch("maple_bot.random.choices", side_effect=[[unique], [epic]]) as choices,
        ):
            result = maple_bot.draw_unique_familiar_potential()

        self.assertEqual(result, (unique[0], epic[0], False))
        self.assertIs(choices.call_args_list[0].args[0], maple_bot.FAMILIAR_UNIQUE_POTENTIALS)
        self.assertIs(choices.call_args_list[1].args[0], maple_bot.FAMILIAR_EPIC_POTENTIALS)

    def test_double_prime_uses_unique_pool_twice(self) -> None:
        first = ("공격력 +6%", 3.42)
        second = ("보스 몬스터 공격 시 데미지 +40%", 1.01)

        with (
            patch("maple_bot.random.random", return_value=0.001),
            patch("maple_bot.random.choices", side_effect=[[first], [second]]) as choices,
        ):
            result = maple_bot.draw_unique_familiar_potential()

        self.assertEqual(result, (first[0], second[0], True))
        self.assertIs(choices.call_args_list[1].args[0], maple_bot.FAMILIAR_UNIQUE_POTENTIALS)

    def test_result_image_uses_familiar_card_size(self) -> None:
        result = maple_bot.create_familiar_result_image(
            "공격력 +6%", "보스 몬스터 공격 시 데미지 +30%"
        )

        with Image.open(result) as image:
            self.assertEqual(image.size, (732, 698))
            self.assertEqual(image.mode, "RGB")
            self.assertEqual(image.getpixel((4, 100)), (149, 69, 6))

    async def test_command_sends_localized_lines_in_one_card_image(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock())
        )

        with (
            patch(
                "maple_bot.draw_unique_familiar_potential",
                return_value=("공격력 +6%", "보스 몬스터 공격 시 데미지 +30%", False),
            ),
            patch(
                "maple_bot.create_familiar_result_image",
                return_value=io.BytesIO(b"image"),
            ) as create_image,
        ):
            await maple_bot.familiar_command.callback(interaction)

        message = interaction.response.send_message.await_args.kwargs
        self.assertEqual(maple_bot.familiar_command.name, "퍼밀리어")
        self.assertNotIn("embed", message)
        self.assertEqual(message["content"], "누적 횟수: 1회")
        self.assertEqual(message["file"].filename, "familiar-result.png")
        self.assertEqual(message["view"].children[0].label, "다시 뽑기")
        self.assertIsNone(message["view"].children[0].emoji)
        self.assertEqual(message["view"].children[1].label, "기대값 계산하기")
        self.assertEqual(message["view"].timeout, 86_400)
        create_image.assert_called_once_with(
            "공격력 +6%", "보스 몬스터 공격 시 데미지 +30%"
        )

    async def test_command_marks_double_prime(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123),
            response=SimpleNamespace(send_message=AsyncMock())
        )

        with patch(
            "maple_bot.draw_unique_familiar_potential",
            return_value=("공격력 +6%", "마력 +6%", True),
        ):
            await maple_bot.familiar_command.callback(interaction)

        message = interaction.response.send_message.await_args.kwargs
        self.assertNotIn("embed", message)
        self.assertIn("더블 프라임", message["content"])

    async def test_reroll_button_replaces_the_card_image(self) -> None:
        initial_result = ("공격력 +6%", "최대 MP +6%", False)
        next_result = ("마력 +6%", "최대 HP +6%", False)
        view = maple_bot.FamiliarSimulatorView(123, initial_result)
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock())
        )
        content = "누적 횟수: 2회"
        file = maple_bot.discord.File(io.BytesIO(b"image"), filename="familiar-result.png")

        with patch(
            "maple_bot.build_familiar_result",
            return_value=(content, file, next_result),
        ) as build_result:
            await view.children[0].callback(interaction)

        build_result.assert_called_once_with(2)
        self.assertEqual(view.draw_count, 2)
        self.assertEqual(view.result, next_result)
        interaction.response.edit_message.assert_awaited_once_with(
            content=content, attachments=[file], view=view
        )

    async def test_expectation_button_shows_current_result_only_to_user(self) -> None:
        result = ("공격력 +6%", "보스 몬스터 공격 시 데미지 +30%", False)
        view = maple_bot.FamiliarSimulatorView(123, result)
        store = Mock()
        store.get.return_value = {
            "probability": 0.0001,
            "expected_attempts": 10_000,
            "rarity_percentile": 12.34,
        }
        interaction = SimpleNamespace(
            client=SimpleNamespace(familiar_expectation_store=store),
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await view.children[1].callback(interaction)

        message = interaction.response.send_message.await_args
        self.assertIn("공격력 +6%", message.args[0])
        self.assertIn("상위 `12.34%`", message.args[0])
        self.assertIn("10,000회", message.args[0])
        self.assertIn("내 1회 이내 달성 확률", message.args[0])
        self.assertTrue(message.kwargs["ephemeral"])

    async def test_other_user_can_check_expectation_but_not_reroll(self) -> None:
        view = maple_bot.FamiliarSimulatorView(123, ("공격력 +6%", "최대 MP +6%", False))
        expectation_interaction = SimpleNamespace(
            user=SimpleNamespace(id=456),
            data={"custom_id": view.show_expectation.custom_id},
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        reroll_interaction = SimpleNamespace(
            user=SimpleNamespace(id=456),
            data={"custom_id": view.reroll.custom_id},
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertTrue(await view.interaction_check(expectation_interaction))
        self.assertFalse(await view.interaction_check(reroll_interaction))
        reroll_interaction.response.send_message.assert_awaited_once_with(
            "이 버튼은 명령어를 실행한 사용자만 누를 수 있습니다.", ephemeral=True
        )


class FamiliarExpectationStoreTests(unittest.TestCase):
    def test_precomputes_every_combination_and_rarity_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FamiliarExpectationStore(Path(directory) / "familiar.db")
            common = store.get(("최대 MP +6%", "방어력 +240", False))
            rare = store.get(("크리티컬 확률 +6%", "메소 드롭률 +100%", False))
            example = store.get(
                ("공격력 +6%", "보스 몬스터 공격 시 데미지 +30%", False)
            )

            connection = store._connect()
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM familiar_expectations"
                ).fetchone()[0]
            finally:
                connection.close()

        self.assertEqual(count, 4_784)
        self.assertLess(rare["probability"], common["probability"])
        self.assertLess(
            rare["rarity_percentile"], common["rarity_percentile"]
        )
        self.assertAlmostEqual(
            rare["expected_attempts"], 1 / rare["probability"]
        )
        self.assertAlmostEqual(example["rarity_percentile"], 4.3485132628)


class PssbCommandTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def interaction(rates: list[tuple[str, float]]) -> SimpleNamespace:
        return SimpleNamespace(
            user=SimpleNamespace(id=123),
            client=SimpleNamespace(fetch_pssb_rates=AsyncMock(return_value=rates)),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_one_draw_sends_one_composite_image(self) -> None:
        result = ("Roaring Green Rain Hood", 5.0)
        interaction = self.interaction([result])

        with patch("maple_bot.random.choices", return_value=[result]):
            await pssb_command.callback(interaction, SimpleNamespace(value=1))

        message = interaction.followup.send.await_args.kwargs
        self.assertEqual(interaction.followup.send.await_count, 1)
        self.assertEqual(message["file"].filename, "pssb-1-results.png")
        self.assertEqual(message["view"].children[0].label, "다시 뽑기")
        self.assertIsNone(message["view"].children[0].emoji)
        self.assertEqual(message["view"].children[1].label, "기대값 계산하기")
        self.assertEqual(message["view"].timeout, 86_400)
        self.assertEqual(
            message["embed"].image.url,
            "attachment://pssb-1-results.png",
        )
        self.assertEqual(
            message["embed"].footer.text,
            "누적 횟수: 1회\n지금까지 낭비한 돈: 3,600 NX",
        )
        with Image.open(message["file"].fp) as result_image:
            self.assertEqual(result_image.size, (664, 591))

    async def test_ssb_name_is_searchable_in_english_and_displayed_in_korean(self) -> None:
        translator = maple_bot.KoreanCommandTranslator()

        self.assertEqual(pssb_command.name, "ssb")
        self.assertEqual(maple_bot.pssb_initials_command.name, "ㅅㅅㅂ")
        self.assertEqual(
            await translator.translate(
                pssb_command._locale_name,
                maple_bot.discord.Locale.korean,
                None,
            ),
            "스스비",
        )

    async def test_korean_initials_alias_uses_the_same_draw_logic(self) -> None:
        result = ("Roaring Green Rain Hood", 5.0)
        interaction = self.interaction([result])

        with patch("maple_bot.random.choices", return_value=[result]):
            await maple_bot.pssb_initials_command.callback(
                interaction, SimpleNamespace(value=1)
            )

        interaction.client.fetch_pssb_rates.assert_awaited_once()
        self.assertEqual(interaction.followup.send.await_count, 1)

    async def test_five_draws_send_one_message_with_five_results(self) -> None:
        result = ("Roaring Green Rain Hood", 5.0)
        interaction = self.interaction([result])

        with patch("maple_bot.random.choices", return_value=[result] * 5):
            await pssb_command.callback(interaction, SimpleNamespace(value=5))

        message = interaction.followup.send.await_args.kwargs
        self.assertEqual(interaction.followup.send.await_count, 1)
        self.assertEqual(message["file"].filename, "pssb-5-results.png")
        self.assertEqual(message["embed"].description.count("Roaring Green Rain Hood"), 5)
        self.assertEqual(
            message["embed"].footer.text,
            "누적 횟수: 5회\n지금까지 낭비한 돈: 18,000 NX",
        )
        with Image.open(message["file"].fp) as result_image:
            self.assertEqual(result_image.size, (664, 336))

    async def test_draw_without_database_match_keeps_empty_slot_and_name(self) -> None:
        result = ("Future PSSB Item", 1.0)
        interaction = self.interaction([result])

        with patch("maple_bot.random.choices", return_value=[result]):
            await pssb_command.callback(interaction, SimpleNamespace(value=1))

        message = interaction.followup.send.await_args.kwargs
        self.assertEqual(message["file"].filename, "pssb-1-results.png")
        self.assertIn("Future PSSB Item", message["embed"].description)

    async def test_reroll_button_refreshes_official_rates_and_replaces_image(self) -> None:
        result = ("New Rotation Item", 2.0)
        view = maple_bot.PssbSimulatorView(123, 1, [("Old Rotation Item", 5.0)])
        interaction = SimpleNamespace(
            client=SimpleNamespace(fetch_pssb_rates=AsyncMock(return_value=[result])),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        file = maple_bot.discord.File(io.BytesIO(b"image"), filename="pssb-1-results.png")

        with (
            patch("maple_bot.draw_pssb_results", return_value=[result]),
            patch(
                "maple_bot.build_pssb_file",
                return_value=(file, "pssb-1-results.png"),
            ),
        ):
            await view.children[0].callback(interaction)

        interaction.client.fetch_pssb_rates.assert_awaited_once()
        self.assertEqual(view.draw_count, 2)
        self.assertEqual(view.results, [result])
        interaction.edit_original_response.assert_awaited_once_with(
            embed=ANY, attachments=[file], view=view
        )

    async def test_expectation_button_shows_expected_boxes_only_to_user(self) -> None:
        view = maple_bot.PssbSimulatorView(
            123,
            5,
            [("Rare Item", 2.0), ("Common Item", 5.0)],
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(send_message=AsyncMock())
        )

        await view.children[1].callback(interaction)

        message = interaction.response.send_message.await_args
        self.assertIn("Rare Item", message.args[0])
        self.assertIn("평균 약 `50회`", message.args[0])
        self.assertIn("평균 약 `20회`", message.args[0])
        self.assertIn("165,600 NX", message.args[0])
        self.assertIn("68,400 NX", message.args[0])
        self.assertIn("내 5회 이내 달성 확률", message.args[0])
        self.assertTrue(message.kwargs["ephemeral"])

    def test_nx_cost_uses_the_cheapest_single_and_set_mix(self) -> None:
        self.assertEqual(maple_bot.pssb_nx_cost(10), 36_000)
        self.assertEqual(maple_bot.pssb_nx_cost(11), 36_000)
        self.assertEqual(maple_bot.pssb_nx_cost(12), 39_600)

    def test_gender_suffix_uses_the_shared_item_name(self) -> None:
        item = pssb_cash_item("Oh My Captain (M) / Oh My Captain (F)")

        self.assertIsNotNone(item)
        self.assertEqual(item["gms_name"], "Oh My Captain")

    def test_duplicate_name_prefers_hangover_makeup_item_with_icon(self) -> None:
        item = pssb_cash_item("Hangover Make-up")

        self.assertEqual(item["id"], "1012603")
        self.assertEqual(item["category"], "Accessory")
        self.assertEqual(item["icon"], "1012603.png")

    def test_appearance_data_is_not_used_for_pssb_items(self) -> None:
        self.assertIsNone(pssb_cash_item("Defiant Face"))

    def test_only_two_percent_or_lower_uses_purple_slot(self) -> None:
        self.assertEqual(maple_bot.pssb_slot_path(2.0), maple_bot.PSSB_ADVANCED_SLOT_PATH)
        self.assertEqual(maple_bot.pssb_slot_path(4.0), maple_bot.PSSB_COMMON_SLOT_PATH)

    def test_two_percent_result_has_subtle_rare_marker(self) -> None:
        self.assertIn("✨ **Rare Item**", maple_bot.format_pssb_result(1, "Rare Item", 2.0))
        self.assertNotIn("✨", maple_bot.format_pssb_result(1, "Common Item", 4.0))


class ScheduleCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_cash_shop_command_uses_saved_latest_link_and_thumbnail(self) -> None:
        interaction = SimpleNamespace(
            client=SimpleNamespace(
                latest_cash_shop={
                    "post_id": 42853,
                    "title": "Cash Shop Update for August 11",
                    "url": "https://example.com/latest-cash-shop",
                    "items": ["블랙 프라이데이 기념 아이템 출시", "불프 스스비"],
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
        self.assertIn("cash-shop)\n\n· 블랙 프라이데이", embed.description)
        self.assertIn("· 블랙 프라이데이 기념 아이템 출시", embed.description)
        self.assertIn("· 불프 스스비", embed.description)
        self.assertEqual(embed.thumbnail.url, "attachment://cash-shop-update.png")
        discord_file.assert_called_once_with(
            maple_bot.CASH_SHOP_UPDATE_IMAGE_PATH,
            filename="cash-shop-update.png",
        )

    def test_cash_shop_sections_stop_before_ongoing_sales(self) -> None:
        source = """
        <h1><strong>The Atelier Reliquary</strong></h1>
        <h1>DAILY DEALS</h1>
        <h1>Coloring Prism and Prism Color Restore</h1>
        <h1>ONGOING SALES</h1>
        <h1>Premium Surprise Style Box</h1>
        """

        self.assertEqual(
            extract_cash_shop_sections(source),
            ["The Atelier Reliquary", "Coloring Prism and Prism Color Restore"],
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
    def test_vocative_suffix_uses_final_hangul_batchim(self) -> None:
        self.assertEqual(maple_bot.korean_vocative_suffix("슈빈"), "아")
        self.assertEqual(maple_bot.korean_vocative_suffix("슈비"), "야")
        self.assertEqual(maple_bot.korean_vocative_suffix("슈비✨"), "야")

    async def test_command_uses_display_name_and_channel_between_1_and_40(self) -> None:
        interaction = SimpleNamespace(
            user=SimpleNamespace(display_name="류*게이"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        selected_message = maple_bot.CHANNEL_RECOMMEND_MESSAGES[2]
        # 채널은 27, 문구는 세 번째 항목으로 고정해 두 무작위 선택이 적용되는지 확인합니다.
        with (
            patch("maple_bot.random.randint", return_value=27) as randint,
            patch("maple_bot.random.choice", return_value=selected_message) as choice,
        ):
            await channel_recommend_command.callback(interaction)

        randint.assert_called_once_with(1, 40)
        choice.assert_called_once_with(maple_bot.CHANNEL_RECOMMEND_MESSAGES)
        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("류\\*게이", message)
        self.assertIn("27채널", message)
        self.assertIn("지금 신호가 왔어", message)

    def test_twenty_messages_are_available(self) -> None:
        self.assertEqual(len(maple_bot.CHANNEL_RECOMMEND_MESSAGES), 20)
        for template in maple_bot.CHANNEL_RECOMMEND_MESSAGES:
            template.format(display_name="슈빈", vocative="아", channel_number=27)
