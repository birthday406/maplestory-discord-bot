"""공식 패치의 비교 기준과 채널별 전송 기록을 재시작 후에도 보존합니다."""
import difflib
import html
import json
import re
import sqlite3
from pathlib import Path


def normalize_revision_labels(text):
    """줄 앞의 구분 제목만 통일하고 본문의 표현과 수치는 그대로 둡니다."""
    pattern = re.compile(
        r'(?m)^(?P<prefix>[ \t]*(?:(?:[-*•]|\d+[.)])[ \t]+)?(?:\*\*)?)'
        r'(?P<label>추가|변경|수정|삭제|제거)(?:됨|[ \t]*사항)?'
        r'(?P<close>\*\*)?(?=[ \t]*(?:[:：]|$))'
    )
    names = {'추가': '추가', '변경': '변경', '수정': '변경', '삭제': '삭제', '제거': '삭제'}
    return pattern.sub(lambda match: match['prefix'] + names[match['label']] + (match['close'] or ''), text)


def patch_text(body):
    # 태그·공백만 바뀐 수정은 무시하되 문단과 표의 내용은 남깁니다.
    body = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', '', body, flags=re.I | re.S)
    body = re.sub(r'</(?:p|div|li|tr|h[1-6])>|<br\s*/?>', '\n', body, flags=re.I)
    body = html.unescape(re.sub(r'<[^>]+>', ' ', body))
    return '\n'.join(line for part in body.splitlines()
                     if (line := ' '.join(part.split())) and line.lower() != 'back to top')


def patch_diff(before, after):
    return '\n'.join(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                          fromfile='이전 원문', tofile='수정 원문', lineterm='', n=2))


class PatchHistory:
    def __init__(self, path=Path('patch-history.db')):
        self.path = path
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS snapshots (post_id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY, post_id INTEGER, title TEXT, url TEXT, changes TEXT,
                    targets TEXT, sent TEXT, summary TEXT,
                    observed_utc TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')

    def observe(self, post_id, title, url, body, targets):
        current = patch_text(body)
        if not current:
            raise ValueError('Empty official patch body')
        with sqlite3.connect(self.path) as db:
            previous = db.execute('SELECT body FROM snapshots WHERE post_id=?', (post_id,)).fetchone()
            if previous and previous[0] != current:
                changes = patch_diff(previous[0], current)
                db.execute('INSERT INTO revisions (post_id,title,url,changes,targets,sent) VALUES (?,?,?,?,?,?)',
                           (post_id, title, url, changes, json.dumps(targets), '[]'))
            # 처음에는 비교 원문이 없으므로 추가 수정 알림을 보내지 않습니다.
            db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)', (post_id, current))

    def pending(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            result = []
            for row in db.execute('SELECT * FROM revisions ORDER BY id'):
                item = dict(row)
                item['targets'], item['sent'] = json.loads(item['targets']), json.loads(item['sent'])
                if set(item['targets']) - set(item['sent']):
                    result.append(item)
            return result

    def set_summary(self, revision_id, summary):
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE revisions SET summary=? WHERE id=?', (summary, revision_id))

    def mark_sent(self, revision_id, channel_id):
        with sqlite3.connect(self.path) as db:
            sent = json.loads(db.execute('SELECT sent FROM revisions WHERE id=?', (revision_id,)).fetchone()[0])
            if channel_id not in sent:
                sent.append(channel_id)
            db.execute('UPDATE revisions SET sent=? WHERE id=?', (json.dumps(sent), revision_id))


def validated_answer(content, source):
    payload = json.loads(content)
    if not isinstance(payload, dict):
        raise ValueError('Invalid patch answer')
    answer, evidence = payload.get('answer'), payload.get('evidence')
    if not isinstance(answer, str) or not answer.strip() or len(answer) > 3000:
        raise ValueError('Invalid patch answer')
    if (not isinstance(evidence, list) or not 1 <= len(evidence) <= 3
            or any(not isinstance(quote, str) or not 10 <= len(quote) <= 400
                   or ' '.join(quote.split()) not in ' '.join(source.split()) for quote in evidence)):
        return '최신 공식 패치노트 원문에서 확인되지 않습니다.', []
    return answer.strip(), evidence
