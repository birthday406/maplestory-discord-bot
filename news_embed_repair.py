"""사용자가 요청한 판매 공지의 용어만 기존 메시지에서 한 번 교정합니다."""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import discord

RECORD = Path(__file__).with_name('news-44597-overview-v3-repair.json')


def corrected_embed(embed):
    data = embed.to_dict()
    url = urlsplit(data.get('url', ''))
    if url.hostname != 'www.nexon.com' or not url.path.startswith(('/maplestory/news/sale/44348/', '/maplestory/news/sale/44314/', '/maplestory/news/sale/44291/', '/maplestory/news/events/44414/', '/maplestory/news/events/44415/', '/maplestory/news/update/44597/')):
        return None
    def fix(text):
        # 조사만 붙은 이름도 교정하되, 다른 단어 일부를 잘못 바꾸지는 않습니다.
        terms = {'페이스': '얼굴', '펀': '페른', '스타크': '슈타르크', '프리에렌': '프리렌',
                 '협업': '콜라보레이션', '기어독': '기어드락', '패밀리어': '퍼밀리어',
                 '신성 심볼': '어센틱심볼', '보너스 스탯': '추가옵션', '노드': '코어',
                 '에렐 라이트': '에릴 라이트', '초즌 세렌': '선택받은 세렌', '칼링': '카링',
                 '오메가 섹터': '지구방위본부', '챌린저 월드 도약': '챌린저 월드 리프',
                 '럭스 사우나': 'VIP 사우나', '럭스사우나': 'VIP 사우나',
                 '철학자의 책': '필로소퍼 북', '뤼그너': '류그너', '벨럼': '벨룸',
                 '수호천사 슬라임': '가디언 엔젤 슬라임', '모현': '묵현',
                 '진정한 거미 반사': '스파이더 인 미러', '진거미 반사': '스파이더 인 미러',
                 '태양 문장': '크레스트 오브 더 솔라', '화염 문장': '불꽃의 문양',
                 '에르다 샤워/분수': '에르다 샤워/파운틴', '에르다 샤워/샘': '에르다 샤워/파운틴',
                 '태양 야누스': '솔 야누스', '순환 고리': '순환의 고리', '원시 수정': '태초의 결정',
                 '불타는 들판 스테이지': '버닝 필드 단계', '불타는 들판': '버닝 필드'}
        pattern = '|'.join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
        return re.sub(r'(?<!\w)(' + pattern + r')(?=(?:의|가|이|은|는|을|를|에|에서|에게|와|과|도|만|로|으로|에는|에서도)\b|\W|$)',
                      lambda match: terms[match[0]], text)
    before = json.dumps(data, ensure_ascii=False)
    for key in ('title', 'description'):
        if key in data:
            data[key] = fix(data[key])
    for field in data.get('fields', []):
        for key in ('name', 'value'):
            field[key] = fix(field[key])
    if json.dumps(data, ensure_ascii=False) == before:
        return None
    return discord.Embed.from_dict(data)


async def repair_news_embeds(client, channels):
    from patch_revision_preview import install_preview
    channels = list(channels)
    await install_preview(client, channels)
    # 재연결 중 중복 실행을 막고 완료 채널은 디스크 기록으로 건너뜁니다.
    if getattr(client, '_news_44348_repair_running', False):
        return
    client._news_44348_repair_running = True
    try:
        completed = json.loads(RECORD.read_text(encoding='utf-8')) if RECORD.exists() else {}
        for channel in channels:
            if str(channel.id) in completed:
                continue
            edited = []
            try:
                async for message in channel.history(limit=None, after=datetime(2026, 9, 1, tzinfo=timezone.utc)):
                    if message.author.id != client.user.id:
                        continue
                    embeds = [corrected_embed(embed) for embed in message.embeds]
                    if not any(embed is not None for embed in embeds):
                        continue
                    # 다른 임베드·첨부·본문은 그대로 두고 기존 임베드만 편집합니다.
                    await message.edit(embeds=[new if new is not None else old
                                              for new, old in zip(embeds, message.embeds)])
                    edited.append(message.id)
                completed[str(channel.id)] = edited
                RECORD.write_text(json.dumps(completed), encoding='utf-8')
                logging.info('News 44414 repair channel=%s edited=%s', channel.id, edited)
            except discord.HTTPException:
                logging.exception('News 44414 repair failed channel=%s', channel.id)
    except (OSError, ValueError):
        logging.exception('News 44414 repair record failed')
    finally:
        client._news_44348_repair_running = False
