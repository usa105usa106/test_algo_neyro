# Coolify deploy

This repo is ready for Coolify + GitHub deployment.

## Recommended Coolify mode

Use **Docker Compose** deployment from this repository. The included `docker-compose.yml` builds the Dockerfile and creates a persistent volume at:

```text
/app/storage
```

That folder stores:

```text
candles/
charts/
exports/
logs/
secrets/
state/
work/
```

Without persistent storage, Parquet files, archives, encrypted API state, and logs can disappear after redeploy.

## Required environment variables in Coolify

Set these in Coolify environment variables:

```text
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ADMIN_TELEGRAM_ID=your_numeric_telegram_user_id
```

Optional variables:

```text
SYMBOLS=BTCUSDT,ETHUSDT
DAYS_BACK=365
BASE_INTERVAL=1m
MEXC_BASE_URL=https://api.mexc.com
TELEGRAM_SEND_LIMIT_MB=48
TZ=UTC
SECRET_ENCRYPTION_KEY=
```

`SECRET_ENCRYPTION_KEY` can stay empty. The bot will generate a Fernet key and store it in `/app/storage/state/fernet.key`. If you want a fixed key, generate it locally:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Coolify setup steps

1. Push this folder to a GitHub repo.
2. In Coolify, create a new resource from GitHub.
3. Select this repo.
4. Choose Docker Compose if Coolify asks for build mode.
5. Add the required environment variables.
6. Deploy.
7. Open Telegram and send `/start` to the bot.

## No public port needed

The bot uses Telegram long polling. It does not need an HTTP port or domain.

## Buttons

```text
Api       — save read-only MEXC API key/secret in encrypted storage
Parquet   — create research_input_BTC_ETH_data_*.zip
Charts    — create research_input_BTC_ETH_charts_*.zip
Log_full  — create log_full_*.zip with logs and export index
Reset     — cancel current task, clear runtime/API state, clean temp work folder
Status    — show current state and latest exports
```

## Important safety note

This collector has no trading code. There are no place order, cancel order, withdraw, transfer, or leverage-changing endpoints.


## Progress messages

During long jobs the bot sends Telegram progress updates at 0/10/20/.../100% for both `Parquet` and `Charts`. Use `Log_full` if a job fails or stops updating.
