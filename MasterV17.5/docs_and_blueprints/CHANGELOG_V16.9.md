# CHANGELOG MasterV16.9 (June 2026)

Full-system check of MasterV16.8 (backend, frontend, chart, signal engine, auto trade, training/adapters, launcher) with fixes.

## Verified working (clean install from the V16.8 ZIP)
- Backend boots on `BACKEND_PORT` (8001), MongoDB connected, Deriv feed `DATA_RECEIVING` for every open non-OTC pair
  (43 synthetic/crypto pairs on a Friday evening; forex re-appears when Deriv marks it open).
- `/api/health`, API-key middleware (401 without `X-API-Key`), pairs, market state, history, SSE tick stream and telemetry stream.
- Signal engine cycles every candle (threshold 85 %, 1m/5m), promotion gate walk-forward runs after boot and after Train-All,
  memory layer (traps / gaps / stop hunts) feeds the chart markers.
- Step 8 adapters (LSTM, Transformer, XGBoost, LightGBM, CatBoost, RF, Extra Trees, HistGBM, MLP, Logistic, MARL) load from
  `models/step8/*.joblib` as TRAINED; Train-All (5000-candle backfill + deep model + adapters per pair) runs automatically
  180 s after boot in a separate worker process. One pair/timeframe takes ~4 min on a 4-core CPU (86 markets => several hours).
- Auto trade DEMO end-to-end on the real Deriv demo account: start -> proposal -> buy -> contract settled -> P/L, cooldown,
  recovery ladder, stop/kill, REAL mode refused without operator key (403) and typed confirmation.
- Frontend `/`, `/trade`, `/autotrade`, `/training`, `/accuracy`: no console errors; chart updates in place per tick
  (`series.update`), timeframe / chart kind / indicator toggles, pair switching, virtual demo trade on `/trade`.
- `yarn typecheck` clean, vitest 13/13.

## Fixes
- **Tick ingestion lag under CPU load** (`market_store.tick`, `deriv_service._tick_consumer`): a tick used to cost ~8
  sequential MongoDB round trips and all 43 symbols were persisted one after another, so `market_instruments.latestEpoch`
  lagged 12-16 s while training ran -> `/trade` returned 409 "No live Deriv price" and the signal freshness filter could
  drop pairs. Now one `bulk_write` for the candle buckets + tick insert + instrument update run concurrently, and all symbols
  of a batch are persisted in parallel (per-symbol lock keeps tick order). Measured freshness: median 0.5 s, max 1.5 s.
- `markets.test.ts`: stale expectation for Binary mode (OTC indices are part of Binary mode since V16.8).
- Version strings -> V16.9 everywhere (health `version`, boot splash, nav, footers, launcher titles, ZIP name
  `MasterV16.9.zip`, `package.json` 16.9.0).

## Notes for local run
- Windows: `setup.bat` once, then `start.bat`. Linux/macOS: `scripts/local_pc/setup-linux.sh`, then `scripts/local_pc/start-all.sh`.
- `main_source_code/backend/.env` is included with the Deriv app id / tokens and the auto-trade operator key. Keep the ZIP private.
- Auto trade only places system-signal trades on PROMOTED pairs (>= 58 % over >= 300 out-of-sample trades) - on a fresh
  install it stays in `WAITING_SIGNAL` until the walk-forward gate promotes pairs; "Accuracy layers" mode trades on indicator
  confluence instead (Risk Settings -> signal source).
