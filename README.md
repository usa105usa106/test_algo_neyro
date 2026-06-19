# ChatGPT Scan Bot 30d — v17_full

Telegram bot for manual / semi-automatic trading analysis with ChatGPT.

## What changed

Old 3-year BTC/ETH research buttons are removed from the menu. The bot now has only 30-day scan buttons, service buttons, and text-based custom symbol scans.

## Main buttons

```text
[ 📊 Gold 30d ]        [ ₿ BTC 30d ]
[ Ξ ETH 30d ]          [ 🥈 Silver 30d ]
[ 🛢 Oil 30d ]         [ 🔥 Multi 5 assets 30d ]
[ ⚙️ Symbols check ]
[ /api ] [ /log_full ] [ /ping ] [ /reset ]
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
/api       — optional encrypted MEXC API key storage for meta/status only
/log_full  — send logs and export index
/ping      — health check, version, RAM/CPU/disk
/reset     — stop active task and clear temporary state
/status    — hidden/debug status command
```

No trading endpoints exist in this bot: no `place_order`, no `cancel_order`, no live trading.


### Newly listed symbols / partial history
If a symbol has less history than `DAYS_BACK` (for example Gold only has ~24 days on MEXC), the bot continues if it downloaded at least `MIN_EFFECTIVE_DAYS` days. Default: `20`. It records a warning in `manifest.json` and `/log_full`.


## v17_full exact-symbol update

- Gold exact: `XAU_USDT` = MEXC `GOLD(XAU)USDT`.
- BTC exact: `BTC_USDT`.
- ETH exact: `ETH_USDT`.
- Silver exact: `SILVER_USDT` = MEXC `SILVER(XAG)USDT`.
- Oil exact: `USOIL_USDT` = MEXC `OIL(WTI)USDT`.
- `XAUT_USDT` and `UKOIL_USDT` are intentionally not used as replacements because prices differ.


## v17_full exact-symbol rule

Fallbacks are disabled intentionally. XAU and XAUT have different prices, and WTI and Brent have different prices.
The bot scans only these exact trade symbols:

- Gold: `XAU_USDT` = `GOLD(XAU)USDT`
- BTC: `BTC_USDT`
- ETH: `ETH_USDT`
- Silver: `SILVER_USDT` = `SILVER(XAG)USDT`
- Oil: `USOIL_USDT` = `OIL(WTI)USDT`

If an exact symbol is unavailable, the scan should fail visibly and `/log_full` should be used for diagnostics.


## v17_full update
- Fixed text aliases: `gold`/`xau` -> `XAU_USDT`, `oil`/`wti` -> `USOIL_USDT`, `silver`/`xag` -> `SILVER_USDT`.
- Custom symbols are exact-only. Writing `xaut` scans `XAUT_USDT`; it is not silently replaced by `XAU_USDT`.
- Removed confusing exact-candidate remapping in archive resolution.

- Custom XAUT/UKOIL scans keep their own setup labels (`Setup XAUT`, `Setup UKOIL`) instead of generic Gold/Oil.


## v17_full format note
- Setup output format uses `SHORT LIMIT` and `LONG LIMIT` instead of `SELL LIMIT` / `BUY LIMIT`.
- Limit orders and TP1/TP2/TP3 are written in a column.

## v17_full TP compact format note
- Setup output now embeds management directly into take-profit lines.
- TP format: `TP1: price — закрыть 33%, SL в б/у`, `TP2: price — закрыть 33%, SL в б/у`, `TP3: price — закрыть остаток`.
- Separate `Сопровождение:` section is removed from `setup_format.txt`.
