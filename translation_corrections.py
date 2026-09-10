"""파일 교정표와 관리자 DM의 번역 표현을 AI 요청에 전달합니다."""
import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path

import discord

GLOSSARY_PATH = Path(__file__).with_name('TRANSLATION_GLOSSARY.md')


class TranslationValidationError(ValueError):
    """인증값·응답 원문 없이 사용자에게 보여줄 수 있는 번역 검사 오류입니다."""


def normalize_term(text):
    return ' '.join(text.replace('’', "'").replace('‘', "'").casefold().split())


def file_terms():
    # 설명 문장은 지시문으로 보내지 않고, 확정 용어 표의 세 열만 읽습니다.
    active, rows = False, {}
    for line in GLOSSARY_PATH.read_text(encoding='utf-8').splitlines():
        if line.strip() == '## 사용자 확정 용어':
            active = True
            continue
        if active and line.startswith('## '):
            break
        if not active or not line.startswith('|'):
            continue
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if cells[0] == '영어 원문' or set(cells[0]) <= set('-: '):
            continue
        if len(cells) != 3 or not all(cells):
            raise ValueError('번역 교정표는 영어 원문·기존 번역·권장 번역 세 열이어야 합니다.')
        term, wrong, preferred = cells
        key = normalize_term(term)
        if key in rows:
            raise ValueError(f'번역 교정표의 원문이 중복됩니다: {term}')
        rows[key] = (term, '' if wrong == '—' else wrong, preferred)
    if not rows:
        raise ValueError('번역 교정표의 사용자 확정 용어가 비어 있습니다.')
    return rows

HELP = ('한 메시지에 다음 형식으로 보내주세요.\n'
        '번역교정\n원문: Ring Polish Swap\n잘못된 번역: 반지 폴리시 스왑\n원하는 표현: 반지 연마 전수\n\n'
        '`번역교정 목록` · `번역교정 삭제 원문`\n'
        '저장 후 새 요약·번역에 적용되며 이미 발송된 메시지는 바뀌지 않습니다.')


def protect_google_terms(texts, corrections=()):
    # 파일 기본값 위에 관리자 교정값을 덮고, 긴 이름부터 한 번에 찾습니다.
    terms = file_terms()
    terms.update({normalize_term(term): (term, wrong, preferred)
                  for term, wrong, preferred in corrections})
    alternatives, preferred = [], {}
    for index, (key, (_, _, target)) in enumerate(sorted(terms.items(), key=lambda row: -len(row[0]))):
        pattern = r'\s+'.join(re.escape(word) for word in key.split())
        pattern = pattern.replace("'", "['’‘]")
        pattern += r'(?:es)?' if key.endswith('box') else r's?'
        # 중의적인 장비 이름은 장비·세트라는 명시적 문맥에서만 치환합니다.
        context = r'(?=\s+(?:equipment|gear|set|armor|weapon)\b)' if key in {'eternal', 'dawn', 'pitched'} else ''
        alternatives.append(f'(?P<t{index}>(?<!\\w){pattern}(?!\\w){context})')
        preferred[f't{index}'] = target
    matcher = re.compile('|'.join(alternatives), re.IGNORECASE)
    prefix = 'ZXQ' + uuid.uuid4().hex.upper()
    protected, mappings = [], []
    for text in texts:
        mapping = {}

        def replace(match):
            token = f'{prefix}B{len(protected)}T{len(mapping)}QXZ'
            mapping[token] = preferred[match.lastgroup]
            return token

        protected.append(matcher.sub(replace, text))
        mappings.append(mapping)
    return protected, mappings


def restore_google_terms(text, mapping):
    # 표식이 사라지거나 중복되면 오역·원문 누락을 숨기지 않고 전송을 중단합니다.
    for token in mapping:
        count = text.count(token)
        if count == 0:
            raise TranslationValidationError('용어사전 복원 실패: Google 응답에서 보호 표식이 누락되거나 변경됐습니다.')
        if count > 1:
            raise TranslationValidationError('용어사전 복원 실패: Google 응답에서 보호 표식이 중복됐습니다.')
    if not mapping:
        return text
    return re.sub('|'.join(map(re.escape, mapping)), lambda match: mapping[match.group()], text)


def parse_correction(text):
    fields = {}
    for line in text.strip().splitlines()[1:]:
        if not line.strip():
            continue
        name, separator, value = line.partition(':')
        name, value = name.strip(), value.strip()
        if not separator or name not in ('원문', '잘못된 번역', '원하는 표현') or name in fields:
            raise ValueError('항목명과 중복 입력을 확인해주세요.')
        if not value or len(value) > 200:
            raise ValueError('각 항목은 1~200자로 입력해주세요.')
        fields[name] = value
    if len(fields) != 3:
        raise ValueError('원문·잘못된 번역·원하는 표현을 모두 입력해주세요.')
    return fields['원문'], fields['잘못된 번역'], fields['원하는 표현']


