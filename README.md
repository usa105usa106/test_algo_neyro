# ChatGPT Scan Bot 30d — 58_full

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


## 50_full exact-symbol update

- Gold exact: `XAU_USDT` = MEXC `GOLD(XAU)USDT`.
- BTC exact: `BTC_USDT`.
- ETH exact: `ETH_USDT`.
- Silver exact: `SILVER_USDT` = MEXC `SILVER(XAG)USDT`.
- Oil exact: `USOIL_USDT` = MEXC `OIL(WTI)USDT`.
- `XAUT_USDT` and `UKOIL_USDT` are intentionally not used as replacements because prices differ.


## 50_full exact-symbol rule

Fallbacks are disabled intentionally. XAU and XAUT have different prices, and WTI and Brent have different prices.
The bot scans only these exact trade symbols:

- Gold: `XAU_USDT` = `GOLD(XAU)USDT`
- BTC: `BTC_USDT`
- ETH: `ETH_USDT`
- Silver: `SILVER_USDT` = `SILVER(XAG)USDT`
- Oil: `USOIL_USDT` = `OIL(WTI)USDT`

If an exact symbol is unavailable, the scan should fail visibly and `/log_full` should be used for diagnostics.


## 50_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## 50_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## 50_full TP compact format note
- Setup output now embeds management directly into take-profit lines.
- TP format: `TP1: price — закрыть 33%, SL в б/у`, `TP2: price — закрыть 33%, SL в б/у`, `TP3: price — закрыть остаток`.
- Separate `Сопровождение:` section is removed from `setup_format.txt`.

## 50_full update
- `setup_format.txt` now forces the final answer to be one markdown `txt` code block.
- LIMIT orders must be one per line.
- TP1/TP2/TP3 must be one per line.
- Absolute bans were added against writing `Лимит: SHORT LIMIT 1 ... SHORT LIMIT 2 ...` or `Тейки: TP1 ... TP2 ... TP3 ...` on one line.

## 50_full update
- Added separate `🎯 A+ Hunter: ON/OFF` toggle.
- A+ Hunter runs a top-200 screener loop and waits 5 minutes after the previous loop fully finishes before the next loop starts.
- If no A+ candidate is found, no archive is created.
- If candidates are found, only candidate symbols are rendered into montage archive.
- A+ Hunter uses its own `task.txt`: true A+ only, MARKET + LIMIT plan, anti-chase rule. Existing standard and montage task files are unchanged.


## 50_full update
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


## 50_full Intraday 30d no-cache update

- Intraday default history is now `INTRADAY_DAYS_BACK=30`.
- If an old environment still has `INTRADAY_DAYS_BACK=7`, the bot forces a minimum of 30 days.
- Intraday uses fresh in-memory downloads on every scan; no parquet/cache is used.
- Intraday futures request throttle is set to `0.35s`, matching the A+ Hunter lightweight scan profile.

## 50_full Intraday progress/message order update

- Intraday now uses a very short live progress message: `Intraday scan - 10%`, `20%`, `90%`, `100% No candidates`.
- If green candidates exist, progress shows: `100% Candidates btc, eth`, then `1/3 archive`, `2/3 archive`, `3/3 archive. Ok`.
- After scan completion the progress message is deleted/replaced by the full final status.
- If a green archive is created, the final status is posted first and the archive file is sent below it.
- During the 5:00 countdown, the final status message is edited every 15 seconds; the archive remains below it until the next scan starts.

## 50_full Intraday robustness audit
- Intraday is fault-tolerant per symbol: if one custom symbol fails to download/analyze, it becomes NO_TRADE/NO_DATA in the status and the remaining symbols continue.

## 50_full Intraday hardening
- Intraday only: stricter MANUAL_REVIEW gates; green now means only clean Intraday A candidate.
- Added WAIT_CONFIRMATION for interesting zones without 5m/15m rejection/hold confirmation.
- Added TRANSITION protection so direct TREND_LONG ↔ TREND_SHORT flips require confirmation across scans.
- Intraday task now allows максимум 1 real tradable setup per archive; B/B+/A- are WAIT only.
- Intraday reports now include explicit day/24h/visible 1m/visible 15m levels and DATA_WARNING fields for chart/data sanity checks.


## 50_full Stress Test update
- Added `🧪 Stress Test` button.
- It builds one parquet-only archive named `multi_test-DDMM.zip`.
- Requested data: Binance Spot `SOLUSDT` 3y, `ADAUSDT` 3y, `XRPUSDT` 3y, `XAUTUSDT` 4 months. `SILVER` is removed.
- Collection runs with 3 async workers and 10% progress buckets.
- Stress Test creates no `task.txt`, no `setup_format.txt`, and no `intraday_task.txt`; old modes remain unchanged.

### 50_full Stress Test spot/completeness fix
- Stress Test now uses Binance Spot `/api/v3/klines` backward chunked collection for long 1m history.
- If any requested symbol returns less than 95% of the requested candles, the bot raises an error, writes details to `/log_full`, and does not send a misleading small archive.
- Other scan modes and their task files remain unchanged.

