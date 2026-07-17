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

printf 'v70 primary storage: %s\n' "${DATA_ROOT:-/app/storage}"
printf 'v70 backup storage: %s\n' "${GMAIL_BACKUP_ROOT:-/app/storage_backup}"
printf 'v70 OAuth listen: %s:%s\n' "${GMAIL_OAUTH_LISTEN_HOST:-0.0.0.0}" "${GMAIL_OAUTH_LISTEN_PORT:-80}"
printf 'v70 Gmail public URL: %s\n' "${SERVICE_URL_GMAILAUTH_80:-${COOLIFY_URL:-<missing>}}"

exec "$@"
