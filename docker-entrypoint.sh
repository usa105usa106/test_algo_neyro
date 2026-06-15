#!/usr/bin/env sh
set -eu

mkdir -p "${DATA_ROOT:-/app/storage}" /tmp/matplotlib

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN is empty. Set it in Coolify environment variables." >&2
  exit 1
fi

if [ -z "${ADMIN_TELEGRAM_ID:-}" ]; then
  echo "WARNING: ADMIN_TELEGRAM_ID is empty. Bot will allow all users who know the bot token." >&2
fi

exec "$@"
