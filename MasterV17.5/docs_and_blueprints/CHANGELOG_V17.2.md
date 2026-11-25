# CHANGELOG · MasterV17.2 (from MasterV17.1)

Full weakness report: `AUDIT_REPORT_V17.2.md`.

## Hard limits (what V17.2 guarantees – and what it does not)
- Nobody can guarantee 100 % wins. V17.2 guarantees **auto-trade == signal**: same pair, same CALL/PUT, same expiry,
  order sent at the signal's entry epoch, every trade stored with the signal id and the measured latency.
- Real target: 55-62 % out-of-sample; when the models are not confident the output is **NO TRADE**.
- No fake / simulated market data. Stale or disconnected Deriv feed → signals **and** trades stop.

## Auto trade
- **Martingale removed.** Flat stake per trade (`baseStake`, manual) – `risk.py`, recovery ladder, `maxSteps`, `targetProfit`, CLI bot deleted.
- **500 ms rule.** `maxSignalLatencyMs` (default 500): an order that would leave later than that after the entry epoch is
  recorded as `CANCELLED_LATE` and never sent.
- **Idempotent orders.** Unique MongoDB index on `requestId` (= mode + signal id). The same signal can never be bought twice.
- **Risk control.** 4 losses in a row → 30-minute cooldown (engine keeps running, resumes with the same flat stake);
  daily loss limit (manual, USD or % of day-start balance) → stop for the day; max trades/day; max stake; max % of balance.
- **Stale feed = no trade.** Uses `DERIV_TICK_STALE_SECONDS`; re-checked right before every order; missing feed = blocked.
- **Atomic engine state** (`$inc` / `find_one_and_update`) – concurrent trades can no longer overwrite P&L / loss-streak counters.
- **Proof per trade.** `signal{…}` + `match{sameDirection, sameExpiry, orderLatencyMs, ackLatencyMs}` on every trade and on the
  signal (`live_signals.autoTrade`). Shown in the Live trade feed.
- Signal sources reduced to `stream` (default, event stream) and `brain` (optional scanner). LLM "second opinion" removed from the order path.
- `/autotrade` UI: Protection card = daily loss limit, max stake, max trades, losses-in-a-row, cooldown minutes, max order latency.

## Model training
- `ml/model_validation.py`: expanding **walk-forward** with purge/embargo, per-model OOS accuracy + Brier, calibrated ensemble,
  **Platt calibration** (applied at inference), **leakage probe** (shuffled labels), **champion gate** – a retrained stack is
  deployed only if it beats the stored champion (`models/step8/champions.json`). `GET /api/v1/ml/status` → `champions`, `validation`.
- `main_source_code/backend/model_validation_report.py SYMBOL TF N` prints the honest OOS report; first result in
  `test_reports/model_validation_R_100_1m.json` (ensemble 53.7 % OOS, confident subset 66.7 % on 48 trades).

## Pipeline / UI
- Each signal carries `pipeline{tick, candle, evaluationStartedAt, featuresAndModelsAt, decisionAt, emittedAt, decision, gate}`.
- MODULE STATUS `Execution policy` shows the real engine state (OFF / ARMED / EXECUTING / FEED_STALE) instead of a hard-coded "BLOCKED".
- Decision output shows **NO TRADE** when gated.

## Experimental
- ws-sniffer / Playwright broker observer: kept, default **OFF** (`QX_OBSERVER_AUTOSTART=false`). Using it may violate the broker's terms.

## Packaging
- `main_source_code/backend/.env.example` lists every variable (empty). Your private `.env` holds the real keys – never share the ZIP.
- Tests: `main_source_code/backend/tests/test_v17_2_autotrade_rules.py` (6 rules, fake executor, real MongoDB).
