import json
import math
from pathlib import Path


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def growth_percent(value):
    value = _number(value)
    if value is None:
        return None
    return value * 100 if value <= 0.05 else value


def read_barrier_distance(proposal_or_contract):
    """
    Read the provider-supplied barrier.  Supports legacy and current fields.
    Missing barrier data is an explicit simulator error, never a guessed value.
    """
    for key in (
        "barrier", "barrier_distance", "barrier_distance_percentage",
        "barrier2", "barrier_spot_distance",
    ):
        value = _number(proposal_or_contract.get(key))
        if value is not None:
            return value
    contract = (
        proposal_or_contract.get("contract")
        or proposal_or_contract.get("contract_info")
        or proposal_or_contract.get("contract_details")
    )
    if isinstance(contract, dict):
        return read_barrier_distance(contract)
    raise KeyError("PROVIDER_BARRIER_DISTANCE_MISSING")


def read_payout(proposal_or_contract):
    for key in ("payout", "payout_amount", "potential_payout", "maximum_payout", "sell_price", "bid_price"):
        value = _number(proposal_or_contract.get(key))
        if value is not None:
            return value
    contract = (
        proposal_or_contract.get("contract")
        or proposal_or_contract.get("contract_info")
        or proposal_or_contract.get("contract_details")
    )
    if isinstance(contract, dict):
        return read_payout(contract)
    raise KeyError("PROVIDER_PAYOUT_MISSING")


def read_take_profit(proposal_or_contract):
    for key in ("take_profit", "take_profit_amount", "take_profit_level"):
        value = _number(proposal_or_contract.get(key))
        if value is not None:
            return value
    limit = proposal_or_contract.get("limit_order")
    if isinstance(limit, dict):
        value = _number(limit.get("take_profit"))
        if value is not None:
            return value
    return None


def read_barrier_reference_spot(proposal_or_contract):
    for key in ("current_spot", "exit_spot", "entry_spot"):
        value = _number(proposal_or_contract.get(key))
        if value is not None and value > 0:
            return value
    contract = proposal_or_contract.get("contract") or proposal_or_contract.get("contract_info")
    if isinstance(contract, dict):
        return read_barrier_reference_spot(contract)
    raise KeyError("PROVIDER_BARRIER_REFERENCE_SPOT_MISSING")


def price_outside_barrier(price, entry, barrier_distance):
    distance = abs(float(barrier_distance))
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("INVALID_PROVIDER_BARRIER_DISTANCE")
    return abs(float(price) - float(entry)) > distance


def replay_ticks(ticks, entry_epoch, entry_quote, growth_rate, barrier_distance,
                 take_profit=None, max_ticks=None, max_seconds=None,
                 barrier_reference_spot=None, stake=1.0):
    """Replay provider ticks in epoch order and return the exact contract outcome."""
    rate_percent = growth_percent(growth_rate)
    if rate_percent not in {1.0, 2.0, 3.0, 4.0, 5.0}:
        raise ValueError("UNSUPPORTED_ACCU_GROWTH_RATE")
    rate = rate_percent / 100
    stake = _number(stake)
    if stake is None or stake <= 0:
        raise ValueError("INVALID_STAKE")
    distance = abs(float(barrier_distance))
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("INVALID_PROVIDER_BARRIER_DISTANCE")
    relative_barrier = barrier_reference_spot is not None
    if relative_barrier:
        reference_spot = _number(barrier_reference_spot)
        if reference_spot is None or reference_spot <= 0:
            raise ValueError("INVALID_PROVIDER_BARRIER_REFERENCE_SPOT")
        barrier_ratio = distance / reference_spot
    selected = sorted(
        (row for row in ticks
         if int(row["epoch"]) >= int(entry_epoch)
         and int(row["epoch"]) - int(entry_epoch) <= (int(max_seconds) if max_seconds else 10**12)),
        key=lambda row: int(row["epoch"]),
    )
    if not selected:
        return {"outcome": "NO_TICKS", "exit_epoch": None, "exit_quote": None, "ticks": 0}

    tp = _number(take_profit)
    max_n = max(1, int(max_ticks)) if max_ticks is not None else None
    previous_quote = float(entry_quote)
    for count, row in enumerate(selected[:max_n] if max_n is not None else selected, start=1):
        quote = float(row["quote"])
        breached = (
            abs(quote - previous_quote) > barrier_ratio * previous_quote
            if relative_barrier
            else price_outside_barrier(quote, entry_quote, distance)
        )
        if breached:
            return {
                "outcome": "LOSS",
                "exit_epoch": int(row["epoch"]),
                "exit_quote": quote,
                "ticks": count,
                "reason": "PROVIDER_BARRIER_CROSS",
            }
        profit = stake * ((1 + rate) ** count - 1)
        if tp is not None and profit >= tp:
            return {
                "outcome": "TP",
                "exit_epoch": int(row["epoch"]),
                "exit_quote": quote,
                "ticks": count,
                "profit": profit,
                "reason": "TAKE_PROFIT",
            }
        previous_quote = quote
    final_tick = selected[min(len(selected), max_n) - 1] if max_n is not None else selected[-1]
    return {
        "outcome": "MAX_TICKS",
        "exit_epoch": int(final_tick["epoch"]),
        "exit_quote": float(final_tick["quote"]),
        "ticks": min(len(selected), max_n) if max_n is not None else len(selected),
        "profit": stake * ((1 + rate) ** (min(len(selected), max_n) if max_n is not None else len(selected)) - 1),
        "reason": "MAX_TICKS",
    }
