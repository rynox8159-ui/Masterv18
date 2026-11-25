# CHANGELOG · MasterV17.3 (from MasterV17.2)

## Auto trade = Event stream (main task)
- **Every event-stream signal is traded by default.** `minSignalConfidence` default is now `0` (= ALL SIGNALS). The `/autotrade`
  page has a two-way switch: `ALL SIGNALS` / `HIGH CONFIDENCE ONLY` (default 80 %, editable 1-99).
- **DEMO + REAL at the same time.** New account mode `both`: the engine opens one Deriv connection per account and buys every
  signal on the demo **and** the real account simultaneously (same pair, direction, duration). P/L, win rate, daily limits,
  loss streak and cooldown are tracked **per account** (`autotrade_state` docs `demo` / `real`). Requires two different tokens
  (`DERIV_DEMO_TOKEN` + `DERIV_LIVE_TOKEN`, or TOKEN_1 = demo / TOKEN_2 = real) and the real operator key + typed `REAL`.
- **Direct buy at entry.** The order is now sent with the full contract parameters (`buy: 1, parameters{…}`) at the signal's entry
  epoch. V17.2 fetched a proposal, slept until entry and bought the proposal id - Deriv had expired it meanwhile
  (`InvalidContractProposal: Unknown contract proposal` -> trade `FAILED`). Fixed.
- **Latency rule enforced.** `maxSignalLatencyMs` (default now 1500 ms) is checked right after the entry wait; later orders are
  written as `CANCELLED_LATE` (V17.2 defined the rule but never called it).
- **Martingale (opt-in).** `martingaleEnabled` (default **off**), `martingaleMultiplier` (1.1-5, default 2), `martingaleMaxSteps`
  (1-10, default 3): stake = base × multiplier^losses-in-a-row, capped by max stake; the ladder preview is shown in the Stake card.
  Flat stake stays the default.
- Pick-up window for upcoming signals widened to 0.3-60 s before entry; `maxConcurrentTrades` default 5 (per account).
- Trades store `mode`, `martingaleStep`, real `buy_price`/`payout` from the Deriv ack; `live_signals.autoTradeByMode.<mode>`.
- `/api/v1/autotrade/start|trades|reset-cycle` accept `mode=both`; status returns `modes`, `states{demo,real}`, `accounts{…}`.

## Model training (heavy mode)
- **Parallel training**: `TRAIN_PARALLEL_MARKETS` (default 2) markets are fitted at the same time; worker pool
  `TRAINING_WORKERS` / `TRAINING_THREADS` default to half the CPU cores.
- **Full retrain every 6 h** (`TRAIN_ALL_INTERVAL_SECONDS=21600`, was 24 h) + `TRAIN_BACKFILL_CANDLES` (5000).
- **Incremental retrain every 30 min** (`TRAIN_INCREMENTAL_INTERVAL_SECONDS`): only the markets whose signals settled (WIN/LOSS)
  since the last pass are retrained -> the models learn from live outcomes between full runs.
- `POST /api/v1/ml/retrain-active {hours}`: heavy retrain of the markets that produced signals recently
  (Training tab -> **Retrain Active**). Training tab shows parallelism, candle depth and the incremental schedule.
- Champion gate, walk-forward OOS validation, Platt calibration and leakage probe from V17.2 are unchanged.

## Honest limits
- 100 % win rate is impossible. V17.3 guarantees **trade == signal** (same pair/direction/expiry, order at entry, latency rule).
  A Deriv Rise/Fall contract settles on ticks (entry spot = first tick after purchase, exit = last tick at expiry) while the signal
  settles on the candle open/close; on very small moves the two can still differ by a tick.

## Packaging
- Version strings / ZIP name -> `MasterV17.3`; `main_source_code/backend/.env` keeps every key and token (keep the ZIP private).
- Tests: `tests/test_v17_2_autotrade_rules.py` (6 rules, incl. late-cancel + duplicate) pass; `test_v17_1_stream_mirror.py`
  / `test_v17_brain_autotrade.py` run against `BACKEND_TEST_URL` (default http://localhost:8001).
