# AI-agent roadmap and handoff

Updated: 2026-10-13

## Working protocol for future agents

1. Read [the directory guide](DIRECTORY_GUIDE.md), then [the architecture map](PROJECT_ARCHITECTURE.md).
2. Trace the relevant route/service and existing tests before editing. Keep model
   code in `backend/ml`, `backend/dl`, or `backend/ds`; keep routes and service
   wiring in the backend root.
3. Preserve local environment files and never copy their values into source,
   logs, documentation, or reports.
4. Run the narrowest relevant tests, then run the full backend or frontend suite
   if the change affects shared behavior.
5. Update the architecture, performance evidence, or this roadmap when an
   implementation changes a documented boundary or validated next step.

## P0 — correctness and validation

- Validate Forex signal quality during an open weekday session; previous
  weekend checks could not validate those instruments.
- Review deep-model gates on EUR/USD and GBP/USD using at least 5,000 eligible
  candles; record the out-of-sample report and do not relax the gate to force a
  promotion.
- Update demo-engine tests for its async lifecycle and real Deriv-price
  settlement; confirm simulated trades remain separate from broker execution.

## P1 — product and provider follow-up

- Surface market-memory traps/gaps and algorithm win-rate evidence in the
  workspace analytics pages.
- Add chart markers for persisted trap/gap events, with provenance and
  timestamps visible.
- Investigate Deriv WebSocket disconnect/keepalive behavior and test reconnects
  without duplicate subscriptions or stale ticks.
- Polish the mobile chart toolbar and backend-health panel below 620 px.

## P2 — optional capabilities

- Add configurable Telegram and/or sound alerts without placing trade orders.
- Use the provider trading-times API for holiday-aware market sessions.
- Enable and test the optional PostgreSQL mirror locally, including its
  fallback behavior when pgvector is unavailable.

## ACCU implementation note (2026-11-11)

- Added Deriv-filtered Accumulator discovery, volatility-heuristic signal
  events, and optional automated ACCU execution with provider TP/SL limits and
  a maximum hold. The heuristic is not performance-validated; do not describe
  it as optimal or relax existing signal/model promotion gates based on it.
- The default ACCU settings are 20% take-profit, 50% stop-loss, and a
  five-minute maximum hold. Real-account execution still requires the existing
  operator key, typed confirmation, and configured risk limits. Validate ACCU
  performance separately before making any accuracy claim.

## Repository status

- The top-level source/dependency/docs layout is in place.
- API, ML, DL, and DS source now lives under `main_source_code/backend/`;
  environment and dependency manifests are under `dependencies/`.
- `main_source_code/database/` documents the current runtime-created schema
  behavior; no versioned migration set is currently checked in.
- No numerical model-performance claim is recorded here until an
  out-of-sample report satisfies the checklist in
  [MODEL_PERFORMANCE.md](MODEL_PERFORMANCE.md).
