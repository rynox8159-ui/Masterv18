# Binary Lab Report

## Method and limitations
- Demo-only research; report generation made no trades and changed no UI/trading behavior.
- All records use Deriv demo `proposal` responses at $1 stake for the shortest available intraday contract (15 minutes). CALL and PUT payouts are separate and actual; unavailable proposals are reported N/A.
- The listed 1m/5m rows use an optimized subset of existing indicator-bundle features (ATR, Bollinger, EMA, MACD, RSI, stochastic, ROC, and candle body/side) and direction labels, but evaluate a 15-minute outcome horizon (15 x 1m bars or 3 x 5m bars). Selected feature values match the existing indicator bundle. Provider contract barriers are not historically replayed; win rate is directional accuracy, not a fully simulated contract settlement. Treat all resulting statuses as candidates, not trading authorization.
- The newest 12,000 chronologically ordered candles are used. The final 20% is a locked, never-seen test window. Model selection uses only purged/embargoed folds in the earlier 80%; training labels must end before the cutoff.
- Test entries are spaced by the 15-minute label horizon, so test labels do not overlap one another. Features use the completed candle i and entry is one second after its close; a sample is excluded if its 200-candle feature history or 15-minute label interval contains a timestamp gap.
- Raw p-values are one-sided normal-approximation Poisson-binomial tests against direction-specific proposal breakeven probabilities; BH is applied across all 58 locked pair/timeframe tests.

## Results

