"""서버에서 운영자가 직접 실행하는 홈페이지 최초 설정 도구."""
import getpass
import os
from pathlib import Path
import re
import subprocess
import sys
import warnings

CONFIG = Path('/etc/sherbet/website.env')
DROPIN = Path('/etc/systemd/system/maple-discord-bot.service.d/60-website.conf')
UNIT_TEXT = '[Service]\nEnvironmentFile=/etc/sherbet/website.env\n'


def config_text(client_id, secret):
    # 줄바꿈·따옴표 등을 허용하지 않아 환경 설정에 다른 항목을 끼워 넣지 못하게 합니다.
    if not re.fullmatch(r'[0-9]{17,20}', client_id):
        raise ValueError('Application ID 형식을 확인해주세요.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{16,256}', secret):
        raise ValueError('Client Secret 형식을 확인해주세요. Bot Token이 아닙니다.')
    return f'SHERBET_WEBSITE_ENABLED=1\nDISCORD_CLIENT_ID={client_id}\nDISCORD_CLIENT_SECRET={secret}\n'


def create_private_file(path, text, mode):
    # 기존 파일은 읽거나 덮어쓰지 않으며 심볼릭 링크도 따라가지 않습니다.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    if os.name != 'posix' or os.geteuid() != 0 or not sys.stdin.isatty():
        raise ValueError('메인 서버의 대화형 SSH 터미널에서 sudo로 실행해주세요.')
    for folder in (CONFIG.parent, DROPIN.parent):
        if folder.is_symlink():
            raise ValueError('설정 경로가 링크입니다. 자동 설정을 중단합니다.')
    if CONFIG.exists() or CONFIG.is_symlink() or DROPIN.exists() or DROPIN.is_symlink():
        raise ValueError('기존 홈페이지 설정 파일이 있습니다. 덮어쓰지 않았습니다.')
    state = subprocess.run(['systemctl', 'show', 'maple-discord-bot', '--property=LoadState', '--value'],
                           check=True, stdout=subprocess.PIPE, text=True)
    if state.stdout.strip() != 'loaded':
        raise ValueError('기존 봇 서비스를 찾지 못했습니다. 자동 설정을 중단합니다.')
    print('샤벳 홈페이지 최초 설정입니다. 비밀값을 채팅에 보내지 마세요.')
    print('서버에 전용 설정 파일을 생성하고 봇 서비스를 재시작합니다.')
    print('홈페이지는 127.0.0.1:8766에만 열립니다. 인터넷에는 공개하지 않습니다.')
    client_id = input('Application ID 입력: ').strip()
    # 숨김 입력을 지원하지 않는 터미널이면 평문 입력으로 전환하지 않고 중단합니다.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        secret = getpass.getpass('OAuth2 Client Secret 입력 (화면에 표시되지 않음): ')
        repeated = getpass.getpass('Client Secret 다시 입력: ')
    if secret != repeated:
        raise ValueError('두 입력이 다릅니다. 파일은 저장하지 않았습니다.')
    text = config_text(client_id, secret)
    if input('설정 파일 생성·서비스 연결·봇 재시작을 적용할까요? [yes 입력]: ').strip() != 'yes':
        print('취소했습니다. 변경 사항이 없습니다.')
        return
    # Secret 파일은 root만 읽을 수 있고 systemd가 실행 환경에 전달합니다.
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    DROPIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    create_private_file(CONFIG, text, 0o600)
    create_private_file(DROPIN, UNIT_TEXT, 0o644)
    subprocess.run(['systemctl','daemon-reload'], check=True)
    subprocess.run(['systemctl','restart','maple-discord-bot'], check=True)
    subprocess.run(['systemctl','is-active','maple-discord-bot'], check=True)
    print('저장·재시작 완료. 이제 홈페이지 응답과 Discord 재접속 확인이 필요합니다.')


def self_test():
    assert 'SHERBET_WEBSITE_ENABLED=1' in config_text('123456789012345678','example-secret-1234')
    for cid, secret in [('abc','example-secret-1234'), ('123456789012345678','bad\nENV=1'),
                        ('123456789012345678','a.b.c'), ('123456789012345678','')]:
        try:
            config_text(cid, secret)
        except ValueError:
            continue
        raise AssertionError('Invalid configuration accepted')
    print('설정값 검증 5개 통과. 실제 파일이나 서비스는 변경하지 않았습니다.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--self-test']:
        self_test()
    else:
        try:
            main()
        except (ValueError, OSError, subprocess.CalledProcessError, getpass.GetPassWarning) as error:
            # 입력값·환경 전체·스택을 출력하지 않습니다.
            print(str(error) if isinstance(error, ValueError) else '설정에 실패했습니다. 입력값을 공유하지 말고 실패 여부만 알려주세요.')
            sys.exit(1)
        except (EOFError, KeyboardInterrupt):
            print('\n입력을 중단했습니다.')
            sys.exit(1)
