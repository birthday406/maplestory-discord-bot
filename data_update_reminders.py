"""새 패치·신규 스스비 공지에 따른 소유자용 데이터 갱신 알림."""
import html
import json
import re
import time
from pathlib import Path

STATE_PATH = Path(__file__).with_name('data-update-reminders.json')


def has_new_pssb(source):
    # 단순 링크·진행 중 판매 항목은 신규 스스비로 오인하지 않습니다.
    for heading in re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>', source, re.I | re.S):
        text = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', heading)).split())
        if text.upper() in {'ONGOING SALES', 'SALES ENDING THIS WEEK'}:
            break
        if re.search(r'\bPremium\s+Surprise\s+Style\s+Box(?:es)?\b|\bPSSB\b', text, re.I):
            return True
    return False


async def check_data_updates(client, posts, *, path=STATE_PATH, now=None):
    from maple_bot import is_patch_notes, is_cash_shop_update, post_url
    now = time.time() if now is None else now
    state = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def save():
        # 전송 성공 기록이 재시작 후에도 남도록 완성된 JSON으로 교체합니다.
        pending = path.with_suffix('.tmp')
        pending.write_text(json.dumps(state), encoding='utf-8')
        pending.replace(path)

    async def notify(text):
        application = await client.application_info()
        await application.owner.send(text)

    post = next((p for p in posts if is_patch_notes(p)), None)
    version = re.search(r'\bv\.?\s*(\d+(?:\.\d+)*)', post['name'], re.I) if post else None
    if version:
        key = version[1]
        if 'versions' not in state:
            state['versions'] = [key]
            save()
        elif key not in state['versions']:
            await notify(f'🔧 **아이템검색 DB 업데이트 필요**\n새 버전 v.{key} 패치노트가 등록됐습니다.\n'
                         f'클라이언트 업데이트 후 아이템 이름·아이콘 DB를 다시 추출해주세요.\n{post_url(post)}')
            state['versions'].append(key)
            save()

    cash = next((p for p in posts if is_cash_shop_update(p)), None)
    if cash is None:
        return
    key = str(cash['id'])
    # 같은 공지도 나중에 신규 스스비 항목이 추가될 수 있어 5분마다 재확인합니다.
    if state.get('cash_post') == key and now - state.get('cash_checked', -300) < 300:
        return
    detail = await client.fetch_post_detail(cash['id'])
    source = detail['body']
    if not isinstance(source, str) or not source.strip():
        raise ValueError('Empty cash shop detail')
    new_pssb = has_new_pssb(source)
    if 'pssb_notified' not in state:
        state['pssb_notified'] = [key] if new_pssb else []
    elif new_pssb and key not in state['pssb_notified']:
        await notify('🔧 **스스비 업데이트 확인 필요**\n캐시샵 공지에 신규 스스비 항목이 등록됐습니다.\n'
                     '새 구성품의 이름·아이콘 DB를 확인해주세요. 확률표는 /스스비 실행 시 자동 조회합니다.\n'
                     + post_url(cash))
        state['pssb_notified'].append(key)
    state['cash_post'], state['cash_checked'] = key, now
    save()
