# Coolify deploy — ChatGPT Scan Bot 30d 39_full

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
5. Press `/ping`; expected version: `39_full`.
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


## 39_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## 39_full update
- Version is now `39_full`.
- Generated setup headers are `Setup Gold / XAU:`, `Setup Silver / XAG:`, and `Setup Oil / WTI:`.
- Setup templates do not include a separate `Актив:` line.


## 39_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## 39_full TP compact format note
- Setup output embeds TP management directly into TP1/TP2/TP3 lines.
- Separate `Сопровождение:` section is removed.

## 39_full update
- Setup output format is now strict vertical format inside one markdown `txt` code block.
- This prevents ChatGPT from merging LIMIT and TP lines into one paragraph.

## 39_full update
- Added separate `🎯 A+ Hunter: ON/OFF` toggle.
- The hunter loop is sequential: scan/build/send must finish, then the 05:00 timer starts.
- Existing scan buttons and their task files are unchanged.


## 39_full update
- A+ Hunter universe now adds forced symbols to top-200 without duplicates.
- Existing scan buttons and existing task texts are unchanged.

## 39_full Intraday update
- Added visible Intraday 05:00 countdown updated every 15 seconds, after the previous scan/archive fully finishes.
- Intraday status is deleted and re-sent at the bottom every scan.
- Green candidate archives are sent as one zip per scan: `intraday_btc-HHMM_DDMM.zip` for one symbol, `intraday_multi-HHMM_DDMM.zip` for multiple symbols.
- Default Intraday symbols: BTC, ETH, XAU, SILVER, USOIL.
- Text commands: `int pol, xrp, sol` to replace the Intraday list; `int del` to restore defaults.
- All Intraday scan details are written to `/log_full`.


## 39_full Intraday 30d no-cache update

- Intraday default history is `INTRADAY_DAYS_BACK=30`.
- If an old environment still has `INTRADAY_DAYS_BACK=7`, the bot forces a minimum of 30 days.
- Intraday does not use parquet/cache; every scan downloads fresh candles in memory.
- Intraday uses A+ Hunter-style public futures throttle: serialized requests with `0.35s` pause.

## 39_full Intraday progress/message order update

- Intraday status now displays simple progress only: `10%`, `20%`, `90%`, `100% No candidates` or `100% Candidates ...`.
- If there are candidates, archive progress is shown as `1/3 archive`, `2/3 archive`, `3/3 archive. Ok`.
- Progress is deleted/replaced by the full scan status at the end of the cycle.
- Final status is posted before the archive; the archive file is sent below the status.
- Countdown still updates every 15 seconds on the final status.

## 39_full Intraday robustness audit
- Intraday is fault-tolerant per symbol: if one custom symbol fails to download/analyze, it becomes NO_TRADE/NO_DATA in the status and the remaining symbols continue.

## 39_full Intraday hardening
- Intraday only: stricter MANUAL_REVIEW, WAIT_CONFIRMATION, TRANSITION regime protection, chart/data sanity fields.
- Old scan/Montage/A+ Hunter task files are unchanged.


## 39_full Stress Test update
- Version is now `39_full`.
- Added `🧪 Stress Test` button for one parquet-only `multi_test-DDMM.zip` archive.
- Stress Test collects `SOL_USDT`/`XRP_USDT`/`ADA_USDT` 3y, `XAUT_USDT` 1y, `SILVER_USDT` 183d with 3 async workers and progress buckets.
- It does not create or modify task files and does not change old scan/montage/A+ Hunter/Intraday modes.