| Pair | TF | Candles | CV OOS n / win | Locked n / win | Wilson 95% CI | CALL payout / BE | PUT payout / BE | Raw p | BH q | Model | Screen | Status |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|
| frxAUDCAD | 1m | 12000 | 190 / 51.58% | 116 / 46.55% | 37.73%–55.59% | 1.70 / 58.82% | 1.92 / 52.08% | 0.96474 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDCAD | 5m | 12000 | 1768 / 49.66% | 461 / 48.37% | 43.84%–52.93% | 1.70 / 58.82% | 1.92 / 52.08% | 0.999997 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDCHF | 1m | 12000 | 190 / 53.68% | 122 / 53.28% | 44.46%–61.90% | 1.70 / 58.82% | 1.92 / 52.08% | 0.678833 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDCHF | 5m | 12000 | 1780 / 51.01% | 395 / 51.39% | 46.47%–56.28% | 1.70 / 58.82% | 1.92 / 52.08% | 0.987317 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDJPY | 1m | 12000 | 190 / 49.47% | 122 / 45.90% | 37.32%–54.73% | 1.71 / 58.48% | 1.91 / 52.36% | 0.98144 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDJPY | 5m | 12000 | 1780 / 51.85% | 396 / 47.73% | 42.85%–52.65% | 1.71 / 58.48% | 1.91 / 52.36% | 0.999985 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDNZD | 1m | 12000 | 190 / 51.05% | 136 / 45.59% | 37.45%–53.97% | 1.70 / 58.82% | 1.92 / 52.08% | 0.992207 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDNZD | 5m | 12000 | 1784 / 51.12% | 396 / 46.97% | 42.11%–51.89% | 1.70 / 58.82% | 1.92 / 52.08% | 0.999997 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDUSD | 1m | 12000 | 190 / 54.21% | 124 / 50.81% | 42.12%–59.45% | 1.71 / 58.48% | 1.91 / 52.36% | 0.872179 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxAUDUSD | 5m | 12000 | 1784 / 51.46% | 396 / 47.73% | 42.85%–52.65% | 1.71 / 58.48% | 1.91 / 52.36% | 0.999982 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURAUD | 1m | 12000 | 190 / 54.74% | 123 / 47.97% | 39.33%–56.72% | 1.91 / 52.36% | 1.71 / 58.48% | 0.952692 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURAUD | 5m | 12000 | 1784 / 50.50% | 398 / 46.73% | 41.89%–51.64% | 1.91 / 52.36% | 1.71 / 58.48% | 0.999999 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURCAD | 1m | 12000 | 190 / 49.47% | 123 / 57.72% | 48.89%–66.09% | 1.80 / 55.56% | 1.77 / 56.50% | 0.371925 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURCAD | 5m | 12000 | 1788 / 50.95% | 397 / 49.62% | 44.73%–54.52% | 1.80 / 55.56% | 1.77 / 56.50% | 0.995871 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURCHF | 1m | 12000 | 190 / 51.58% | 123 / 52.03% | 43.28%–60.67% | 1.67 / 59.88% | 1.89 / 52.91% | 0.94871 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURCHF | 5m | 12000 | 1788 / 53.91% | 400 / 53.00% | 48.10%–57.84% | 1.67 / 59.88% | 1.89 / 52.91% | 0.918462 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURGBP | 1m | 12000 | 190 / 51.05% | 138 / 52.90% | 44.60%–61.04% | 1.88 / 53.19% | 1.68 / 59.52% | 0.840158 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURGBP | 5m | 12000 | 1792 / 51.62% | 400 / 51.25% | 46.36%–56.11% | 1.88 / 53.19% | 1.68 / 59.52% | 0.98384 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURJPY | 1m | 9106 | 140 / 46.43% | 99 / 51.52% | 41.80%–61.12% | 1.70 / 58.82% | 1.92 / 52.08% | 0.75119 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURJPY | 5m | 12000 | 1796 / 51.11% | 466 / 52.36% | 47.83%–56.86% | 1.70 / 58.82% | 1.92 / 52.08% | 0.996747 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURNZD | 1m | 9103 | 140 / 47.86% | 98 / 42.86% | 33.51%–52.74% | 1.84 / 54.35% | 1.72 / 58.14% | 0.997562 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURNZD | 5m | 12000 | 1800 / 51.17% | 465 / 52.04% | 47.50%–56.55% | 1.84 / 54.35% | 1.72 / 58.14% | 0.984175 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURUSD | 1m | 9135 | 66 / 46.97% | 93 / 38.71% | 29.45%–48.87% | 1.72 / 58.14% | 1.84 / 54.35% | 0.999524 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxEURUSD | 5m | 12000 | 1800 / 49.11% | 465 / 45.16% | 40.70%–49.71% | 1.72 / 58.14% | 1.84 / 54.35% | 1 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPAUD | 1m | 9102 | 140 / 47.86% | 98 / 51.02% | 41.27%–60.69% | 1.90 / 52.63% | 1.72 / 58.14% | 0.828883 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPAUD | 5m | 12000 | 1804 / 51.55% | 468 / 48.93% | 44.43%–53.45% | 1.90 / 52.63% | 1.72 / 58.14% | 0.999851 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPCAD | 1m | 9106 | 140 / 46.43% | 99 / 48.48% | 38.88%–58.20% | 1.67 / 59.88% | 1.89 / 52.91% | 0.931015 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPCAD | 5m | 12000 | 1804 / 50.33% | 467 / 50.32% | 45.80%–54.83% | 1.67 / 59.88% | 1.89 / 52.91% | 0.997363 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPCHF | 1m | 9105 | 140 / 47.14% | 99 / 54.55% | 44.75%–64.00% | 1.67 / 59.88% | 1.88 / 53.19% | 0.689767 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPCHF | 5m | 12000 | 1804 / 50.17% | 466 / 52.36% | 47.83%–56.86% | 1.67 / 59.88% | 1.88 / 53.19% | 0.842718 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPJPY | 1m | 8226 | 134 / 44.03% | 87 / 39.08% | 29.50%–49.59% | 1.70 / 58.82% | 1.92 / 52.08% | 0.997102 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPJPY | 5m | 12000 | 1816 / 51.10% | 474 / 53.80% | 49.30%–58.24% | 1.70 / 58.82% | 1.92 / 52.08% | 0.902333 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPNZD | 1m | 8224 | 61 / 54.10% | 86 / 45.35% | 35.25%–55.84% | 1.80 / 55.56% | 1.77 / 56.50% | 0.982478 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPNZD | 5m | 12000 | 1816 / 52.97% | 472 / 54.03% | 49.51%–58.47% | 1.80 / 55.56% | 1.77 / 56.50% | 0.854657 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPUSD | 1m | 8224 | 134 / 52.99% | 86 / 52.33% | 41.90%–62.55% | 1.67 / 59.88% | 1.89 / 52.91% | 0.808375 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxGBPUSD | 5m | 12000 | 1816 / 49.83% | 475 / 51.37% | 46.88%–55.83% | 1.67 / 59.88% | 1.89 / 52.91% | 0.993076 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxNZDJPY | 1m | 8223 | 134 / 46.27% | 87 / 43.68% | 33.74%–54.15% | 1.54 / 64.94% | 1.96 / 51.02% | 0.988803 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxNZDJPY | 5m | 12000 | 1816 / 51.27% | 474 / 46.20% | 41.76%–50.70% | 1.54 / 64.94% | 1.96 / 51.02% | 1 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxNZDUSD | 1m | 8226 | 134 / 58.96% | 88 / 51.14% | 40.87%–61.31% | 1.69 / 59.17% | 1.87 / 53.48% | 0.744536 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxNZDUSD | 5m | 12000 | 2000 / 52.35% | 468 / 50.21% | 45.70%–54.72% | 1.69 / 59.17% | 1.87 / 53.48% | 0.999406 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDCAD | 1m | 4520 | 0 / N/A | 0 / N/A | N/A | 1.88 / 53.19% | 1.67 / 59.88% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDCAD | 5m | 12000 | 2000 / 50.10% | 468 / 52.35% | 47.82%–56.84% | 1.88 / 53.19% | 1.67 / 59.88% | 0.998491 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDCHF | 1m | 4523 | 0 / N/A | 0 / N/A | N/A | 1.68 / 59.52% | 1.87 / 53.48% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDCHF | 5m | 12000 | 2000 / 49.00% | 468 / 51.07% | 46.55%–55.57% | 1.68 / 59.52% | 1.87 / 53.48% | 0.954008 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDJPY | 1m | 4521 | 0 / N/A | 0 / N/A | N/A | 1.72 / 58.14% | 1.90 / 52.63% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDJPY | 5m | 12000 | 2000 / 51.65% | 468 / 49.15% | 44.64%–53.66% | 1.72 / 58.14% | 1.90 / 52.63% | 0.998145 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDMXN | 1m | 4521 | 0 / N/A | 0 / N/A | N/A | 1.83 / 54.64% | 1.63 / 61.35% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDMXN | 5m | 12000 | 1996 / 49.10% | 469 / 53.94% | 49.42%–58.41% | 1.83 / 54.64% | 1.63 / 61.35% | 0.986207 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDPLN | 1m | 4519 | 0 / N/A | 0 / N/A | N/A | 1.84 / 54.35% | 1.62 / 61.73% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxUSDPLN | 5m | 12000 | 1996 / 49.55% | 469 / 51.60% | 47.08%–56.09% | 1.84 / 54.35% | 1.62 / 61.73% | 0.999021 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxXAGUSD | 1m | 3120 | 0 / N/A | 0 / N/A | N/A | 1.64 / 60.98% | 1.82 / 54.95% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxXAGUSD | 5m | 11400 | 200 / 63.50% | 128 / 42.19% | 33.98%–50.85% | 1.64 / 60.98% | 1.82 / 54.95% | 0.999918 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |
| frxXAUUSD | 1m | 3121 | 0 / N/A | 0 / N/A | N/A | 1.70 / 58.82% | 1.77 / 56.50% | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxXAUUSD | 5m | 11402 | 200 / 50.50% | 128 / 55.47% | 46.82%–63.79% | 1.70 / 58.82% | 1.77 / 56.50% | 0.658089 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxXPDUSD | 1m | 3121 | 0 / N/A | 0 / N/A | N/A | N/A (TradingDurationNotAllowed) | N/A (TradingDurationNotAllowed) | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxXPDUSD | 5m | 11401 | 200 / 58.00% | 0 / N/A | N/A | N/A (TradingDurationNotAllowed) | N/A (TradingDurationNotAllowed) | 1 | 1 | logistic | FAIL | CANDIDATE_UNCONFIRMED |
| frxXPTUSD | 1m | 3121 | 0 / N/A | 0 / N/A | N/A | N/A (TradingDurationNotAllowed) | N/A (TradingDurationNotAllowed) | 1 | 1 | none | FAIL | CANDIDATE_UNCONFIRMED |
| frxXPTUSD | 5m | 11402 | 200 / 58.50% | 0 / N/A | N/A | N/A (TradingDurationNotAllowed) | N/A (TradingDurationNotAllowed) | 1 | 1 | lightgbm | FAIL | CANDIDATE_UNCONFIRMED |

## Controls
- **Shuffled-label control:** 1000 refit permutations per eligible record; mean passing records by chance per shuffle: 0.000; maximum in one shuffle: 0; records passing at least once: 0 of 58 (eligible records: 25).
- Each null iteration refits the record's CV-selected model on shuffled training labels and tests the untouched lockbox with the same breakeven, Wilson, one-sided p-value, and BH gates. The model family selection is frozen from development folds; the lockbox is never used for selection.
- **Leakage checks:** all 49 locked rows have each feature timestamp strictly before entry and no training-label horizon overlaps the cutoff: True; the test begins after the purged cutoff and embargo.
- Contract payout and horizon mismatch: the provider rejected 1m/5m CALL durations for forex. Reported proposals use the shortest available intraday duration, 15m; they do not prove profitability for a hypothetical 1m/5m contract.
- **EDGE status:** 0; all records remain `CANDIDATE_UNCONFIRMED` because their labels are directional candle outcomes, not replayed Deriv contract settlements.
