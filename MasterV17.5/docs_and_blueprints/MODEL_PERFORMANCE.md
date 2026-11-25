# Model performance and evaluation evidence

## Current evidence status

This repository does not contain a verified, versioned report establishing
live predictive performance across markets. Do not describe models as
profitable, accurate, or production-ready based only on a `TRAINED`/`READY`
status, confidence score, bootstrap completion, or an in-sample result.

The application deliberately keeps broker execution disabled. Deep models must
pass chronological out-of-sample win-rate/expected-value gates and calibration
checks before live-signal promotion; PAPER models remain shadow-only. These
gates are safety/eligibility checks, not a guarantee of future performance.

## Where to inspect evidence

- `GET /api/v1/ml/status`: current model readiness, training activity, sample
  counts, and bootstrap/refresh state.
- `GET /api/v1/signals/research?source=deriv&symbol=<symbol>&timeframe=<tf>`:
  chronological strategy research. Inspect the returned report before making
  claims about one market or strategy.
- `GET /api/v1/signals/calibration`: calibration status and available sample
  information.
- MongoDB `deep_model_training_runs`, `deep_model_registry`,
  `signal_research_runs`, and `signal_calibration_runs`: persisted run evidence.

Use the current API response and code as the authority for exact fields; this
document intentionally does not hard-code changing runtime metrics.

## Reporting checklist

Every performance report should include:

1. Model/strategy name and code or model version.
2. Provider/source, symbol, timeframe, and market session.
3. Training and holdout date ranges; confirm the split is chronological and
   strictly out-of-sample.
4. Number of eligible and excluded observations, including the exclusion
   reasons.
5. Win rate with sample size and uncertainty interval, expected value/net
   payout assumptions, calibration result, and baseline comparison.
6. Whether the result is `PAPER`, candidate-only, or eligible for live-signal
   promotion; state explicitly that no broker execution is enabled.
7. Reproducible report location and evaluation timestamp.

Do not pool different symbols, sources, or timeframes without reporting
stratified results. Do not convert missing/insufficient data into a positive
score. Record new measured reports in a dated, non-secret artifact under
`test_reports/` and link it here only after validation.

## V17.2 evidence (2026-10-24)

`test_reports/model_validation_R_100_1m.json` – Deriv R_100, 1m, 1 500 closed candles (1 300 samples), expanding walk-forward,
4 purged folds, 1 176 out-of-sample rows:

| model | OOS accuracy | Brier (raw → Platt) | confident subset (p ≥ 0.58) |
|---|---|---|---|
| xgboost | 52.8 % | 0.285 → 0.248 | 63.3 % (60) |
| lightgbm | 50.9 % | 0.301 → 0.248 | 57.7 % (104) |
| catboost | 51.7 % | 0.276 → 0.248 | 61.5 % (143) |
| logistic | 48.0 % | 0.308 → 0.249 | – |
| **calibrated ensemble** | **53.7 %** | 0.248 | **66.7 % (48)** · 96 % of rows would be NO TRADE |

Leakage probe (shuffled labels): 52.6 % → pass. Deployment rule: a retrained stack replaces the champion only with ≥ +0.5 pt OOS
accuracy and no worse Brier (`ml/model_validation.py::challenger_wins`). These are evidence, not a profit guarantee.
