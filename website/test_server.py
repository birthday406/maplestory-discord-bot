"""실제 Discord 요청·비밀정보 없이 로그인 경계를 검증합니다."""
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace
import discord
from aiohttp.test_utils import AioHTTPTestCase
from website.server import create_app, is_admin, ORIGIN


class LoginTests(AioHTTPTestCase):
    async def test_login_limit_recovers_and_static_stays_available(self):
        for _ in range(10):
            self.assertEqual((await self.request('GET', '/auth/login')).status, 302)
        blocked = await self.request('GET', '/auth/login')
        self.assertEqual(blocked.status, 429)
        self.assertIn('Retry-After', blocked.headers)
        self.assertEqual((await self.request('GET', '/updates.json')).status, 200)
        import time
        later = time.monotonic() + 61
        with patch('website.server.monotonic', return_value=later):
            self.assertEqual((await self.request('GET', '/auth/login')).status, 302)

    async def test_public_updates_without_login(self):
        response = await self.request('GET', '/updates.json')
        self.assertEqual(response.status, 200)
        entries = await response.json()
        self.assertGreater(len(entries), 0)
        self.assertEqual([e['date'] for e in entries], sorted([e['date'] for e in entries], reverse=True))
        self.assertTrue(all(e['title'] and e['items'] for e in entries))
        self.remote.assert_not_awaited()

    async def test_public_policies_without_discord_login(self):
        response = await self.request('GET', '/policies.json')
        self.assertEqual(response.status, 200)
        data = await response.json()
        self.assertIn('sections', data['privacy'])
        self.assertIn('sections', data['terms'])
        self.remote.assert_not_awaited()

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
        self.save = AsyncMock(return_value={'enabled':True})
        return create_app('example-id','example-secret',self.remote,guild_snapshot=self.snapshot,save_news=self.save)

    async def test_save_requires_csrf_and_current_admin(self):
        cookies=await self.authorize()
        res=await self.request('GET','/api/session',cookies=cookies)
        csrf=(await res.json())['csrf']
        payload={'channelId':'11','enabled':True,'previous':False}
        denied=await self.request('PATCH','/api/guilds/1/news',cookies=cookies,json=payload)
        self.assertEqual(denied.status,403)
        self.save.assert_not_called()
        self.remote.side_effect=[[{'id':'1','name':'test','permissions':'0'}]]
        denied=await self.request('PATCH','/api/guilds/1/news',cookies=cookies,json=payload,headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
        self.assertEqual(denied.status,403)
        self.save.assert_not_called()
        self.remote.side_effect=[[{'id':'1','name':'test','permissions':'8'}]]
        saved=await self.request('PATCH','/api/guilds/1/news',cookies=cookies,json=payload,headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
        self.assertEqual(saved.status,200)
        self.save.assert_awaited_once_with(1,11,True,False)

    async def test_save_rejects_string_booleans_and_unknown_fields(self):
        cookies=await self.authorize()
        res=await self.request('GET','/api/session',cookies=cookies)
        csrf=(await res.json())['csrf']
        for payload in ({'channelId':'11','enabled':'false','previous':False},
                        {'channelId':'11','enabled':True,'previous':False,'guildId':'2'}):
            res=await self.request('PATCH','/api/guilds/1/news',cookies=cookies,json=payload,headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
            self.assertEqual(res.status,400)
        self.save.assert_not_called()

    async def test_alert_route_rejects_unsupported_setting(self):
        cookies=await self.authorize()
        session=await self.request('GET','/api/session',cookies=cookies)
        csrf=(await session.json())['csrf']
        for kind, expected in [('server',400),('news',200),('miracle_time',503)]:
            self.remote.side_effect=[[{'id':'1','name':'test','permissions':'8'}]]
            result=await self.request('PATCH','/api/guilds/1/alerts/'+kind,cookies=cookies,
                json={'channelId':'11','enabled':True,'previous':False},
                headers={'Origin':ORIGIN,'X-CSRF-Token':csrf})
            self.assertEqual(result.status,expected)

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
    async def test_simple_alerts_preserve_other_settings_and_require_attachments(self):
        from channel_settings import set_news_alert, SIMPLE_ALERTS
        bot=SimpleNamespace(alert_channels={k:{99} for k in SIMPLE_ALERTS},persist_state=Mock())
        permissions=discord.Permissions.all()
        permissions.attach_files=False
        for kind in ('cash_transfer','ursus'):
            with self.assertRaises(PermissionError):
                set_news_alert(bot,11,True,permissions,kind=kind)
        set_news_alert(bot,11,True,permissions,kind='miracle_time')
        self.assertEqual(bot.alert_channels['miracle_time'],{11,99})
        self.assertEqual(bot.alert_channels['news'],{99})
        with self.assertRaises(ValueError):
            set_news_alert(bot,11,True,permissions,kind='server')

    async def test_save_rejects_channel_from_another_guild(self):
        from website.bot_view import save_news_setting
        from aiohttp import web
        guild=Mock()
        guild.fetch_channels=AsyncMock(return_value=[])
        bot=Mock()
        bot.is_ready.return_value=True
        bot.fetch_guild=AsyncMock(return_value=guild)
        with self.assertRaises(web.HTTPBadRequest):
            await save_news_setting(bot,1,999,True,False)
        bot.persist_state.assert_not_called()

    async def test_news_enable_checks_permissions_but_disable_is_allowed(self):
        from channel_settings import set_news_alert
        bot=SimpleNamespace(alert_channels={'news':{11}},persist_state=Mock())
        with self.assertRaises(PermissionError):
            set_news_alert(bot,12,True,discord.Permissions.none())
        self.assertEqual(bot.alert_channels['news'],{11})
        set_news_alert(bot,11,False,discord.Permissions.none())
        self.assertEqual(bot.alert_channels['news'],set())

    async def test_failed_file_replace_keeps_previous_state(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        import maple_bot
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'
            path.write_text('{"previous":true}')
            with patch.object(maple_bot,'STATE_PATH',path), patch.object(maple_bot.os,'replace',side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    maple_bot.save_state(set(),set(),None,{'news':{11}})
            self.assertEqual(path.read_text(),'{"previous":true}')

    async def test_news_save_rolls_back_memory_and_rejects_stale_change(self):
        from channel_settings import set_news_alert, SettingConflict
        bot = SimpleNamespace(alert_channels={'news':{99}}, persist_state=Mock())
        permissions = discord.Permissions.all()
        set_news_alert(bot,11,True,permissions,expected=False)
        self.assertEqual(bot.alert_channels['news'],{11,99})
        with self.assertRaises(SettingConflict):
            set_news_alert(bot,11,False,permissions,expected=False)
        bot.persist_state.side_effect=OSError('disk full')
        with self.assertRaises(OSError):
            set_news_alert(bot,11,False,permissions,expected=True)
        self.assertEqual(bot.alert_channels['news'],{11,99})

    async def test_only_selected_guild_settings_are_returned(self):
        from website.bot_view import guild_snapshot
        channel = Mock(spec=discord.TextChannel)
        channel.id, channel.name, channel.position = 11, '공지', 0
        channel.permissions_for.return_value = discord.Permissions.all()
        guild = Mock()
        guild.id, guild.name = 1, '길드'
        guild.roles=[]
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
        self.assertEqual(result['channels'][0]['enabled'],['공지 알림'])
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


class ExtendedSaveTests(LoginTests):
    async def get_application(self):
        self.remote=AsyncMock()
        self.snapshot=AsyncMock(return_value={'readOnly':True})
        self.save=AsyncMock(return_value={'enabled':True})
        self.extended=AsyncMock(return_value={'enabled':True,'changed':True})
        return create_app('example-id','example-secret',self.remote,guild_snapshot=self.snapshot,save_news=self.save,save_alert=self.extended)

    # 서버 역할 필드는 반드시 함께 전달하고 기존 역할까지 비교하도록 넘깁니다.
    async def test_role_save_is_validated_and_forwarded(self):
        cookies=await self.authorize()
        res=await self.request('GET','/api/session',cookies=cookies)
        headers={'Origin':ORIGIN,'X-CSRF-Token':(await res.json())['csrf']}
        data={'channelId':'11','enabled':True,'previous':True,'roleId':'8','previousRoleId':'7'}
        self.remote.side_effect=[[{'id':'1','name':'test','permissions':'8'}]]
        saved=await self.request('PATCH','/api/guilds/1/alerts/server',cookies=cookies,headers=headers,json=data)
        self.assertEqual(saved.status,200)
        self.extended.assert_awaited_once_with(1,11,True,True,'server',8,7)
        self.extended.reset_mock()
        data['roleId']=8
        self.remote.side_effect=[[{'id':'1','name':'test','permissions':'8'}]]
        rejected=await self.request('PATCH','/api/guilds/1/alerts/server',cookies=cookies,headers=headers,json=data)
        self.assertEqual(rejected.status,400)
        self.extended.assert_not_called()



class SettingOperationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import maple_bot
        self.apply = maple_bot.MapleNewsBot.apply_channel_setting
        self.bot=SimpleNamespace(alert_channels={k:set() for k in maple_bot.ALERT_TYPES},
            server_alert_roles={}, persist_state=Mock(), sunny_sunday=None)
        self.channel=Mock(spec=discord.TextChannel)
        self.channel.id=11
        self.channel.guild=SimpleNamespace(id=1)
        self.permissions=discord.Permissions.all()

    async def test_failed_persistence_does_not_send_or_change_settings(self):
        self.bot.persist_state.side_effect=OSError('disk full')
        with self.assertRaises(OSError):
            await self.apply(self.bot,self.channel,'sunny_list',True,self.permissions,expected=False)
        self.assertEqual(self.bot.alert_channels['sunny_list'],set())

    async def test_role_replacement_checks_previous_role(self):
        from channel_settings import SettingConflict
        role=Mock();role.id=7;role.guild=SimpleNamespace(id=1);role.is_default.return_value=False
        await self.apply(self.bot,self.channel,'server',True,self.permissions,role=role,expected=False,previous_role=None)
        self.assertEqual(self.bot.server_alert_roles,{'11':7})
        role.id=8
        with self.assertRaises(SettingConflict):
            await self.apply(self.bot,self.channel,'server',True,self.permissions,role=role,expected=True,previous_role=6)
        self.assertEqual(self.bot.server_alert_roles,{'11':7})

    async def test_voice_modes_cannot_overlap(self):
        self.channel=Mock(spec=discord.VoiceChannel);self.channel.id=11
        self.bot.alert_channels['info_time']={11}
        with self.assertRaises(ValueError):
            await self.apply(self.bot,self.channel,'info_utc',True,self.permissions,expected=False)
        self.assertEqual(self.bot.alert_channels['info_utc'],set())

    async def test_concurrent_enable_sends_once(self):
        import asyncio
        self.bot.sunny_sunday={'title':'example','entries':[]}
        self.bot.send_sunny_sunday_to_channel=AsyncMock()
        results=await asyncio.gather(*[
            self.apply(self.bot,self.channel,'sunny_list',True,self.permissions) for _ in range(2)])
        self.assertEqual(self.bot.alert_channels['sunny_list'],{11})
        self.assertEqual(sum(r['changed'] for r in results),1)
        self.assertEqual(self.bot.send_sunny_sunday_to_channel.await_count,1)

    async def test_effect_failure_is_reported_without_losing_saved_setting(self):
        self.channel=Mock(spec=discord.VoiceChannel);self.channel.id=11
        self.channel.edit=AsyncMock(side_effect=TimeoutError())
        with self.assertLogs(level='ERROR'):
            result=await self.apply(self.bot,self.channel,'info_utc',True,self.permissions)
        self.assertTrue(result['warning'])
        self.assertEqual(self.bot.alert_channels['info_utc'],{11})


class PublicOriginTests(AioHTTPTestCase):
    async def get_application(self):
        self.remote=AsyncMock(side_effect=[{'access_token':'test-only','expires_in':3600},{'id':'1','username':'example'}])
        return create_app('example-id','example-secret',self.remote,public_origin='https://sherbet.example')

    async def test_https_login_uses_registered_origin_and_secure_cookie(self):
        res=await self.client.get('/auth/login',headers={'Host':'sherbet.example'},allow_redirects=False)
        self.assertEqual(res.status,302)
        query=parse_qs(urlsplit(res.headers['Location']).query)
        self.assertEqual(query['redirect_uri'],['https://sherbet.example/auth/callback'])
        self.assertTrue(res.cookies['sherbet_login']['secure'])
        denied=await self.client.get('/api/session',headers={'Host':'other.example'})
        self.assertEqual(denied.status,403)

    async def test_https_session_cookie_is_secure(self):
        login=await self.client.get('/auth/login',headers={'Host':'sherbet.example'},allow_redirects=False)
        state=parse_qs(urlsplit(login.headers['Location']).query)['state'][0]
        callback=await self.client.get('/auth/callback',params={'state':state,'code':'test-only'},
            headers={'Host':'sherbet.example'},cookies={'sherbet_login':login.cookies['sherbet_login'].value},allow_redirects=False)
        self.assertEqual(callback.status,302)
        self.assertTrue(callback.cookies['sherbet_session']['secure'])
        self.assertTrue(callback.cookies['sherbet_session']['httponly'])

    async def test_public_http_or_embedded_path_is_rejected(self):
        for origin in ['http://sherbet.example','https://sherbet.example/path','https://user@sherbet.example','https://sherbet.example?x=1']:
            with self.assertRaises(ValueError):
                create_app(public_origin=origin)


if __name__ == '__main__':
    unittest.main()
