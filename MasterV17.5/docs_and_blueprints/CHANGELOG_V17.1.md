# CHANGELOG · MasterV17.1 (from MasterV16.9)

Built from the 19 tasks in `MASTER CANDLE.docx`.

## Terminal
- **"Forex" → "DERIV"** in the header market-mode switch (terminal + trade page).
- **"ENGINE · ALL 43 PAIRS"** note removed from the tab bar.
- **New multi-pair tab bar** (`MarketRail.tsx`, `v171.css`): compact tabs with flag/icon, pair name, **live price**
  (green/red when it moves), live dot, close button, active tab highlighted with the blue underline, smooth scroll.
- **"+" opens a Deriv-style market picker**: groups *Derived · Forex · Stocks & indices · Commodities · Crypto · Favourites*,
  sections per Deriv submarket (Volatility, Crash/Boom, Jump, Step, …), search, live price per row, closed markets listed
  as "Market closed". Everything comes from Deriv `active_symbols` live – no hard-coded list.
- **Logo**: the header brand now uses the same animated Master Candle logo that the loading screen shows (`Brand.tsx`).
- Decision output shows the **brain signal** (direction, confidence, indicators agreeing, best contract) instead of a
  permanent `NO_SIGNAL · ANALYTICAL ONLY` card while the walk-forward promotion gate is still collecting samples.
- Version strings → V17.1 everywhere (boot splash, footer, ZIP name, health endpoint).

## Backend
- **Signal Brain** (`main_source_code/backend/brain.py`, API `/api/v1/brain/*`): every Deriv tick feeds a per-symbol
  buffer; closed candles feed the indicator vote engine. For each symbol it scores Rise/Fall and Digit contracts
  (Even/Odd, Over/Under all barriers, Matches/Differs all digits) with win-probability, payout-aware **edge** and a 0-100
  confidence score. Statistical shrinkage prevents noise from looking like an edge.
- **Learning**: every settled auto trade is recorded per *symbol × contract type × market condition* (`brain_stats`,
  `brain_outcomes`); learned win rates shift future confidence and picks (win-algorithm detection).
- `DerivService.tick_listeners` – zero-copy tick fan-out to the brain, no extra Deriv connection.
- Telemetry evidence now carries `brain`, `brainPick` and gate `BRAIN`.

## Auto trade (full redesign, one frame)
- `/autotrade`: **Demo / Real** switch, live balance, today P/L, win rate, trades today, engine phase, START / STOP / KILL.
- **Signal source**: *EVENT STREAM* (default, mirrors system signals) or *BRAIN*; **Trade type** (brain): *AUTO* (brain picks the best contract among enabled groups) or *MANUAL* (you pick Rise, Fall, Even, Odd,
  Over, Under, Matches, Differs + digit barrier). Touch/No Touch, Higher/Lower, Accumulators, Multipliers, Turbos,
  Vanillas are shown but disabled ("phase 2").
- **Stake**: *MANUAL* fixed amount or *AUTO* (% of balance scaled by confidence), min brain confidence, scan interval.
- **Protection**: daily loss limit (USD or % of day-start balance), max stake, max trades/day, pause after 3-4 losses
  in a row, cooldown after a loss. Real mode keeps the typed `REAL` confirmation + operator key.
- Live trade feed with result badges and P/L, engine log, brain picks with edge, learned win rates.
- Engine: new `signalSource = "brain"` cycle (`_brain_cycle`, `_brain_trade`), barrier-aware proposals
  (`DerivExecutor.proposal(..., barrier=)`), idempotent orders, outcome → brain learning.

## Setup / tooling
- `main_source_code/database/init_db.py` + `scripts/local_pc/init-db.(sh|bat)`: prepares **any local MongoDB**
  (collections + indexes) from `MONGO_URL` / `DB_NAME` in `.env`; wired into both setup scripts.
- `tests/ui_smoke.py` + `scripts/local_pc/run-ui-smoke.(sh|bat)`: Playwright UI smoke test (13 checks).
- ws-sniffer / Playwright observer scripts verified (`start-chrome-debug`, `start-sniffer`, `start-observer`).
- Python 3.11/3.12 + Node 20 pinned; torch installed from the CPU wheel index by the setup scripts.

## V17.1 round 2 (user feedback)
- **Event stream ⇒ auto trade (mirror mode, default)**: `signalSource = "stream"`. Every signal the system pipeline emits
  (the ones you see in the Event stream / Decision output) is bought on the Deriv account at its entry candle — same pair,
  same Rise/Fall direction, same duration — concurrently per pair (`maxConcurrentTrades`). The settled result is written
  back onto the signal (`live_signals.autoTrade`) so the Decision output shows "Auto trade DEMO · WIN +0.88 USD".
- **Signal gates now observe instead of hide** (`SIGNAL_PROMOTION_GATE=observe`, `SIGNAL_PRECISION_GATE=observe` in .env):
  pipeline-validated signals keep their LIVE_VALIDATED / PAPER_SHADOW tier; the promotion/precision verdict is recorded on
  the signal (`promotion`, `precisionPassed`). Set to `enforce` to go back to V16.9 hiding.
- **Decision output card = system-flow signal only** (brain no longer mixed in): pair + market type (Deriv synthetic /
  forex / crypto / Binary OTC), contract (Rise/Fall · timeframe), alert time, entry time, close time (UTC + local),
  CALL/PUT, confidence, indicators agree/oppose, pair record, system-flow line (modules online, model accuracy, agents,
  provenance), settled result badge (WIN/LOSS) and the mirrored auto-trade result. Recent settled signals stay visible
  for 15 minutes.
- **Chart ultra-smooth**: forming candle is tweened per tick (ease-out 320 ms per rAF) instead of jumping once per second;
  the rolling history window no longer triggers a full `setData` redraw (only pair/timeframe/indicator changes or a real
  history reload do), fixing the once-a-minute jump; snapshot updates can no longer drag the drawn close backwards.
- Brain scoring is now conservative (edge from the lower confidence bound of the win probability), so Over 8 / Matches
  style contracts cannot look profitable on a few lucky ticks.
- Manual START clears a saved loss-streak pause; `_place_contract` does risk checks before waiting for the entry candle.

## Honest note
No signal engine can guarantee profit. V17.1 avoids low-edge trades (confidence/edge filter), learns from results and
caps losses with hard limits. Test in DEMO first.
