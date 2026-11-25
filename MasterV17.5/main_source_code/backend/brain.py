"""V17.2 Signal Brain: indicator + statistics engine with confidence scoring and outcome learning.

Every Deriv tick feeds a per-symbol ring buffer (last digits, micro momentum). Closed candles feed the
indicator vote engine (signal_engine.evaluate). Each candidate contract (Rise/Fall, Even/Odd,
Over/Under, Matches/Differs) gets a 0-100 confidence that is scaled by the learned win rate of
(symbol, contract type, market condition) so the auto trader prefers what actually wins.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque

from signal_engine import MIN_CANDLES, evaluate

logger = logging.getLogger(__name__)

TICK_WINDOW = 400
DIGIT_WINDOW = 120
RISE_FALL_TYPES = ("CALL", "PUT")
DIGIT_TYPES = ("DIGITEVEN", "DIGITODD", "DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITMATCH")
TYPE_GROUP = {**{t: "RISEFALL" for t in RISE_FALL_TYPES}, **{t: "DIGITS" for t in DIGIT_TYPES}}
DIGIT_SYMBOL_RE = re.compile(r"^(R_|1HZ|JD|RDB)")  # Deriv offers digit contracts on volatility / jump / daily-reset indices only
PHASE2_TYPES = ("HIGHER", "LOWER", "ONETOUCH", "NOTOUCH", "MULTUP", "MULTDOWN", "ACCU", "TURBOSLONG", "TURBOSSHORT", "VANILLALONGCALL", "VANILLALONGPUT")


def last_digit(price: float, pip_size: int) -> int:
    return int(round(abs(price) * 10 ** pip_size)) % 10


def _wilson_lower(wins: int, total: int, z: float = 1.64) -> float:
    if total <= 0:
        return 0.0
    p = wins / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - spread) / denom)


class SignalBrain:
    def __init__(self, db, store=None, deriv=None):
        self.db = db
        self.store = store
        self.deriv = deriv
        self.ticks: dict[str, deque] = {}
        self.pip_size: dict[str, int] = {}
        self._decimal_hist: dict[str, dict[int, int]] = {}
        self.stats: dict[str, dict] = {}  # key "symbol|type|bucket" -> {wins, losses}
        self.last_signal: dict[str, dict] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    # ---------- ingestion ----------
    def on_tick(self, symbol: str, price: float, epoch: float):
        buf = self.ticks.get(symbol)
        if buf is None:
            buf = self.ticks[symbol] = deque(maxlen=TICK_WINDOW)
        buf.append((float(price), float(epoch)))
        # pip size = most common number of decimals over the recent window (robust to float artefacts)
        text = repr(float(price))
        decimals = len(text.split(".")[1]) if "." in text and not text.endswith(".0") else 0
        hist = self._decimal_hist.setdefault(symbol, {})
        hist[decimals] = hist.get(decimals, 0) + 1
        if sum(hist.values()) > 500:
            for k in list(hist):
                hist[k] = hist[k] // 2
        self.pip_size[symbol] = max(hist, key=hist.get)

    async def load(self):
        if self._loaded:
            return
        async with self._lock:
            if self._loaded:
                return
            try:
                await self.db.brain_stats.create_index("key", unique=True)
                await self.db.brain_outcomes.create_index([("symbol", 1), ("contractType", 1), ("closedAt", -1)])
                rows = await self.db.brain_stats.find({}, {"_id": 0}).to_list(5000)
                self.stats = {row["key"]: {"wins": int(row.get("wins", 0)), "losses": int(row.get("losses", 0))} for row in rows}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Brain stats load failed: %s", type(exc).__name__)
            self._loaded = True

    # ---------- learning ----------
    def _key(self, symbol, contract_type, bucket):
        return f"{symbol}|{contract_type}|{bucket}"

    def learned(self, symbol, contract_type, bucket):
        exact = self.stats.get(self._key(symbol, contract_type, bucket), {"wins": 0, "losses": 0})
        broad = {"wins": 0, "losses": 0}
        prefix = f"{symbol}|{contract_type}|"
        for key, value in self.stats.items():
            if key.startswith(prefix):
                broad["wins"] += value["wins"]
                broad["losses"] += value["losses"]
        total = exact["wins"] + exact["losses"]
        pool = exact if total >= 8 else broad
        n = pool["wins"] + pool["losses"]
        rate = round(100.0 * pool["wins"] / n, 1) if n else None
        return {"wins": pool["wins"], "losses": pool["losses"], "samples": n, "winRate": rate, "lowerBound": round(100 * _wilson_lower(pool["wins"], n), 1) if n else None}

    async def record_outcome(self, symbol, contract_type, bucket, result, profit=None, mode="demo"):
        if result not in {"WIN", "LOSS"}:
            return
        key = self._key(symbol, contract_type, bucket)
        entry = self.stats.setdefault(key, {"wins": 0, "losses": 0})
        entry["wins" if result == "WIN" else "losses"] += 1
        try:
            await self.db.brain_stats.update_one({"key": key}, {"$set": {"key": key, "symbol": symbol, "contractType": contract_type, "bucket": bucket, **entry}}, upsert=True)
            await self.db.brain_outcomes.insert_one({"symbol": symbol, "contractType": contract_type, "bucket": bucket, "result": result, "profit": profit, "mode": mode, "closedAt": time.time()})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brain outcome persist failed: %s", type(exc).__name__)

    # ---------- analysis ----------
    def digit_stats(self, symbol):
        buf = list(self.ticks.get(symbol, ()))[-DIGIT_WINDOW:]
        pip = self.pip_size.get(symbol, 2)
        digits = [last_digit(price, pip) for price, _ in buf]
        counts = [digits.count(d) for d in range(10)]
        n = len(digits)
        freq = [round(c / n, 4) if n else 0.1 for c in counts]
        even = sum(freq[d] for d in range(0, 10, 2))
        return {"samples": n, "digits": digits[-20:], "counts": counts, "frequency": freq, "even": round(even, 4), "odd": round(1 - even, 4), "last": digits[-1] if digits else None, "pipSize": pip}

    def micro_momentum(self, symbol):
        buf = list(self.ticks.get(symbol, ()))
        if len(buf) < 12:
            return {"direction": None, "strength": 0.0, "ticks": len(buf)}
        prices = [p for p, _ in buf[-60:]]
        ups = sum(1 for a, b in zip(prices, prices[1:]) if b > a)
        downs = sum(1 for a, b in zip(prices, prices[1:]) if b < a)
        drift = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0.0
        returns = [(b - a) / a for a, b in zip(prices, prices[1:]) if a]
        vol = (sum(r * r for r in returns) / len(returns)) ** 0.5 if returns else 0.0
        strength = abs(ups - downs) / max(1, ups + downs)
        return {"direction": "CALL" if ups > downs else "PUT" if downs > ups else None, "strength": round(strength, 3), "drift": drift, "volatility": vol, "ticks": len(buf)}

    def _bucket(self, votes_conf, momentum, vol):
        conf_band = "hi" if votes_conf >= 70 else "mid" if votes_conf >= 55 else "lo"
        vol_band = "v-hi" if vol > 0.0008 else "v-mid" if vol > 0.0002 else "v-lo"
        return f"{conf_band}:{vol_band}:{'mom' if momentum and momentum.get('strength', 0) > 0.2 else 'flat'}"

    async def candles(self, symbol, timeframe="1m", limit=300):
        if self.store is None:
            return []
        try:
            rows = await self.store.candles("deriv", symbol, timeframe, limit=limit)
        except Exception:  # noqa: BLE001
            return []
        now = time.time()
        step = {"1m": 60, "5m": 300, "15m": 900}.get(timeframe, 60)
        return [c for c in rows if float(c.get("epoch", 0)) + step <= now]

    async def analyse(self, symbol, timeframe="1m"):
        await self.load()
        closed = await self.analyse_candles(symbol, timeframe)
        vote = evaluate(closed[-300:]) if len(closed) >= MIN_CANDLES else {"direction": "NO_SIGNAL", "confidence": 0, "reason": "INSUFFICIENT_CLOSED_CANDLES"}
        momentum = self.micro_momentum(symbol)
        digits = self.digit_stats(symbol)
        vol = momentum.get("volatility", 0.0) or 0.0
        vote_conf = float(vote.get("confidence") or 0)
        bucket = self._bucket(vote_conf, momentum, vol)
        candidates = []

        # --- Rise / Fall: indicator votes + micro momentum agreement (indicator confidence is overconfident → damped) ---
        direction = vote.get("direction")
        if direction in RISE_FALL_TYPES:
            agree = momentum.get("direction") == direction
            p_obs = 0.5 + (vote_conf / 100.0 - 0.5) * (0.55 if agree else 0.4) + (momentum.get("strength", 0) * 0.05 if agree else 0)
            candidates.append(self._candidate(symbol, direction, None, min(0.66, p_obs), 0.5, bucket, f"{len(vote.get('agreeing', []))} indicators agree · momentum {'confirms' if agree else 'neutral'}", 1, "m", 100))
        elif momentum.get("direction") and momentum.get("strength", 0) >= 0.35:
            candidates.append(self._candidate(symbol, momentum["direction"], None, 0.5 + momentum["strength"] * 0.12, 0.5, bucket, "tick momentum only (indicators undecided)", 1, "m", 100))

        # --- Digits: empirical frequency over the recent window (needs enough ticks) ---
        n = digits["samples"]
        if n >= 40 and DIGIT_SYMBOL_RE.match(symbol):
            freq = digits["frequency"]
            even = digits["even"]
            side, p = ("DIGITEVEN", even) if even >= 0.5 else ("DIGITODD", 1 - even)
            candidates.append(self._candidate(symbol, side, None, p, 0.5, bucket, f"{side[5:].lower()} {p:.0%} of last {n} ticks", 5, "t", n))
            for barrier in range(0, 9):
                p_over = sum(freq[d] for d in range(barrier + 1, 10))
                candidates.append(self._candidate(symbol, "DIGITOVER", barrier, p_over, (9 - barrier) / 10, bucket, f"digit > {barrier} seen {p_over:.0%}", 5, "t", n))
            for barrier in range(1, 10):
                p_under = sum(freq[d] for d in range(0, barrier))
                candidates.append(self._candidate(symbol, "DIGITUNDER", barrier, p_under, barrier / 10, bucket, f"digit < {barrier} seen {p_under:.0%}", 5, "t", n))
            for d in range(10):
                candidates.append(self._candidate(symbol, "DIGITDIFF", d, 1 - freq[d], 0.9, bucket, f"digit {d} appeared {freq[d]:.0%}", 5, "t", n))
                candidates.append(self._candidate(symbol, "DIGITMATCH", d, freq[d], 0.1, bucket, f"digit {d} appeared {freq[d]:.0%}", 5, "t", n))

        candidates.sort(key=lambda c: (-c["confidence"], -c["edge"]))
        result = {
            "symbol": symbol, "timeframe": timeframe, "generatedAt": time.time(), "bucket": bucket,
            "riseFall": {"direction": direction if direction in RISE_FALL_TYPES else "NO_SIGNAL", "confidence": round(vote_conf, 1), "reason": vote.get("reason"), "agreeing": vote.get("agreeing", []), "opposing": vote.get("opposing", []), "indicators": vote.get("indicators", {})},
            "momentum": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in momentum.items()},
            "digits": digits, "candidates": candidates, "best": candidates[0] if candidates else None,
            "closedCandles": len(closed),
        }
        self.last_signal[symbol] = result
        return result

    async def analyse_candles(self, symbol, timeframe):
        return await self.candles(symbol, timeframe)

    def _candidate(self, symbol, contract_type, barrier, p_obs, base_p, bucket, reason, duration, unit, samples=0):
        learned = self.learned(symbol, contract_type, bucket)
        # shrink the observed probability toward the fair base rate; small samples barely move it
        # selection bias (best-of-N digit/barrier picks) needs much heavier shrinkage than a plain estimate
        prior = {"DIGITMATCH": 2000.0, "DIGITDIFF": 400.0, "DIGITOVER": 300.0, "DIGITUNDER": 300.0, "DIGITEVEN": 200.0, "DIGITODD": 200.0}.get(contract_type, 100.0)
        shrink = samples / (samples + prior) if samples else 1.0
        p = base_p + (float(p_obs) - base_p) * shrink
        if learned["samples"]:
            weight = min(0.6, learned["samples"] / 40)
            learned_p = (learned["lowerBound"] if learned["samples"] >= 10 else learned["winRate"]) / 100.0
            p = (1 - weight) * p + weight * learned_p
        p = max(0.01, min(0.99, p))
        payout = 0.95 if contract_type in RISE_FALL_TYPES else 0.95 if contract_type in {"DIGITEVEN", "DIGITODD"} else max(0.02, 0.975 / base_p - 1)
        # edge is computed from the LOWER confidence bound of p, so high-payout/low-probability contracts
        # (Over 8, Matches…) cannot look profitable on a handful of lucky ticks
        n_eff = max(20, int(samples or 0))
        p_low = _wilson_lower(int(round(p * n_eff)), n_eff, z=1.28)
        edge = p_low * (1 + payout) - 1  # conservative expected return per 1 unit staked
        score = max(0.0, min(99.0, 50 + edge * 100 * 3))
        return {
            "contractType": contract_type, "group": TYPE_GROUP[contract_type], "barrier": barrier,
            "probability": round(p * 100, 1), "payoutRate": round(payout, 3), "edge": round(edge * 100, 2),
            "confidence": round(score, 1), "learned": learned,
            "reason": reason, "duration": duration, "durationUnit": unit, "bucket": bucket,
            "label": contract_label(contract_type, barrier),
        }

    async def best(self, symbol, allowed_groups=("RISEFALL", "DIGITS"), allowed_types=None, timeframe="1m", barrier=None):
        analysis = await self.analyse(symbol, timeframe)
        for candidate in analysis["candidates"]:
            if candidate["group"] not in allowed_groups:
                continue
            if allowed_types and candidate["contractType"] not in allowed_types:
                continue
            if barrier is not None and candidate["barrier"] is not None and candidate["barrier"] != barrier:
                continue
            return candidate, analysis
        return None, analysis

    def auto_stake(self, balance, confidence, percent, max_stake, min_stake=0.35):
        if not balance or balance <= 0:
            return min_stake
        edge = max(0.0, (float(confidence) - 50.0) / 50.0)  # 0 at 50%, 1 at 100%
        stake = balance * (percent / 100.0) * (0.4 + 0.6 * edge)
        return round(max(min_stake, min(stake, max_stake, balance)), 2)

    async def overview(self, symbols, limit=12):
        rows = []
        for symbol in symbols[:40]:
            try:
                analysis = await self.analyse(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.debug("brain overview %s failed: %s", symbol, type(exc).__name__)
                continue
            if analysis["best"]:
                rows.append({"symbol": symbol, "best": analysis["best"], "riseFall": analysis["riseFall"], "momentum": analysis["momentum"], "digitSamples": analysis["digits"]["samples"]})
        rows.sort(key=lambda r: -r["best"]["confidence"])
        return rows[:limit]

    def learning_report(self):
        report = {}
        for key, value in self.stats.items():
            symbol, contract_type, bucket = key.split("|", 2)
            entry = report.setdefault(f"{symbol}|{contract_type}", {"symbol": symbol, "contractType": contract_type, "wins": 0, "losses": 0, "buckets": 0})
            entry["wins"] += value["wins"]
            entry["losses"] += value["losses"]
            entry["buckets"] += 1
        rows = list(report.values())
        for row in rows:
            n = row["wins"] + row["losses"]
            row["winRate"] = round(100 * row["wins"] / n, 1) if n else None
            row["label"] = contract_label(row["contractType"], None)
        rows.sort(key=lambda r: -(r["winRate"] or 0))
        return rows


def contract_label(contract_type, barrier):
    names = {
        "CALL": "Rise", "PUT": "Fall", "DIGITEVEN": "Even", "DIGITODD": "Odd", "DIGITOVER": "Over", "DIGITUNDER": "Under",
        "DIGITDIFF": "Differs", "DIGITMATCH": "Matches",
    }
    label = names.get(contract_type, contract_type)
    return f"{label} {barrier}" if barrier is not None and contract_type in {"DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITMATCH"} else label
