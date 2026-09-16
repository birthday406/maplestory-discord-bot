import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord_news import NewsStore, NewsRelay, is_link_only_notice, validate_message, translate_or_original, deliver_all, make_embeds


def message(mid='1547812204580179969', body='Maintenance starts soon.'):
    return dict(id=mid, channel_id='309809230095843328', author='Miso', body=body,
                created_at='2026-09-11T03:33:06Z', links=[], images=[])


class DiscordNewsTests(unittest.IsolatedAsyncioTestCase):
    def test_only_explicit_current_game_up_opens_servers(self):
        from discord_news import is_game_up
        for body in ('Game is up!', 'Hi Maplers,\n\nThe **game is now up**!'):
            self.assertTrue(is_game_up(body))
        for body in ('Game is not up.', 'We will announce when game is up.',
                     '~~Game is up!~~ Maintenance is extended.', 'The channel maintenance has been completed.',
                     'Cash Shop is up.', '> Game is up!\nThis was incorrect.', 'Game is up?',
                     'Game is up! However, maintenance has been extended.'):
            self.assertFalse(is_game_up(body), body)

    def test_open_claim_survives_restart_and_deduplicates_cycle_and_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'news.db'
            store = NewsStore(path)
            self.assertTrue(store.claim_open('maintenance:1', '123', 10))
            store = NewsStore(path)
            self.assertFalse(store.claim_open('maintenance:1', '124', 10))
            self.assertFalse(store.claim_open('maintenance:2', '123', 10))
            self.assertTrue(store.claim_open('maintenance:1', '123', 20))
            self.assertTrue(store.claim_open('maintenance:2', '125', 10))

    async def test_api_up_never_sends_open_alert(self):
        from maple_bot import MapleNewsBot
        bot = SimpleNamespace(maintenance_watch={'saw_down': True, 'end_timestamp': 0, 'monitor_from_timestamp': 0},
                              server_status='down', fetch_server_status=AsyncMock(return_value=dict.fromkeys(('Scania', 'Bera', 'Kronos', 'Hyperion'), True)),
                              send_server_open_alert=AsyncMock(), persist_state=lambda: None)
        await MapleNewsBot.check_server_status.coro(bot)
        bot.send_server_open_alert.assert_not_awaited()

    async def test_open_notification_replay_and_second_announcement_only_mentions_once(self):
        from maple_bot import MapleNewsBot
        with tempfile.TemporaryDirectory() as folder:
            store = NewsStore(Path(folder) / 'news.db')
            channel = SimpleNamespace(id=10, send=AsyncMock())
            bot = SimpleNamespace(maintenance_watch={'post_id': 123}, server_alert_roles={'10': 99},
                                  alert_text_channels=lambda kind: [channel], persist_state=lambda: None,
                                  send_owner_dm=AsyncMock())
            row = message(body='Game is up!')
            await MapleNewsBot.send_server_open_alert(bot, row, store)
            await MapleNewsBot.send_server_open_alert(bot, row, NewsStore(store.path))
            row['id'] = '1547812204580179999'
            await MapleNewsBot.send_server_open_alert(bot, row, store)
            channel.send.assert_awaited_once()
            self.assertEqual(channel.send.call_args.kwargs['content'], '<@&99>')
            self.assertNotIn('주요 월드가 모두', channel.send.call_args.kwargs['embed'].description)

    async def test_game_up_route_does_not_require_news_subscription_or_translation(self):
        from discord_news import process_event
        with tempfile.TemporaryDirectory() as folder:
            store = NewsStore(Path(folder) / 'news.db')
            store.observe([message()], '2026-09-11T04:00:00Z')
            store.observe([message(body='Game is up!')], '2026-09-11T04:01:00Z')
            bot = SimpleNamespace(send_server_open_alert=AsyncMock())
            await process_event(bot, store, store.pending()[0], [])
            bot.send_server_open_alert.assert_awaited_once()
            self.assertFalse(store.pending())

    def test_notice_colors_follow_current_status_not_old_restrictions(self):
        cases = [
            ('The v.271 Patch Notes are available.', 0xF1C40F),
            ('Hi Maplers,\n\nWe will be having an unscheduled channel maintenance soon.', 0xE67E22),
            ('Potential resets using Cubes will be temporarily disabled.', 0xE67E22),
            ('Maintenance has been extended by two hours.', 0xE67E22),
            ('Hello Maplers,\n\nThe channel maintenance has been completed and Potential resets using Cubes has been re-enabled.\n\nCubes used before resets were temporarily disabled are refunded.', 0x2ECC71),
            ('~~Maintenance has been completed.~~\n\nMaintenance has been extended.', 0xE67E22),
            ('Maintenance has not been completed.', 0xE67E22),
        ]
        for body, expected in cases:
            with self.subTest(body=body):
                self.assertEqual(make_embeds(message(body=body), '번역')[0].colour.value, expected)

    def test_baseline_restart_edit_and_stale_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'news.db'
            store = NewsStore(path)
            self.assertEqual(store.observe([message()], '2026-09-11T04:00:00Z'), 0)
            store = NewsStore(path)
            self.assertEqual(store.observe([message(body='Maintenance completed.')], '2026-09-11T04:01:00Z'), 1)
            self.assertEqual(store.observe([message()], '2026-09-11T04:00:00Z'), 0)
            self.assertEqual(store.observe([message(body='Maintenance completed.')], '2026-09-11T04:02:00Z'), 0)
            self.assertEqual(store.pending()[0]['current']['body'], 'Maintenance completed.')

    def test_new_messages_and_image_query_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = NewsStore(Path(folder) / 'news.db')
            first = message(); first['images'] = ['https://cdn.discordapp.com/attachments/1/2/a.png?ex=1']
            store.observe([first], '2026-09-11T04:00:00Z')
            first['images'][0] = first['images'][0].replace('ex=1', 'ex=2')
            self.assertEqual(store.observe([first], '2026-09-11T04:01:00Z'), 0)
            self.assertEqual(store.observe([first, message('1547812204580179970')], '2026-09-11T04:02:00Z'), 1)

    def test_only_known_link_promotion_is_skipped(self):
        url = 'https://www.nexon.com/maplestory/news/update/44597/updated-title'
        row = message(body=f'Hi Maplers,\nThe v.271 - Patch Notes is [HERE]({url}) @News')
        row['links'] = [url]
        self.assertTrue(is_link_only_notice(row, {44597}))
        self.assertFalse(is_link_only_notice(row, set()))
        row['body'] += '\nMaintenance has been extended by 2 hours.'
        self.assertFalse(is_link_only_notice(row, {44597}))

    def test_wrong_channel_and_media_host_rejected(self):
        row = message(); row['channel_id'] = '123'
        with self.assertRaises(ValueError): validate_message(row)
        row = message(); row['images'] = ['http://localhost/private']
        with self.assertRaises(ValueError): validate_message(row)

    async def test_cash_shop_promotion_and_separate_banner_are_not_reposted(self):
        from discord_news import process_event
        url = 'https://www.nexon.com/maplestory/news/sale/44528/cash-shop-update-for-september-16'
        row = message('1549555981942398987', f'Hi Maplers! :spiritBongo:\n\nTake a look at the **Cash Shop Update** for **September 16th** [HERE]({url}), featuring Royal Styles and more!')
        row.update(links=[url], created_at='2026-09-15T23:02:15.453Z')
        self.assertTrue(is_link_only_notice(row, {44528}))
        self.assertFalse(is_link_only_notice(row, set()))
        for extra in ('\nMaintenance has been extended.', ' Cubes have been disabled.', '\nGame is up!'):
            self.assertFalse(is_link_only_notice({**row, 'body': row['body'] + extra}, {44528}))
        banner = message('1549555931342180442', '')
        banner.update(images=['https://cdn.discordapp.com/attachments/1/2/image.png'], created_at='2026-09-15T23:02:03.389Z')
        with tempfile.TemporaryDirectory() as folder:
            store = NewsStore(Path(folder) / 'news.db')
            store.observe([message()], '2026-09-15T23:00:00Z')
            store.observe([banner, row], '2026-09-15T23:02:16Z')
            bot = SimpleNamespace(sent_ids={44528})
            with patch('discord_news.deliver_all', new_callable=AsyncMock) as delivery:
                for event in store.pending():
                    await process_event(bot, store, event, [SimpleNamespace(id=1)])
                delivery.assert_not_awaited()
            self.assertFalse(store.pending())

    async def test_standalone_image_waits_then_is_preserved_after_restart(self):
        from datetime import datetime, timezone, timedelta
        from discord_news import process_event
        now = datetime.now(timezone.utc)
        banner = message('1549555931342180442', '')
        banner.update(images=['https://cdn.discordapp.com/attachments/1/2/image.png'], created_at=now.isoformat())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'news.db'
            store = NewsStore(path)
            store.observe([message()], (now - timedelta(seconds=2)).isoformat())
            store.observe([banner], now.isoformat())
            bot = SimpleNamespace(sent_ids=set())
            with patch('discord_news.deliver_all', new_callable=AsyncMock) as delivery:
                await process_event(bot, store, store.pending()[0], [SimpleNamespace(id=1)])
                delivery.assert_not_awaited()
                store = NewsStore(path)
                with patch('discord_news.datetime') as clock:
                    clock.fromisoformat = datetime.fromisoformat
                    clock.now.return_value = now + timedelta(seconds=61)
                    await process_event(bot, store, store.pending()[0], [SimpleNamespace(id=1)])
                delivery.assert_awaited_once()
                self.assertEqual(delivery.call_args.args[2]['current']['images'], banner['images'])
            self.assertFalse(store.pending())

    async def test_image_before_unknown_or_unrelated_notice_is_preserved(self):
        from discord_news import process_event
        url = 'https://www.nexon.com/maplestory/news/update/44597/title'
        for author, created, known in [('Miso', '2026-09-11T03:33:18Z', set()),
                                       ('Potaro', '2026-09-11T03:33:18Z', {44597}),
                                       ('Miso', '2026-09-11T03:35:18Z', {44597})]:
            with self.subTest(author=author, created=created, known=known), tempfile.TemporaryDirectory() as folder:
                store = NewsStore(Path(folder) / 'news.db')
                banner = message('1547812204580179970', '')
                banner['images'] = ['https://cdn.discordapp.com/attachments/1/2/image.png']
                row = message('1547812204580179971', f'Patch Notes is [HERE]({url})')
                row.update(author=author, created_at=created, links=[url])
                store.observe([message()], '2026-09-11T03:30:00Z')
                store.observe([banner, row], '2026-09-11T03:36:00Z')
                with patch('discord_news.deliver_all', new_callable=AsyncMock) as delivery:
                    await process_event(SimpleNamespace(sent_ids=known), store, store.pending()[0], [SimpleNamespace(id=1)])
                    delivery.assert_awaited_once()

    async def test_timeout_keeps_translation_alive_for_edit(self):
        ready = asyncio.Event()
        async def translate():
            await ready.wait()
            return '점검 종료'
        task = asyncio.create_task(translate())
        self.assertIsNone(await translate_or_original(task, timeout=0.01))
        self.assertFalse(task.cancelled())
        ready.set()
        self.assertEqual(await task, '점검 종료')

    async def test_raw_message_is_edited_and_channel_failure_is_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            store = NewsStore(Path(folder) / 'news.db')
            store.observe([message()], '2026-09-11T04:00:00Z')
            store.observe([message('1547812204580179970')], '2026-09-11T04:01:00Z')
            event = store.pending()[0]
            posted = SimpleNamespace(id=99, edit=AsyncMock())
            good = SimpleNamespace(id=1, send=AsyncMock(return_value=posted), get_partial_message=lambda mid: posted)
            bad = SimpleNamespace(id=2, send=AsyncMock(side_effect=RuntimeError('offline')))
            with self.assertRaises(RuntimeError):
                await deliver_all(None, store, event, 'Original', False, [bad, good])
            self.assertEqual(good.send.call_args.kwargs['embeds'][0].description, 'Original')
            await deliver_all(None, store, event, '번역 완료', True, [good])
            self.assertEqual(posted.edit.call_args.kwargs['embeds'][0].description, '번역 완료')
            good.send.assert_awaited_once()
            await deliver_all(None, store, event, '번역 완료', True, [good])
            posted.edit.assert_awaited_once()

    async def test_shadow_consumes_events_without_posting(self):
        with tempfile.TemporaryDirectory() as folder:
            relay = NewsRelay(Path(folder) / 'inbox', Path(folder) / 'news.db')
            relay.store.observe([message()], '2026-09-11T04:00:00Z')
            relay.store.observe([message('1547812204580179970')], '2026-09-11T04:01:00Z')
            bot = SimpleNamespace(send_owner_dm=AsyncMock())
            channel = SimpleNamespace(send=AsyncMock())
            await relay.tick(bot, [channel])
            channel.send.assert_not_awaited()
            self.assertEqual(relay.store.pending(), [])


if __name__ == '__main__':
    unittest.main()
