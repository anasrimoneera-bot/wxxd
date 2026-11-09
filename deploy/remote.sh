#!/usr/bin/env bash
# Server-side operations, called by deploy.bat over SSH:
#   ssh root@server "bash -s -- <command> [args]" < deploy/remote.sh
# Can also be run on the server:  bash /opt/wxxd/deploy/remote.sh <command>
#
# Commands: deploy <rev> | pull-deploy <branch> | restart | status | logs | test
#           backup | list-backups | rollback <file> | clean-backups [--yes]
#
# Everything is wrapped in main() so bash reads the whole script before running
# it (the script arrives on stdin; commands must not consume it).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/wxxd}"
PKG="/tmp/wxxd-release.tar.gz"
BACKUP_DIR="$APP_DIR/data/backups"
KEEP_DAYS=7

say()  { echo "==> $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

app_port() {
  local p
  p=$(grep -E '^APP_PORT=' "$APP_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d '[:space:]' || true)
  echo "${p:-8000}"
}

mysql_running() {
  docker compose ps --status running --services 2>/dev/null </dev/null | grep -qx mysql
}

do_backup() {  # $1 = label (optional)
  if ! mysql_running; then
    say "Database container not running yet - backup skipped (first deploy?)"
    return 0
  fi
  mkdir -p "$BACKUP_DIR"
  local f="$BACKUP_DIR/wxxd-$(date +%Y%m%d-%H%M%S)${1:+-$1}.sql.gz"
  say "Backing up database -> $f"
  docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers wxxd' </dev/null | gzip > "$f"
  [ -s "$f" ] || fail "backup file is empty"
  ls -lh "$f"
}

health_check() {
  local port; port=$(app_port)
  say "Health check on port $port"
  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
      echo "OK: application is running"
      docker compose ps </dev/null
      [ -f deploy/VERSION ] && echo "Deployed version: $(cat deploy/VERSION)"
      return 0
    fi
    sleep 2
  done
  echo "ERROR: health check failed. Last API log lines:"
  docker compose logs --tail=60 api </dev/null
  return 1
}

build_and_start() {
  say "Building and restarting containers (first build takes several minutes)"
  docker compose up -d --build --remove-orphans </dev/null
  say "Removing old unused images"
  docker image prune -f </dev/null >/dev/null
  health_check
}

check_env() {
  [ -f "$APP_DIR/.env" ] || fail "$APP_DIR/.env not found. Create it first (see README)."
}

cmd_deploy() {  # code uploaded by deploy.bat to $PKG
  local rev="${1:-unknown}"
  [ -f "$PKG" ] || fail "$PKG not found (upload failed?)"
  mkdir -p "$APP_DIR"; cd "$APP_DIR"; check_env
  do_backup before-deploy
  say "Replacing code (version $rev). Kept untouched: .env, data/"
  rm -rf backend frontend deploy
  tar -xzf "$PKG" -C "$APP_DIR"
  rm -f "$PKG"
  echo "$rev $(date '+%F %T')" > deploy/VERSION
  build_and_start
}

cmd_pull_deploy() {  # like the ERP tool: pull on the server (needs GitHub access)
  local branch="${1:-main}"
  cd "$APP_DIR"; check_env
  [ -d .git ] || fail "$APP_DIR is not a git checkout - use full deploy [1] instead"
  do_backup before-deploy
  say "git fetch origin $branch (Ctrl+C if it hangs: GitHub unreachable, use [1])"
  timeout 180 git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=30 fetch origin "$branch" </dev/null
  # Tracked code is replaced; untracked .env and data/ are not touched.
  git reset --hard "origin/$branch" </dev/null
  git log -1 --format="%h %ci" > deploy/VERSION
  build_and_start
}

cmd_restart() { cd "$APP_DIR"; say "Restarting api and workers"; docker compose restart api worker </dev/null; health_check; }

cmd_status() {
  cd "$APP_DIR"
  docker compose ps </dev/null || true
  echo; echo "--- version ---"; cat deploy/VERSION 2>/dev/null || echo "unknown"
  echo; echo "--- disk ---"; df -h / | tail -1
  echo; echo "--- data size ---"; du -sh data/mysql data/backups 2>/dev/null || true
  echo; echo "--- backups ---"; ls "$BACKUP_DIR"/*.sql.gz 2>/dev/null | wc -l
}

cmd_logs() { cd "$APP_DIR"; docker compose logs -f --tail=40 api worker </dev/null; }

cmd_test() {
  echo "[OK] SSH connected"
  echo "host:    $(uname -n)"
  echo "time:    $(date)"
  echo "docker:  $(docker --version 2>/dev/null || echo MISSING)"
  echo "compose: $(docker compose version 2>/dev/null || echo MISSING)"
  echo "disk:    $(df -h / | tail -1 | awk '{print $4" free of "$2}')"
  echo
  echo "--- key files ---"
  [ -f "$APP_DIR/.env" ] && echo "[OK] $APP_DIR/.env" || echo "[MISSING] $APP_DIR/.env"
  [ -d "$APP_DIR/data" ] && echo "[OK] $APP_DIR/data" || echo "[--] $APP_DIR/data (created on first deploy)"
  [ -f /etc/nginx/conf.d/wxxd.conf ] && echo "[OK] nginx site /etc/nginx/conf.d/wxxd.conf" || echo "[MISSING] nginx site"
  curl -fsS "http://127.0.0.1:$(app_port)/api/health" >/dev/null 2>&1 && echo "[OK] app responding" || echo "[--] app not responding"
}

cmd_list_backups() { ls -lht "$BACKUP_DIR"/*.sql.gz 2>/dev/null | head -20 || echo "no backups yet"; }

cmd_rollback() {
  local name="${1:-}"; [ -n "$name" ] || fail "usage: rollback <backup file name>"
  cd "$APP_DIR"
  local f="$BACKUP_DIR/$(basename "$name")"
  [ -f "$f" ] || fail "backup not found: $f"
  mysql_running || fail "database container is not running"
  do_backup before-rollback
  say "Stopping api and workers"
  docker compose stop api worker </dev/null
  say "Restoring $f"
  gunzip -c "$f" | docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot wxxd'
  say "Starting api and workers"
  docker compose start api worker </dev/null
  health_check
  echo "=== rollback done ==="
}

cmd_clean_backups() {
  mkdir -p "$BACKUP_DIR"
  local old; old=$(find "$BACKUP_DIR" -name '*.sql.gz' -mtime +$KEEP_DAYS | sort)
  if [ -z "$old" ]; then echo "No backups older than $KEEP_DAYS days."; return 0; fi
  echo "$old"
  echo "--- $(echo "$old" | wc -l) file(s) older than $KEEP_DAYS days ---"
  if [ "${1:-}" = "--yes" ]; then
    echo "$old" | xargs rm -f
    echo "Deleted. Remaining: $(ls "$BACKUP_DIR"/*.sql.gz 2>/dev/null | wc -l)"
  fi
}

main() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    deploy)        cmd_deploy "$@" ;;
    pull-deploy)   cmd_pull_deploy "$@" ;;
    restart)       cmd_restart ;;
    status)        cmd_status ;;
    logs)          cmd_logs ;;
    test)          cmd_test ;;
    backup)        cd "$APP_DIR"; do_backup manual ;;
    list-backups)  cmd_list_backups ;;
    rollback)      cmd_rollback "$@" ;;
    clean-backups) cmd_clean_backups "$@" ;;
    *) echo "usage: remote.sh deploy|pull-deploy|restart|status|logs|test|backup|list-backups|rollback|clean-backups"; exit 2 ;;
  esac
}

main "$@"
