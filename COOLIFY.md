# Coolify deploy — ChatGPT Scan Bot 30d v16.5-chatgpt-scan-30d-exact-symbols-checked

## Required env

```text
TELEGRAM_BOT_TOKEN=...
ADMIN_TELEGRAM_ID=...
DATA_ROOT=/data/storage
TELEGRAM_SEND_LIMIT_MB=48
DAYS_BACK=30
BASE_INTERVAL=1m
MEXC_BASE_URL=https://api.mexc.com
SECRET_ENCRYPTION_KEY=
```

## Run

1. Deploy container.
2. Open Telegram.
3. Send `/start`.
4. The latest message will contain the current button panel at the bottom of the chat.
5. Press `/ping`; expected version: `v16.5-chatgpt-scan-30d-exact-symbols-checked`.
6. Press a scan button, for example `📊 Gold 30d`.

## Output

The bot sends archive files like:

```text
chatgpt_scan-gold-HHMM_DDMM.zip
chatgpt_scan-multi-HHMM_DDMM.zip
```

Stamp is UTC+3 / MSK style: `HHMM_DDMM`.

## Data source

MEXC Futures public candles:

```text
https://api.mexc.com/api/v1/contract/kline/{symbol}
```

One asset = 1m candles for 30 days + 5 charts.
Multi = 5 assets × 5 charts = 25 charts.

If MEXC rate-limits or returns “too frequent”, the bot increases pause and retries.


### Newly listed symbols / partial history
If a symbol has less history than `DAYS_BACK` (for example Gold only has ~24 days on MEXC), the bot continues if it downloaded at least `MIN_EFFECTIVE_DAYS` days. Default: `20`. It records a warning in `manifest.json` and `/log_full`.
