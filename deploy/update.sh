#!/usr/bin/env bash
# Server-side update: pull latest code, rebuild, restart, health check.
# Usage (on the server):  bash /opt/wxxd/deploy/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-8000}

echo "==> [1/4] Pulling latest code"
git pull --ff-only

echo "==> [2/4] Building and restarting containers"
docker compose up -d --build --remove-orphans

echo "==> [3/4] Removing old unused images"
docker image prune -f >/dev/null

echo "==> [4/4] Health check on port ${PORT}"
for i in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "OK: application is running"
    docker compose ps
    git log -1 --format="Deployed version: %h %ci"
    exit 0
  fi
  sleep 2
done
echo "ERROR: health check failed. Last API log lines:"
docker compose logs --tail=60 api
exit 1
