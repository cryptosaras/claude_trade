#!/usr/bin/env bash
# Runs on the VPS from cron every 2 minutes: pull new commits and apply them.
# Strategy/config/ui changes apply instantly (bind mounts + engine hot-reload).
# app/ or requirements changes trigger a rebuild.
set -euo pipefail
cd /opt/claude_trade

git fetch origin main --quiet
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

CHANGED=$(git diff --name-only "$LOCAL" "$REMOTE")
git reset --hard origin/main --quiet
echo "$(date -u +%FT%TZ) pulled $REMOTE, changed: $CHANGED" >> ops/autopull.log

if echo "$CHANGED" | grep -qE '^(app/|requirements|Dockerfile|docker-compose)'; then
  docker compose up -d --build >> ops/autopull.log 2>&1
  echo "$(date -u +%FT%TZ) rebuilt containers" >> ops/autopull.log
fi
