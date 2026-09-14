import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import maple_bot


class EndingReminderTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_loops_send_ending_reminders(self):
        for kind, lead, loop in (
            (maple_bot.ALERT_CASH_TRANSFER, 86400, maple_bot.MapleNewsBot.check_cash_shop_transfer),
            (maple_bot.ALERT_MIRACLE_TIME, 3600, maple_bot.MapleNewsBot.check_miracle_time),
        ):
            event = {"start_timestamp":1, "end_timestamp":200000, "notified_channel_ids":[1]}
            schedule = {"cash_shop_transfer":event} if kind == maple_bot.ALERT_CASH_TRANSFER else {"miracle_time":[event]}
            schedule["url"] = "https://example.com/patch"
            channel = SimpleNamespace(send=AsyncMock())
            bot = SimpleNamespace(patch_events=schedule, sent_ids={1}, alert_channels={kind:{1}},
                                  get_channel=lambda _:channel, persist_state=Mock())
            with patch.object(maple_bot.discord, "TextChannel", SimpleNamespace), patch("maple_bot.datetime") as clock, patch("maple_bot.discord.File") as attachment:
                clock.now.return_value.timestamp.return_value = 200000-lead
                await loop.coro(bot)
                await loop.coro(bot)
            channel.send.assert_awaited_once()
            self.assertEqual(event["ending_notified"], {"200000":[1]})
            if kind == maple_bot.ALERT_CASH_TRANSFER:
                sent = channel.send.await_args.kwargs
                self.assertIn("종료 임박", sent["embed"].title)
                self.assertIn("참여 조건", sent["embed"].description)
                self.assertIn("<t:200000:R>", sent["embed"].description)
                self.assertEqual(sent["embed"].thumbnail.url, "attachment://" + maple_bot.CASH_SHOP_TRANSFER_IMAGE_PATH.name)
                self.assertIs(sent["file"], attachment.return_value)
                attachment.return_value.close.assert_called_once()

    async def test_boundaries_restart_and_both_lead_times(self):
        for lead in (3600, 86400):
            event = {"start_timestamp": 1, "end_timestamp": 200000}
            channel = SimpleNamespace(send=AsyncMock())
            client = SimpleNamespace(get_channel=lambda _: channel, persist_state=Mock())
            with patch.object(maple_bot.discord, "TextChannel", SimpleNamespace):
                for now in (200000-lead-1, 200000, 200001):
                    await maple_bot.send_event_ending_reminders(client, event, {1}, "이벤트", lead, now)
                channel.send.assert_not_awaited()
                await maple_bot.send_event_ending_reminders(client, event, {1}, "이벤트", lead, 200000-lead)
                restored = json.loads(json.dumps(event))
                await maple_bot.send_event_ending_reminders(client, restored, {1}, "이벤트", lead, 199999)
                channel.send.assert_awaited_once()
                client.persist_state.assert_called_once()
                self.assertIn("<t:200000:R>", channel.send.await_args.args[0])

    async def test_one_channel_failure_retries_only_failed_channel(self):
        error = discord.HTTPException(SimpleNamespace(status=500, reason="error"), "failed")
        first, second = SimpleNamespace(send=AsyncMock(side_effect=error)), SimpleNamespace(send=AsyncMock())
        client = SimpleNamespace(get_channel=lambda key: {1:first, 2:second}[key], persist_state=Mock())
        event = {"start_timestamp": 1, "end_timestamp": 10000}
        with patch.object(maple_bot.discord, "TextChannel", SimpleNamespace):
            with self.assertLogs(level="ERROR"):
                await maple_bot.send_event_ending_reminders(client, event, {1,2}, "미라클", 3600, 9000)
            first.send.side_effect = None
            await maple_bot.send_event_ending_reminders(client, event, {1,2}, "미라클", 3600, 9001)
        self.assertEqual(first.send.await_count, 2)
        second.send.assert_awaited_once()

    async def test_not_started_short_event_is_not_announced(self):
        client = SimpleNamespace(get_channel=Mock(), persist_state=Mock())
        await maple_bot.send_event_ending_reminders(
            client, {"start_timestamp": 100, "end_timestamp": 200}, {1}, "미라클", 3600, 99)
        client.get_channel.assert_not_called()

    def test_patch_refresh_preserves_history_but_rescheduled_end_can_notify(self):
        current = {"post_id":1, "cash_shop_transfer":{
            "start_timestamp":1, "end_timestamp":200000, "ending_notified":{"200000":[1]}},
            "miracle_time":[{"start_timestamp":1,"end_timestamp":200000,"equipment":"모자",
                             "ending_notified":{"200000":[2]}}]}
        for post_id in (1, 2):
            updated = {"post_id":post_id,"cash_shop_transfer":{"start_timestamp":1,"end_timestamp":210000},
                       "miracle_time":[{"start_timestamp":1,"end_timestamp":200000,"equipment":"모자"}]}
            merged = maple_bot.merge_patch_events(current, updated)
            self.assertEqual(merged["cash_shop_transfer"]["ending_notified"], {"200000":[1]})
            self.assertNotIn("210000", merged["cash_shop_transfer"]["ending_notified"])
            self.assertEqual(merged["miracle_time"][0]["ending_notified"], {"200000":[2]})
