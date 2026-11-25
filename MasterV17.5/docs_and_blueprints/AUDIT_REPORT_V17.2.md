# MasterV17.2 · Audit & weakness report (from MasterV17.1)

Scope: every file under `main_source_code/`, `auto_trade/`, `scripts/`, `dependencies/`, `docs_and_blueprints/` of the
MasterV17.1 archive that was shared via a public Google-Drive link. Verified live against Deriv (public feed + the
authenticated options API) on 2026-10-24.

Legend: **FIXED** = repaired in V17.2 · **MITIGATED** = reduced but needs your action · **OPEN** = documented, not changed.

## 1. Secrets

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| S1 | `main_source_code/backend/.env` in the public ZIP contained two live Deriv PAT tokens (`DERIV_API_TOKEN_1/2`, demo + real, both still valid when tested), the `AUTOTRADE_WEB_REAL_CONTROL_KEY`, and a Quotex e-mail + password in clear text. | CRITICAL | **MITIGATED** – V17.2 ships `.env.example` with every key empty. Your keys are written back only into the private `.env`. **Revoke the two tokens at https://app.deriv.com/account/api-token, create new ones, change the Quotex password.** |
| S2 | `DERIV_APP_ID` is alphanumeric (`34D5…`). Verified: it is the new `api.derivws.com` App-ID format and works (accounts `DOT…` demo / `ROT…` real listed). | info | OK |
| S3 | Backend API key is hard-coded in `api_key_auth.py` + `apiKey.ts` (same value, "built-in"). Fine for a single-user local app; set `BACKEND_API_KEY` for anything exposed. | LOW | OPEN (documented) |

