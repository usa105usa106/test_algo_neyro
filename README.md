# ChatGPT Scan Bot 30d — 39_full

Telegram bot for manual / semi-automatic trading analysis with ChatGPT.

## What changed

Old 3-year BTC/ETH research buttons are removed from the menu. The bot now has only 30-day scan buttons, service buttons, and text-based custom symbol scans.

## Main buttons

```text
[ 📊 Gold 30d ]        [ ₿ BTC 30d ]
[ Ξ ETH 30d ]          [ 🥈 Silver 30d ]
[ 🛢 Oil 30d ]         [ 🔥 Multi 5 assets 30d ]
[ 🧩 Montage: ON/OFF ] [ 🎯 A+ Hunter: ON/OFF ]
[ 📊 Intraday: ON/OFF ]
[ ⚙️ Symbols check ]
[ /help ] [ /api ] [ /log_full ] [ /ping ] [ /reset ]
```

## Data source

MEXC Futures public market endpoints:

```text
https://api.mexc.com/api/v1/contract/kline/{symbol}
```

Base interval: `Min1` / bot config `1m`.

The bot downloads 1m OHLC for the last 30 days and builds exactly 5 charts per asset.
Each chart title includes the latest close price and the chart draws a dashed horizontal line at that latest close.

```text
1D   — requested 30d / available actual days
4H   — requested 30d / available actual days
1H   — requested 30d / available actual days
15m  — last 7 days
1m   — last 24 hours
```

The 30-day 1m parquet file is also included in the archive, so ChatGPT can rebuild all timeframes if needed.

## Buttons and symbols

```text
Gold 30d   -> XAU_USDT (GOLD(XAU)USDT)
BTC 30d    -> BTC_USDT
ETH 30d    -> ETH_USDT
Silver 30d -> SILVER_USDT
Oil 30d    -> USOIL_USDT (OIL(WTI)USDT)
Multi 30d  -> XAU_USDT + BTC_USDT + ETH_USDT + SILVER_USDT + USOIL_USDT
```

No fallback/substitution is used. `⚙️ Symbols check` verifies only the exact symbols listed above.


## Custom text symbol scan

Main buttons stay focused on the 5 priority assets. For another MEXC Futures USDT symbol, send a short text message in the chat:

```text
xrp
sol
bnb
XRP_USDT
```

The bot converts `xrp` to exact symbol `XRP_USDT`, collects the same 30d 1m archive, builds the same 5 charts, and sends:

```text
chatgpt_scan-xrp-HHMM_DDMM.zip
```

No fallback/substitution is used for custom symbols either. If the exact contract is unavailable on MEXC Futures, the scan fails visibly and `/log_full` should be used for diagnostics.

## Archive name

Archive name uses UTC+3 / Moscow-style creation time:

```text
chatgpt_scan-gold-HHMM_DDMM.zip
chatgpt_scan-multi-HHMM_DDMM.zip
```

Example:

```text
chatgpt_scan-gold-2326_1906.zip
```


## Setup headers

Generated instruction files require these human-readable setup headers:

```text
Setup Gold / XAU:
Setup BTC:
Setup ETH:
Setup Silver / XAG:
Setup Oil / WTI:
```

The setup answer must not include a separate `Актив:` line.

## Archive contents

```text
manifest.json
task.txt
candles/<SYMBOL>_1m_30d.parquet
charts/<SYMBOL>/<SYMBOL>_1D.png
charts/<SYMBOL>/<SYMBOL>_4H.png
charts/<SYMBOL>/<SYMBOL>_1H.png
charts/<SYMBOL>/<SYMBOL>_15m.png
charts/<SYMBOL>/<SYMBOL>_1m.png
meta/api_status.json
meta/exchange_info.json
```

`task.txt` instructs ChatGPT to respond only with the ready Elite 5 Rejection / Rostislav-style setup. If there is no setup, ChatGPT should answer:

