"""사용자가 지정한 v271 피해량 수정 알림의 직업별 보기."""
import json
import logging
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from embed_style import embed_title
import discord

STATE = Path(__file__).with_name('patch-v271-view.json')
URL = 'https://www.nexon.com/maplestory/news/update/44597/v-271-maplestory-x-frieren-beyond-journey-s-end-patch-notes'
# 기존 알림의 수치를 그대로 보존합니다. 직업 순서: 하야토, 칸나, 린, 묵현.
JOBS = ('하야토', '칸나', '린', '묵현')
VALUES = (
    ('2,430', '1,782', '693', '2,900', '792', '1,089', '2,224', '1,502'),
    ('3,525', '3,400', '1,917', '3,400', '2,190', '3,012', '4,140', '2,760'),
    ('2,073', '1,360', '533', '2,254', '599', '828', '2,174', '1,449'),
    ('1,620', '2,228', '859', '3,728', '1,005', '1,361', '1,863', '1,242'),
)


def page(index):
    v = VALUES[index]
    text = (
        f'**{JOBS[index]} · 스킬 피해량**\n\n'
        f'**에르다 샤워/파운틴** · Lv.30\n900% → **{v[0]}%**\n\n'
        f'**스파이더 인 미러**\n공간 붕괴: 990% → **{v[1]}%**\n'
        f'스파이더 인 미러: 385% → **{v[2]}%**\n\n'
        f'**크레스트 오브 더 솔라**\n미트라의 불꽃: 1,650% → **{v[3]}%**\n'
        f'불꽃의 문양: 440% → **{v[4]}%**\n단일 공격: 605% → **{v[5]}%**\n\n'
        f'**솔 야누스**\n순환의 고리: 690% → **{v[6]}%**\n태초의 결정: 1,035% → **{v[7]}%**'
    )
    if index == 3:
        text += (
            '\n\n**솔 헤카테 · 피해량 변경**\n'
            '솔 에르다 입자 · Lv.30: 1,149% → **800%**\n\n'
            '**스틱스**\n피해량: 2,503% → **1,427%**\n'
            '죽음의 씨앗: 2,620% → **1,489%**\n죽음의 씨앗 개화: 3,055% → **1,729%**\n\n'
            '**플레게톤**\n피해량: 1,627% → **735%**\n균열: 1,298% → **586%**'
        )
    embed = discord.Embed(title=embed_title('패치노트 추가 수정'), url=URL, description=text, color=0xF1C40F)
    embed.add_field(name='원문', value=f'[공식 패치노트 확인]({URL})', inline=False)
    embed.set_footer(text=f'v.271 · {index + 1}/4 · 아래에서 직업 선택 · 채널 공용 화면')
    return embed


class JobButton(discord.ui.Button):
    def __init__(self, index):
        super().__init__(label=JOBS[index], custom_id=f'patch:v271:damage:{index}', style=discord.ButtonStyle.secondary)
        self.index = index

    async def callback(self, interaction):
        await interaction.response.edit_message(embed=page(self.index))


class PatchRevisionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for index in range(4):
            self.add_item(JobButton(index))


async def install_preview(client, channels):
    # 버튼은 재시작할 때마다 등록하되 기존 메시지 편집은 성공 기록으로 한 번만 합니다.
    if getattr(client, '_patch_preview_registered', False):
        return
    client.add_view(PatchRevisionView())
    client._patch_preview_registered = True
    try:
        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {}
        for channel in channels:
            if state.get(str(channel.id), {}).get('completed'):
                continue
            record = state.setdefault(str(channel.id), {'messages': {}})
            async for message in channel.history(limit=None, after=datetime(2026, 9, 9, tzinfo=timezone.utc)):
                if message.author.id != client.user.id or len(message.embeds) != 1:
                    continue
                embed = message.embeds[0]
                text = embed.description or ''
                if (embed.url != URL or embed.title != '패치노트 추가 수정'
                        or not all(job in text for job in JOBS)
                        or '솔 헤카테' not in text):
                    continue
                # 실제 게시된 수치가 예시와 다르면 잘못 덮어쓰지 않고 확인을 요청합니다.
                def damages(value):
                    return Counter(number.replace(',', '') for number in re.findall(r'([\d,]+)%', value))
                if damages(text) != damages('\n'.join(page(index).description for index in range(4))):
                    raise ValueError(f'Patch preview damage mismatch: {message.id}')
                key = str(message.id)
                if record['messages'].get(key, {}).get('edited'):
                    continue
                # 원본을 먼저 보관해 필요하면 해당 메시지만 되돌릴 수 있습니다.
                record['messages'][key] = {'original': embed.to_dict(), 'edited': False}
                STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
                await message.edit(embed=page(0), view=PatchRevisionView())
                record['messages'][key]['edited'] = True
                STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            record['completed'] = True
            STATE.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    except (discord.HTTPException, OSError, ValueError):
        logging.exception('Patch preview installation failed')
