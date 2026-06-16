# MEXC Research Collector Bot — no-folders v8

Telegram data-collector for BTC/ETH research archives. All project files are in the repository root; no `src/` or `systemd/` folders are required.

## What changed in v8

- `MEXC_MARKET_TYPE=futures` is now hardcoded in `config.py`.
- `MIN_COVERAGE_RATIO=0.80` is now hardcoded in `config.py`.
- Futures base URL is hardcoded as `https://api.mexc.com`.
- You do not need to add these variables in Coolify.

## Required Coolify variables

Only these are required:

```env
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ADMIN_TELEGRAM_ID=your_numeric_telegram_id
```

Optional:

```env
SYMBOLS=BTCUSDT,ETHUSDT
DAYS_BACK=365
BASE_INTERVAL=1m
TELEGRAM_SEND_LIMIT_MB=48
SECRET_ENCRYPTION_KEY=optional_fernet_key
```

## Buttons

- `Api` — save/read-only MEXC key data if you want, not required for public klines.
- `Parquet` — creates `research_input_BTC_ETH_data_*.zip`.
- `Charts` — creates `research_input_BTC_ETH_charts_*.zip`.
- `Log_full` — sends full logs/runtime state.
- `Reset` — cancels jobs and clears runtime state.
- `Status` — shows current job state.

## Expected output

`Parquet` downloads about one year of 1m futures klines for BTC/ETH. For a valid one-year archive, expect roughly 525k candles per symbol. If coverage is below 80%, the bot stops and reports the issue instead of silently creating a bad small archive.
