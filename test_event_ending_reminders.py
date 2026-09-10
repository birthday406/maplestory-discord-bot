import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import maple_bot as bot


class CashEndingReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_ended_alert_skips_past_and_sends_armed_event_once(self):
        channel = Mock(spec=discord.TextChannel)
        channel.send = AsyncMock()
        client = SimpleNamespace(get_channel=lambda _: channel, persist_state=Mock())
        old = {'start_timestamp': 1, 'end_timestamp': 100}
        await bot.send_cash_transfer_ended_alert(client, old, {42}, 101)
        channel.send.assert_not_awaited()
        event = {'start_timestamp': 200, 'end_timestamp': 300}
        await bot.send_cash_transfer_ended_alert(client, event, {42}, 299)
        channel.send.assert_not_awaited()
        # 공지 재수집으로 객체가 교체되어도 감시·전송 기록은 유지됩니다.
        event = bot.merge_patch_events(
            {'cash_shop_transfer': event},
            {'post_id': 2, 'cash_shop_transfer': {'start_timestamp': 200, 'end_timestamp': 300}},
        )['cash_shop_transfer']
        for now in (300, 301):
            await bot.send_cash_transfer_ended_alert(client, event, {42}, now)
        channel.send.assert_awaited_once()
        self.assertIn('캐시이동 이벤트가 종료되었습니다.', channel.send.await_args.args[0])
        self.assertEqual(event['ended_notified']['300'], [42])

    async def test_ended_alert_retries_failure_and_skips_unseen_changed_end(self):
        channel = Mock(spec=discord.TextChannel)
        channel.send = AsyncMock(side_effect=discord.HTTPException(
            SimpleNamespace(status=503, reason='Unavailable'), 'retry'))
        client = SimpleNamespace(get_channel=lambda _: channel, persist_state=Mock())
        event = {'start_timestamp': 1, 'end_timestamp': 300}
        await bot.send_cash_transfer_ended_alert(client, event, {42}, 299)
        with self.assertLogs(level='ERROR'):
            await bot.send_cash_transfer_ended_alert(client, event, {42}, 300)
        self.assertNotIn(42, event['ended_notified']['300'])
        channel.send.side_effect = None
        await bot.send_cash_transfer_ended_alert(client, event, {42}, 301)
        self.assertEqual(event['ended_notified']['300'], [42])
        event['end_timestamp'] = 250
        await bot.send_cash_transfer_ended_alert(client, event, {42}, 302)
        self.assertEqual(channel.send.await_count, 2)

    async def test_real_attachment_is_sent_closed_and_not_sent_twice(self):
        event = {'start_timestamp': 1, 'end_timestamp': 100000}
        channel = Mock(spec=discord.TextChannel)
        files = []

        async def send(**payload):
            attachment = payload['file']
            self.assertFalse(attachment.fp.closed)
            self.assertTrue(attachment.fp.read(8).startswith(b'\x89PNG'))
            self.assertIn('종료 임박', payload['embed'].title)
            files.append(attachment)

        channel.send = AsyncMock(side_effect=send)
        client = SimpleNamespace(get_channel=lambda _: channel, persist_state=Mock(),
            patch_events={'url': 'https://example.com', 'cash_shop_transfer': event})
        for _ in range(2):
            await bot.send_event_ending_reminders(client, event, {42}, '캐시이동', 86400, 90000)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].fp.closed)
        self.assertEqual(event['ending_notified']['100000'], [42])
        client.persist_state.assert_called_once()

    async def test_failed_send_closes_attachment_and_remains_retryable(self):
        event = {'start_timestamp': 1, 'end_timestamp': 100000}
        channel = Mock(spec=discord.TextChannel)
        files = []

        async def send(**payload):
            files.append(payload['file'])
            raise discord.HTTPException(SimpleNamespace(status=503, reason='Unavailable'), 'retry')

        channel.send = AsyncMock(side_effect=send)
        client = SimpleNamespace(get_channel=lambda _: channel, persist_state=Mock(),
            patch_events={'url': 'https://example.com', 'cash_shop_transfer': event})
        with self.assertLogs(level='ERROR'):
            for _ in range(2):
                await bot.send_event_ending_reminders(client, event, {42}, '캐시이동', 86400, 90000)
        self.assertEqual(len(files), 2)
        self.assertTrue(all(file.fp.closed for file in files))
        self.assertEqual(event['ending_notified']['100000'], [])
        client.persist_state.assert_not_called()