```text
wait, сейчас лучше не входить, подожди и пришли новый архив.
```

## Rate limits / retries

The MEXC futures client is intentionally serialized and slow. If MEXC returns HTTP/app-level rate limit or “too frequent” errors, the bot increases request pause and retries.

Default futures pause starts around 1.25 seconds between requests.

## Service commands

```text
/start     — push a fresh button panel to the bottom of the chat
/help      — show bot commands and Intraday instructions
/api       — optional encrypted MEXC API key storage for meta/status only
/log_full  — send logs and export index
/ping      — health check, version, RAM/CPU/disk
/reset     — stop active task and clear temporary state
/status    — hidden/debug status command
```

No trading endpoints exist in this bot: no `place_order`, no `cancel_order`, no live trading.


### Newly listed symbols / partial history
If a symbol has less history than `DAYS_BACK` (for example Gold only has ~24 days on MEXC), the bot continues if it downloaded at least `MIN_EFFECTIVE_DAYS` days. Default: `20`. It records a warning in `manifest.json` and `/log_full`.


## 39_full exact-symbol update

- Gold exact: `XAU_USDT` = MEXC `GOLD(XAU)USDT`.
- BTC exact: `BTC_USDT`.
- ETH exact: `ETH_USDT`.
- Silver exact: `SILVER_USDT` = MEXC `SILVER(XAG)USDT`.
- Oil exact: `USOIL_USDT` = MEXC `OIL(WTI)USDT`.
- `XAUT_USDT` and `UKOIL_USDT` are intentionally not used as replacements because prices differ.


## 39_full exact-symbol rule

Fallbacks are disabled intentionally. XAU and XAUT have different prices, and WTI and Brent have different prices.
The bot scans only these exact trade symbols:

- Gold: `XAU_USDT` = `GOLD(XAU)USDT`
- BTC: `BTC_USDT`
- ETH: `ETH_USDT`
- Silver: `SILVER_USDT` = `SILVER(XAG)USDT`
- Oil: `USOIL_USDT` = `OIL(WTI)USDT`

If an exact symbol is unavailable, the scan should fail visibly and `/log_full` should be used for diagnostics.


## 39_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## 39_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## 39_full TP compact format note
- Setup output now embeds management directly into take-profit lines.
- TP format: `TP1: price — закрыть 33%, SL в б/у`, `TP2: price — закрыть 33%, SL в б/у`, `TP3: price — закрыть остаток`.
- Separate `Сопровождение:` section is removed from `setup_format.txt`.

## 39_full update
- `setup_format.txt` now forces the final answer to be one markdown `txt` code block.
- LIMIT orders must be one per line.
- TP1/TP2/TP3 must be one per line.
- Absolute bans were added against writing `Лимит: SHORT LIMIT 1 ... SHORT LIMIT 2 ...` or `Тейки: TP1 ... TP2 ... TP3 ...` on one line.

## 39_full update
- Added separate `🎯 A+ Hunter: ON/OFF` toggle.
- A+ Hunter runs a top-200 screener loop and waits 5 minutes after the previous loop fully finishes before the next loop starts.
- If no A+ candidate is found, no archive is created.
- If candidates are found, only candidate symbols are rendered into montage archive.
- A+ Hunter uses its own `task.txt`: true A+ only, MARKET + LIMIT plan, anti-chase rule. Existing standard and montage task files are unchanged.


## 39_full update
- A+ Hunter universe is now top-200 most liquid USDT futures plus forced symbols without duplicates.
- Forced symbols are resolved only from real MEXC Futures ticker symbols: NVDA/NVIDIA, TSLA, USOIL, SILVER, XAU, BTC, ETH, SP500/US500/SPX, GOOGL/GOOGLE, NAS100/US100/NASDAQ.
- Existing scan buttons and existing task texts are unchanged.

## Intraday mode

New button: `📊 Intraday: ON/OFF`.

