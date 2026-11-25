# Auto-trader

This standalone bot reads live signals from the existing backend's read-only
`/api/v1/signals/live` endpoint and checks
`/api/v1/signals/execution-eligibility` before submitting a Deriv contract. By
default it considers only Deriv-origin, live-validated, non-OTC signals and
requires at least four agreeing votes and a measured pair track record of at
least six settled outcomes with at least 55% accuracy. Signal confidence is a
model/confluence score, not a promise of future win rate.

## Setup

1. Install the backend's existing requirements if they are not already
   available, including `websockets`, `httpx`, and `python-dotenv`.
2. Copy `.env.example` to `.env` in this directory and supply
   `BACKEND_API_KEY`, `DERIV_APP_ID`, and a demo API token.
3. Run from the repository root with `python -m auto_trade`.

The bot defaults to demo mode. Real-account trading requires selecting
`DERIV_ACCOUNT_MODE=live`, setting `LIVE_TRADING_ENABLED=true`, and providing a
separate `DERIV_LIVE_TOKEN`. Authorization verifies the account type and stops
if it does not match the selected mode. Credentials are read only from
environment variables or `auto_trade/.env`; they are never written to the
state file or logs.

The highlighted Decision output carousel is populated from the backend
`/api/v1/signals/live` response's `upcoming` list; the bot reads this same list.
To also trade its `PAPER_SHADOW` / “MODEL NOT PROMOTED” cards, explicitly set
`ALLOW_UNPROMOTED_SIGNALS=true`. This can enable those signals in either demo
or live mode. For such signals only, an empty pair track record is permitted,
but any existing record below the configured accuracy floor still blocks the
trade. The override bypasses only the backend's model-promotion and promoted
ensemble eligibility reasons; all other eligibility, signal, payout, and risk
checks remain mandatory. Because an unpromoted signal can be untrained and has
no verified pair accuracy, the recommended setting—especially for a real
account—is to leave this option disabled.

## Accumulator setups

The backend discovers Accumulator underlyings from Deriv's unfiltered
`active_symbols` catalog and confirms each Forex symbol's available growth
rates with `contracts_for`. It publishes 1-minute provider-candle volatility setups at
`/api/v1/signals/accumulators` and records new setups in the regular Event
stream. The default growth-rate heuristic selects among Deriv's supported
1–5% rates; manual rate override is available on `/autotrade`.

These are explicitly **unvalidated heuristic observations**, not an optimal
strategy, measured win probability, or profit guarantee. The scanner records
them as `PAPER_SHADOW`; the live Accumulator endpoint and Auto Trade accept only
market-matched setups with a passed out-of-sample validation report. No
Accumulator-specific validator currently promotes these heuristic candidates,
so they remain visible in the event log but are not eligible for Auto Trade.
When validated candidates become available, the existing 20%-of-stake
take-profit, 50%-of-stake stop-loss, five-minute maximum hold, stake,
concurrency, daily-loss, loss-streak, and real-account confirmation safeguards
still apply.

## Risk controls

- Rise/Fall contracts can monitor the authenticated `proposal_open_contract` stream
  and request an early sell at the configured take-profit or stop-loss threshold.
  Optional trailing mode arms after take-profit and exits on a configured pullback.
  If Deriv rejects an early sell, the contract remains monitored and settles at
  normal expiry; the rejection is logged.
- Accumulator contracts use Deriv-side take-profit and stop-loss limits and are
  monitored for settlement or the configured maximum hold.
- Default base stake is 0.2% of the current balance.
- Every contract's maximum loss (stake) is capped at 1% of balance.
- Trading stops after realized losses reach 3% of the balance at the start of
  the UTC trading day. Before buying, the bot also caps the next stake to the
  remaining daily loss budget so one trade cannot exceed that limit.
- Recovery is limited to four consecutive losses and requires either a
  validated-model confidence of at least 98 or a measured signal track record
  of at least 98%. Neither is a guarantee.
- The recovery target includes accumulated realized losses, the original base
  stake, and a profit target equal to the base stake. The stake is calculated
  from the proposal's *net* payout rate. If that stake exceeds the 1% risk cap,
  the trade is skipped; the bot never raises the cap to force recovery.
- At most the configured number of contracts are managed concurrently. Signal
  IDs are durably marked before a buy request to prevent duplicate submissions.
  An ambiguous/unresolved buy pauses trading and requires manual review.

The local operational state is stored in `auto_trade/state.json`. Keep this
file intact while the bot is in use; deleting it resets the locally tracked
daily loss and recovery history. This software does not guarantee profits and
should be tested with a demo account first.

## Web dashboard

The dashboard is available at `/autotrade`; it uses the backend's
`/api/v1/autotrade` routes and starts in demo mode. The dashboard auto-trader
consumes Accumulator setups only; Binary Rise/Fall signals remain available on
the separate Binary screen and are never submitted by this engine. Select
`DEMO + REAL` to run eligible Accumulator orders on both accounts. Real-mode starts are disabled
unless the backend environment sets both `AUTOTRADE_WEB_LIVE_ENABLED=true` and
`AUTOTRADE_WEB_REAL_CONTROL_KEY` to a strong operator secret. The operator must
also enter that secret and explicitly confirm `REAL` in the dashboard. Keep the
secret server-side, use HTTPS, and never commit it or expose it in frontend
configuration. Real mode additionally requires `DERIV_LIVE_TOKEN` and the
Deriv app ID to be configured for the backend.

Deriv signals pass through the precision gate before they are shown. Rejected
signals are stored as `PRECISION_SHADOW` and still settle against market data,
but are not presented as live trade recommendations. Neither the gate nor the
dashboard can guarantee a winning trade.
