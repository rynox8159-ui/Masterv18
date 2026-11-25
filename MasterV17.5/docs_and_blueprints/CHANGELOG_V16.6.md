# Master Candle V16.6 – change log (on top of V16.3)

## Runs everywhere, verified
- Full backend test-suite green (173 passed): stale tests for the deep-model worker, Step 8 adapters and the
  signal emitter were brought in line with the current pipeline.
- Frontend `tsc` type-check clean; Vite dev server honours `PORT` (cloud preview) and still defaults to 5173 locally.
- `setup-windows.bat`: the `node_modules` junction now uses absolute paths (relative junctions silently broke Vite).
- Backend health / boot splash / footer / ZIP name all report **MasterV16.6**.

## ML / DL / DS pipeline
- Step 8 ensemble inference is fail-safe: a model whose persisted feature shape no longer matches the live feature
  vector (CatBoost “Feature N is present in model but not in pool”, XGBoost/LightGBM shape errors) is skipped and
  logged instead of aborting the whole 11-model stack. Explicit `FEATURE_SHAPE_DRIFT` check before `predict_proba`.
- Inference cache key includes the set of trained adapters, so a freshly trained model is used on the next cycle.
- Deriv: all 44 live pairs accepted (`DERIV_SYMBOLS=ALL`), 1m/5m candle streams on every pair, 5000-candle
  backfill; 88 live market agents (pair × timeframe) with walk-forward proven-edge gating. Signals stay fail-closed:
  a pair is released only when its out-of-sample hit-rate ≥ 56 % on ≥ 40 samples with Wilson lower bound > 50 %.

## Quotex WS sniffer → your own Chrome tab (manual login)
- New scripts `scripts/local_pc/start-chrome-debug.(bat|sh)` open **your** Chrome/Edge with
  `--remote-debugging-port=9222` and a private profile; log in to Quotex there yourself (captcha / 2FA fine).
- `scripts/local_pc/start-sniffer.(bat|sh)` (or **WS Sniffer** in the left menu) attaches the observer to that tab
  via CDP (`OBSERVER_MODE=ws_sniffer`). It reuses the existing Quotex tab, reloads it once so the WebSocket feed is
  captured, tracks **all open pairs** (list, payout, ticks, 1m history), and self-heals (reload) if the feed goes
  silent for 90 s. Read-only: it never places trades on Quotex.
- `start-all.bat` step 4 offers: [C] Chrome-tab sniffer · [O] separate Chromium observer · [N] skip.
- `/sniffer` page explains the three-step flow.

## Auto trade (Deriv demo + real)
- New **Master Candle system signals** card: upcoming entries with countdown and latest settled results, next to
  the system-signal gate. Phase `WAITING_SIGNAL` is now explained in the hero and pipeline strip.
- Settled trades raise a WIN / LOSS toast; Today / Total P/L tiles flash green/red on change.
- Mobile: pipeline strip wraps into a 2-column grid (no horizontal overflow at 390 px).
- Demo start → Deriv demo account authorised (balance shown) → stop verified end-to-end.

## ZIP
- `MasterV16.6.zip` is built by **Settings → Download / rail button** or `POST /api/v1/project/download` and
  contains all source, `.env` with the existing secret keys, models, scripts, docs and a bounded MongoDB snapshot.
  `.emergent`, `test_reports`, browser profiles and `node_modules` are excluded.
