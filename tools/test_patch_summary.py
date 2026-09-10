"""봇을 시작하거나 공지를 보내지 않고 기존 요약·번역을 한 번 실행합니다."""

import argparse
import asyncio
import getpass
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from openai import AsyncOpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from maple_bot import MapleNewsBot, NEWS_MODEL, format_news_summary
from translation_corrections import GLOSSARY_PATH, file_terms, TranslationValidationError


async def run(source: Path, title: str) -> int:
    # .env는 읽지 않습니다. 없는 키만 터미널에서 숨김 입력받고 파일에는 저장하지 않습니다.
    keys = {}
    try:
        for name in ('OPENAI_API_KEY',):
            value = os.environ.get(name, '').strip()
            if not value:
                if not sys.stdin.isatty():
                    print('키 입력이 필요합니다. 대화형 터미널에서 직접 실행해주세요.')
                    return 2
                value = getpass.getpass(f'{name} 입력 (화면에 표시되지 않음): ').strip()
            if not value:
                print('빈 키가 입력되어 테스트를 취소했습니다.')
                return 2
            keys[name] = value
    except (EOFError, KeyboardInterrupt):
        print('\n키 입력을 취소했습니다.')
        return 2

    started = time.monotonic()
    stage = '용어사전·원문 검사'
    try:
        # 요약 비용이 발생하기 전에 사전 파일을 검사합니다. 운영과 같은 요약 함수를 씁니다.
        terms = file_terms()
        print(f'용어사전 적용: {GLOSSARY_PATH} ({len(terms)}개)', flush=True)
        body = source.read_text(encoding='utf-8')
        if not body.strip():
            raise ValueError('Empty source')
        # 자동 재시도를 끄고 운영 봇 객체·Discord 연결·저장 상태는 만들지 않습니다.
        async with AsyncOpenAI(api_key=keys['OPENAI_API_KEY'], max_retries=0, timeout=120) as client:
            bot = SimpleNamespace(openai=client)
            print(f'{NEWS_MODEL} 요약 요청 시작', flush=True)
            stage = 'GPT 요약'
            korean = format_news_summary(await MapleNewsBot.summarize(bot, {'name': title, 'body': body}))
            print(f'전체 완료: {time.monotonic() - started:.1f}초\n\n{korean}')
            return 0
    except TranslationValidationError as error:
        # 우리가 정의한 고정 안내문만 출력하므로 키나 API 원문은 노출하지 않습니다.
        print(f'실패 단계: {stage}\n원인: {error}\n자동 재시도하지 않습니다.')
        return 1
    except Exception as error:
        # 오류 본문에 인증 정보가 섞일 수 있어 오류 종류만 표시합니다.
        print(f'실패 단계: {stage}. 오류 종류: {type(error).__name__}. 자동 재시도하지 않습니다.')
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path,
                        default=ROOT / 'artifacts/patch-v271-test/source.txt')
    parser.add_argument('--title', default="v.271 - MapleStory x Frieren: Beyond Journey's End Patch Notes")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.source, args.title)))
