#!/usr/bin/env bash
set -uo pipefail

cd /home/ubuntu/maplestory-discord-bot

export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
export BACKFILL_ALERT_PATH="$PWD/maplebot-backfill-alert.txt"
CURRENT_LEVEL_PATH="$PWD/maplebot-backfill-current-level"

start_level=299
if [[ -r "$CURRENT_LEVEL_PATH" ]]; then
  read -r saved_level < "$CURRENT_LEVEL_PATH"
  if [[ "$saved_level" =~ ^(28[0-9]|29[0-9])$ ]]; then
    start_level="$saved_level"
  fi
fi

for ((level = start_level; level >= 280; level--)); do
  printf '%s\n' "$level" > "$CURRENT_LEVEL_PATH"
  while true; do
    echo "[$(date -u +%FT%TZ)] level $level start"
    if nice -n 10 .venv-backfill/bin/python tools/backfill_maplebot.py \
      --level "$level" \
      --ssh-host ubuntu@10.0.0.232 \
      --ssh-key /home/ubuntu/.ssh/ranking-sync \
      --remote-script /home/ubuntu/maplestory-discord-bot/tools/backfill_maplebot.py \
      --db /home/ubuntu/maplestory-discord-bot/ranking.db \
      --checkpoint "maplebot-backfill-level-$level.jsonl" \
      --delay 1 \
      --max-errors 3; then
      break
    fi
    if [[ -s "$BACKFILL_ALERT_PATH" ]]; then
      scp -q -i /home/ubuntu/.ssh/ranking-sync \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$BACKFILL_ALERT_PATH" \
        ubuntu@10.0.0.232:/home/ubuntu/maplestory-discord-bot/maplebot-backfill-alert.txt || true
    fi
    echo "[$(date -u +%FT%TZ)] level $level stopped; retrying in 5 seconds"
    sleep 5
  done
done
