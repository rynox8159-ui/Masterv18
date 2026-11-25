import math
import json
import sqlite3
from collections import defaultdict

import numpy as np
from scipy import stats

ACCU_POWER_MINIMUM_TEST_TRADES = 1404


def wilson_lower_bound(wins, n, z=1.6448536269514722):
    if n <= 0:
        return 0.0
    p = wins / n
    den = 1 + z*z/n
    center = (p + z*z/(2*n)) / den
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / den
    return max(0.0, center-half)


def benjamini_hochberg(pvalues):
    m = len(pvalues)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: pvalues[i])
    adjusted = [0.0]*m
    minimum = 1.0
    for rank_index in range(m-1, -1, -1):
        original_index = order[rank_index]
        rank = rank_index + 1
        minimum = min(minimum, pvalues[original_index] * m / rank)
        adjusted[original_index] = minimum
    return adjusted


def save_loss_features(path, ticks_by_symbol, losses):
    """Append one 30-tick pre-loss feature record for every simulated loss."""
    with open(path, "a", encoding="utf-8") as handle:
        for loss in losses:
            symbol = str(loss["symbol"])
            features = loss_features(
                ticks_by_symbol.get(symbol, []),
                int(loss["epoch"]),
                symbol,
            )
            if features is not None:
                features["distance_to_barrier"] = loss.get("barrier_distance")
                handle.write(json.dumps(features, sort_keys=True) + "\n")


def loss_features(ticks, loss_epoch, symbol):
    before = [row for row in ticks if int(row["epoch"]) <= int(loss_epoch)]
    window = before[-30:]
    if len(window) < 30:
        return None
    quotes = np.array([float(row["quote"]) for row in window], dtype=float)
    returns = np.diff(quotes) / quotes[:-1]
    abs_returns = np.abs(returns)
    recent = abs_returns[-5:]
    baseline = abs_returns[:-5] if len(abs_returns) > 5 else abs_returns
    vol_ratio = float(recent.mean() / baseline.mean()) if baseline.size and baseline.mean() > 0 else None
    big_threshold = float(np.quantile(abs_returns, 0.95)) if abs_returns.size else 0.0
    big_indices = np.flatnonzero(abs_returns >= big_threshold)
    seconds_since_big = (
        int(loss_epoch) - int(window[int(big_indices[-1] + 1)]["epoch"])
        if big_indices.size else None
    )
    boom_crash = "Boom" in symbol or "Crash" in symbol or symbol.startswith(("BOOM", "CRASH"))
    return {
        "symbol": symbol,
        "loss_epoch": int(loss_epoch),
        "recent_return_mean": float(returns[-5:].mean()),
        "recent_return_std": float(returns[-5:].std(ddof=1)) if len(returns) >= 6 else 0.0,
        "return_distribution": [float(value) for value in returns],
        "vol_ratio": vol_ratio,
        "time_since_last_big_move_seconds": seconds_since_big,
        "distance_to_barrier": None,
        "boom_crash_time_since_spike_seconds": seconds_since_big if boom_crash else None,
    }


def split_and_analyse(trades):
    """Strict chronological 70/30 split. Analysis uses only test-period trades."""
    grouped = defaultdict(list)
    for trade in trades:
        grouped[str(trade["symbol"])].append(trade)
    rows = []
    for symbol, symbol_trades in sorted(grouped.items()):
        symbol_trades.sort(key=lambda row: int(row["epoch"]))
        split = int(len(symbol_trades)*0.7)
        train = symbol_trades[:split]
        test = symbol_trades[split:]
        if not test:
            continue
        values = np.array([float(row["pnl"]) for row in test], dtype=float)
        n = len(values)
        mean = float(values.mean())
        wins = int(np.sum(values > 0))
        win_rate = wins/n
        se = float(values.std(ddof=1)/math.sqrt(n)) if n > 1 else 0.0
        ci_low = mean - 1.6448536269514722*se
        if n > 1:
            t_result = stats.ttest_1samp(values, 0.0, alternative="greater")
            pvalue = float(t_result.pvalue)
        else:
            pvalue = 1.0
        rows.append({
            "symbol": symbol,
            "n_train": len(train),
            "n_test": n,
            "n_trades": n,
            "win_rate": win_rate,
            "ev_per_trade": mean,
            "ci95_low_one_sided": ci_low,
            "p_value": pvalue,
        })
    adjusted = benjamini_hochberg([row["p_value"] for row in rows])
    for row, q in zip(rows, adjusted):
        row["bh_adjusted_p"] = q
        row["edge"] = (
            "INSUFFICIENT_DATA"
            if row["n_test"] < ACCU_POWER_MINIMUM_TEST_TRADES
            else "EDGE"
            if row["ci95_low_one_sided"] > 0 and q < 0.05
            else "NO_EDGE"
        )
    return rows