- Scans exact symbols every 5 minutes after the previous scan/archive fully finishes.
- Data window: fresh 30-day 1m download on every scan, no parquet/cache reuse.
- Speed profile: same as A+ Hunter public futures profile — serialized requests with 0.35s throttle, not a separate thread pool.
- Default Intraday list: `BTC_USDT`, `ETH_USDT`, `XAU_USDT`, `SILVER_USDT`, `USOIL_USDT`.
- The visible timer is copied from A+ Hunter logic: `05:00`, updated every 15 seconds.
- The Intraday status is one Telegram message. Every scan it is deleted and sent again at the bottom of the chat.
- Creates an archive only for green `MANUAL_REVIEW` candidates.
- If 1 green candidate appears: `intraday_btc-HHMM_DDMM.zip` / `intraday_xau-HHMM_DDMM.zip` etc.
- If 2+ green candidates appear in the same scan, they are packed into one zip: `intraday_multi-HHMM_DDMM.zip`.
- Archive timestamp is taken at the end of archive creation.
- Auto-trading is OFF. The bot has no order placement endpoints.
- Old standard scan, Montage mode, A+ Hunter, and their old `task.txt`/`setup_format.txt` files are not changed.
- The whole Intraday process is logged into `full.log`, available through `/log_full`.

Intraday list commands:

```text
int pol, xrp, sol
int pol, int xrp, int sol
int del
```

`int ...` replaces the default Intraday list with the supplied symbols. `int del` restores the default 5 symbols.

Optional env:

```env
INTRADAY_SCAN_INTERVAL_SEC=300
INTRADAY_DAYS_BACK=30
```


## 39_full Intraday 30d no-cache update

- Intraday default history is now `INTRADAY_DAYS_BACK=30`.
- If an old environment still has `INTRADAY_DAYS_BACK=7`, the bot forces a minimum of 30 days.
- Intraday uses fresh in-memory downloads on every scan; no parquet/cache is used.
- Intraday futures request throttle is set to `0.35s`, matching the A+ Hunter lightweight scan profile.

## 39_full Intraday progress/message order update

- Intraday now uses a very short live progress message: `Intraday scan - 10%`, `20%`, `90%`, `100% No candidates`.
- If green candidates exist, progress shows: `100% Candidates btc, eth`, then `1/3 archive`, `2/3 archive`, `3/3 archive. Ok`.
- After scan completion the progress message is deleted/replaced by the full final status.
- If a green archive is created, the final status is posted first and the archive file is sent below it.
- During the 5:00 countdown, the final status message is edited every 15 seconds; the archive remains below it until the next scan starts.

## 39_full Intraday robustness audit
- Intraday is fault-tolerant per symbol: if one custom symbol fails to download/analyze, it becomes NO_TRADE/NO_DATA in the status and the remaining symbols continue.

## 39_full Intraday hardening
- Intraday only: stricter MANUAL_REVIEW gates; green now means only clean Intraday A candidate.
- Added WAIT_CONFIRMATION for interesting zones without 5m/15m rejection/hold confirmation.
- Added TRANSITION protection so direct TREND_LONG ↔ TREND_SHORT flips require confirmation across scans.
- Intraday task now allows максимум 1 real tradable setup per archive; B/B+/A- are WAIT only.
- Intraday reports now include explicit day/24h/visible 1m/visible 15m levels and DATA_WARNING fields for chart/data sanity checks.


## 39_full Stress Test update
- Added `🧪 Stress Test` button.
- It builds one parquet-only archive named `multi_test-DDMM.zip`.
- Requested data: `SOL_USDT` 3y, `XRP_USDT` 3y, `ADA_USDT` 3y, `XAUT_USDT` 1y, `SILVER_USDT` 183d.
- Collection runs with 3 async workers and 10% progress buckets.
- Stress Test creates no `task.txt`, no `setup_format.txt`, and no `intraday_task.txt`; old modes remain unchanged.
