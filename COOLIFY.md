# Coolify deploy — ChatGPT Scan Bot 30d 55_full

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
5. Press `/ping`; expected version: `55_full`.
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


## 50_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## 50_full update
- Version is now `50_full`.
- Generated setup headers are `Setup Gold / XAU:`, `Setup Silver / XAG:`, and `Setup Oil / WTI:`.
- Setup templates do not include a separate `Актив:` line.


## 50_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## 50_full TP compact format note
- Setup output embeds TP management directly into TP1/TP2/TP3 lines.
- Separate `Сопровождение:` section is removed.

## 50_full update
- Setup output format is now strict vertical format inside one markdown `txt` code block.
- This prevents ChatGPT from merging LIMIT and TP lines into one paragraph.

## 50_full update
- Added separate `🎯 A+ Hunter: ON/OFF` toggle.
- The hunter loop is sequential: scan/build/send must finish, then the 05:00 timer starts.
- Existing scan buttons and their task files are unchanged.


## 50_full update
- A+ Hunter universe now adds forced symbols to top-200 without duplicates.
- Existing scan buttons and existing task texts are unchanged.

## 50_full Intraday update
- Added visible Intraday 05:00 countdown updated every 15 seconds, after the previous scan/archive fully finishes.
- Intraday status is deleted and re-sent at the bottom every scan.
- Green candidate archives are sent as one zip per scan: `intraday_btc-HHMM_DDMM.zip` for one symbol, `intraday_multi-HHMM_DDMM.zip` for multiple symbols.
- Default Intraday symbols: BTC, ETH, XAU, SILVER, USOIL.
- Text commands: `int pol, xrp, sol` to replace the Intraday list; `int del` to restore defaults.
- All Intraday scan details are written to `/log_full`.


## 50_full Intraday 30d no-cache update

- Intraday default history is `INTRADAY_DAYS_BACK=30`.
- If an old environment still has `INTRADAY_DAYS_BACK=7`, the bot forces a minimum of 30 days.
- Intraday does not use parquet/cache; every scan downloads fresh candles in memory.
- Intraday uses A+ Hunter-style public futures throttle: serialized requests with `0.35s` pause.

## 50_full Intraday progress/message order update

- Intraday status now displays simple progress only: `10%`, `20%`, `90%`, `100% No candidates` or `100% Candidates ...`.
- If there are candidates, archive progress is shown as `1/3 archive`, `2/3 archive`, `3/3 archive. Ok`.
- Progress is deleted/replaced by the full scan status at the end of the cycle.
- Final status is posted before the archive; the archive file is sent below the status.
- Countdown still updates every 15 seconds on the final status.

## 50_full Intraday robustness audit
- Intraday is fault-tolerant per symbol: if one custom symbol fails to download/analyze, it becomes NO_TRADE/NO_DATA in the status and the remaining symbols continue.

## 50_full Intraday hardening
- Intraday only: stricter MANUAL_REVIEW, WAIT_CONFIRMATION, TRANSITION regime protection, chart/data sanity fields.
- Old scan/Montage/A+ Hunter task files are unchanged.


## 50_full Stress Test update
- Version is now `50_full`.
- Added `🧪 Stress Test` button for one parquet-only `multi_test-DDMM.zip` archive.
- Stress Test collects Binance Spot `SOLUSDT`/`ADAUSDT`/`XRPUSDT` 3y and `XAUTUSDT` 4 months with 3 async workers and progress buckets. `SILVER` is removed from Stress Test.
- It does not create or modify task files and does not change old scan/montage/A+ Hunter/Intraday modes.

### 50_full Stress Test spot/completeness fix
- Stress Test now uses Binance Spot `/api/v3/klines` backward chunked collection for long 1m history.
- If any requested symbol returns less than 95% of the requested candles, the bot raises an error, writes details to `/log_full`, and does not send a misleading small archive.
- Old scan/intraday/task files remain unchanged.

