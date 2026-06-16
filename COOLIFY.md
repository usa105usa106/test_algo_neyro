# Coolify deploy — no-folders v8

Upload all files directly into the repository root. Do not create `src` or `systemd` folders.

## Coolify setup

1. Create a GitHub repository.
2. Upload all files from this archive into the root of the repository.
3. In Coolify, create a new application from the GitHub repository.
4. Use Docker Compose or Dockerfile deploy.
5. Add only these required environment variables:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ADMIN_TELEGRAM_ID=your_numeric_telegram_id
```

You do **not** need to add `MEXC_MARKET_TYPE` or `MIN_COVERAGE_RATIO`; they are hardcoded in `config.py`:

```text
MEXC market type: futures
MEXC futures base URL: https://api.mexc.com
Minimum coverage: 0.80
```

6. Deploy.
7. Open Telegram and send `/start` to the bot.
8. Press `Reset`, then `Parquet`, then `Charts`.

## Persistent storage

The Docker Compose file mounts:

```text
mexc_research_storage:/app/storage
```

Keep this volume so Parquet files, logs, encrypted API state and generated archives survive redeploys.
