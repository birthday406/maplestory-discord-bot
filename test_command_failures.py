import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import maple_bot


class CommandFailureTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, client):
        return SimpleNamespace(client=client, user=SimpleNamespace(id=123),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()))

    async def test_patch_defers_before_network_and_keeps_link_on_image_timeout(self):
        bot = object.__new__(maple_bot.MapleNewsBot)
        bot.latest_patch = None
        bot.session = SimpleNamespace(get=Mock(side_effect=asyncio.TimeoutError()))
        interaction = self.interaction(bot)
        async def posts():
            self.assertTrue(interaction.response.defer.called)
            return [{"id": 123, "category": "update", "name": "v.270 Patch Notes", "imageThumbnail": "/test.jpg"}]
        bot.fetch_posts = posts
        await maple_bot.patch_command.callback(interaction)
        sent = interaction.followup.send.call_args
        self.assertIn("/123/", sent.args[0])
        self.assertIsNone(sent.kwargs["file"])

    async def test_patch_listing_timeout_and_empty_list_have_user_message(self):
        for fetch in (AsyncMock(side_effect=asyncio.TimeoutError()), AsyncMock(return_value=[])):
            interaction = self.interaction(SimpleNamespace(latest_patch=None, fetch_posts=fetch))
            await maple_bot.patch_command.callback(interaction)
            self.assertTrue(interaction.response.defer.called)
            self.assertIn("찾지 못했습니다", interaction.followup.send.call_args.args[0])

    async def test_async_timeouts_are_reported_for_server_and_pssb(self):
        for command, args in ((maple_bot.server_status_command, ()), (maple_bot.pssb_command, (SimpleNamespace(value=1),))):
            interaction = self.interaction(SimpleNamespace(
                fetch_server_status=AsyncMock(side_effect=asyncio.TimeoutError()),
                fetch_pssb_rates=AsyncMock(side_effect=asyncio.TimeoutError())))
            await command.callback(interaction, *args)
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        view = maple_bot.PssbSimulatorView(123, 1, [])
        await view.reroll.callback(interaction)
        self.assertIn("불러오지 못했습니다", interaction.followup.send.call_args.args[0])

    async def test_downloader_returns_none_on_python310_timeout(self):
        bot = object.__new__(maple_bot.MapleNewsBot)
        bot.session = SimpleNamespace(get=Mock(side_effect=asyncio.TimeoutError()))
        self.assertIsNone(await bot.fetch_character_image("https://example.com/image.png"))
