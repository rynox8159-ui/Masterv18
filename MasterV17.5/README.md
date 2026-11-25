# NAQSO · Master Candle · MasterV17.3 (live Deriv edition)

> What changed in V17.3: see `docs_and_blueprints/CHANGELOG_V17.3.md` (older: `CHANGELOG_V17.2.md`, `AUDIT_REPORT_V17.2.md`, `CHANGELOG_V17.1.md`). Quick start: `LOCAL_RUN.txt`.

## What is new in V17.3 (Deriv Accumulator auto trade, demo + gated real)
- **Accumulator-only auto trade**: the dashboard trades Deriv-confirmed Accumulator setups; Binary Rise/Fall signals are for the separate Binary screen and are not submitted by the auto-trader.
- **Validated-only signal cards**: unpromoted Binary and Accumulator candidates remain shadow observations and are not displayed or traded. Step 8 artifacts are isolated by Deriv source, symbol, and timeframe.
- **Accumulator safety gate**: the current volatility heuristic has no Accumulator-specific out-of-sample validator, so it remains shadow-only and cannot currently produce eligible Auto Trade orders.
- **Accumulator risk limits**: candidate selection, settings, and order placement enforce Deriv's 4-5% growth-rate range. Take-profit and stop-loss orders default to 5% of stake and are capped at 5%. Growth is a contract parameter, not a promised return; fills, slippage, and losses remain possible, so capital preservation cannot be guaranteed.
- Legacy shared Step 8 model files are left untouched but ignored because their adapter-to-market provenance is ambiguous; retrain each market to create isolated artifacts.
- **DEMO + REAL together**: mode `both` can submit each eligible Accumulator setup to demo and real accounts, with existing per-account confirmation and risk limits.
- **Direct buy at entry** (no expiring proposal id) + enforced order-latency rule (`CANCELLED_LATE`).
- **Martingale opt-in** (off by default): multiplier + max steps, capped by max stake.
- **Heavy training**: 2 markets in parallel, full retrain every 6 h, incremental retrain every 30 min from settled signals, `Retrain Active` button.

Live Deriv multi-market + 1-minute Forex Binary terminal with a 500-slot market-agent registry,
ML/DL training adapters (LSTM, Transformer, XGBoost, LightGBM, CatBoost, Random Forest, Extra Trees,
HistGradientBoosting, MLP, Logistic, MARL), a market-algorithm detection layer, Accumulator-only auto trade with safety margin,
a market-memory layer (traps / gap-up / gap-down / stop-hunts / algorithm win-rates) and a
virtual-funds Trade mode that settles on real Deriv prices. No platform-specific dependency –
everything runs on your own PC.

## 1. What you need (one time)
| Tool | Version | Download |
|------|---------|----------|
| Python | 3.10 – 3.12 | https://www.python.org/downloads/ (tick "Add to PATH") |
| Node.js | 20 or newer | https://nodejs.org |
| MongoDB Community | 6+ | https://www.mongodb.com/try/download/community (runs on 127.0.0.1:27017) |

Local runtime settings are kept next to their source: `main_source_code/backend/.env` and `main_source_code/frontend/.env`. Both are excluded from Git. Setup scripts create them from the adjacent `.env.example` files only if missing.

The project ZIP (`MasterV17.2.zip`, Settings → Download or the rail button) is a complete,
manifest-verified backup: all source, `.env` files **with secret keys**, model files
(`main_source_code/backend/models/`), requirements, scripts, docs and a bounded MongoDB snapshot.
`ZIP_MANIFEST.json → manifestCheck` lists anything missing. Keep the file private.

## What is new in V17.2 (audit + repair release)
- **Auto trade = signal, provably.** Flat stake (no martingale – the recovery ladder is gone), order sent at the signal's entry
  epoch, **cancelled if it would leave > 500 ms late**, one order per signal (unique DB key), every trade stores the signal id,
  direction/expiry match and the measured latency.
- **Risk control:** 4 losses in a row → 30 min cooldown, daily loss limit (manual) → stop, max trades/day, max stake, stale
  Deriv feed → no trade. Real mode still needs the operator key + typed `REAL`.
- **Model training:** walk-forward (purged) out-of-sample validation, leakage probe, Platt calibration, champion gate
  (a new model is deployed only if it beats the old one). `python main_source_code/backend/model_validation_report.py R_100 1m 1500`.
