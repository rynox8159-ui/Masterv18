# Master Candle V16.3 – change log (edited on top of V16.1, no rebuild)

## Backend
- **Deriv feed smoothness**: faster reconnect (1s → max 20s), heartbeat silence 30s (was 90s), tick-stale reconnect 30s (was 120s), 5s recv poll, extra candle streams retry in 2s. All tunable in `.env` (`DERIV_*`, `MARKET_FRESHNESS_SECONDS=45`).
- **All Deriv pairs tracked**: `DERIV_SYMBOLS=ALL` – 44 pairs on the main stream + extra candle connections; every pair analysed on 1m/5m.
- **Market Algorithm Detection Layer** (`algorithm_detector.py`): Hurst regime, return autocorrelation, colour n-gram, streak exhaustion, wick rejection, range z-score, FFT cycle. Applied to Deriv *and* Quotex/OTC (binary) signals. Fail-closed vetoes: dead/explosive volatility, flat market, random-walk-without-edge, layer opposes signal (`ALGO_LAYER_STRICT=true`).
- **ML/DL/DS**: real scikit-learn RandomForest (replaces the toy one) + new ExtraTrees, HistGradientBoosting, MLP neural net, Logistic regression → 11-model stack (LSTM, Transformer, XGBoost, LightGBM, CatBoost, RF, ET, HGB, MLP, LR, MARL). All tabular models feed the Bayesian-weighted tree probability. Inference skips a model on feature-shape drift instead of failing the whole ensemble.
- **Automatic training**: Train-All starts automatically after boot (`TRAIN_ALL_ON_STARTUP=true`, delay `TRAIN_ALL_STARTUP_DELAY_SECONDS`) unless a run finished in the last 24h, then every 24h. Manual Train All still available.
- **500-agent system**: every Deriv pair × timeframe is a live agent that records each evaluation (direction, gate, regime, edge) and its settled win-rate. `GET /api/v1/agents/live`.
- **System flow**: `GET /api/v1/modules` is now live (agents, feed, algorithm layer, model stack, signal engine, auto trade).
- **Auto trade (demo + real)** – new default source *System signals 1m/5m*. A system signal is traded only when ALL pass: min confidence, validated tier, 24h system accuracy (≥10 settled), pair signal history (samples + win-rate), bot's own pair record, payout ≥ minimum, **safety margin** = evidence win-rate ≥ breakeven (1/(1+payout)) + `safetyMarginPct`, and the accuracy-layer double-check. Entry is timed to the signal's candle; duration = signal timeframe. Old "accuracy layers" mode is still selectable.
- Quotex observer: latest quote per pair is never dropped (throttled quotes are flushed), history backfill posted in parallel chunks.
- Local run fixes: frontend `.env.local` no longer forces the wrong proxy port 7007 (now follows `BACKEND_PORT`), `tzdata` added for Windows time zones.

## V16.3 update 2 (feed + accuracy)
- Deriv ticks are received instantly and processed latest-per-symbol (no backlog) · CPU ~47% (was 150%) · processing lag < 1s.
- Clock: Deriv `time` API with round-trip compensation corrects a drifting PC clock (chart countdown + signal timing).
- Extra candle sockets are persistent, so a primary reconnect never makes the feed stale.
- **Walk-forward proven-edge gate**: every pair x timeframe is back-tested on its own history (train 65% / test 35%); signals are released only when the out-of-sample hit-rate is >= 56% on >= 40 samples AND the 95% Wilson lower bound is > 50%, and the proven detectors agree with the signal. Honest confidence = capped at the measured out-of-sample hit-rate.
- 1m markets are assessed 12-25s before entry; late signals are dropped (never published after the candle opened).
- Signal history: edge-gate accuracy chips, hours window applied to the table, full CSV export.
- Quotex observer: robust auto-login with QX_EMAIL/QX_PASSWORD (.env), follows ALL open pairs (QX_FOLLOW_LIMIT=200), latest quote never dropped. Quotex blocks US/cloud IPs - run `scripts/local_pc/start-observer.bat` on your own PC.

## Frontend
- Boot splash with animated Master Candle logo + loading steps.
- Backend health card kept in the System monitor sidebar; header SOURCE label and status pills removed (info lives in the card).
- Chart header border removed – pair/price/OHLC/toolbar float over the chart; chart uses the full area.
- Drag handle between chart and Event stream (drag up/down, double-click resets, remembered).
- Smooth candle render: forming candle updates in place instead of full redraw.
- Agent control center shows real agents, regimes, evaluations and win-rates.
- Auto trade page in one frame + "System signal auto-trade" panel (source switch, 1m/5m, layer double-check, gate results).
- Higher text/colour contrast, micro-animations, mobile fixes.

## Honest note
No system can guarantee winning trades. Current measured system accuracy is shown live (Agents dialog / Auto trade panel). With the default gates the bot simply waits when the measured edge is not above breakeven + safety margin. Always test in DEMO first.
