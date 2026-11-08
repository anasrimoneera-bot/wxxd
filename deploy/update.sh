#!/usr/bin/env bash
# Server-side update: rebuild, restart, health check.
#   bash deploy/update.sh                    -> git pull first (needs GitHub access)
#   bash deploy/update.sh --no-pull <rev>    -> code already uploaded by deploy-update.bat
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=$(grep -E '^APP_PORT=' .env 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]')
PORT=${PORT:-8000}

if [ "${1:-}" = "--no-pull" ]; then
  echo "==> [1/4] Code uploaded from PC (version ${2:-unknown})"
  echo "${2:-unknown} $(date '+%F %T')" > deploy/VERSION
else
  echo "==> [1/4] Pulling latest code"
  git pull --ff-only
  git log -1 --format="%h %ci" > deploy/VERSION
fi

echo "==> [2/4] Building and restarting containers"
docker compose up -d --build --remove-orphans

echo "==> [3/4] Removing old unused images"
docker image prune -f >/dev/null

echo "==> [4/4] Health check on port ${PORT}"
for i in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    echo "OK: application is running"
    docker compose ps
    echo "Deployed version: $(cat deploy/VERSION)"
    exit 0
  fi
  sleep 2
done
echo "ERROR: health check failed. Last API log lines:"
docker compose logs --tail=60 api
exit 1