- **Honest numbers:** first report → ensemble 53.7 % OOS on R_100 1m, 66.7 % on the confident subset (48 trades). Most minutes are NO TRADE.
- ws-sniffer / Playwright observer kept experimental and **OFF by default**.

## What was new in V17.1
- **DERIV market picker + multi-pair tab bar**: "+" opens a Deriv-style picker (All / Derived / Forex / Stocks & indices / Commodities / Crypto / Favourites,
  grouped by Deriv submarket, search, live prices, closed markets marked). Tabs show flag, name, live price (green/red), close button.
  "Forex" label is now "DERIV"; the "ENGINE · ALL N PAIRS" note is gone.
- **Signal Brain** (`backend/brain.py`, `/api/v1/brain/*`): indicator + statistics engine with payout-aware edge, 0-100 confidence,
  and learning from every settled trade (symbol × contract type × market condition).
- **Two independent Deriv signal streams**: the backend continuously scans verified real Forex pairs for fixed-time Rise/Fall signals (1m and 5m) and scans Deriv-supported Forex Accumulators separately, regardless of which UI button is selected. The Forex and Binary buttons only switch which stream the UI displays. Other market categories and OTC instruments do not produce these signals. Deriv quotes may differ from Quotex quotes, so fixed-time signals are references rather than guaranteed matching trades.
- **Decision output** shows the full system-flow signal: pair + market type, contract, alert/entry/close time, direction, confidence, indicators, pair record, modules/model/agents line, result and mirrored trade result.
- **Auto trade redesigned in one frame** (`/autotrade`): Demo/Real, balance, P/L, win rate; supported Rise/Fall and bounded-stake multiplier contracts from eligible Deriv signals (other Deriv contract types remain unavailable until supported); stake AUTO (% balance × confidence) / MANUAL; protection limits; live Deriv feed; brain picks. Deriv App ID/demo/real tokens can be entered in the page and are held in backend memory only; Demo remains the default.
- **Any local MongoDB**: `python main_source_code/database/init_db.py` (also run by setup scripts) creates collections + indexes from `.env`.
- **Playwright UI smoke test**: `scripts/local_pc/run-ui-smoke.(sh|bat)` → `tests/ui_smoke.py`.
- Header logo = loading-screen logo. Full list: `docs_and_blueprints/CHANGELOG_V17.1.md`.

## What is new in V16.8
- **One-click launcher**: `start.bat` (project root) starts MongoDB, the backend and the frontend, waits for both health
  checks and only then opens the browser. Clear `[X]` errors for MongoDB not running, ports 8001/5173 already in use,
  missing `.env` or packages. `setup.bat` = one-time install. Python 3.11/3.12 and Node 20 are pinned in
  `requirements.txt` / `package.json`.
- **Deriv connection**: one shared request channel (history, active_symbols, time) instead of a new socket per request,
  application heartbeat, exponential reconnect + automatic resubscribe, paced requests (Deriv rate limits), tick/candle
  fan-out over persistent connections so **every non-OTC Deriv pair** (forex, all synthetic indices incl. Boom/Crash/Jump/Step,
  crypto, commodities, indices) is streamed. "Market Closed" comes from Deriv's own `exchange_is_open` flag polled every
  60 s - no hardcoded weekend rule (crypto/synthetics trade 24/7).
- **Chart**: live ticks and newly opened candles are applied in place (`series.update`), no full redraw every tick; EMA/BB/RSI
  overlays update incrementally; candles are de-duplicated per epoch so a reconnect never leaves duplicate or missing bars;
  up = green / down = red by each candle's own open/close.
- **Accuracy proof / promotion gate** (`/accuracy`): walk-forward backtest per pair & timeframe on candles the detectors
  have not seen, payout-aware break-even. A pair is **PROMOTED** only at **>= 58 % win rate over >= 300 out-of-sample trades**
  and a 95 % Wilson lower bound above break-even; otherwise its live signal is switched off (shadow-settled only) and auto
  trade skips it. Runs after every Train-All and every 6 h. The "Model NOT TRAINED / ANALYTICAL ONLY NOT PROMOTED" gate stays.
