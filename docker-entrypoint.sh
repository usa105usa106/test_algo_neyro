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

printf 'v67 primary storage: %s\n' "${DATA_ROOT:-/app/storage}"
printf 'v67 backup storage: %s\n' "${GMAIL_BACKUP_ROOT:-/app/storage_backup}"
printf 'v67 OAuth listen: %s:%s\n' "${GMAIL_OAUTH_LISTEN_HOST:-0.0.0.0}" "${GMAIL_OAUTH_LISTEN_PORT:-8080}"
env | grep '^SERVICE_URL_GMAIL' | sed 's/=.*$/=<generated>/' || true

# Gmail routing must never prevent the Telegram bot and its other modes from
# starting. Start nginx best-effort, then replace PID 1 with the bot process.
# Docker/Coolify healthcheck independently decides whether Traefik may route
# Gmail HTTP traffic to port 80.
if nginx -t -c /app/nginx.conf; then
  if nginx -c /app/nginx.conf; then
    echo "v67 Gmail router started on container port 80"
  else
    echo "WARNING: Gmail router failed to start; Telegram bot will continue without public Gmail callback." >&2
  fi
else
  echo "WARNING: Gmail router config check failed; Telegram bot will continue without public Gmail callback." >&2
fi

exec "$@"
