import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import maple_bot


class EventNoticeTests(unittest.IsolatedAsyncioTestCase):
    hot = '<p>All giveaway start at 12:00 AM UTC on the noted day, and end at 12:00 AM UTC on the following day.</p><table><tr><th>Hot Week Rewards</th></tr><tr><td>Monday, February 9, 2026<br>Monday, February 16, 2026</td><td>Hot Week Box: VIP Booster (x3)</td></tr></table>'
    sale = '<h1>Glowing and Bright Cubes</h1><p>PDT (UTC -7): Saturday, March 14, 2026 1:00 AM - Monday, March 16, 2026 12:59 AM</p><p>Available in Heroic worlds only</p><p>Price: <s>360,000,000 mesos</s> 270,000,000 mesos</p>'

    def test_hotweek_uses_each_reward_day_not_box_expiry(self):
        entries, uncertain = maple_bot.extract_event_notice("Hot Weeks!", self.hot, "hot_week")
        self.assertFalse(uncertain)
        self.assertEqual(len(entries), 2)
        self.assertEqual(datetime.fromtimestamp(entries[0]["start"], timezone.utc).isoformat(), "2026-02-09T00:00:00+00:00")
        self.assertEqual(entries[0]["end"] - entries[0]["start"], 86400)
        self.assertIn("VIP Booster", entries[0]["detail"])
        list_source = self.hot[:self.hot.index("<table>")] + '<ul><li><strong>Monday, January 27, 2025: Hot Week Box - Mon</strong><ul><li>VIP Booster (3)</li></ul></li></ul>'
        listed, unknown = maple_bot.extract_event_notice("Hot Weeks and Bonus Cube Daily Deal!", list_source, "hot_week")
        self.assertFalse(unknown)
        self.assertEqual(len(listed), 1)

    def test_cube_discount_converts_timezone_and_excludes_special_cubes(self):
        entries, uncertain = maple_bot.extract_event_notice("March 14 Cube Daily Deals!", self.sale, "cube_sale")
        self.assertFalse(uncertain)
        self.assertEqual(datetime.fromtimestamp(entries[0]["start"], timezone.utc).isoformat(), "2026-03-14T08:00:00+00:00")
        special = self.sale.replace("Glowing and Bright Cubes", "Violet Cube Sale")
        self.assertEqual(maple_bot.extract_event_notice("Violet Cube Daily Deal!", special, "cube_sale"), ([], False))
        self.assertEqual(maple_bot.extract_event_notice("Hot Weeks and Bonus Cube Daily Deal!", self.sale.replace("Glowing and Bright Cubes", "Bonus Bright Cube Daily Deal"), "cube_sale"), ([], False))
        self.assertTrue(maple_bot.extract_event_notice("Cube Sale", "<h1>Glowing and Bright Cubes</h1><p>new date format</p>", "cube_sale")[1])

    async def test_live_future_expired_and_network_failure_are_distinct(self):
        client = SimpleNamespace(fetch_posts=AsyncMock(return_value=[{
            "id": 123, "name": "March 14 Cube Daily Deals!", "category": "sale", "summary": "", "liveDate": "2026-03-12T16:00:00Z"
        }]), fetch_post_detail=AsyncMock(return_value={"body": self.sale}))
        entries, uncertain = await maple_bot.fetch_event_notices(client, "cube_sale")
        self.assertFalse(uncertain)
        before = maple_bot.build_event_notice_embed("cube_sale", entries, False, entries[0]["start"] - 1)
        after = maple_bot.build_event_notice_embed("cube_sale", entries, False, entries[0]["end"])
        self.assertIn("예정", before.fields[0].name)
        self.assertIn("없습니다", after.description)
        await maple_bot.fetch_event_notices(client, "cube_sale")
        self.assertEqual(client.fetch_posts.await_count, 1)
        interaction = SimpleNamespace(client=SimpleNamespace(fetch_posts=AsyncMock(side_effect=TimeoutError())),
            response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        await maple_bot.cube_sale_command.callback(interaction)
        self.assertIn("확인하지 못했습니다", interaction.followup.send.call_args.kwargs["embed"].description)