- **Auto trade** (`/autotrade`): tabs Live P&L · Open Trades · History · Risk Settings; demo is the default, real needs the
  operator key + typed confirmation; risk controls: max stake, daily loss limit as fixed USD **or** % of the UTC-day start
  balance, auto stop after 3 or 4 losses in a row, kill switch (cancels pending orders, sells the open contract). Idempotent
  orders (request ID, duplicate blocked), open trades re-synced from Deriv after a reconnect/restart, no new trades while the
  Deriv feed is disconnected, signals cancelled when Deriv marks the pair closed, daily limits reset at 00:00 UTC.
- **Header**: navigation, account and status controls moved to the right side.

## What was new in V16.6
- **Quotex sniffer in your own Chrome tab**: `scripts/local_pc/start-chrome-debug.bat` opens your Chrome with remote
  debugging (port 9222) → log in to Quotex manually → `start-sniffer.bat` / **WS Sniffer** menu attaches to that tab and
  tracks every open pair (read-only, no Quotex orders). `start-all.bat` offers it as option [C].
- **Auto trade**: Master Candle system-signal feed card, WIN/LOSS toasts, P/L flash, `WAITING_SIGNAL` phase, mobile fixes.
- **Pipeline hardening**: Step 8 ensemble skips shape-drifted models instead of failing; all 173 backend tests pass.
- `setup-windows.bat` junction fix; version strings and ZIP name → MasterV16.8.

## What was new in V16.3
- **Deriv connection**: application-level heartbeat (no false pong timeouts), 2s→60s exponential reconnect, automatic
  re-subscription, new pairs picked up every 15 min via `active_symbols` without restart. History paging skips weekend days
  so every pair gets the full 5000-candle backfill.
- **Header connection strip**: Backend · Database · Deriv · Quotex sniffer with exact reasons when the backend is unreachable.
- **Training pipeline** (`/training`): *Fetch History → Train All* for every tracked pair, live progress, per-pair accuracy /
  Brier / tier, daily automatic retrain, Step 8 adapters (XGBoost, LightGBM, CatBoost, RF, LSTM, Transformer, MARL) persisted
  per market, heavy fitting in a separate worker process so live ticks are never delayed.
- **Feature store**: `market_features` collection (RSI, EMA 20/50, ATR, Bollinger, MACD, returns…) refreshed every minute for
  1m/5m/15m; ticks keep a TTL retention, candles are permanent.
- **Auto trade**: controls and stake-ladder (chip levels) in one frame, demo + real (real needs the operator key
  `AUTOTRADE_WEB_REAL_CONTROL_KEY` from `.env`, typed confirmation and configured daily-loss / max-stake limits). UI is English only.
- **Quotex proof** (`/sniffer`): live frame counter, frames/second, last tick per pair and raw frame log from the WS sniffer /
  Playwright observer. Log in manually in the Chromium window; only the browser profile stays on your PC.
- **Windows scripts**: `setup-windows.bat` picks Python 3.11/3.12, installs the CPU torch wheel and explains the VC++ runtime
  fix; `start-all.bat` starts MongoDB service if needed and shows [OK]/[X] for every step.

## 2. Setup (one time)
**Windows:** double-click `setup.bat` (= `scripts/local_pc/setup-windows.bat`)
**Linux / macOS:** `./scripts/local_pc/setup-linux.sh`

## 3. Run
**Windows:** double-click `start.bat` in the project root (MongoDB + backend + frontend, waits for health checks, then opens the browser). `scripts/local_pc/start-all.bat` calls the same launcher.
**Linux / macOS:** `./scripts/local_pc/start-all.sh`

- Backend → http://127.0.0.1:8001 (API docs: http://127.0.0.1:8001/docs)
- Frontend → http://localhost:5173 (Vite proxies `/api` to the backend)

## 4. Project layout

```text
main_source_code/
├── backend/
│   ├── ml/                 # Machine-learning adapters and orchestration
│   ├── dl/                 # Deep-learning training and inference
│   ├── ds/                 # Features, forecasting, research, calibration
│   └── tests/
├── frontend/               # Vite/React source and build configuration
└── database/               # Versioned schema, migrations, and seed data
dependencies/
├── backend/                # Python requirements and virtual environment
└── frontend/               # Frontend package manifest, lockfile, node_modules
main_source_code/
├── backend/.env            # Local backend settings (ignored)
└── frontend/.env           # Local Vite settings (ignored)
docs_and_blueprints/        # Architecture, stack, performance, deployment, roadmap
```

