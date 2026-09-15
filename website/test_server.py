"""실제 Discord 요청·비밀정보 없이 로그인 경계를 검증합니다."""
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import discord
from aiohttp.test_utils import AioHTTPTestCase
from website.server import create_app, is_admin, ORIGIN


class LoginTests(AioHTTPTestCase):
    async def get_application(self):
        self.remote = AsyncMock()
        return create_app("example-id", "example-secret", self.remote)

    async def request(self, method, path, **kwargs):
        headers = {"Host": "127.0.0.1:8766", **kwargs.pop("headers", {})}
        return await self.client.request(method, path, headers=headers, allow_redirects=False, **kwargs)

    async def authorize(self):
        start = await self.request("GET", "/auth/login")
        query = parse_qs(urlsplit(start.headers['Location']).query)
        self.assertEqual(query['scope'], ['identify guilds'])
        self.remote.side_effect = [
            {"access_token":"fake-access", "expires_in":3600},
            {"id":"123", "username":"sample"},
        ]
        res = await self.request("GET", "/auth/callback?" + "state=" + query['state'][0] + "&code=fake-code",
                                 cookies={"sherbet_login":start.cookies['sherbet_login'].value})
        self.assertEqual(res.status, 302)
        self.assertTrue(res.cookies['sherbet_session']['httponly'])
        self.assertEqual(res.cookies['sherbet_session']['samesite'], 'Lax')
        return {'sherbet_session':res.cookies['sherbet_session'].value}

    async def test_invalid_state_cannot_exchange_code(self):
        res = await self.request('GET','/auth/callback?state=wrong&code=anything')
        self.assertEqual(res.status,400)
        self.remote.assert_not_called()

    async def test_guilds_require_login(self):
        self.assertEqual((await self.request('GET','/api/guilds')).status,401)

    async def test_session_does_not_expose_token_and_logout_needs_csrf(self):
        cookies = await self.authorize()
        res = await self.request('GET','/api/session',cookies=cookies)
        data = await res.json()
        self.assertNotIn('fake-access', await res.text())
        self.assertEqual(data['user']['name'], 'sample')
        denied = await self.request('POST','/auth/logout',cookies=cookies)
        self.assertEqual(denied.status,403)
        out = await self.request('POST','/auth/logout',cookies=cookies,
                                 headers={'Origin':ORIGIN,'X-CSRF-Token':data['csrf']})
        self.assertEqual(out.status,200)
        self.assertEqual((await self.request('GET','/api/guilds',cookies=cookies)).status,401)

    async def test_only_admin_guilds_returned(self):
        cookies = await self.authorize()
        self.remote.side_effect = [[{'id':'1','name':'owner','owner':True},
                                    {'id':'2','name':'admin','permissions':'8'},
                                    {'id':'3','name':'manager','permissions':'32'}]]
        res = await self.request('GET','/api/guilds',cookies=cookies)
        self.assertEqual([g['id'] for g in (await res.json())['guilds']],['1','2'])

    async def test_private_files_and_host_blocked(self):
        for path in ('/.env','/server.py','/state.json'):
            self.assertEqual((await self.request('GET',path)).status,404)
        res = await self.client.get('/api/session',headers={'Host':'attacker.example'})
        self.assertEqual(res.status,403)

    async def test_callback_is_single_use(self):
        await self.authorize()
        self.remote.reset_mock()
        res = await self.request('GET','/auth/callback?state=replay&code=fake')
        self.assertEqual(res.status,400)
        self.remote.assert_not_called()


class UnconfiguredTests(AioHTTPTestCase):
    async def get_application(self):
        return create_app()

    async def test_not_ready_no_redirect_to_discord(self):
        res = await self.client.get('/api/session',headers={'Host':'127.0.0.1:8766'})
        self.assertFalse((await res.json())['ready'])
        res = await self.client.get('/auth/login',headers={'Host':'127.0.0.1:8766'},allow_redirects=False)
        self.assertEqual(res.headers['Location'],'/#account')


class GuildDetailTests(LoginTests):
    async def get_application(self):
        self.remote = AsyncMock()
        self.snapshot = AsyncMock(return_value={'guild':{'id':'1','name':'sample'},'channels':[], 'readOnly':True})
        return create_app('example-id','example-secret',self.remote,guild_snapshot=self.snapshot)

    async def test_admin_is_rechecked_before_bot_access(self):
        cookies = await self.authorize()
        self.remote.side_effect = [[{'id':'1','name':'sample','permissions':'0'}]]
        res = await self.request('GET','/api/guilds/1',cookies=cookies)
        self.assertEqual(res.status,403)
        self.snapshot.assert_not_called()

    async def test_authorized_read_and_no_write_route(self):
        cookies = await self.authorize()
        self.remote.side_effect = [[{'id':'1','name':'sample','permissions':'8'}]]
        res = await self.request('GET','/api/guilds/1',cookies=cookies)
        self.assertEqual(res.status,200)
        self.assertTrue((await res.json())['readOnly'])
        self.snapshot.assert_awaited_once_with(1)
        self.assertEqual((await self.request('POST','/api/guilds/1',cookies=cookies)).status,405)


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_guild_settings_are_returned(self):
        from website.bot_view import guild_snapshot
        channel = Mock(spec=discord.TextChannel)
        channel.id, channel.name, channel.position = 11, '공지', 0
        channel.permissions_for.return_value = discord.Permissions.all()
        guild = Mock()
        guild.id, guild.name = 1, '길드'
        guild.fetch_channels = AsyncMock(return_value=[channel])
        guild.fetch_member = AsyncMock(return_value=object())
        guild.get_role.return_value = SimpleNamespace(name='알림')
        bot = Mock()
        bot.user = SimpleNamespace(id=123)
        bot.is_ready.return_value = True
        bot.fetch_guild = AsyncMock(return_value=guild)
        bot.alert_channels = {'news':{11,99}, 'sunny':{99}}
        bot.server_alert_roles = {'11':7,'99':8}
        result = await guild_snapshot(bot,1,{'news':'공지','sunny':'썬데이'})
        self.assertEqual(result['channels'][0]['enabled'],['공지'])
        self.assertEqual([c['id'] for c in result['channels']],['11'])
        self.assertEqual(bot.alert_channels, {'news':{11,99},'sunny':{99}})

    async def test_disconnected_bot_does_not_return_empty_settings(self):
        from website.bot_view import guild_snapshot
        from aiohttp import web
        bot = Mock()
        bot.is_ready.return_value=False
        with self.assertRaises(web.HTTPServiceUnavailable):
            await guild_snapshot(bot,1,{})

    async def test_disabled_website_does_not_start(self):
        from website.bot_view import start_website
        from unittest.mock import patch
        with patch.dict('os.environ', {'SHERBET_WEBSITE_ENABLED':'0'}):
            self.assertIsNone(await start_website(Mock(),{}))


if __name__ == '__main__':
    unittest.main()