## 2. Auto-trade engine (`auto_trade/dashboard_engine.py`)

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| A1 | **Martingale.** `recovery_stake()` (`auto_trade/risk.py`) raised the stake after every loss in the *system*/*layers* modes; the UI exposed a "Loss recovery ladder", `maxSteps`, `targetProfit`, `stopAfterCycleLoss`; the REAL confirmation text described the ladder. | HIGH | **FIXED** – flat stake only. `risk.py`, the CLI runner and the ladder UI are deleted; settings `maxSteps/targetProfit/stopAfterCycleLoss/lossCooldownMinutes/llm*` rejected by the API. |
| A2 | **Idempotency was not enforced.** Orders carried a `requestId` but `autotrade_trades` had no unique index, so the `DuplicateKeyError` branch could never fire; after a backend restart the same signal could be bought twice (in-memory `traded_signal_ids` only). | HIGH | **FIXED** – unique partial index `uniq_request_id` created in `load()`; test `test_duplicate_signal_cannot_order_twice`. |
| A3 | **No late-order rule.** Stream mode slept until the entry epoch and then always bought – a slow proposal/reconnect could place the order seconds after the signal's candle opened. | HIGH | **FIXED** – `maxSignalLatencyMs` (default **500 ms**). Later orders are written as `CANCELLED_LATE` with the measured latency; the signal gets `autoTrade.result = CANCELLED_LATE`. Test `test_late_signal_is_cancelled_not_traded`. |
| A4 | **Race condition on engine state.** Concurrent stream tasks shared one `state` dict and did read-modify-write + `save_state`; P&L, loss streak and daily-trade counters could be overwritten → daily loss limit bypass. | HIGH | **FIXED** – all counters use atomic `$inc` / `find_one_and_update`. |
| A5 | Loss-streak rule: default 3 losses → permanent stop; separate 5-minute pause after *every* loss. Requested behaviour: 4 losses → 30-minute cooldown. | MED | **FIXED** – `maxConsecutiveLosses=4`, `streakCooldownMinutes=30`; the engine stays running in `COOLDOWN`, resets the streak when the pause ends; daily loss limit still **stops** for the day. Test `test_flat_stake_and_four_losses_trigger_cooldown`. |
| A6 | `feed_connected()` returned **True when no feed object was wired** and used a fixed 60 s staleness. | MED | **FIXED** – no feed = blocked; staleness = `DERIV_TICK_STALE_SECONDS` (30 s); the stale check is repeated right before the order. Test `test_stale_feed_blocks_trading`. |
| A7 | `llm_verify()` (OpenAI call through `emergentintegrations`) sat inside the order path. An external LLM must never decide/ delay a live order. | MED | **FIXED** – removed. |
| A8 | No proof that trade == signal. | MED | **FIXED** – every trade stores `signal{id,direction,timeframe,entryEpoch,expiryEpoch}` and `match{sameDirection,sameExpiry,orderLatencyMs,ackLatencyMs}`; shown in the live feed. |

## 3. Data layer (`deriv_service.py`, `dashboard_execution.py`)

| # | Finding | Status |
|---|---------|--------|
| D1 | Public feed: shared request channel, heartbeat (`DERIV_HEARTBEAT_SILENCE_SECONDS`), exponential reconnect, resubscribe, per-symbol stale detection and `exchange_is_open` polling are present and were observed working (43 pairs streaming). | OK |
| D2 | No mock/simulated market data in the pipeline. `demo_routes.py` ("SIMULATED" ledger) is a *virtual-balance* paper account settled on real ticks, clearly labelled; `signal_research.py` uses a seeded RNG only for bootstrap resampling of real outcomes. | OK (documented) |
| D3 | Weekend history fallback (`history` → last Friday close when `latest` is empty) returns **real** older candles, not synthetic ones. | OK |

## 4. Decision pipeline / signals (`signal_service.py`, `telemetry_routes.py`)

| # | Finding | Status |
|---|---------|--------|
| P1 | MODULE STATUS card `risk-control` was hard-coded to `BLOCKED · Execution disabled · Analytical only` – false since V17.1 added execution. | **FIXED** – reflects the real engine (OFF/ARMED/EXECUTING/FEED_STALE, flat stake, latency rule). |
| P2 | No per-stage timestamps. | **FIXED** – each signal carries `pipeline{tick,candle,evaluationStartedAt,featuresAndModelsAt,decisionAt,emittedAt,decision,gate}`. |
| P3 | NO-TRADE gate exists (confidence threshold ≥ `MIN_SIGNAL_CONFIDENCE`, agree/oppose, vetoes, micro-momentum, loss guard, promotion/precision gates in `observe` mode). Order of modules in telemetry matches `PROJECT_ARCHITECTURE.md`. UI label changed `NO_SIGNAL` → `NO TRADE`. | OK |
| P4 | `signal_service.py` is a 2 900-line monolith; refactoring it was out of scope for a repair release. | OPEN |

## 5. Model training (`ml/ml_model_adapters.py`, `dl/`, `promotion_gate.py`)

| # | Finding | Status |
|---|---------|--------|
| M1 | **No out-of-sample test for the Step 8 stack.** Every adapter was fit on 100 % of the samples and immediately persisted to `models/step8/*.joblib`, overwriting the previous model – no champion comparison. | **FIXED** – `ml/model_validation.py`: expanding walk-forward with purge/embargo (= label horizon), per-model OOS accuracy + Brier, ensemble, Platt calibration, shuffled-label leakage probe. A retrained stack is adopted/persisted **only** if `challenger_wins()` (≥ +0.5 pt OOS accuracy, Brier not worse, leakage probe passed). Champions stored in `models/step8/champions.json`, visible in `GET /api/v1/ml/status`. |
| M2 | Leakage check of `ds/feature_engineering.py`: features for sample *i* use candles `[i, i+window)`, label uses close at `i+window-1+horizon` → no look-ahead in features. Shuffled-label probe on R_100 1m: 52.6 % (≤ 55 % = pass). | OK |
| M3 | `random_forest` / `extra_trees`-style adapters in `ml_model_adapters.py` are hand-rolled stump ensembles (not scikit-learn forests); `marl` is a Q-table toy. They are advisory voters; the validated stack is xgboost / lightgbm / catboost / logistic. | OPEN (documented) |
| M4 | Honest accuracy (first V17.2 report, `test_reports/model_validation_R_100_1m.json`, 1 500 closed 1m candles, 4 purged folds, 1 176 OOS rows): ensemble **53.7 %**, Brier 0.248; on the ~4 % of rows the calibrated ensemble is confident (p ≥ 0.58) **66.7 % (48 trades)**. This is below the 55-62 % target overall → the NO-TRADE gate is doing the work; expect most minutes to be NO TRADE. | reported |

## 6. Infrastructure / packaging

| # | Finding | Status |
|---|---------|--------|
| I1 | PostgreSQL (`postgres_service.py`, 694 lines) and Redis remain in the tree but are **disabled by default** (`POSTGRES_ENABLED=false`, Redis `MEMORY_FALLBACK`). Nothing in the trade path depends on them. | OPEN (kept optional, not deleted) |
| I2 | Quotex observer autostarted when `QX_PROXY` was set. | **FIXED** – default OFF (`QX_OBSERVER_AUTOSTART=false`). ws-sniffer / Playwright connector stay experimental. |
| I3 | 11 unused auto-trade React components + CLI bot (`auto_trade/runner.py`, `config.py`, `signals.py`, `state.py`, `execution.py`) carried the martingale logic. | **FIXED** – deleted. |
| I4 | Version strings / ZIP name. | **FIXED** – `MasterV17.2` everywhere (health endpoint, splash, footer, export). |

## 7. Verification performed

* `pytest main_source_code/backend/tests/test_v17_2_autotrade_rules.py` → 6 passed (fake executor, real MongoDB).
* Live: backend connected to Deriv public feed (43 pairs, `DATA_RECEIVING`), DEMO auto trade started → account `DOT…` balance read, phase `WAITING_SIGNAL`, stopped cleanly.
* `python main_source_code/backend/model_validation_report.py R_100 1m 1500` → report above.
* Frontend `tsc -b --noEmit` clean; `/` and `/autotrade` render with the new protection rules.
