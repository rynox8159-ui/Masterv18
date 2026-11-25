# Master Candle / NAQSO — MasterV17.3 (PRD & progress memory)

## Original problem statement (user, June 2026, Banglish)
Unzip `MasterV17.2 (3).zip`, run backend + frontend, make every Event-stream signal auto trade (event stream WIN = auto trade WIN,
demo + real), make model training heavy / high-performance as the main source of accurate signals, and deliver a renewed
`MasterV17.3.zip` with .env, requirements, all secrets/tokens — 100 % working. Ask questions in Bangla if unclear.
User choices (ask_human): demo default with real toggle **and** demo + real together; fixed stake **and** martingale option;
all signals **and** optional high-confidence filter; keep training as is but make it work heavily; live chart + ultra-smooth candles + advanced UI.

## Architecture
- `/app/main_source_code/backend` (FastAPI, port 8001, `/api` prefix; `/app/backend` symlink), MongoDB `test_database`.
- `/app/main_source_code/frontend` (Vite + React 19 + TS); deps + package.json in `/app/dependencies/frontend` (`/app/frontend` symlink,
  `yarn start` = vite on $PORT, `/api` proxied to 8001). node_modules symlinked into main_source_code/frontend.
- `/app/auto_trade/dashboard_engine.py` (multi-account engine), `dashboard_execution.py` (Deriv executor, direct `buy_parameters`).
- Training: `training_orchestrator.py` (parallel + incremental), `training_workers.py` (process pool), `ml/`, `dl/`, `ds/`.
- Project ZIP: `POST /api/v1/project/build` → `_export/MasterV17.3.zip` (also copied to `/app/MasterV17.3.zip`).

## Implemented (2026-06, V17.3 session)
- Setup from the uploaded ZIP; torch CPU + requirements installed; services on supervisor; version strings → V17.3.
- Auto trade: modes demo / real / both (one executor per account, per-account state, limits, cooldown); `minSignalConfidence`
  default 0 (ALL SIGNALS) with HIGH CONFIDENCE toggle; martingale opt-in (multiplier, max steps, capped by max stake);
  direct buy with contract parameters at entry (fixes expired-proposal `FAILED` trades); latency rule now enforced (`CANCELLED_LATE`);
  pick-up window 0.3–60 s; UI: DEMO | REAL | DEMO + REAL, dual balances, martingale ladder, account badge per trade.
- Training: `TRAIN_PARALLEL_MARKETS=2`, workers/threads = half the cores, full retrain every 6 h, incremental retrain every 30 min from
  settled signals, `POST /api/v1/ml/retrain-active`, Training tab shows schedule + Retrain Active button.
- Tests: `tests/test_v17_2_autotrade_rules.py` 6/6; e2e `test_stream_mirror_e2e` → demo trade CLOSED, signal WIN = trade WIN (+0.88),
  order latency 5.9 ms. Stale test expectations (URL, version, brain default) fixed.
- Docs: `CHANGELOG_V17.3.md`, README, LOCAL_RUN, .env/.env.example (new TRAIN_* keys, DERIV_DEMO_TOKEN / DERIV_LIVE_TOKEN).

## Honest notes
- 100 % win is impossible; guarantee is trade == signal. Deriv settles on ticks, the signal on candle open/close → rare 1-tick mismatch.
- Real-account buy path not exercised (demo only). `both` requires two distinct tokens.
- Signals are rare by design (NO TRADE gate, MIN_SIGNAL_CONFIDENCE 85 in signal_service) — auto trade mirrors whatever the stream emits.

## Backlog
- P1: candle-aligned expiry (`date_expiry`) for Rise/Fall to remove the residual tick mismatch where Deriv allows it.
- P1: real payout per pair from Deriv proposals stored into `pair_promotions`.
- P2: Telegram alerts, CSV export of auto-trade history, chart UI polish (volume pane, signal markers on chart).