### 50_full Stress Test 2 update
- Added `🧪 Stress Test 2` button.
- Stress Test 2 collects MEXC Futures 1m parquet for the last 30 days: `SOL_USDT`, `XRP_USDT`, `ADA_USDT`, `XAUT_USDT`, `XAU_USDT`, `SILVER_USDT`, `BTC_USDT`, `ETH_USDT`.
- Collection uses 3 async workers and sends 10% progress buckets.
- If a symbol is unavailable or has incomplete 30d history, that symbol is skipped and the archive is still created from successful symbols.
- Output archive name: `multi_test2-DDMM.zip`; large archives are split into binary `.part001/.part002/...` pieces that can be reassembled.
- No task files are created or modified by Stress Test 2.

## 50_full Intraday instruction cleanup
- Intraday archive instructions no longer mark every pre-entry move toward TP as an automatic missed setup.
- LIMIT retest remains allowed if price returns to the planned entry/retest zone and closed 15m structure is still valid.
- TP1 management updated: close 33%; move remainder to BE only after a 15m close beyond TP1 in the trade direction or after TP2.
- Intraday dataframe normalization now accepts both live lower-case OHLCV and exported archive TitleCase OHLCV for safer audit/replay.

## 50_full Intraday task-only fix
- Version is now `50_full`.
- Intraday archive instructions now avoid over-confirming green MANUAL_REVIEW candidates: if the report/CSV already contain closed hold/reclaim/rejection, the manual answer must not demand an extra 15m close/retest.
- Quality 68-69 can remain a cautious Intraday A LIMIT when DATA_WARNING is absent, trap/late are low, pressure supports direction, closed confirmation exists, and RR is clean.
- Near 24h high/low is not an automatic reject; it can be the TP/liquidity target when room/RR is acceptable and there is no closed rejection/sweep against the trade.



## 50_full Intraday missed-entry / stop-quality task fix
- Version is now `50_full`.
- Intraday task only: if TP1 was reached before limit fill, the old setup is MISSED; new entry requires fresh closed 15m confirmation.
- Intraday task only: stop quality is checked for every symbol; SL inside normal noise / wick zone / liquidity magnet is forbidden.
- Do not tighten SL just to keep RR attractive; if structural SL ruins RR, answer WAIT.

## 50_full Intraday replay-polished update
- Historical 5-minute replay was performed on 30d 1m parquet for BTC, ETH, XAU, SILVER, USOIL, GRAM, XRP and ADA.
- Intraday now uses closed HTF candles, pressure/trap/late/efficiency gates, structural stop audit, local-room gate and MEXC fee-drag gate.
- Only Intraday logic/task and Intraday duplicate cooldown were changed. See `INTRADAY_STAGE_01_10_REPORT.md`.


## 51_full Intraday full-audit fix
- Fixed dead Sweep Reversal gating by using directional trap/late risk instead of penalizing the valid sweep itself.
- Fixed Range Edge direction: proximity to the lower edge no longer blocks LONG through the SHORT late-risk score, and vice versa.
- RANGE now requires a measured closed-15m range with width, path-efficiency, and repeated edge touches; residual states no longer become RANGE automatically.
- Entry/stop/targets are playbook-aware: VWAP for Trend Pullback, reclaimed prior edge for Sweep Reversal, confirmed range boundary for Range Edge.
- Local room uses the nearest closed-15m pivot/target obstacle instead of the farthest 2h high/low.
- Trend Pullback now has a hard minimum of 0.40R to the nearest closed 15m obstacle; the former strong-breakout exception was removed after it admitted low-room stop-outs in replay.
- SILVER Trend Pullback requires pressure edge >=23 because every filled 20-21 edge candidate in the exact 30d audit stopped out.
- Live data freshness and recent 1m gaps/duplicates are checked before a green candidate is allowed.
- Archive HTF CSVs are closed-only (`15m_closed`, `1h_closed`, `4h_closed`).
- Duplicate suppression fingerprints entry/stop/TP/rank/pressure and is committed only after successful Telegram delivery.
- Only Intraday logic, Intraday archive task, Intraday sending/cooldown handling, version, and documentation changed.
- Full Stage 11–12 audit and all-scan replay: `INTRADAY_STAGE_11_12_AUDIT_51_REPORT.md`.


## 52_full Intraday frequency + structural-stop update
- Only Intraday engine/task and the application version were changed.
- Trend Pullback replay gates are frequency-balanced by asset.
- Trend stops use 4h of closed 15m structure with a 2.30 ATR floor for crypto/alts and 2.40 ATR for metals/energy; maximum 3.60 ATR.
- Trend quality/room/fee gates: 66 / 0.20R / 0.80R. Sweep/Range keep the stricter 51_full reversal gates.
- SILVER Trend Pullback is disabled after a negative exact replay; SILVER Sweep/Range remain available.
- Historical section for `52_full`.


