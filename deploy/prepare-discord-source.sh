#!/bin/sh
# 보조 1번에서 설치 승인 후 실행합니다. 봇·랭킹 서비스는 여기서 재시작하지 않습니다.
set -eu
cd /home/ubuntu/maplestory-discord-bot
python3 -m venv .venv-discord
.venv-discord/bin/python -m pip install -r discord-collector-code/requirements-discord-collector.txt
PLAYWRIGHT_SKIP_BROWSER_GC=1 .venv-discord/bin/python -m playwright install --with-deps chromium
sudo apt-get install -y x11vnc novnc websockify
printf '%s\n' '브라우저 준비 완료. 직접 로그인과 수집 검증 후 서비스를 등록하세요.'
