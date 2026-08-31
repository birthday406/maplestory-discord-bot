#!/usr/bin/env bash
set -uo pipefail

cd /home/ubuntu/maplestory-discord-bot

export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"
export BACKFILL_ALERT_PATH="$PWD/maplebot-backfill-alert.txt"

for level in {299..280}; do
  echo "[$(date -u +%FT%TZ)] level $level start"
  if ! nice -n 10 .venv-backfill/bin/python tools/backfill_maplebot.py \
    --level "$level" \
    --ssh-host ubuntu@10.0.0.232 \
    --ssh-key /home/ubuntu/.ssh/ranking-sync \
    --remote-script /home/ubuntu/maplestory-discord-bot/tools/backfill_maplebot.py \
    --db /home/ubuntu/maplestory-discord-bot/ranking.db \
    --checkpoint "maplebot-backfill-level-$level.jsonl" \
    --delay 1 \
    --max-errors 3; then
    if [[ -s "$BACKFILL_ALERT_PATH" ]]; then
      scp -q -i /home/ubuntu/.ssh/ranking-sync \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
        "$BACKFILL_ALERT_PATH" \
        ubuntu@10.0.0.232:/home/ubuntu/maplestory-discord-bot/maplebot-backfill-alert.txt || true
    fi
    exit 1
  fi
done
