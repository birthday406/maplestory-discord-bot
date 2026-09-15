"""실행 중인 봇의 현재 상태만 조회합니다. 메시지 전송이나 설정 저장은 없습니다."""
import os
from datetime import datetime, timezone

import discord
from aiohttp import web
from website.server import create_app


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
        active = [label for kind, label in labels.items() if channel.id in bot.alert_channels.get(kind, set())]
        role_id = bot.server_alert_roles.get(str(channel.id))
        role = guild.get_role(role_id) if role_id else None
        rows.append({'id':str(channel.id), 'name':channel.name,
                     'type':'voice' if isinstance(channel, discord.VoiceChannel) else 'text',
                     'enabled':active, 'mentionRole':role.name if role else None,
                     'permissions':{'view':permissions.view_channel, 'send':permissions.send_messages,
                                    'embed':permissions.embed_links, 'attach':permissions.attach_files,
                                    'manage':permissions.manage_channels}})
    return {'guild':{'id':str(guild.id),'name':guild.name}, 'channels':rows,
            'readOnly':True, 'checkedAt':datetime.now(timezone.utc).isoformat()}


async def start_website(bot, labels):
    # 기본은 꺼짐입니다. 명시적으로 활성화할 때만 봇과 같은 프로세스에서 조회합니다.
    if os.environ.get('SHERBET_WEBSITE_ENABLED') != '1':
        return None
    cid, secret = os.environ.get('DISCORD_CLIENT_ID',''), os.environ.get('DISCORD_CLIENT_SECRET','')
    if not cid or not secret:
        raise ValueError('홈페이지 OAuth 설정이 필요합니다.')
    async def snapshot(gid):
        return await guild_snapshot(bot, gid, labels)
    runner = web.AppRunner(create_app(cid, secret, guild_snapshot=snapshot), access_log=None)
    await runner.setup()
    try:
        await web.TCPSite(runner, '127.0.0.1', 8766).start()
    except Exception:
        await runner.cleanup()
        raise
    return runner
