#!/bin/sh
# 모든 접속은 loopback에만 바인딩합니다. SSH 터널로 사용자만 로그인합니다.
set -eu
cd /home/ubuntu/maplestory-discord-bot
umask 077
mkdir -p outputs/discord-login
if [ -S /tmp/.X11-unix/X99 ]; then
    printf '%s\n' '화면 :99가 이미 사용 중입니다. 기존 세션을 확인하세요.'
    exit 1
fi
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp > outputs/discord-login/display.log 2>&1 &
display_pid=$!
trap 'kill "$display_pid" 2>/dev/null || true' EXIT INT TERM
# 가상 화면이 준비되기 전에 브라우저·VNC가 접속해 종료되지 않게 기다립니다.
attempt=0
while [ ! -S /tmp/.X11-unix/X99 ]; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 10 ] || exit 1
    kill -0 "$display_pid" || exit 1
    sleep 1
done
x11vnc -display :99 -localhost -rfbport 5901 -forever -shared -nopw > outputs/discord-login/vnc.log 2>&1 &
vnc_pid=$!
trap 'kill "$vnc_pid" "$display_pid" 2>/dev/null || true' EXIT INT TERM
websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5901 > outputs/discord-login/web.log 2>&1 &
web_pid=$!
trap 'kill "$web_pid" "$vnc_pid" "$display_pid" 2>/dev/null || true' EXIT INT TERM
DISPLAY=:99 .venv-discord/bin/python discord-collector-code/discord_news_collector.py --login --once
