"""실행 중인 봇의 설정 조회와 공지 알림 저장을 연결합니다."""
import os
from datetime import datetime, timezone

import discord
from aiohttp import web
from website.server import create_app
from channel_settings import WEB_SETTINGS, VOICE_SETTINGS


async def guild_snapshot(bot, guild_id, labels):
    if not bot.is_ready():
        raise web.HTTPServiceUnavailable(text="샤벳이 Discord에 연결 중입니다. 잠시 후 다시 확인해주세요.")
    if bot.get_guild(guild_id) is None:
        raise web.HTTPNotFound(text="샤벳이 이 서버에 없거나 아직 서버 정보를 받지 못했습니다.")
    try:
        # 채널·역할·봇 멤버를 다시 받아 현재 채널별 권한을 계산합니다.
        guild = await bot.fetch_guild(guild_id)
        channels = await guild.fetch_channels()
        member = await guild.fetch_member(bot.user.id)
    except discord.Forbidden:
        raise web.HTTPForbidden(text="샤벳이 서버 정보를 조회할 권한이 없습니다.")
    except discord.NotFound:
        raise web.HTTPNotFound(text="서버 또는 샤벳의 참여 정보를 찾지 못했습니다.")
    except discord.HTTPException:
        raise web.HTTPBadGateway(text="Discord 채널 조회에 실패했습니다. 잠시 후 다시 확인해주세요.")
    rows = []
    for channel in sorted(channels, key=lambda c: (c.position, c.id)):
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
            continue
        permissions = channel.permissions_for(member)
        active = [WEB_SETTINGS.get(kind,label) for kind, label in labels.items() if channel.id in bot.alert_channels.get(kind, set())]
        role_id = bot.server_alert_roles.get(str(channel.id))
        role = guild.get_role(role_id) if role_id else None
        rows.append({'id':str(channel.id), 'name':channel.name,
                     'newsEnabled':channel.id in bot.alert_channels.get('news',set()),
                     'settings':{kind:channel.id in bot.alert_channels.get(kind,set()) for kind in WEB_SETTINGS},
                     'roleId':str(role_id) if role_id else None,
                     'type':'voice' if isinstance(channel, discord.VoiceChannel) else 'text',
                     'enabled':active, 'mentionRole':role.name if role else None,
                     'permissions':{'view':permissions.view_channel, 'send':permissions.send_messages,
                                    'embed':permissions.embed_links, 'attach':permissions.attach_files,
                                    'manage':permissions.manage_channels}})
    return {'guild':{'id':str(guild.id),'name':guild.name}, 'channels':rows,
            'settingLabels':WEB_SETTINGS, 'roles':[{'id':str(r.id),'name':r.name} for r in guild.roles if not r.is_default()],
            'readOnly':False, 'checkedAt':datetime.now(timezone.utc).isoformat()}


async def save_news_setting(bot, guild_id, channel_id, enabled, previous, kind='news', role_id=None, previous_role=None):
    from channel_settings import SettingConflict
    if kind not in WEB_SETTINGS:
        raise web.HTTPBadRequest(text='지원하지 않는 설정입니다.')
    if not bot.is_ready():
        raise web.HTTPServiceUnavailable(text='샤벳이 연결 중입니다.')
    if bot.get_guild(guild_id) is None:
        raise web.HTTPNotFound(text='샤벳의 서버 참여 정보를 찾지 못했습니다.')
    try:
        # 요청자가 전달한 채널 ID를 신뢰하지 않고 해당 서버 목록에서 다시 찾습니다.
        guild = await bot.fetch_guild(guild_id)
        channel = next((c for c in await guild.fetch_channels() if c.id==channel_id),None)
        if not isinstance(channel,discord.VoiceChannel if kind in VOICE_SETTINGS else discord.TextChannel):
            raise web.HTTPBadRequest(text='설정에 맞는 현재 서버의 채널을 선택해주세요.')
        role = guild.get_role(role_id) if role_id else None
        member = await guild.fetch_member(bot.user.id)
        result = await bot.apply_channel_setting(channel,kind,enabled,channel.permissions_for(member),
            expected=previous,role=role,previous_role=previous_role)
    except SettingConflict as error:
        raise web.HTTPConflict(text=str(error))
    except ValueError as error:
        raise web.HTTPBadRequest(text=str(error))
    except (PermissionError,discord.Forbidden):
        raise web.HTTPForbidden(text='샤벳의 채널 보기·메시지 보내기·링크 첨부 권한을 확인해주세요.')
    except discord.NotFound:
        raise web.HTTPNotFound(text='채널 또는 서버를 찾지 못했습니다.')
    except discord.HTTPException:
        raise web.HTTPBadGateway(text='Discord 권한 조회에 실패했습니다. 다시 시도해주세요.')
    except OSError:
        raise web.HTTPInternalServerError(text='파일 저장에 실패했습니다. 기존 설정을 유지합니다.')
    return result


async def start_website(bot, labels):
    # 기본은 꺼짐입니다. 명시적으로 활성화할 때만 봇과 같은 프로세스에서 조회합니다.
    if os.environ.get('SHERBET_WEBSITE_ENABLED') != '1':
        return None
    cid, secret = os.environ.get('DISCORD_CLIENT_ID',''), os.environ.get('DISCORD_CLIENT_SECRET','')
    if not cid or not secret:
        raise ValueError('홈페이지 OAuth 설정이 필요합니다.')
    async def snapshot(gid):
        return await guild_snapshot(bot, gid, labels)
    async def save(gid,cid,enabled,previous):
        return await save_news_setting(bot,gid,cid,enabled,previous)
    async def save_alert(gid,cid,enabled,previous,kind,role_id=None,previous_role=None):
        return await save_news_setting(bot,gid,cid,enabled,previous,kind,role_id,previous_role)
    runner = web.AppRunner(create_app(cid, secret, guild_snapshot=snapshot, save_news=save, save_alert=save_alert, public_origin=os.environ.get('SHERBET_PUBLIC_ORIGIN'), ranking_path=bot.ranking_store.path), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, '127.0.0.1', 8766).start()
    except Exception:
        await runner.cleanup()
        raise
    return runner
