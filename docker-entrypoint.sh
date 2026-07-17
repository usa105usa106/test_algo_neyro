#!/usr/bin/env sh
set -eu

mkdir -p "${DATA_ROOT:-/app/storage}" "${GMAIL_BACKUP_ROOT:-/app/storage_backup}" /tmp/matplotlib

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN is empty. Set it in Coolify environment variables." >&2
  exit 1
fi

if [ -z "${ADMIN_TELEGRAM_ID:-}" ]; then
  echo "WARNING: ADMIN_TELEGRAM_ID is empty. Bot will allow all users who know the bot token." >&2
fi

printf 'v66 primary storage: %s\n' "${DATA_ROOT:-/app/storage}"
printf 'v66 backup storage: %s\n' "${GMAIL_BACKUP_ROOT:-/app/storage_backup}"
printf 'v66 OAuth listen: %s:%s\n' "${GMAIL_OAUTH_LISTEN_HOST:-0.0.0.0}" "${GMAIL_OAUTH_LISTEN_PORT:-8080}"
env | grep '^SERVICE_URL_GMAIL' | sed 's/=.*$/=<generated>/' || true


nginx -t -c /app/nginx.conf
nginx -c /app/nginx.conf

# Fail fast if the stable port-80 router did not start.
python -c "import urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); o.open('http://127.0.0.1/router-healthz', timeout=3).read()"

"$@" &
app_pid=$!

shutdown() {
  kill -TERM "$app_pid" 2>/dev/null || true
  nginx -s quit -c /app/nginx.conf 2>/dev/null || true
}
trap shutdown INT TERM

set +e
wait "$app_pid"
status=$?
set -e
nginx -s quit -c /app/nginx.conf 2>/dev/null || true
exit "$status"
