# CHANGELOG MasterV16.8 (June 2026)

## Launcher
- New root `start.bat`: MongoDB check/start, port-in-use check (8001/5173), package/.env pre-check, backend health wait,
  frontend wait, then opens the browser. `setup.bat` wraps the one-time setup. `start-all.bat` delegates to `start.bat`.
- Python 3.11/3.12 pinned in `requirements.txt`; Node `>=20.10 <23` in `package.json` engines.

## Deriv / backend
- `DerivRequestChannel`: shared request/response websocket with req_id routing, auto-reconnect and 250 ms request pacing.
- `is_real_market_instrument` accepts every non-OTC, non-suspended Deriv market (forex, synthetic_index incl. Boom/Crash/Jump/Step,
  cryptocurrency, commodities, indices, basket_index).
- Primary socket keeps <= 50 tick streams; extra symbols get persistent tick connections; candles (1m/5m/15m) are fanned out
  over persistent connections (16 symbols each).
- `active_symbols` polled every 60 s (`DERIV_MARKET_STATUS_INTERVAL_SECONDS`): `exchangeIsOpen` per instrument, newly opened
  / listed pairs trigger a resubscribe. `instrument_is_open` uses only Deriv's flag (hardcoded weekend rule removed).
- Indexes: `pair_promotions(symbol,timeframe)`, `live_signals(source,symbol,timeframe,status,expiryEpoch)`, `live_signals(status,generatedAt)`,
  `autotrade_trades(mode,openedAt)`, `autotrade_trades.requestId` unique sparse.

## Accuracy proof / promotion gate
- `promotion_gate.py`: walk-forward backtest per pair/timeframe (`build_profile(train_fraction=0.5, max_points=3200)` on up to
  4000 closed candles), payout-aware break-even, `PROMOTION_MIN_WIN_RATE=58`, `PROMOTION_MIN_SAMPLES=300`, Wilson 95 % lower bound
  above break-even. Boom/Crash use a conservative 10 % payout estimate (asymmetric payouts). Routes `/api/v1/promotion/report|run|status`.
- `signal_service`: Deriv signals on non-promoted pairs get `validationTier=NOT_PROMOTED_SHADOW` (hidden, still settled) and `promotion` field.
- Runs after every Train-All and every `PROMOTION_INTERVAL_SECONDS` (6 h); first run `PROMOTION_STARTUP_DELAY_SECONDS` after boot.
- Frontend `/accuracy` report screen (win rate, samples, Wilson LB, break-even, payout, expectancy, reason, last training).

## Auto trade
- Settings: `maxConsecutiveLosses` (3-4), `dailyLossLimitMode` (amount|percent), `dailyLossLimitPercent`, `requirePromotedPair`.
- State: `consecutiveLosses`, `dayStartBalance` (UTC day). Halt on loss streak, effective daily limit (USD or % of day-start balance).
- `POST /api/v1/autotrade/kill`: cancels PENDING_ORDER rows, sells the open contract (`sell` price 0), stops the engine.
- Idempotent orders via unique `requestId` (signal id or symbol:direction:entry minute); PENDING_ORDER row inserted before `buy`.
- `_reconcile_open_trades()` after (re)connect: OPEN trades are awaited via `proposal_open_contract` and settled.
- Feed guard: no order while the shared Deriv feed is disconnected/stale; signal cancelled (`MARKET_CLOSED`) when Deriv marks the pair closed;
  non-promoted pairs skipped.
- UI: tabs Live P&L / Open Trades / History / Risk Settings, kill switch button, demo default / real labelled.

## Chart / header
- Incremental rendering (series.update for live tick and new candle, overlay last-point update), epoch de-duplication,
  markers only re-set when changed, candle borders coloured by own direction.
- Header controls moved to the right; version strings -> V16.8.

## 0-lag chart + verified ZIP (second pass)
- Backend `GET /api/telemetry/ticks?symbol=` : SSE of raw Deriv ticks the instant they arrive (`DerivService.publish_tick`
  fan-out, bounded queues, slow clients drop oldest). Chart subscribes via `useTickStream` (callback, no React state).
- `LiveChart`: ticks are coalesced with `requestAnimationFrame` into one `series.update()` of the forming candle
  (high/low/close only; a new bucket appends one bar). Price / O-H-L-C header updated through DOM refs; component wrapped
  in `React.memo`, Home memoises the chart controls. Historical candles are only `setData()`-ed when the pair/timeframe/kind/
  indicators change or history is replaced after a reconnect.
- Project ZIP is now built in the background into `_export/MasterV16.8.zip` (`POST /api/v1/project/build`, `GET /status`,
  `GET /download`), verified with `zipfile.testzip()` + SHA-256 (`MasterV16.8.zip.sha256`) and served as a static file.
  The previous streamed ZIP was cut by the 60 s proxy timeout -> WinRAR "archive is corrupt". Nested `.zip` files are excluded.
