"""공개 게임 데이터만 읽습니다. Discord 사용자별 조회 기록은 제공하지 않습니다."""
import asyncio
import sqlite3
import time
from contextlib import closing
from datetime import date
from pathlib import Path

from aiohttp import web
from ranking_store import ranking_total_exp
from maple_data import LEVEL_EXP

WORLDS = {45: 'Kronos', 19: 'Scania', 1: 'Bera', 70: 'Hyperion'}


def read_ranking(path, world, nickname):
    # 읽기 전용 연결로 운영 DB를 만들거나 수집 중인 값을 바꾸지 않습니다.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as db:
        db.row_factory = sqlite3.Row
        deadline = time.monotonic() + 3
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute('BEGIN')
        columns = 'name,world_id,job_name,level,exp,updated_date,image_url'
        if not nickname:
            # 공식 월드 순위 자체를 사용합니다. 레벨·경험치·이름으로 순위를 만들지 않습니다.
            rows = db.execute('''SELECT c.name,c.world_id,c.job_name,c.level,c.exp,c.image_url,
                r.ranking,r.updated_date FROM official_world_rankings r
                JOIN characters c ON c.name_key=r.name_key AND c.world_id=r.world_id
                WHERE r.world_id=? AND c.level>=260 AND r.ranking>0
                ORDER BY r.ranking LIMIT 100''', (world,)).fetchall()
            return {'world': WORLDS[world], 'rows': [dict(r) for r in rows]}
        row = db.execute(f'SELECT {columns},ranking,legion_level,legion_rank,achievement_score,achievement_rank FROM characters WHERE name_key=?',
                         (nickname.casefold(),)).fetchone()
        if row is None:
            raise web.HTTPNotFound(text='저장된 캐릭터가 없습니다. 자동 수집 대상은 Lv.260 이상입니다.')
        character = dict(row)
        world_rank = db.execute('SELECT ranking FROM official_world_rankings WHERE name_key=? AND world_id=?',
            (nickname.casefold(), character['world_id'])).fetchone()
        character['worldRank'] = world_rank['ranking'] if world_rank else None
        history = db.execute('SELECT snapshot_date,level,exp FROM ranking_snapshots WHERE name_key=? '
                             'ORDER BY snapshot_date DESC LIMIT 15', (nickname.casefold(),)).fetchall()
        gains = []
        ordered = list(reversed(history))
        for previous, current in zip(ordered, ordered[1:]):
            before = ranking_total_exp(previous['level'], previous['exp'])
            after = ranking_total_exp(current['level'], current['exp'])
            # 누락 날짜를 하루 증가량으로 오해하지 않도록 실제 일수도 표시합니다.
            days = (date.fromisoformat(current['snapshot_date']) - date.fromisoformat(previous['snapshot_date'])).days
            gains.append({'date': current['snapshot_date'], 'days': days,
                          'exp': after - before if before is not None and after is not None and after >= before else None})
        level = character['level']
        character['remainingExp'] = max(0, LEVEL_EXP[level-200] - character['exp']) if 200 <= level < 300 else None
        character['requiredExp'] = LEVEL_EXP[level-200] if 200 <= level < 300 else None
        character['world'] = WORLDS.get(character['world_id'], str(character['world_id']))
        return {'character': character, 'gains': gains}


def ranking_handler(path):
    cache = {}
    lock = asyncio.Lock()

    async def handle(request):
        nickname = request.query.get('nickname', '').strip()
        if len(nickname) > 12 or sum(2 if ord(c) > 127 else 1 for c in nickname) > 12:
            raise web.HTTPBadRequest(text='닉네임은 한글 2바이트, 영문·숫자 1바이트 기준 최대 12바이트입니다.')
        try:
            world = int(request.query.get('world', '45'))
        except ValueError:
            raise web.HTTPBadRequest(text='월드를 확인해주세요.')
        if world not in WORLDS:
            raise web.HTTPBadRequest(text='지원하지 않는 월드입니다.')
        if lock.locked():
            raise web.HTTPTooManyRequests(text='조회 중입니다. 잠시 후 다시 시도해주세요.', headers={'Retry-After':'2'})
        async with lock:
            now = time.monotonic()
            for key in list(cache):
                if cache[key][0] <= now:
                    cache.pop(key)
            key = (world, nickname.casefold())
            if key not in cache:
                try:
                    result = await asyncio.to_thread(read_ranking, path, world, nickname)
                except sqlite3.Error:
                    raise web.HTTPServiceUnavailable(text='랭킹 데이터를 읽지 못했습니다. 잠시 후 다시 시도해주세요.')
                # ponytail: 메모리 캐시 최대 128개. 트래픽 증가 시 별도 조회 서버를 검토합니다.
                if len(cache) >= 128:
                    cache.pop(next(iter(cache)))
                cache[key] = (now + 60, result)
            return web.json_response(cache[key][1])
    return handle