def source_glossary(source, corrections=()):
    # 파일을 매번 읽으므로 교정표 수정은 다음 요청부터 반영됩니다. DB 복사는 하지 않습니다.
    terms = file_terms()
    terms.update({normalize_term(term): (term, wrong, preferred)
                  for term, wrong, preferred in corrections})
    remaining, rows = normalize_term(source), []
    for key, (term, wrong, preferred) in sorted(terms.items(), key=lambda item: -len(item[0])):
        # 긴 용어부터 찾고 다른 단어의 일부는 제외합니다. 일반적인 s 복수형도 허용합니다.
        pattern = re.compile(r'(?<!\w)' + re.escape(key) + r's?(?!\w)')
        if not pattern.search(remaining):
            continue
        row = {'source': term, 'preferred': preferred}
        if wrong:
            row['avoid'] = wrong
        if key in {'eternal', 'dawn', 'pitched'}:
            row['context'] = 'Equipment/set names only; not ordinary words or other names.'
        rows.append(row)
        # 이미 찾은 긴 표현 안의 짧은 용어를 중복해서 보내지 않습니다.
        remaining = pattern.sub(lambda match: ' ' * len(match.group()), remaining)
    return json.dumps(rows, ensure_ascii=False, separators=(',', ':')) if rows else ''


class CorrectionStore:
    def __init__(self, path=Path('translation-corrections.db')):
        self.path = path
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS corrections '
                       '(key TEXT PRIMARY KEY, source TEXT, wrong TEXT, preferred TEXT)')

    def list(self):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT source, wrong, preferred FROM corrections ORDER BY key').fetchall()

    def save(self, source, wrong, preferred):
        # 같은 원문은 대소문자와 관계없이 수정하며 사전 크기를 제한합니다.
        with sqlite3.connect(self.path) as db:
            key = source.casefold()
            exists = db.execute('SELECT 1 FROM corrections WHERE key=?', (key,)).fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM corrections').fetchone()[0] >= 100:
                raise ValueError('최대 100개까지 저장할 수 있습니다. 기존 항목을 정리해주세요.')
            db.execute('INSERT OR REPLACE INTO corrections VALUES (?,?,?,?)', (key, source, wrong, preferred))

    def delete(self, source):
        with sqlite3.connect(self.path) as db:
            return db.execute('DELETE FROM corrections WHERE key=?', (source.casefold(),)).rowcount

    def glossary(self, source):
        return source_glossary(source, self.list())


class CorrectionView(discord.ui.View):
    def __init__(self, owner_id, store, values, *, delete=False):
        super().__init__(timeout=180)
        self.owner_id, self.store, self.values = owner_id, store, values
        self.delete, self.finished = delete, False
        if delete:
            self.confirm.label = '삭제'
            self.confirm.style = discord.ButtonStyle.danger

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or self.finished:
            await interaction.response.send_message('요청한 관리자만 유효한 확인 버튼을 사용할 수 있습니다.', ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        self.finished = True
        self.stop()

    @discord.ui.button(label='저장', style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        # DB 기록 성공 후에만 완료로 표시합니다. 실패하면 다시 누를 수 있습니다.
        try:
            if self.delete:
                changed = self.store.delete(self.values[0])
                text = '번역교정을 삭제했습니다.' if changed else '이미 삭제된 항목입니다.'
            else:
                self.store.save(*self.values)
                text = '저장했습니다. 이후 새 요약·번역에 적용합니다. 기존 메시지는 바뀌지 않습니다.'
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        except sqlite3.Error:
            logging.error('translation_correction phase=save_failed')
            await interaction.response.send_message('저장하지 못했습니다. 잠시 후 다시 시도해주세요.', ephemeral=True)
            return
        self.finished = True
        self.stop()
        await interaction.response.edit_message(content=text, view=None)

    @discord.ui.button(label='취소', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        self.finished = True
        self.stop()
        await interaction.response.edit_message(content='취소했습니다.', view=None)


async def handle_correction_dm(bot, message):
    if not await bot.is_owner(message.author):
        await message.channel.send('봇 관리자만 번역교정을 사용할 수 있습니다.')
        return
    text = message.content.strip()
    store = bot.correction_store
    # 사용자 입력이 멘션으로 실행되지 않게 모든 응답에서 멘션을 차단합니다.
    async def send(content, **kwargs):
        await message.channel.send(content, allowed_mentions=discord.AllowedMentions.none(), **kwargs)
    try:
        if text == '번역교정 목록':
            rows = store.list()
            if not rows:
                await send('저장된 번역교정이 없습니다.')
            page = ''
            for source, wrong, preferred in rows:
                line = f'{source} → {preferred} (기존: {wrong})\n'
                if len(page) + len(line) > 1800:
                    await send(page)
                    page = ''
                page += line
            if page:
                await send(page)
        elif text.startswith('번역교정 삭제 '):
            source = text[len('번역교정 삭제 '):].strip()
            row = next((row for row in store.list() if row[0].casefold() == source.casefold()), None)
            if row is None:
                await send('일치하는 원문이 없습니다. `번역교정 목록`을 확인해주세요.')
            else:
                await send(f'삭제할 교정: {row[0]} → {row[2]}\n3분 안에 확인해주세요.',
                           view=CorrectionView(message.author.id, store, row, delete=True))
        elif text.splitlines()[0] == '번역교정' and len(text.splitlines()) > 1:
            values = parse_correction(text)
            await send(f'원문: {values[0]}\n잘못된 번역: {values[1]}\n원하는 표현: {values[2]}\n'
                       '같은 원문은 덮어씁니다. 3분 안에 저장을 눌러주세요.',
                       view=CorrectionView(message.author.id, store, values))
        else:
            await send(HELP)
    except ValueError as error:
        await send(f'{error}\n\n{HELP}')
    except sqlite3.Error:
        logging.error('translation_correction phase=read_failed')
        await send('교정 DB를 확인하지 못했습니다. 잠시 후 다시 시도해주세요.')
