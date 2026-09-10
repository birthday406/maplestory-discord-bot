import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import maple_bot as bot


class SunnyRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # 썬데이 검사는 별도 DM 알림의 실제 상태 파일을 읽거나 바꾸지 않습니다.
        reminders = patch('data_update_reminders.check_data_updates', new_callable=AsyncMock)
        reminders.start()
        self.addCleanup(reminders.stop)

    async def test_shining_banner_requires_both_perks_on_same_sunday(self):
        destruction = '30% reduced chance of item destruction when enhancing items below 21 Stars'
        discount = '30% off Star Force enhancements'
        client = SimpleNamespace(translate_texts=AsyncMock(return_value=[]))
        entries = [
            ('September 13, 2026', False, [destruction, discount]),
            ('September 20, 2026', False, [destruction]),
            ('September 27, 2026', False, [discount]),
            ('October 04, 2026', True, [discount]),
        ]
        results = await bot.MapleNewsBot.translate_sunny_sunday(client, entries)
        banner = f'{bot.ANIMATED_TWINKLE_EMOJI} **스페셜: 샤이닝 스타포스** {bot.ANIMATED_TWINKLE_EMOJI}'
        self.assertEqual(results[0]['value'], banner + '\n'
                         '- 21성 이하에서 스타포스 강화 시 파괴 확률 30% 감소\n'
                         '- 스타포스 강화 비용 30% 할인')
        self.assertNotIn('샤이닝 스타포스', results[1]['value'])
        self.assertNotIn('샤이닝 스타포스', results[2]['value'])
        self.assertTrue(results[3]['value'].startswith(banner))

    def client(self, schedule):
        post = {'id': 44597, 'category': 'update', 'name': 'v.271 - Test Patch Notes',
                'liveDate': '2026-09-08T00:00:00Z'}
        return SimpleNamespace(fetch_posts=AsyncMock(return_value=[post]),
            fetch_post_detail=AsyncMock(return_value={'body': ''}),
            create_patch_event_schedule=Mock(return_value=None),
            create_sunny_sunday_schedule=AsyncMock(return_value=schedule),
            send_alert_embed=AsyncMock(), sent_ids={44597}, patch_events={'post_id': 42415},
            latest_cash_shop=None, maintenance_watch=None,
            sunny_sunday={'post_id': 42415, 'entries': [
                {'timestamp': 1800000000, 'message_ids': {'42': 123}}]},
            saved_categories=set(bot.WATCHED_CATEGORIES), persist_state=Mock())

    async def test_processed_patch_replaces_stale_schedule_once(self):
        schedule = {'post_id': 44597, 'title': 'v.271', 'url': 'https://example.com',
                    'entries': [{'timestamp': 1800000000, 'name': 'Sunday', 'value': 'Perk', 'message_ids': {}}]}
        client = self.client(schedule)
        await bot.MapleNewsBot.check_news.coro(client)
        await bot.MapleNewsBot.check_news.coro(client)
        self.assertEqual(client.sunny_sunday['post_id'], 44597)
        self.assertEqual(client.sunny_sunday['entries'][0]['message_ids'], {'42': 123})
        client.send_alert_embed.assert_awaited_once()
        client.persist_state.assert_called_once()

    async def test_missing_schedule_keeps_old_and_retries_on_next_detail_poll(self):
        client = self.client(None)
        old = client.sunny_sunday
        await bot.MapleNewsBot.check_news.coro(client)
        await bot.MapleNewsBot.check_news.coro(client)
        self.assertIs(client.sunny_sunday, old)
        self.assertEqual(client.create_sunny_sunday_schedule.await_count, 1)
        client._last_news_detail_refresh_at = None
        await bot.MapleNewsBot.check_news.coro(client)
        self.assertEqual(client.create_sunny_sunday_schedule.await_count, 2)
        client.send_alert_embed.assert_not_awaited()
        client.persist_state.assert_not_called()