The optional WS sniffer / Playwright observer is under
`main_source_code/extensions/ws-sniffer/playwright/`. Start scripts use the
canonical paths above. See [the directory guide](docs_and_blueprints/README.md)
before editing an unfamiliar area.

To build a Windows WS Sniffer app, run
`scripts\local_pc\build-ws-sniffer-exe.bat` on Windows with Python 3.10–3.12.
The portable app bundle is created under
`main_source_code/extensions/ws-sniffer/playwright/dist/`; see its README for
backend URL, browser profile, and run instructions.

## 5. Backend API key (automatic)
- The key is built into the code: `main_source_code/backend/api_key_auth.py` (DEFAULT_API_KEY) and
  `main_source_code/frontend/src/lib/apiKey.ts` hold the same value, so the frontend connects automatically.
- Optional custom key: set `BACKEND_API_KEY` in `main_source_code/backend/.env` AND the same value in
  `VITE_BACKEND_API_KEY` in `main_source_code/frontend/.env`.
- Every `/api/v1/*` request sends header `X-API-Key` (SSE streams use `?api_key=`).
  Public: `/api/health`, `/api/v1/auth/status`.

## 6. Modes
| Mode | Route | Data |
|------|-------|------|
| Advanced (signal terminal) | `/` | Live Deriv candles, telemetry, signals, agents, memory |
| Trade (broker-style) | `/trade` | Live Deriv prices, virtual balance, Up/Down with expiry |

The signal terminal has two display modes; selecting one does not stop the other backend scanner:
- **Deriv Forex · ACCU** → provider-confirmed Accumulator setups on real, non-OTC Forex pairs. Only independently validated setups can be displayed or traded; unvalidated setups remain unavailable.
- **Deriv Binary · fixed-time** → real, non-OTC Deriv Forex pairs with 1-minute and 5-minute Rise/Fall signals for manual fixed-time use.
- Weekend filter: forex & commodities close Friday 22:00 UTC → Sunday 22:00 UTC and are hidden
  automatically (Deriv `exchange_is_open` + session clock). Crypto and Volatility indices stay 24/7.

## 7. Useful endpoints
`/api/v1/pairs?mode=FOREX|BINARY` · `/api/v1/signals/live` · `/api/v1/signals/check` ·
`/api/v1/ml/status` · `/api/v1/memory/summary` · `/api/v1/memory/events?symbol=R_10` ·
`/api/v1/memory/notes` · `/api/v1/memory/algorithms` · `/api/v1/training/knowledge` ·
`/api/v1/system/loop` · `/api/v1/demo/*`

The terminal's **Project ZIP** action requires `ADMIN_DOWNLOAD_PASSWORD`.
Observer browser viewing is available while the Playwright Observer is running.
It requires both the normal API key and a configured `PROVIDER_CONTROL_KEY_SHA256`
because the remote view can expose a logged-in browser session.

## 8. How signals are made (pipeline)
Deriv ticks/candles → MongoDB → feature engineering + behaviour finder → indicator agents
(trend, momentum, breakout, volatility, HTF) → confluence + ensemble fusion → validated deep
model (must pass out-of-sample win-rate/EV gates) → multi-timeframe filter →
**market-memory gate** (blocks CALL after a fresh bull trap, PUT after a bear trap, any signal
right after a gap; penalises weak algorithms) → live calibration threshold → signal.
Every result (WIN/LOSS) is written back into algorithm memory and online training.

See [the docs index](docs_and_blueprints/README.md) for the architecture, tech
stack, model-performance evidence, deployment notes, and the AI-agent roadmap.

## Security note (V17.2)
MasterV17.1 was shared through a public link with live tokens inside. **Revoke those Deriv tokens and create new ones**, change the Quotex password, and keep this ZIP private. `main_source_code/backend/.env.example` lists every key without values.

## Security note (V16.8)
`main_source_code/backend/.env` in this ZIP contains your Deriv App ID, API tokens and the auto-trade REAL operator key.
**Never share this ZIP.** If it leaks, revoke the tokens at https://app.deriv.com/account/api-token immediately and create new
ones. `main_source_code/backend/.env.example` lists every key without values.
