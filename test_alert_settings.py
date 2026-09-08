import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import maple_bot


class AlertSettingsTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, channels=None, settings=None, roles=None):
        channels = channels or {}
        roles = roles or {}
        return SimpleNamespace(
            guild=SimpleNamespace(
                get_channel=channels.get,
                get_role=roles.get,
            ),
            permissions=SimpleNamespace(administrator=True),
            client=SimpleNamespace(alert_channels=settings or {}, server_alert_roles={}),
            response=SimpleNamespace(send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_rejects_dm_and_non_admin_before_reading_settings(self):
        for is_dm in (False, True):
            interaction = self.interaction()
            if is_dm:
                interaction.guild = None
            else:
                interaction.permissions.administrator = False
            interaction.client = None
            await maple_bot.alert_settings_command.callback(interaction)
            interaction.response.send_message.assert_awaited_once_with(
                "이 명령어는 서버 관리자만 사용할 수 있습니다.", ephemeral=True
            )

    async def test_current_guild_only_roles_and_no_state_changes(self):
        # 999는 다른 서버 채널, 888은 삭제된 채널을 나타냅니다.
        settings = {
            maple_bot.ALERT_NEWS: {111, 222, 888, 999},
            maple_bot.ALERT_SERVER: {111, 222},
            maple_bot.INFO_UTC: {333},
        }
        interaction = self.interaction(
            {i: SimpleNamespace(mention=f"<#{i}>") for i in (111, 222, 333)},
            settings,
            {444: SimpleNamespace(mention="<@&444>")},
        )
        interaction.client.server_alert_roles = {"111": 444, "222": 555, "999": 666}
        before = copy.deepcopy(vars(interaction.client))
        await maple_bot.alert_settings_command.callback(interaction)
        call = interaction.response.send_message.await_args
        content = call.args[0]
        for expected in ("<#111>", "<#222>", "<#333>", "<@&444>"):
            self.assertIn(expected, content)
        self.assertIn("미설정 또는 확인 불가", content)
        self.assertIn("등록된 채널 없음", content)
        for hidden in ("999", "888", "666", "555"):
            self.assertNotIn(hidden, content)
        self.assertTrue(call.kwargs["ephemeral"])
        self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        self.assertEqual(vars(interaction.client), before)

    async def test_empty_settings_and_large_list(self):
        interaction = self.interaction()
        await maple_bot.alert_settings_command.callback(interaction)
        self.assertEqual(
            interaction.response.send_message.await_args.args[0].count("등록된 채널 없음"),
            len(maple_bot.ALERT_TYPES),
        )
        channels = {
            i: SimpleNamespace(mention=f"<#{i}>")
            for i in range(100000000000000000, 100000000000000300)
        }
        interaction = self.interaction(channels, {maple_bot.ALERT_NEWS: set(channels)})
        await maple_bot.alert_settings_command.callback(interaction)
        calls = [interaction.response.send_message.await_args]
        calls.extend(interaction.followup.send.await_args_list)
        self.assertGreater(len(calls), 1)
        for call in calls:
            self.assertLessEqual(len(call.args[0]), 1900)
            self.assertTrue(call.kwargs["ephemeral"])
            self.assertEqual(call.kwargs["allowed_mentions"].to_dict(), {"parse": []})
        content = "".join(call.args[0] for call in calls)
        for channel in channels.values():
            self.assertEqual(content.count(channel.mention), 1)

    async def test_command_registered_with_admin_and_guild_restrictions(self):
        command = maple_bot.alert_settings_command
        self.assertTrue(command.guild_only)
        self.assertTrue(command.default_permissions.administrator)
        self.assertFalse(command.allowed_installs.user)
        bot = SimpleNamespace(
            tree=SimpleNamespace(
                set_translator=AsyncMock(), add_command=Mock(), sync=AsyncMock()
            ),
            add_command=Mock(),
            persist_state=Mock(),
        )
        with patch.object(maple_bot.aiohttp, "ClientSession"):
            await maple_bot.MapleNewsBot.setup_hook(bot)
        bot.tree.add_command.assert_any_call(command)
