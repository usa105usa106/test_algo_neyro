# Intraday 58_full — code audit and safety fixes

## Scope

Audit target: `57_full` after the custom-symbol candle fix.

Changed only:

- Intraday runtime/state handling in `bot.py`;
- Intraday plan validation in `intraday_engine.py`;
- Intraday archive task/deadline handling in `intraday_archive.py`;
- application version and documentation.

No trading threshold was loosened or tightened. Other scan modes and their task prompts were not changed.

## Real bugs found and fixed

### 1. Pending LIMIT survived restart, but its duplicate cooldown did not

`state/intraday_pending_limits.json` was loaded after restart, while `intraday_candidate_sent_at` started empty. The first scan could therefore resend the same green setup and overwrite its `sent_at/expires_at`, silently extending an old LIMIT idea.

Fix: rebuild the per-setup duplicate state from every persisted pending LIMIT at process start and whenever Intraday is turned ON.

### 2. NO_DATA or a missing report left an old LIMIT active

The pending evaluator previously executed `continue` when the current scan had `NO_DATA` or no report for the symbol. That contradicted the stale-order rule: a plan that cannot be reconfirmed by fresh candles must not stay active.

Fix: send a separate conditional cancellation notice and remove the pending setup after successful Telegram delivery.

### 3. Invalid rounded plan geometry could remain green

For arbitrary low-priced/coarse-tick custom symbols, rounding or insufficient structure could produce non-finite prices, zero Entry/Stop risk, equal levels, or incorrectly ordered TP values. Existing room/fee checks skipped NaN values, so a malformed plan could theoretically retain `MANUAL_REVIEW`.

Fix: before quality/rank publication, validate that all Entry/Stop/TP values are finite and positive and that:

- LONG: `Stop < Entry < TP1 < TP2 < TP3`;
- SHORT: `Stop > Entry > TP1 > TP2 > TP3`.

Any failure becomes `WAIT_CONFIRMATION`; no archive is built for it.

### 4. `int ...` could race an already-running Intraday scan

The command replaced `runtime.intraday_symbols`, but the active cycle had already copied the old list. It could finish later, send an archive for the old symbols, and repopulate pending LIMIT state after the user had changed the list.

Fix: when Intraday is ON, `int ...` first cancels and awaits the old cycle, clears its pending state, replaces the symbols, and immediately starts a fresh cycle on the new list.

### 5. One-character exact symbols were rejected

The custom-symbol regex required at least two characters. An exact contract base such as `S` could not be entered even if `S_USDT` existed.

Fix: accept one to 25 characters before `_USDT`; exact MEXC contract validation still occurs through the candle request.

### 6. Exact 15-minute boundary expiry was off by one candle

At exactly `13:15:00`, the old deadline calculation selected `13:45` instead of the complete `13:15–13:30` candle.

Fix: exact boundary publication expires at the next boundary (`13:30`); publication even one second later uses the next complete candle and expires at `13:45`.

### 7. Archive task and runtime monitor could use different deadlines

The archive task calculated validity during archive construction, while pending state recalculated it after Telegram delivery. Crossing a 15-minute boundary could make `intraday_task.txt` say one deadline while the bot monitored another.

Fix: archive construction now creates one publication/deadline timestamp, writes it to the task and manifest, returns it to the runtime, and the pending monitor stores that exact same epoch. An archive already expired before Telegram send is discarded.

## Trading logic deliberately unchanged

- Trend local room: `0.12R`.
- Trend structural stop floor: `2.30 ATR15` for crypto/alts, `2.40 ATR15` for metals/energy.
- Maximum Trend stop: `4.00 ATR15`.
- Trend targets: `0.80R / 1.60R / 2.40R`.
- LONG/SHORT direction logic for every exact custom symbol.
- Pressure, trap, late, HTF, quality and fee gates.
- 45-minute setup-aware archive duplicate cooldown.

The new plan-geometry gate only rejects mathematically broken plans; it does not reject a valid wider stop or a normal low-local-room setup.

## Validation performed

1. Full Python compilation of the bot: passed.
2. Unit checks: custom parser, one-character symbol, deadline boundaries, NO_DATA/missing-report cancellation, persisted cooldown restoration and invalid plan geometry: passed.
3. Async race test: an active old-symbol Intraday task was cancelled before `int gram, pol` restarted the cycle: passed.
4. Real archive build: generated `INTRADAY_TASK 58_full`; task and manifest contained the same expiry epoch: passed.
5. Parquet equivalence check against `57_full`: 288 checkpoints, 48 each for BTC, BCH, ETH, XAU, SILVER and USOIL. Decisions, direction, playbook, Entry, Stop, TP1/TP2/TP3 and local room differences: **0**.

The equivalence result is stored in `INTRADAY_58_REPLAY_EQUIVALENCE.json`.

## Version

`APP_VERSION = 58_full`
