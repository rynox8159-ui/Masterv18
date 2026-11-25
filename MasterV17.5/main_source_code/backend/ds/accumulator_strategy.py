"""Transparent, unvalidated volatility heuristic for Deriv Accumulator setups."""
import math
from statistics import median


ACCUMULATOR_SHADOW_STRATEGY_VERSION = "accu-volatility-v2"
ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT = 4
ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT = 5
ACCUMULATOR_SHADOW_MAX_HOLD_TICKS = 5
ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES = 5
ACCUMULATOR_SHADOW_MIN_SAMPLES = 300
ACCUMULATOR_SHADOW_MIN_WIN_RATE = 58.0
ACCUMULATOR_SHADOW_MIN_PROFIT_FACTOR = 1.1


def _growth_percent(value):
    return value * 100 if value <= 0.05 else value


def growth_rate_percent(value):
    return _growth_percent(float(value))


def _wilson_lower_bound(wins, samples):
    if samples <= 0:
        return 0.0
    z = 1.96
    rate = wins / samples
    denominator = 1 + z * z / samples
    center = rate + z * z / (2 * samples)
    margin = z * math.sqrt(rate * (1 - rate) / samples + z * z / (4 * samples * samples))
    return max(0.0, (center - margin) / denominator * 100)


def accumulator_shadow_report(samples):
    """Assess settled Deriv Demo ACCU outcomes for one exact strategy policy."""
    completed = []
    for row in samples:
        try:
            profit = float(row["profit"])
            stake = float(row["stake"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(profit) or not math.isfinite(stake) or stake <= 0:
            continue
        completed.append((profit, stake))

    decided = [profit for profit, _stake in completed if profit != 0]
    wins = sum(profit > 0 for profit in decided)
    losses = len(decided) - wins
    gross_profit = sum(profit for profit, _stake in completed if profit > 0)
    gross_loss = -sum(profit for profit, _stake in completed if profit < 0)
    net_profit = sum(profit for profit, _stake in completed)
    average_win = gross_profit / wins if wins else 0.0
    average_loss = gross_loss / losses if losses else 0.0
    break_even_rate = (
        average_loss / (average_win + average_loss) * 100
        if average_win + average_loss
        else 100.0
    )
    profit_factor = gross_profit / gross_loss if gross_loss else None
    recent = completed[-30:]
    recent_net = sum(profit for profit, _stake in recent)
    total_stake = sum(stake for _profit, stake in completed)
    win_rate = wins / len(decided) * 100 if decided else None
    wilson_lower = _wilson_lower_bound(wins, len(decided))

    if len(decided) < ACCUMULATOR_SHADOW_MIN_SAMPLES:
        reason = "INSUFFICIENT_DEMO_SAMPLES"
    elif win_rate is None or win_rate < ACCUMULATOR_SHADOW_MIN_WIN_RATE:
        reason = "WIN_RATE_BELOW_MINIMUM"
    elif wilson_lower <= break_even_rate:
        reason = "WILSON_BOUND_NOT_ABOVE_BREAKEVEN"
    elif net_profit <= 0:
        reason = "NET_PROFIT_NOT_POSITIVE"
    elif gross_loss and profit_factor < ACCUMULATOR_SHADOW_MIN_PROFIT_FACTOR:
        reason = "PROFIT_FACTOR_BELOW_MINIMUM"
    elif len(recent) < 30 or recent_net <= 0:
        reason = "RECENT_30_NOT_PROFITABLE"
    else:
        reason = "VALIDATED"

    ready = reason == "VALIDATED"
    return {
        "status": "VALIDATED" if ready else "SHADOW_VALIDATION_PENDING",
        "readyForLive": ready,
        "samples": len(decided),
        "minSamples": ACCUMULATOR_SHADOW_MIN_SAMPLES,
        "wins": wins,
        "losses": losses,
        "winRate": round(win_rate, 2) if win_rate is not None else None,
        "wilsonLowerBound": round(wilson_lower, 2),
        "breakEvenWinRate": round(break_even_rate, 2),
        "profitFactor": round(profit_factor, 3) if profit_factor is not None else None,
        "netProfit": round(net_profit, 2),
        "returnOnStakePercent": round(net_profit / total_stake * 100, 2) if total_stake else None,
        "recent30NetProfit": round(recent_net, 2),
        "reason": reason,
    }


def evaluate_accumulator(
    symbol, candles, growth_rates, *, take_profit_percent=5, max_hold_ticks=5,
):
    """Return a stability setup only when recent provider candles show non-expanding range."""
    valid = []
    for row in candles:
        try:
            values = [float(row[key]) for key in ("open", "high", "low", "close")]
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) and value > 0 for value in values):
            continue
        open_price, high, low, close = values
        if low > min(open_price, close) or high < max(open_price, close):
            continue
        valid.append((row.get("epoch"), high, low, close))
    if len(valid) < 30:
        return None

    ranges = [(high - low) / close for _, high, low, close in valid[-30:]]
    baseline = median(ranges)
    recent = median(ranges[-5:])
    if baseline <= 0:
        return None
    ratio = recent / baseline
    if ratio > 1.05 or ranges[-1] > baseline * 2:
        return None

    available = []
    for value in growth_rates:
        try:
            rate = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(rate) and 4 <= _growth_percent(rate) <= 5:
            available.append((rate, _growth_percent(rate)))
    if not available:
        return None

    desired_percent = 5 if ratio <= 0.55 else 4
    raw_rate, growth_percent = min(available, key=lambda item: (abs(item[1] - desired_percent), item[1]))
    drift = abs(valid[-1][3] / valid[-6][3] - 1)
    if drift > max(baseline * 5, 0.0001):
        return None

    return {
        "symbol": symbol,
        "contractType": "ACCU",
        "growthRate": raw_rate,
        "growthRatePercent": growth_percent,
        "takeProfitPercent": float(take_profit_percent),
        "maxHoldTicks": int(max_hold_ticks),
        "stabilityScore": round(max(0, min(100, 100 * (1.05 - ratio) / 1.05)), 1),
        "relativeVolatility": round(ratio, 3),
        "candleEpoch": valid[-1][0],
        "reason": f"1m range contraction · recent/baseline volatility {ratio:.2f}x",
        "strategyTier": "UNVALIDATED_VOLATILITY_HEURISTIC",
    }
