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

printf 'v65 primary storage: %s\n' "${DATA_ROOT:-/app/storage}"
printf 'v65 backup storage: %s\n' "${GMAIL_BACKUP_ROOT:-/app/storage_backup}"
printf 'v65 OAuth listen: %s:%s\n' "${GMAIL_OAUTH_LISTEN_HOST:-0.0.0.0}" "${GMAIL_OAUTH_LISTEN_PORT:-80}"
env | grep '^SERVICE_URL_GMAIL' | sed 's/=.*$/=<generated>/' || true

exec "$@"
