# INTRADAY 56_full — stale LIMIT and frequency audit

## Final decision

- Trend `local_room` was raised only from **0.10R to 0.12R**. A larger 0.15–0.35R floor was rejected because replay showed a material frequency loss on several assets.
- The main protection is not a large room filter: a pending LIMIT is cancelled when the next Intraday scan no longer confirms the same green setup, when price moves **0.60R** toward TP1 before fill, or after one complete 15m candle (15–30 minutes).
- Structural stop rules remain unchanged: Trend minimum **2.30 ATR15** for crypto/alts, **2.40 ATR15** for XAU/SILVER/USOIL, maximum **4.00 ATR15**; no micro-stop relaxation was added.
- Only Intraday engine/task/runtime handling and version/docs were changed. Other modes and tasks are untouched.

## Frequency replay

Counts below are distinct green `MANUAL_REVIEW` episodes, not fills or profit claims. The replay ran 8,064 five-minute decision points per asset over about 28 days.

| Asset | 0.10R control | 0.12R final | Per day | Per 30d | LONG / SHORT | Change |
|---|---:|---:|---:|---:|---:|---:|
| BTC | 27 | 26 | 0.93 | 27.9 | 14 / 12 | -3.7% |
| BCH | 26 | 25 | 0.89 | 26.8 | 16 / 9 | -3.8% |
| ETH | 34 | 33 | 1.18 | 35.4 | 17 / 16 | -2.9% |
| XAU | 26 | 24 | 0.86 | 25.7 | 9 / 15 | -7.7% |
| SILVER | 68 | 64 | 2.29 | 68.6 | 38 / 26 | -5.9% |
| USOIL | 70 | 62 | 2.21 | 66.4 | 31 / 31 | -11.4% |

At 0.12R the mode remains active: BTC/BCH/XAU stay near one setup per day, ETH is above one, and Silver/Oil remain above two. POL follows the same LONG/SHORT code path, but no POL parquet was supplied, so no numerical POL frequency is claimed.

## Supplied BTC incident

- Initial scan: `TREND_SHORT / MANUAL_REVIEW`, Entry `63976.57`, Stop `64213.32`, TP1 `63787.17`, local room `0.274R`.
- Next scan at 10:11:26 UTC: `TREND_SHORT / WAIT_PULLBACK`.
- In 56_full the bot emits a cancellation notice on that next scan and removes the old pending setup after Telegram confirms delivery. The historical 14:34 MSK fill therefore would not remain valid.

Cancellation text:

> ❌ BTC: если старая SHORT LIMIT 63,976.57 выставлена — снять. Новый scan: TREND_SHORT / WAIT_PULLBACK, прежний сценарий больше не MANUAL_REVIEW.

## Implemented Intraday-only changes

1. Persist pending LIMIT metadata in `state/intraday_pending_limits.json` so a restart does not forget an active stale-order watch.
2. Cancel before fill on `>=0.60R` missed movement, expiry, WAIT/TRANSITION, direction/playbook change, or materially rebuilt Entry/Stop.
3. Commit a pending setup only after the archive was successfully delivered to Telegram; retry cancellation notices after send failure.
4. Clear pending Intraday setups when Intraday is disabled, symbol list is replaced, or `/reset` is used.
5. Update only `INTRADAY_TASK` to include validity/cancellation rules.

## Validation

- Version: `56_full`.
- Parser `int bch, pol`: `('set', ['BCH_USDT', 'POL_USDT'], None)`.
- Archive smoke: `{'built': True, 'file': 'intraday_btc-2348_1207.zip', 'collector_version': '56_full', 'task_header': 'INTRADAY_TASK 56_full', 'contains_local_room_0_12R': True, 'contains_missed_move_0_60R': True, 'contains_validity_rule': True, 'candidate_count': 1}`.
- Python compile/import validation passes.
