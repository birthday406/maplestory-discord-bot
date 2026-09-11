"""전용 브라우저로 공식 announcements의 화면을 읽어 완성된 파일만 전송합니다."""
import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from discord_news import CHANNEL_URL, fingerprint, validate_message
from ranking_worker import sync_ready_batches


def write_packet(outbox, rows, status):
    # 메인이 쓰다 만 파일을 읽지 않게 임시 파일에서 원자적으로 교체합니다.
    outbox.mkdir(parents=True, exist_ok=True)
    packet = {'version': 1, 'observed': datetime.now(timezone.utc).isoformat(),
              'status': status, 'messages': rows}
    path = outbox / f'{time.time_ns()}.part'
    with path.open('w', encoding='utf-8') as stream:
        json.dump(packet, stream, ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.replace(path.with_suffix('.jsonl'))


async def run(args):
    from playwright.async_api import async_playwright
    profile = args.profile.resolve()
    profile.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        profile.chmod(0o700)
    script = Path(__file__).with_name('discord_news_dom.js').read_text(encoding='utf-8')
    target = os.getenv('DISCORD_NEWS_SYNC_TARGET')
    if target and 'discord-news' not in target.rsplit(':', 1)[-1]:
        raise ValueError('A separate discord-news inbox is required')
    sync_task = None
    async with async_playwright() as playwright:
        # 로그인 프로필은 이 서버에만 보관합니다. 기존 개인 브라우저 프로필은 복사하지 않습니다.
        async with await playwright.chromium.launch_persistent_context(
            str(profile), executable_path=playwright.chromium.executable_path, headless=not args.login, locale='en-US', timezone_id='UTC',
            chromium_sandbox=True, accept_downloads=False,
        ) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(CHANNEL_URL, wait_until='domcontentloaded', timeout=60000)
            last_signature = None
            last_emit = 0
            failures = 0
            try:
                while True:
                    try:
                        if not args.login and page.url == CHANNEL_URL:
                            await page.keyboard.press('Control+End')
                        rows = await page.evaluate(script)
                        rows = [validate_message(r) for r in rows]
                        status = 'ok' if rows else 'login_required' if '/login' in page.url else 'unavailable'
                        signature = (status, tuple((r['id'], fingerprint(r)) for r in rows))
                        # 첫 화면을 기준점으로 보내고 이후 변경은 2초 간격으로 감지합니다.
                        if signature != last_signature or time.monotonic() - last_emit >= 30:
                            write_packet(args.outbox, rows, status)
                            logging.info('discord_source status=%s messages=%s', status, len(rows))
                            last_signature, last_emit = signature, time.monotonic()
                        failures = 0 if rows else failures + 1
                        if args.once and rows:
                            return
                        # 로그인 화면에서는 사용자의 인증을 기다립니다. 오류 복구만 낮은 빈도로 합니다.
                        if failures >= 30 and not args.login and status == 'unavailable':
                            await page.goto(CHANNEL_URL, wait_until='domcontentloaded', timeout=60000)
                            failures = 0
                    except Exception as error:
                        if page.is_closed():
                            raise
                        logging.warning('discord_source error_type=%s', type(error).__name__)
                        write_packet(args.outbox, [], 'error')
                        await asyncio.sleep(30)
                    if target and (sync_task is None or sync_task.done()):
                        if sync_task:
                            await asyncio.gather(sync_task, return_exceptions=True)
                        sync_task = asyncio.create_task(asyncio.wait_for(sync_ready_batches(
                            args.outbox, target=target, ssh_key=os.getenv('DISCORD_NEWS_SYNC_SSH_KEY')), 30))
                    await asyncio.sleep(2)
            finally:
                if sync_task:
                    sync_task.cancel()
                    await asyncio.gather(sync_task, return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=Path('discord-news-profile'))
    parser.add_argument('--outbox', type=Path, default=Path('discord-news-outbox'))
    parser.add_argument('--login', action='store_true', help='원격 화면에서 직접 로그인하는 창을 엽니다.')
    parser.add_argument('--once', action='store_true', help='첫 정상 화면 한 번만 기록하고 종료합니다.')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except Exception as error:
        logging.error('discord_source stopped error_type=%s', type(error).__name__)
        raise SystemExit(1)
