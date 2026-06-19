# Coolify deploy — ChatGPT Scan Bot 30d v17_full

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
5. Press `/ping`; expected version: `v17_full`.
6. Press a scan button, for example `📊 Gold 30d`, or send a text symbol like `xrp` for a custom exact-symbol archive.

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

One asset = 1m candles for 30 days + 5 charts with latest-close price line/title.
Multi = 5 assets × 5 charts = 25 charts.
Custom text symbol, e.g. `xrp`, creates the same one-asset archive for `XRP_USDT`.

If MEXC rate-limits or returns “too frequent”, the bot increases pause and retries.


### Newly listed symbols / partial history
If a symbol has less history than `DAYS_BACK` (for example Gold only has ~24 days on MEXC), the bot continues if it downloaded at least `MIN_EFFECTIVE_DAYS` days. Default: `20`. It records a warning in `manifest.json` and `/log_full`.


## v17_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## v17_full update
- Version is now `v17_full`.
- Generated setup headers are `Setup Gold / XAU:`, `Setup Silver / XAG:`, and `Setup Oil / WTI:`.
- Setup templates do not include a separate `Актив:` line.