## 54_full Intraday full-replay + live-safety update
- Full chronological replay covers every eligible 5-minute scan across all 8 assets; no candidate preselection.
- ETH and ADA Trend Pullback require at least 0.30R room to the nearest closed 15m obstacle; other Trend assets keep 0.20R.
- Rolling 24h levels and data-integrity checks are timestamp-based; missing 1m chunks abort the Intraday symbol instead of being skipped.
- Each symbol uses fresh exchange server time, the loop aligns to the next 5-minute boundary, Intraday OFF/ON clears hysteresis, archive cooldown commits only actually included candidates, and build temp directories are removed.
- Intraday archives export closed 15m/1h/4h/1D CSV context.
- `/ping` must report `54_full`.


## 55_full Intraday duplicate/frequency safety fix
- Engine thresholds and trade logic are unchanged from 54_full.
- Duplicate cooldown is 45 minutes and uses quantized Entry/Stop structure, so a materially new setup is not hidden merely because symbol/playbook/direction match the previous one.
- Stale duplicate keys are pruned from runtime memory.
- Sweep diagnostics use the actual reversal trap limit.
- Trend stop lookback documentation is synchronized with code: 20 closed 15m candles = 5 hours.
- Historical 55_full release expected `/ping` = `55_full`; current release is listed below.


## Intraday 55_full
- Цикл: полный скан -> новый таймер 5:00 -> следующий полный скан; без привязки к часам.
- Trend: all supported assets LONG/SHORT; Sweep/Range доступны всем активам.
- LIMIT на 0.15 ATR15 глубже VWAP; стоп за 5 часами закрытой 15m структуры (20 свечей), минимум 2.30/2.40 ATR15, максимум 4.00 ATR15.
- TP: 0.80R / 1.60R / 2.40R.
- Частота повышается входом и профилем, а не микростопом.


## 56_full Intraday stale-LIMIT safety fix
- Only Intraday engine, Intraday archive task, Intraday pending-LIMIT monitoring, version, and documentation changed. Other modes and their tasks are unchanged.
- Trend local-room floor is raised minimally from 0.10R to 0.12R. It is intentionally not raised to TP1's 0.80R, so frequency is not strangled. Sweep/Range room gates remain unchanged.
- A plan is rejected before publication if price has already travelled 0.60R from the proposed LIMIT toward TP1.
- After archive delivery, pending LIMIT ideas are monitored across scans and persisted in `state/intraday_pending_limits.json`.
- Before fill, the bot tells the user to remove an old LIMIT if price travels 0.60R toward TP1, the next scan becomes WAIT/TRANSITION/opposite, or Entry/Stop materially rebuild.
- If the scenario remains valid, the LIMIT gets one complete 15m candle after publication (15–30 minutes), then expires. This avoids both 90-minute stale fills and a frequency-killing 1-minute TTL.
- Stops remain structural: Trend minimum 2.30 ATR15 for crypto/alts or 2.40 ATR15 for metals/energy; maximum 4.00 ATR15. TP remains 0.80R / 1.60R / 2.40R.
- `/ping` must report `56_full`.

## 57_full Intraday custom-symbol candle fix
- Only the Intraday candle downloader, Intraday task header/rule, version, and documentation changed. Trading logic, stops, targets, frequency thresholds, other modes, and their tasks are unchanged.
- `int gram`, `int pol`, `int dogs`, and other exact MEXC Futures symbols are not restricted by a fixed whitelist.
- Intraday downloads newest candles first instead of assuming that the contract existed at the beginning of the full 30-day window. This fixes valid newer/renamed contracts being marked `NO_DATA` because an old leading MEXC page was empty.
- If newest-first paging is unavailable or stale, Intraday performs a second tolerant exact-symbol forward pass. It never substitutes another coin or a similarly named contract.
- Missing or stale recent candles still create `DATA_WARNING`/WAIT; the fix does not convert broken data into a green setup.
- `/ping` must report `57_full`.


## 58_full Intraday full code audit
- Only Intraday runtime/state handling, Intraday plan validation, Intraday archive task/deadline, application version, and documentation changed. Other modes and their task prompts are unchanged.
- Persisted pending LIMITs now restore their setup cooldown after restart/ON, so the same archive cannot be resent and silently receive a new lifetime.
- A pending LIMIT is cancelled on `NO_DATA` or a missing completed-scan report; an old plan is never left active when fresh candles cannot confirm it.
- Invalid/non-finite/tick-collapsed Entry/Stop/TP geometry is forced to `WAIT_CONFIRMATION` before a green archive can be built.
- `int ...` now stops any active old-symbol Intraday cycle before replacing the list and immediately restarts on the new symbols. One-character exact MEXC symbols are accepted too.
- The archive task and runtime pending monitor use one shared expiry timestamp, including correct exact-15m-boundary handling.
- Trading thresholds are unchanged: Trend local room remains `0.12R`; structural stops remain 2.30/2.40 ATR minimum and 4.00 ATR maximum; targets remain 0.80/1.60/2.40R.
- Parquet equivalence audit: 288 checkpoints across BTC/BCH/ETH/XAU/SILVER/USOIL produced zero decision/Entry/Stop/TP differences versus 57_full.
- `/ping` must report `58_full`.
