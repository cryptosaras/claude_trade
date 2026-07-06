#!/usr/bin/env bash
# Runs on the VPS from cron every 2 minutes: pull new commits and apply them.
# Strategy/config/ui changes apply instantly (bind mounts + engine hot-reload).
# app/ or requirements changes trigger a rebuild.
# Convergence is tracked in ops/.applied_sha (written only after a successful
# apply), NOT via HEAD: if a rebuild fails (network/OOM), the next run retries
# instead of leaving containers silently running old app/ code forever.
set -euo pipefail
cd /opt/claude_trade

# one run at a time: a rebuild can take longer than the 2-min cron interval
exec 9>ops/.autopull.lock
flock -n 9 || exit 0

git fetch origin main --quiet
REMOTE=$(git rev-parse origin/main)
APPLIED=$(cat ops/.applied_sha 2>/dev/null || git rev-parse HEAD)
[ "$APPLIED" = "$REMOTE" ] && exit 0

CHANGED=$(git diff --name-only "$APPLIED" "$REMOTE" 2>/dev/null \
          || echo "app/unknown-base-forcing-rebuild")
git reset --hard origin/main --quiet
echo "$(date -u +%FT%TZ) pulled $REMOTE, changed: $CHANGED" >> ops/autopull.log

if echo "$CHANGED" | grep -qE '^(app/|requirements|Dockerfile|docker-compose)'; then
  docker compose up -d --build >> ops/autopull.log 2>&1
  echo "$(date -u +%FT%TZ) rebuilt containers" >> ops/autopull.log
fi
echo "$REMOTE" > ops/.applied_sha
