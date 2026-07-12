# Intraday 55_full — bidirectional/frequency/code audit

## Scope

- Audited only Intraday engine, Intraday candidate delivery/cooldown, Intraday archive task, and version/docs.
- Other scan modes, A+ Hunter, Ratio logic, standard archive tasks, market downloader behavior, and trading execution logic were not changed.
- Replay data: 30-day 1m parquet supplied for BTC, ETH, XAU, SILVER, USOIL, and BCH. The first 48 hours were used as warm-up, leaving 8,064 five-minute scans / 27.9965 measured days per symbol.
- POL parquet was not supplied and external MEXC access was unavailable, so no numerical POL frequency is claimed.

## Bidirectional result

- BCH produced both directions: **16 LONG + 10 SHORT** distinct green episodes.
- `int bch, pol` parses to `BCH_USDT, POL_USDT`. Both symbols use the same direction-agnostic engine path; there is no permanent per-asset LONG/SHORT ban.

## Exact green-setup frequency on supplied parquet

| Symbol | Distinct green episodes | LONG | SHORT | Per day | Per 30d | Old 60m coarse cooldown notifications | Old notifications/day |
|---|---:|---:|---:|---:|---:|---:|---:|
| BCH | 26 | 16 | 10 | 0.93 | 27.9 | 21 | 0.75 |
| BTC | 27 | 15 | 12 | 0.96 | 28.9 | 21 | 0.75 |
| ETH | 34 | 18 | 16 | 1.21 | 36.4 | 27 | 0.96 |
| XAU | 25 | 9 | 16 | 0.89 | 26.8 | 19 | 0.68 |
| SILVER | 68 | 38 | 30 | 2.43 | 72.9 | 47 | 1.68 |
| USOIL | 70 | 35 | 35 | 2.50 | 75.0 | 52 | 1.86 |

These are distinct deterministic `MANUAL_REVIEW` setup episodes after trend-flip hysteresis, not guaranteed LIMIT fills. Actual executed trades can be lower because a LIMIT may not fill or the human review may reject the archive.

## Bugs found and fixed

1. **New setup hidden as an old duplicate.** The old per-candidate key was only `symbol + playbook + direction`, with a 60-minute cooldown. A rebuilt setup with materially changed Entry/Stop could therefore be suppressed. `55_full` uses a quantized structural key containing regime, direction, Entry, and Stop, and restores the cooldown to 45 minutes. Small drift remains suppressed; a materially new structure passes.
2. **Sweep diagnostic used the wrong trap threshold.** Promotion used reversal trap `<=24`, but the WAIT explanation checked the Trend limit `>32`. Values 25–32 therefore produced an inaccurate generic reason. Both Sweep directions now use `MAX_REVERSAL_TRAP_RISK`.
3. **Trend lookback text disagreed with code.** The code uses 20 closed 15m candles = 5 hours, while one comment/README line said 6 hours. Documentation is synchronized to 5 hours; calculation is unchanged.
4. **Runtime key growth prevention.** Because setup-aware keys can change over time, stale duplicate keys are pruned after six hours or eight cooldown periods, whichever is longer.

## What was deliberately not changed

- No Intraday threshold was loosened or tightened: quality, pressure, trap, late, HTF, room, fee, stop, Entry, and TP formulas are identical to `54_full`.
- No non-Intraday mode/task was modified.
- The replay shows the engine is already near the requested intraday frequency for BTC/BCH/XAU and inside it for ETH; Silver/Oil generate more raw episodes, while duplicate removal brings visible archive frequency closer to 1–2/day.

## Verification

- `compileall`: passed.
- Imports: `config`, `intraday_engine`, `intraday_archive`, `mexc`, `bot`, `archive_builder` passed with project requirements installed.
- 48 sampled historical engine comparisons (`54_full` vs `55_full`) across all six supplied symbols: **0 decision/plan differences**.
- Parser test: `int bch, pol` -> `BCH_USDT`, `POL_USDT`.
- Duplicate-key smoke test: minor Entry/Stop drift stays the same key; materially changed structure receives a new key.
- Intraday archive smoke test: BCH green archive built, task header is `INTRADAY_TASK 55_full`, 11 files included, temporary build directory removed.

## Practical conclusion

The engine was not reduced to one trade per week. On the supplied BCH month it generated about **0.93 distinct green setup/day (27.9 per 30 days)** and both directions. The real frequency loss was in candidate delivery: the old coarse 60-minute duplicate key reduced BCH visibility from 26 episodes to 21 notifications on this replay. `55_full` fixes that suppression without opening weaker trades.
