# INTRADAY 57_full — custom symbol candle audit

## Scope
- Fixed only the Intraday candle download path, Intraday task wording/version, application version, and documentation.
- Trading decisions, LONG/SHORT logic, local-room `0.12R`, structural stops, targets, stale-LIMIT cancellation, all other modes, and all non-Intraday tasks are unchanged.

## Root cause
`56_full` used strict forward 30-day paging with `fail_on_empty_chunk=True`. A valid contract could have an empty old page at the beginning of the requested 30-day window (for example, before listing or because the MEXC start/end endpoint did not serve that historical segment). After three identical empty responses, the whole symbol was converted to `NO_DATA` even when current candles existed.

This is a data-pagination bug, not a fixed-symbol whitelist bug. The command parser already accepted arbitrary exact symbols:
- `int gram` -> `GRAM_USDT`
- `int pol` -> `POL_USDT`
- `int gram, int pol` -> `GRAM_USDT`, `POL_USDT`
- `int dogs, xrp, sol` -> `DOGS_USDT`, `XRP_USDT`, `SOL_USDT`

## Fix
1. Intraday now requests the newest exact-contract candles first and pages backward through the requested window.
2. If newest-first data is empty or stale, Intraday performs a second tolerant forward pass that skips old empty pre-listing chunks instead of aborting the symbol.
3. The freshest exact-symbol frame is selected. No similarly named contract and no substitute coin is used.
4. Recent missing/stale candles are still detected by the existing Intraday integrity checks and remain `WAIT`/`DATA_WARNING`; incomplete data cannot become a green setup.
5. Symbols with available recent history shorter than 30 days can still be analyzed. The engine itself still requires at least 120 one-minute candles and enough recent structure for a valid setup.

## Verification
- Python compile/import audit: PASS.
- Parser tests for GRAM/POL/DOGS/XRP/SOL: PASS.
- Mock newest-first test: PASS (`GRAM_USDT`, 300 recent rows, no forward fallback).
- Mock empty-newest fallback test: PASS (`POL_USDT`, 500 recent rows selected from tolerant forward paging).
- Mock late-listing backward paging test: PASS (2 days of available data inside a 5-day requested window; 2,881 rows retained despite older empty pages).
- Synthetic short-history engine test: PASS (3 continuous days / 4,320 rows analyzed as `TREND_LONG / WAIT_PULLBACK`, not `NO_DATA`).
- Diff audit: only `bot.py`, `mexc.py`, Intraday task/version comments, `config.py`, and docs changed.

## Live API limitation
The build sandbox had no DNS access to `api.mexc.com`, so a live GRAM/POL request could not be executed here. The exact failure path shown in the screenshot was reproduced with mocked MEXC page behavior and is covered by both newest-first and tolerant-forward tests.

## Deploy check
- Expected `/ping`: `57_full`.
- No new environment variables.
