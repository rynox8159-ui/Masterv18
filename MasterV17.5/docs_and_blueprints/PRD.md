# PRD – NAQSO / Master Candle v10
## Original problem (Bengali, summarised)
Historical product brief (created before the directory reorganization): FastAPI + Vite React app. Backend: make 500 agents/ML/DL/training adapters work; DB memory layer (algorithms, traps, gap-up, gap-down, behaviour notes); high-accuracy signal pipeline; Deriv forex + binary pairs with weekend filtering like real brokers; backend API key used by frontend; keep local environment settings unchanged; no platform dependency; must run locally from zip. Frontend: professional pair tab bar, live chart, sidebars showing backend/signal health, high text visibility, broker-style pair names, no fake data, Trade/Advanced modes kept separate.
## Implemented (2026-10-11)
- requirements fixed (non-existent pins), torch CPU; API key middleware + local bootstrap (api_key_auth.py)
- market_memory.py (trap/gap/stop-hunt events, behaviour notes, algorithm win-rate memory, signal gate) + /api/v1/memory/*
- pairs_routes.py /api/v1/pairs (Forex/Binary modes, payouts, weekend session filter)
- Demo engine settles on real Deriv ticks; forex rejected when closed
- Frontend: ApiKeyGate, LiveChart (lightweight-charts v5, HA/bars/area, EMA/BB/RSI, markers), BackendHealthPanel, Trade mode on live pairs, mock data removed, font visibility upgrade
- Local scripts (setup/start .bat/.sh), README, NEXT_STEPS.txt, zip /app/NAQSO-v10.zip (also /NAQSO-v10.zip on preview)
## Backlog
P0: weekday forex signal validation; deep-model tier gates on forex; update old backend tests for async demo engine
P1: memory views in workspace pages; trap/gap markers on chart; Deriv keepalive tuning; mobile polish
P2: Telegram/sound alerts; Deriv trading_times holidays; PostgreSQL mirror test

## Session 2026-10-11 (Masterv12 verification in Emergent preview)
- Loaded Masterv12.zip into /app (Vite TS frontend on 3000, FastAPI on 8001). Historical note: a local environment file was restored from the user's source archive; current local settings belong beside their source under `main_source_code/`.
- Verified: health, API key gate, MongoDB (25+ collections filling), Deriv public WS (15 live pairs on weekend), 7 Step8 adapters train, deep PyTorch model trains on 5000 candles with purged OOS gate (1HZ100V 1m = BELOW_TIER_GATES, honest), signals engine cycles, memory layer, demo trade on live prices, frontend / and /trade.
- Fixes: forex session now opens Sunday 22:00 UTC (pairs_config.is_forex_market_open); Step8 ensemble confidence now probability-based + 'agreement' field (was vote-count, showed 85% with ~50% models); LSTM/Transformer/RF PUT confidence fixed (max(p,1-p)); 4 outdated tests updated. pytest 175 pass. Testing agent iteration_1: 100%.
- Download: /Masterv12-verified.zip (no .env inside).

## Session 2026-10-11 (part 2)
- Memory Views: MarketMemoryPanel.tsx (trap/gap events table with type filters + algorithm win-rate table) on /signals and /analytics.
- Chart Trap Markers: LiveChart TRAPS toggle; BULL/BEAR TRAP, GAP UP/DOWN, STOP HUNT markers from /api/v1/memory/events per pair+TF.
- Forex test: deriv_service.history falls back to last Friday close when Deriv 'latest' is empty (weekend). backend/forex_model_check.py: EUR/USD 1m OOS win-rate 47.2%, GBP/USD 1m 52.9% -> both BELOW_TIER_GATES (58% obs / 80% live), correctly not promoted. Live forex signal test pending Monday session.
- Testing agent iteration_2: 100%.

## Session 2026-10-11 (part 3 – v13 advanced-mode fixes)
- Telemetry agents A2–A6 computed live from candles when no signal (no more empty WAITING cards); A7 shows deep-model training status/OOS win-rate.
- Signals: PAPER_SHADOW (model-not-promoted) signals now displayed with clear label + pair track record; pre-signal alerts for any pair; countdown/running state.
- Loss guard: 2 consecutive displayed losses -> 30 min global pause; max 2 open signals; pair win-rate <55% (>=6 settled) hidden as GUARD_SHADOW (still settled so it can recover). Env GUARD_*.
- UI: chart taller (grid min 820px), TradingView logo removed, premium pair tabs with coin/flag/volatility logos, picker counts, Binary mode = Deriv real + Quotex OTC, readable text layer, event stream shows all events.
- Observer/settings: no operator-key prompt when PROVIDER_CONTROL_KEY_SHA256 unset (API-key gate protects); observer launches Chromium locally (cloud preview has no display).
- Local: `scripts/local_pc/setup-windows.bat` (venv, torch, Playwright Chromium, `.env` copy), `scripts/local_pc/start-all.bat` with backend pre-flight.
- Testing agent iteration_3: 100%.

## Session 2026-10-11 (part 4)
- Pair tabs slimmed (44px, transparent, active blue underline, 24px logos, short names).
- Signals only 1m/5m (SIGNAL_TIMEFRAMES env, default 1m,5m; settings UI limited).
- Observers: WS Sniffer falls back (auto-starts local Chrome w/ remote debugging, else built-in Chromium); headless auto on no-display servers; backend reachability + Cloudflare/login status lines; provider status 'detail' + UI status banner; STOPPED vs FAILED fixed. Cloud preview IP gets Cloudflare challenge from Quotex -> observers must run on user's PC.
- Testing agent iteration_4: 100%.
