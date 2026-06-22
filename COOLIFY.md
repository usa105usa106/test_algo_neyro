# Coolify deploy — ChatGPT Scan Bot 30d v33_full

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
5. Press `/ping`; expected version: `v33_full`.
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


## v33_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## v33_full update
- Version is now `v33_full`.
- Generated setup headers are `Setup Gold / XAU:`, `Setup Silver / XAG:`, and `Setup Oil / WTI:`.
- Setup templates do not include a separate `Актив:` line.


## v33_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## v33_full TP compact format note
- Setup output embeds TP management directly into TP1/TP2/TP3 lines.
- Separate `Сопровождение:` section is removed.

## v33_full update
- Setup output format is now strict vertical format inside one markdown `txt` code block.
- This prevents ChatGPT from merging LIMIT and TP lines into one paragraph.

## v33_full update
- Added separate `🎯 A+ Hunter: ON/OFF` toggle.
- The hunter loop is sequential: scan/build/send must finish, then the 05:00 timer starts.
- Existing scan buttons and their task files are unchanged.


## v33_full update
- A+ Hunter universe now adds forced symbols to top-200 without duplicates.
- Existing scan buttons and existing task texts are unchanged.
