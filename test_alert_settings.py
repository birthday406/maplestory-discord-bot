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
        command = maple_bot.channel_settings_command
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
        registered = [call.args[0].name for call in bot.tree.add_command.call_args_list]
        self.assertNotIn("news-alert", registered)
        self.assertNotIn("alert-settings", registered)
        self.assertNotIn("info-channel", registered)


class UnifiedChannelSettingsTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self):
        return SimpleNamespace(guild=SimpleNamespace(id=1), permissions=SimpleNamespace(administrator=True),
            client=SimpleNamespace(configure_alert_channel=AsyncMock(), configure_info_channel=AsyncMock()),
            response=SimpleNamespace(send_message=AsyncMock()))

    async def test_each_kind_routes_to_existing_storage(self):
        for kind in maple_bot.CHANNEL_SETTING_TYPES:
            for action in ("on", "off"):
                interaction = self.interaction()
                info = kind in {maple_bot.INFO_TIME, maple_bot.INFO_UTC, maple_bot.INFO_EXCHANGE}
                channel = Mock(spec=maple_bot.discord.VoiceChannel if info else maple_bot.discord.TextChannel)
                await maple_bot.channel_settings_command.callback(interaction,
                    maple_bot.app_commands.Choice(name=kind,value=kind), channel,
                    maple_bot.app_commands.Choice(name=action,value=action))
                if info:
                    interaction.client.configure_info_channel.assert_awaited_once_with(interaction,channel,kind,action=="on")
                    interaction.client.configure_alert_channel.assert_not_called()
                else:
                    interaction.client.configure_alert_channel.assert_awaited_once_with(interaction,channel,action=="on",kind,maple_bot.CHANNEL_SETTING_TYPES[kind],None)
                    interaction.client.configure_info_channel.assert_not_called()

    async def test_empty_command_is_read_only_and_invalid_input_never_saves(self):
        interaction = self.interaction()
        with patch.object(maple_bot.alert_settings_command, '_callback', new=AsyncMock()) as status:
            await maple_bot.channel_settings_command.callback(interaction)
            status.assert_awaited_once_with(interaction)
        choice = maple_bot.app_commands.Choice
        for kwargs in ({'kind':choice(name='공지',value=maple_bot.ALERT_NEWS)},
                       {'kind':choice(name='공지',value=maple_bot.ALERT_NEWS),'channel':Mock(spec=maple_bot.discord.VoiceChannel),'action':choice(name='켜기',value='on')},
                       {'kind':choice(name='UTC',value=maple_bot.INFO_UTC),'channel':Mock(spec=maple_bot.discord.TextChannel),'action':choice(name='켜기',value='on')}):
            await maple_bot.channel_settings_command.callback(interaction,**kwargs)
        interaction.client.configure_alert_channel.assert_not_called()
        interaction.client.configure_info_channel.assert_not_called()
        self.assertEqual(interaction.response.send_message.await_count,3)
        for guild,admin in ((None,True),(object(),False)):
            interaction = self.interaction();interaction.guild=guild;interaction.permissions.administrator=admin
            await maple_bot.channel_settings_command.callback(interaction)
            interaction.client.configure_alert_channel.assert_not_called()
            self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])

    async def test_server_role_is_forwarded_and_unrelated_role_rejected(self):
        interaction=self.interaction();channel=Mock(spec=maple_bot.discord.TextChannel);role=object()
        choice=maple_bot.app_commands.Choice
        await maple_bot.channel_settings_command.callback(interaction,choice(name='서버',value=maple_bot.ALERT_SERVER),channel,choice(name='켜기',value='on'),role)
        self.assertIs(interaction.client.configure_alert_channel.call_args.args[-1],role)
        interaction.client.configure_alert_channel.reset_mock()
        await maple_bot.channel_settings_command.callback(interaction,choice(name='공지',value=maple_bot.ALERT_NEWS),channel,choice(name='켜기',value='on'),role)
        interaction.client.configure_alert_channel.assert_not_called()