### 50_full Stress Test 2 update
- Added `🧪 Stress Test 2` button.
- Stress Test 2 collects MEXC Futures 1m parquet for 30d: SOL/XRP/ADA/XAUT/XAU/SILVER/BTC/ETH.
- Missing or incomplete symbols are skipped; successful parquet files are still zipped and sent.
- Large `multi_test2-DDMM.zip` files are split into binary `.partNNN` pieces, same reassembly principle as the existing bot.

## 50_full Intraday instruction cleanup
- Version is now `50_full`.
- Intraday archive instructions: no instant “missed setup” if price returns to a valid retest zone with closed 15m structure intact.
- TP1 management: close 33%; move remainder to BE only after 15m close beyond TP1 in direction or after TP2.
- Intraday OHLCV normalization accepts both live lower-case data and exported archive CSV TitleCase data.

## 50_full Intraday task-only fix
- Version is now `50_full`.
- Only Intraday archive task/instructions were adjusted: avoid unnecessary extra confirmation for already-confirmed green MANUAL_REVIEW candidates and treat 24h high/low as possible target/liquidity when RR is enough.



## 50_full Intraday missed-entry / stop-quality task fix
- Version is now `50_full`.
- Intraday task only: TP1 before limit fill = missed setup; new entry needs fresh closed 15m confirmation.
- Intraday task only: no tight stop inside noise/magnet for any asset; use structural SL or WAIT.

## 50_full Intraday replay-polished deployment
- Deploy normally; no new environment variables are required.
- `/ping` must report `50_full`.
- After deploy, keep Intraday in shadow/manual-review mode first and compare new archives with `INTRADAY_STAGE_01_10_REPORT.md`.


## 51_full Intraday deployment
- `/ping` must report `51_full`.
- Intraday archives now export closed-only HTF CSV files and retry Telegram delivery without activating duplicate cooldown after a failed send.
- Other modes and their tasks are unchanged.

### 51_full replay hardening
- Trend Pullback local room is a hard >=0.40R gate; no strong-breakout override.
- SILVER Trend Pullback requires pressure edge >=23.
- Other modes and tasks are unchanged.
- Validation report: `INTRADAY_STAGE_11_12_AUDIT_51_REPORT.md`.


## 52_full Intraday frequency + structural-stop update
- Only Intraday engine/task and the application version were changed.
- Trend Pullback replay gates are frequency-balanced by asset.
- Trend stops use 4h of closed 15m structure with a 2.30 ATR floor for crypto/alts and 2.40 ATR for metals/energy; maximum 3.60 ATR.
- Trend quality/room/fee gates: 66 / 0.20R / 0.80R. Sweep/Range keep the stricter 51_full reversal gates.
- SILVER Trend Pullback is disabled after a negative exact replay; SILVER Sweep/Range remain available.
- `/ping` must report `54_full`.


## 54_full deploy note
- This build includes the full chronological Intraday replay fix and live data/cooldown/temp safety changes.
- Other scan modes and their task prompts are unchanged.
- `/ping` must report `54_full`.


## Intraday 54_full
- Цикл: полный скан -> новый таймер 5:00 -> следующий полный скан; без привязки к часам.
- Trend: all supported assets LONG/SHORT; Sweep/Range доступны всем активам.
- LIMIT на 0.15 ATR15 глубже VWAP; стоп за 6 часами закрытой 15m структуры, минимум 2.30/2.40 ATR15, максимум 4.00 ATR15.
- TP: 0.80R / 1.60R / 2.40R.
- Частота повышается входом и профилем, а не микростопом.


## 55_full deploy note
- Intraday-only change: setup-aware 45-minute duplicate cooldown, stale-key pruning, corrected Sweep reversal diagnostic, and synchronized 5-hour Trend lookback text.
- Trading thresholds and all non-Intraday modes/tasks are unchanged.
- `/ping` must report `55_full`.
