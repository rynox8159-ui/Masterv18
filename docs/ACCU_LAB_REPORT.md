# ACCU Lab Research Report

## Status

- **Calibration: BLOCKED — INSUFFICIENT_DATA**
- The read-only account-history query found **6 settled demo ACCU trades**, including the 3 smoke-test trades; this is below the required 50.
- Calibration was not run: **0 compared / 50 required**; mismatch rate is **N/A**, not 100%. Calibration is blocked until at least 50 settled trades are available for comparison.
- Per-symbol held-out statistical report: **INSUFFICIENT_DATA** for every symbol.
- The lab itself placed no orders. A separately authorized demo smoke test ran; no UI or production trading behavior was changed.

## Power-analysis minimum

- Status uses **INSUFFICIENT_DATA** whenever a symbol has fewer than **1,404 settled outcomes**.
- The minimum is an exact one-sided binomial power calculation: family-wise alpha `0.05/27` (Bonferroni across the 27 symbols), 80% power, null win probability `0.50`, and a predeclared detectable alternative of `0.55`. At n=1,404, the critical count is 757 wins; the null tail probability is 0.001805 and power at 0.55 is 0.80024.
- This is a conservative planning assumption, not a measured ACCU breakeven or payout-derived edge threshold. The available ACCU proposal responses did not provide a usable positive payout, so no payout-based EV or win-probability null is claimed.

## ACCU Symbols Discovered

`1HZ100V`, `1HZ10V`, `1HZ15V`, `1HZ25V`, `1HZ30V`, `1HZ50V`, `1HZ75V`, `1HZ90V`, `BOOM1000`, `BOOM150N`, `BOOM300N`, `BOOM50`, `BOOM500`, `BOOM600`, `BOOM900`, `CRASH1000`, `CRASH150N`, `CRASH300N`, `CRASH50`, `CRASH500`, `CRASH600`, `CRASH900`, `R_10`, `R_100`, `R_25`, `R_50`, `R_75`

All discovered symbols are `INSUFFICIENT_DATA` because each has fewer than 1,404 settled outcomes. No positive edge is claimed.

## Demo smoke test

- **Run:** 15-minute bounded demo test; first 608.1 seconds included order attempts/settlement, followed by 292 seconds with no additional orders.
- **Contract:** R_100 ACCU, 4% growth, $1 stake, $0.05 take-profit; hard cap of 3 orders.
- **Orders placed:** 3; **buy/proposal rejects:** 0; **settled:** 3/3; each returned +$0.08, total +$0.24.
- **Rejected early-sell responses:** `InvalidtoSell: Contract cannot be sold at entry tick. Please wait for the next tick.` (three initial attempts); `BetExpired: The contract has expired.` (one retry). These were sell requests, not rejected buys; all three contracts subsequently reported sold/settled.
- The smoke trades are not strategy-generated or held-out model observations, so they are excluded from the per-symbol edge statistics below. They count toward the settled-demo-trade prerequisite; the account has 6 settled trades in total, still below 50.

## Per-Symbol Research Results

The table below reports the lab's strategy/outcome dataset and excludes smoke-only orders and account-history trades that cannot be replayed against a complete recorded tick window.

| Symbol | n trades | Win rate | EV/trade | 95% one-sided CI lower | p-value | BH-adjusted p | Edge |
|---|---:|---:|---:|---:|---:|---:|---|
| 1HZ100V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ10V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ15V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ25V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ30V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ50V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ75V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| 1HZ90V | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM1000 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM150N | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM300N | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM50 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM500 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM600 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| BOOM900 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH1000 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH150N | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH300N | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH50 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH500 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH600 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| CRASH900 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| R_10 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| R_100 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| R_25 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| R_50 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |
| R_75 | 0 | N/A | N/A | N/A | N/A | N/A | INSUFFICIENT_DATA |

## Reproducibility and Safety

- The simulator requires barrier distance and payout fields from Deriv proposal/open-contract responses and raises if they are absent.
- ACCU replay compares each quote with the preceding tick using provider-reported barrier distance relative to provider-reported spot, applies take-profit against compounded growth, and excludes any replay window containing a recorded gap.
- The held-out analysis is chronological (70% train, 30% test) and applies Benjamini-Hochberg across symbols.
- Loss feature extraction is implemented for 30-tick pre-loss windows.
- The lab contains no `buy` call and is isolated under `backend/lab/`.
