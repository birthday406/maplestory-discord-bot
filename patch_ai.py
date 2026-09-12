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


def revision_context(before, current, body):
    # 변경 위치의 상위 제목과 표·주변 문장을 함께 보관합니다. 이후 원문이 바뀌어도 맥락이 섞이지 않습니다.
    headings = {patch_text(text): int(level) for level, text in
                re.findall(r'<h([1-6])\b[^>]*>(.*?)</h\1>', body, re.I | re.S)}
    old, new = before.splitlines(), current.splitlines()
    paths, stack = [], {}
    for line in new:
        if line in headings:
            level = headings[line]
            stack = {n: title for n, title in stack.items() if n < level}
            stack[level] = line
        paths.append(' > '.join(stack.values()))
    parts = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        heading = paths[min(c, len(paths) - 1)] if paths else ''
        parts.append('상위 제목: ' + (heading or '확인되지 않음') + '\n이전 주변 문맥:\n' +
                     '\n'.join(old[max(0, a-12):b+8]) + '\n수정 후 주변 문맥:\n' +
                     '\n'.join(new[max(0, c-12):d+8]))
    return '\n\n'.join(parts)


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
            if 'context' not in {row[1] for row in db.execute('PRAGMA table_info(revisions)')}:
                db.execute('ALTER TABLE revisions ADD COLUMN context TEXT')

    def observe(self, post_id, title, url, body, targets):
        current = patch_text(body)
        if not current:
            raise ValueError('Empty official patch body')
        with sqlite3.connect(self.path) as db:
            previous = db.execute('SELECT body FROM snapshots WHERE post_id=?', (post_id,)).fetchone()
            if previous and previous[0] != current:
                changes = patch_diff(previous[0], current)
                context = revision_context(previous[0], current, body)
                db.execute('INSERT INTO revisions (post_id,title,url,changes,targets,sent,context) VALUES (?,?,?,?,?,?,?)',
                           (post_id, title, url, changes, json.dumps(targets), '[]', context))
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
