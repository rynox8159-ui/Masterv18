"""Auto-trade engine (V17.3): event-stream signal -> identical Deriv Rise/Fall contract on demo, real or both accounts.

Guarantees (enforced in code, logged per trade):
  * trade == signal: same symbol, same direction, same expiry; the order is sent at the signal's entry epoch (direct buy, no proposal id)
  * an order that would leave later than ``maxSignalLatencyMs`` after the entry epoch is CANCELLED_LATE
  * one contract per signal per account (unique ``requestId`` index in MongoDB -> duplicate inserts are rejected)
  * no trade while the Deriv feed is disconnected or stale
  * stake: flat by default; martingale only when ``martingaleEnabled`` (base x multiplier^losses, capped by ``martingaleMaxSteps`` / ``maxStake``)
  * ``maxConsecutiveLosses`` (default 4) losses in a row -> ``streakCooldownMinutes`` (default 30) pause; daily loss limit -> stop (per account)
"""
import asyncio
import logging
import math
import os
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from .accuracy import evaluate
from .dashboard_execution import DerivExecutor, DerivRequestError, PublicFeed
from pairs_config import is_real_deriv_forex
from ds.accumulator_strategy import (
    ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
    ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
    ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
    ACCUMULATOR_SHADOW_STRATEGY_VERSION,
    ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
)

logger = logging.getLogger(__name__)
AUTO_TRADE_DIR = Path(__file__).resolve().parent

SYNTHETIC_SYMBOLS = ["1HZ100V", "1HZ75V", "1HZ50V", "1HZ25V", "1HZ10V", "R_100", "R_75", "R_50", "R_25", "R_10"]
try:
    from server_clock import now as market_now  # Deriv-corrected clock (PC clock drift safe)
except ImportError:  # standalone CLI use
    market_now = time.time

FEED_STALE_SECONDS = max(5, int(os.environ.get("DERIV_TICK_STALE_SECONDS", "30")))

DEFAULT_SETTINGS = {
    "baseStake": 1.0,
    "duration": 1,
    "durationUnit": "m",
    "symbols": SYNTHETIC_SYMBOLS,
    "minVotes": 5,
    "minScore": 70,
    "minAdx": 20,
    "minAtrPercentile": 20,
    "maxAtrPercentile": 95,
    "pairMinSamples": 5,
    "pairMinWinRate": 50,
    "dailyLossLimit": 20.0,
    "dailyLossLimitMode": "amount",
    "dailyLossLimitPercent": 5.0,
    "dailyProfitTarget": 0,
    "maxStake": 100.0,
    "maxDailyTrades": 50,
    "maxStakeBalancePercent": 2.0,
    "maxConsecutiveLosses": 4,
    "streakCooldownMinutes": 30,
    "maxSignalLatencyMs": 500,
    "maxSignalSlippageBps": 25.0,
    "multiplierStopLossPercent": 50.0,
    "multiplierTakeProfitPercent": 0.0,
    "riseFallStopLossPercent": 0.0,
    "riseFallTakeProfitPercent": 20.0,
    "riseFallTrailingEnabled": False,
    "riseFallTrailingPullbackPercent": 5.0,
    "accumulatorEnabled": True,
    "accumulatorShadowValidationEnabled": False,
    "riskSafetyEnabled": True,
    "accumulatorGrowthRateMode": "auto",
    "accumulatorGrowthRate": 5,
    "accumulatorTakeProfitPercent": 5,
    "accumulatorMaxHoldTicks": 5,
    "accumulatorMaxHoldMinutes": 5,
    "signalSource": "accumulator",
    "systemTimeframes": ["1m", "5m"],
    "minSignalConfidence": 0,
    "maxConcurrentTrades": 5,
    "martingaleEnabled": False,
    "martingaleMultiplier": 2.0,
    "martingaleMaxSteps": 3,
    "requirePromotedPair": False,
    # brain mode (optional scanner)
    "tradeTypeMode": "auto",
    "contractType": "CALL",
    "digitBarrier": 5,
    "stakeMode": "manual",
    "autoStakePercent": 1.0,
    "minBrainConfidence": 62,
    "enabledGroups": ["RISEFALL", "DIGITS"],
    "brainSymbols": [],
    "tradeIntervalSeconds": 6,
}
NUMERIC_LIMITS = {
    "baseStake": (0.35, 10000), "duration": (1, 60),
    "minVotes": (3, 7), "minScore": (0, 100), "minAdx": (0, 60), "minAtrPercentile": (0, 100),
    "maxAtrPercentile": (0, 100), "pairMinSamples": (1, 500), "pairMinWinRate": (0, 100),
    "dailyLossLimit": (0, 1_000_000), "dailyProfitTarget": (0, 1_000_000), "maxStake": (0.35, 1_000_000),
    "maxDailyTrades": (1, 500), "maxStakeBalancePercent": (0.5, 2),
    "minSignalConfidence": (0, 99), "maxConsecutiveLosses": (2, 10), "streakCooldownMinutes": (1, 720),
    "maxSignalLatencyMs": (1, 500), "maxSignalSlippageBps": (1, 1000),
    "multiplierStopLossPercent": (0, 1000), "multiplierTakeProfitPercent": (0, 1000),
    "riseFallStopLossPercent": (0, 100), "riseFallTakeProfitPercent": (0, 100),
    "riseFallTrailingPullbackPercent": (0.1, 100),
    "accumulatorGrowthRate": (4, 5), "accumulatorTakeProfitPercent": (1, 5),
    "accumulatorMaxHoldTicks": (1, 100), "accumulatorMaxHoldMinutes": (1, 60),
    "dailyLossLimitPercent": (0.5, 100),
    "martingaleMultiplier": (1.1, 5.0), "martingaleMaxSteps": (1, 10),
    "digitBarrier": (0, 9), "autoStakePercent": (0.1, 2), "minBrainConfidence": (50, 95), "tradeIntervalSeconds": (2, 120), "maxConcurrentTrades": (1, 10),
}
LEGACY_DEFAULT_BRAIN_SYMBOLS = (
    ["frxEURUSD", "frxGBPUSD", "frxUSDJPY", "frxAUDUSD", "frxUSDCAD", "frxUSDCHF", "frxNZDUSD", "frxEURGBP"],
    ["R_100", "R_75", "R_50", "R_25", "R_10", "1HZ100V", "1HZ75V", "1HZ50V", "1HZ25V", "1HZ10V"],
)
INTEGER_SETTINGS = {"duration", "minVotes", "pairMinSamples", "maxDailyTrades", "maxConsecutiveLosses", "streakCooldownMinutes", "maxSignalLatencyMs", "digitBarrier", "tradeIntervalSeconds", "maxConcurrentTrades", "martingaleMaxSteps", "accumulatorGrowthRate", "accumulatorMaxHoldTicks", "accumulatorMaxHoldMinutes"}
MONEY_SETTINGS = {"baseStake", "maxStake", "dailyLossLimit", "dailyProfitTarget", "dailyLossLimitPercent", "martingaleMultiplier"}
MODES = ("demo", "real")
BRAIN_CONTRACT_TYPES = {"CALL", "PUT", "DIGITEVEN", "DIGITODD", "DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITMATCH"}
BARRIER_TYPES = {"DIGITOVER", "DIGITUNDER", "DIGITDIFF", "DIGITMATCH"}
# Short-expiry Boom/Crash and crypto signals use bounded-stake multiplier positions.
UNTRADABLE_RISE_FALL = re.compile(r"^(BOOM|CRASH|cry)", re.IGNORECASE)
STREAM_MULTIPLIER = 100
SIGNAL_ENTRY_MAX_AGE_SECONDS = 5.0
ENTRY_PREFLIGHT_SECONDS = 0.25


def stream_contract(symbol, direction):
    if direction not in {"CALL", "PUT"}:
        raise ValueError(f"Unsupported event-stream direction: {direction}")
    if UNTRADABLE_RISE_FALL.match(str(symbol)):
        return ("MULTUP" if direction == "CALL" else "MULTDOWN"), STREAM_MULTIPLIER
    return direction, None


async def wait_multiplier_exit(executor, contract_id, exit_epoch, stake, stop_loss_percent, take_profit_percent):
    return await executor.wait_multiplier(
        contract_id, exit_epoch, stake, stop_loss_percent, take_profit_percent, now_fn=market_now,
    )


async def wait_rise_fall_exit(executor, contract_id, exit_epoch, stake, stop_loss_percent, take_profit_percent, trailing_enabled, trailing_pullback_percent):
    monitor = getattr(executor, "wait_rise_fall", None)
    if monitor is None:
        return await executor.wait_contract(contract_id, max(30.0, float(exit_epoch) - market_now() + 30.0))
    return await monitor(contract_id, exit_epoch, stake, stop_loss_percent, take_profit_percent, trailing_enabled, trailing_pullback_percent, now_fn=market_now)


def accumulator_rate_percent(value):
    value = float(value)
    return value * 100 if value <= 0.05 else value


def is_supported_accumulator_growth_rate(value):
    try:
        rate = float(value)
        percent = accumulator_rate_percent(rate)
    except (TypeError, ValueError):
        return False
    return math.isfinite(rate) and 4 <= percent <= 5


def accumulator_stake_with_safety(stake, base_stake, factor, state):
    factor = min(1.0, max(0.5, float(factor)))
    if int(state.get("consecutiveLosses") or 0) >= 2 or float(state.get("dailyPnl") or 0) < 0:
        factor = min(factor, 0.5)
    return round(max(0.35, min(float(stake), float(base_stake)) * factor), 2)


def load_credentials():
    values = {**dotenv_values(AUTO_TRADE_DIR / ".env"), **{k: v for k, v in os.environ.items() if k.startswith("DERIV_")}}
    pick = lambda *keys: next((str(values[k]).strip() for k in keys if (values.get(k) or "").strip()), "")
    return {
        "app_id": pick("DERIV_APP_ID"),
        "demo": pick("DERIV_DEMO_TOKEN", "DERIV_API_TOKEN_1"),
        "real": pick("DERIV_LIVE_TOKEN", "DERIV_API_TOKEN_2"),
    }


def clamp_settings(settings):
    settings = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}
    for key, (low, high) in NUMERIC_LIMITS.items():
        value = min(max(float(settings.get(key, DEFAULT_SETTINGS[key])), low), high)
        settings[key] = int(value) if key in INTEGER_SETTINGS or (value.is_integer() and key not in MONEY_SETTINGS) else value
    return {**DEFAULT_SETTINGS, **settings}


def empty_state(mode):
    return {"id": mode, "dailyPnl": 0.0, "totalPnl": 0.0, "day": None, "wins": 0, "losses": 0, "ties": 0, "lastPayoutRate": 0.85,
            "dailyTrades": 0, "cooldownUntil": 0.0, "consecutiveLosses": 0, "dayStartBalance": None, "streakPauses": 0, "cancelledLate": 0}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


class AutoTradeEngine:
    def __init__(self, db):
        self.db = db
        self.feed = PublicFeed()
        self.settings = dict(DEFAULT_SETTINGS)
        self.task = None
        self.mode = "demo"  # demo | real | both
        self.phase = "IDLE"
        self.stop_reason = None
        self.executors = {}  # account mode -> DerivExecutor (one per account; "both" = demo + real)
        self.last_scan = []
        self.last_scan_at = None
        self.current_trade = None
        self.logs = deque(maxlen=300)
        self.stop_requested = False
        self.signal_service = None
        self.accumulator_signal_service = None
        self.traded_signal_ids = deque(maxlen=500)
        self.stream_tasks = set()  # one task per live signal (concurrent pairs)
        self.accumulator_tasks = set()
        self.submitted_accumulator_signals = set()
        self.untradable_symbols = set()
        self.tracked_trade_ids = set()
        self.deriv_feed = None  # shared public DerivService (connection state + Deriv open/closed flag per pair)
        self.kill_requested = False
        self.brain = None
        self.brain_scan_offset = 0
        self._credential_overrides = {}

    def credentials(self):
        return {**load_credentials(), **self._credential_overrides}

    def update_credentials(self, changes):
        allowed = {"app_id", "demo", "real"}
        if not changes or set(changes) - allowed:
            raise ValueError("Provide appId, demoToken, or realToken")
        normalized = {}
        for key, raw in changes.items():
            if raw is None:
                continue
            value = str(raw).strip()
            if not value:
                continue
            if len(value) > (20 if key == "app_id" else 256):
                raise ValueError(f"{key} is too long")
            if key == "app_id" and not value.isdigit():
                raise ValueError("appId must contain digits only")
            normalized[key] = value
        if not normalized:
            raise ValueError("Enter at least one non-empty credential")
        if self.running and (
            "app_id" in normalized or set(self.modes).intersection(normalized)
        ):
            raise RuntimeError("CREDENTIALS_CANNOT_CHANGE_WHILE_RUNNING")
        self._credential_overrides.update(normalized)
        credentials = self.credentials()
        return {
            "appId": bool(credentials["app_id"]),
            "demoToken": bool(credentials["demo"]),
            "realToken": bool(credentials["real"]),
            "distinctTokens": credentials["demo"] != credentials["real"],
        }

    async def is_deriv_instrument_eligible(self, symbol):
        instrument = await self.db.market_instruments.find_one(
            {"source": "deriv", "symbol": symbol},
            {
                "_id": 0, "source": 1, "symbol": 1, "market": 1,
                "submarket": 1, "label": 1, "marketDataEligible": 1,
                "signalEligible": 1, "isOTC": 1, "is_otc": 1,
            },
        )
        return bool(
            instrument
            and is_real_deriv_forex(instrument)
            and instrument.get("marketDataEligible") is True
            and instrument.get("signalEligible") is True
        )

    # ---------- risk helpers ----------
    @property
    def modes(self):
        return list(MODES) if self.mode == "both" else [self.mode]

    @property
    def executor(self):
        return self.executors.get(self.modes[0]) or next(iter(self.executors.values()), None)

    @executor.setter
    def executor(self, value):
        if value is None:
            self.executors = {}
        else:
            self.executors = {self.modes[0]: value}

    def stake_for(self, state):
        """Flat stake, or (opt-in) martingale: base × multiplier^losses-in-a-row, capped at martingaleMaxSteps and maxStake."""
        s = self.settings
        base = float(s["baseStake"])
        if not s.get("martingaleEnabled"):
            return base, 0
        step = min(int(state.get("consecutiveLosses") or 0), int(s["martingaleMaxSteps"]))
        return round(min(base * float(s["martingaleMultiplier"]) ** step, float(s["maxStake"])), 2), step

    def effective_daily_loss_limit(self, state, mode=None):
        s = self.settings
        if s.get("dailyLossLimitMode") == "percent":
            ex = self.executors.get(mode or state.get("id")) or self.executor
            base = state.get("dayStartBalance") or (ex.balance if ex else None)
            return round(float(base) * float(s["dailyLossLimitPercent"]) / 100.0, 2) if base else 0.0
        return float(s["dailyLossLimit"] or 0)

    def feed_connected(self):
        feed = self.deriv_feed
        if feed is None:
            return False
        age = None if feed.last_tick is None else time.time() - feed.last_tick
        return feed.state in {"DATA_RECEIVING", "CONNECTED"} and age is not None and age <= FEED_STALE_SECONDS

    def feed_status(self):
        feed = self.deriv_feed
        if feed is None:
            return {"connected": False, "state": "NO_FEED", "ageSeconds": None, "staleAfterSeconds": FEED_STALE_SECONDS}
        age = None if feed.last_tick is None else round(time.time() - feed.last_tick, 1)
        return {"connected": self.feed_connected(), "state": feed.state, "ageSeconds": age, "staleAfterSeconds": FEED_STALE_SECONDS}

    def pair_open(self, symbol):
        feed = self.deriv_feed
        return True if feed is None else feed.market_is_open(symbol) is not False

    def pair_promoted(self, symbol, timeframe):
        if not self.settings.get("requirePromotedPair", False):
            return True
        try:
            from promotion_gate import is_promoted
        except ImportError:
            return True
        return is_promoted(symbol, timeframe)

    async def kill(self):
        """Kill switch: stop now, cancel pending orders, sell any open contract at market."""
        self.kill_requested = True
        self.stop_requested = True
        self.stop_reason = "KILL SWITCH · trading halted, pending orders cancelled"
        open_trades = {t["id"]: t for t in await self.db.autotrade_trades.find({"mode": {"$in": self.modes}, "status": "OPEN", "contractId": {"$ne": None}}, {"_id": 0}).to_list(50)}
        if self.current_trade and self.current_trade.get("contractId"):
            open_trades.setdefault(self.current_trade["id"], self.current_trade)
        executors = dict(self.executors)
        if self.task is not None and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        cancelled = await self.db.autotrade_trades.update_many({"mode": {"$in": self.modes}, "status": "PENDING_ORDER"}, {"$set": {"status": "CANCELLED", "result": "CANCELLED", "closedAt": now_iso()}})
        sold = 0
        for trade in open_trades.values():
            executor = executors.get(trade.get("mode"))
            if executor is None:
                continue
            try:
                await executor.ensure()
                result = await executor.sell(trade["contractId"])
                profit = round(float(result.get("sold_for") or 0) - float(trade["stake"]), 2)
                await self.db.autotrade_trades.update_one({"id": trade["id"]}, {"$set": {"status": "CLOSED", "result": "WIN" if profit > 0 else "LOSS" if profit < 0 else "TIE", "profit": profit, "closedAt": now_iso(), "killSwitch": True}})
                await self.db.autotrade_state.update_one({"id": trade["mode"]}, {"$inc": {"dailyPnl": profit, "totalPnl": profit}}, upsert=True)
                sold += 1
            except Exception as exc:  # noqa: BLE001 - contract may already be settled
                self.log("WARN", f"Kill switch could not sell contract {trade['contractId']}: {exc}")
        for executor in executors.values():
            await executor.close()
        self.current_trade = None
        self.phase = "STOPPED"
        self.task = None
        self.log("WARN", f"KILL SWITCH · {cancelled.modified_count} pending order(s) cancelled · {sold} open contract(s) sold")
        return {"cancelledPending": cancelled.modified_count, "soldOpen": sold}

    async def _reconcile_open_trades(self):
        """After a reconnect/restart: sync OPEN trades from Deriv and settle them before trading again."""
        rows = await self.db.autotrade_trades.find({"mode": {"$in": self.modes}, "status": "OPEN"}, {"_id": 0}).to_list(50)
        rows = [row for row in rows if row["id"] not in self.tracked_trade_ids and row.get("mode") in self.executors]
        if not rows:
            return
        self.phase = "RECONCILING"
        for trade in rows:
            executor = self.executors[trade["mode"]]
            try:
                self.current_trade = trade
                if trade.get("executionType") == "ACCUMULATOR":
                    result = await executor.wait_accumulator(
                        trade["contractId"],
                        max(1.0, float(trade.get("closeAtEpoch") or market_now()) - market_now()),
                        max_hold_ticks=int(trade.get("accumulatorMaxHoldTicks") or ACCUMULATOR_SHADOW_MAX_HOLD_TICKS),
                        now_fn=market_now,
                    )
                elif trade.get("executionType") == "MULTIPLIER":
                    result = await wait_multiplier_exit(
                        executor, trade["contractId"], trade.get("closeAtEpoch") or market_now(), float(trade.get("stake") or 0),
                        float(trade.get("multiplierStopLossPercent", self.settings["multiplierStopLossPercent"])),
                        float(trade.get("multiplierTakeProfitPercent", self.settings["multiplierTakeProfitPercent"])),
                    )
                else:
                    result = await wait_rise_fall_exit(
                        executor, trade["contractId"], trade.get("closeAtEpoch") or market_now() + 600,
                        float(trade.get("stake") or 0),
                        float(trade.get("riseFallStopLossPercent", self.settings["riseFallStopLossPercent"])),
                        float(trade.get("riseFallTakeProfitPercent", self.settings["riseFallTakeProfitPercent"])),
                        bool(trade.get("riseFallTrailingEnabled", self.settings["riseFallTrailingEnabled"])),
                        float(trade.get("riseFallTrailingPullbackPercent", self.settings["riseFallTrailingPullbackPercent"])),
                    )
                await self._settle(trade, result, float(trade.get("payoutRate") or 0.85))
                self.log("INFO", f"Reconciled open contract {trade['contractId']} after reconnect")
            except DerivRequestError as exc:
                self.log("WARN", f"Could not reconcile contract {trade['contractId']}: {exc}")
                await self.db.autotrade_trades.update_one({"id": trade["id"]}, {"$set": {"status": "UNKNOWN", "closedAt": now_iso(), "reconcileError": str(exc)[:200]}})
        self.current_trade = None

    # ---------- persistence ----------
    async def load(self):
        # idempotency boundary: one Deriv order per requestId (= mode + signal id)
        await self.db.autotrade_trades.create_index([("requestId", ASCENDING)], unique=True, partialFilterExpression={"requestId": {"$exists": True}}, name="uniq_request_id")
        await self.db.autotrade_trades.create_index([("mode", ASCENDING), ("openedAt", ASCENDING)], name="mode_opened")
        saved = await self.db.autotrade_settings.find_one({"id": "settings"}, {"_id": 0, "id": 0})
        if saved:
            if saved.get("brainSymbols") in LEGACY_DEFAULT_BRAIN_SYMBOLS:
                saved["brainSymbols"] = []
            self.settings = clamp_settings(saved)

    async def update_settings(self, patch):
        if not isinstance(patch, dict):
            raise ValueError("Settings must be an object")
        merged = {**self.settings}
        for key, value in patch.items():
            if key not in DEFAULT_SETTINGS:
                raise ValueError(f"Unknown setting: {key}")
            if key in NUMERIC_LIMITS:
                if isinstance(value, bool):
                    raise ValueError(f"{key} must be numeric")
                low, high = NUMERIC_LIMITS[key]
                value = float(value)
                if not math.isfinite(value) or not low <= value <= high:
                    raise ValueError(f"{key} must be between {low} and {high}")
                if key in INTEGER_SETTINGS:
                    if not value.is_integer():
                        raise ValueError(f"{key} must be a whole number")
                    value = int(value)
                elif value.is_integer() and key not in MONEY_SETTINGS:
                    value = int(value)
            elif key in {"symbols", "brainSymbols"}:
                if not isinstance(value, list):
                    raise ValueError(f"{key} must be a list")
                value = list(dict.fromkeys(str(symbol).strip() for symbol in value if str(symbol).strip()))[:40]
                if key == "symbols" and not value:
                    raise ValueError("Select at least one symbol")
            elif key == "signalSource":
                if value not in {"accumulator", "binary"}:
                    raise ValueError("signalSource must be 'accumulator' or 'binary'")
            elif key in {"tradeTypeMode", "stakeMode"}:
                if value not in {"auto", "manual"}:
                    raise ValueError(f"{key} must be 'auto' or 'manual'")
            elif key == "accumulatorGrowthRateMode":
                if value not in {"auto", "manual"}:
                    raise ValueError("accumulatorGrowthRateMode must be 'auto' or 'manual'")
            elif key == "contractType":
                if value not in BRAIN_CONTRACT_TYPES:
                    raise ValueError(f"contractType must be one of {sorted(BRAIN_CONTRACT_TYPES)}")
            elif key == "enabledGroups":
                if not isinstance(value, list) or not set(value) <= {"RISEFALL", "DIGITS"} or not value:
                    raise ValueError("enabledGroups must be a non-empty subset of ['RISEFALL', 'DIGITS']")
            elif key == "systemTimeframes":
                if not isinstance(value, list) or not set(value) <= {"1m", "5m"} or not value:
                    raise ValueError("systemTimeframes must be a non-empty subset of ['1m', '5m']")
            elif key == "durationUnit":
                if value not in {"t", "m"}:
                    raise ValueError("durationUnit must be 't' (ticks) or 'm' (minutes)")
            elif key == "dailyLossLimitMode":
                if value not in {"amount", "percent"}:
                    raise ValueError("dailyLossLimitMode must be 'amount' or 'percent'")
            elif not isinstance(value, bool):
                raise ValueError(f"{key} must be a boolean")
            merged[key] = value
        if merged["baseStake"] > merged["maxStake"]:
            raise ValueError("Base stake cannot exceed max stake")
        if merged["minAtrPercentile"] > merged["maxAtrPercentile"]:
            raise ValueError("Minimum ATR percentile cannot exceed maximum")
        if (
            self.running
            and merged["signalSource"] == "accumulator"
            and merged["accumulatorShadowValidationEnabled"]
            and "real" in self.modes
        ):
            raise ValueError("ACCUMULATOR_SHADOW_VALIDATION_DEMO_ONLY")
        self.settings = merged
        await self.db.autotrade_settings.replace_one({"id": "settings"}, {"id": "settings", **merged}, upsert=True)
        self.log("INFO", "Settings updated")
        return self.settings

    async def get_state(self, mode):
        doc = await self.db.autotrade_state.find_one({"id": mode}, {"_id": 0})
        return {**empty_state(mode), **(doc or {})}

    async def save_state(self, state):
        await self.db.autotrade_state.update_one({"id": state["id"]}, {"$set": state}, upsert=True)

    async def reset_cycle(self, mode):
        """Clear the loss-streak pause / cooldown (manual operator decision)."""
        if self.running:
            raise RuntimeError("Stop auto trade before resetting its loss-streak pause")
        modes = list(MODES) if mode == "both" else [mode]
        for m in modes:
            await self.db.autotrade_state.update_one({"id": m}, {"$set": {"consecutiveLosses": 0, "cooldownUntil": 0.0}}, upsert=True)
        self.log("WARN", f"{mode} loss-streak pause reset manually")
        return await self.get_state(modes[0])

    async def pair_stats(self, mode, symbol, limit=20):
        rows = await self.db.autotrade_trades.find({"mode": mode, "symbol": symbol, "result": {"$in": ["WIN", "LOSS"]}}, {"_id": 0, "result": 1}).sort("openedAt", -1).limit(limit).to_list(limit)
        return {"wins": sum(r["result"] == "WIN" for r in rows), "losses": sum(r["result"] == "LOSS" for r in rows)}

    async def accumulator_pair_stats(self, mode, symbol, limit=10):
        rows = await self.db.autotrade_trades.find(
            {
                "mode": mode,
                "symbol": symbol,
                "executionType": "ACCUMULATOR",
                "result": {"$in": ["WIN", "LOSS"]},
            },
            {"_id": 0, "result": 1},
        ).sort("closedAt", -1).limit(limit).to_list(limit)
        results = [row["result"] for row in rows]
        wins = results.count("WIN")
        losses = results.count("LOSS")
        loss_streak = next((index for index, result in enumerate(results) if result != "LOSS"), len(results))
        return {
            "wins": wins,
            "losses": losses,
            "samples": len(results),
            "winRate": wins / len(results) if results else None,
            "recentResults": results,
            "consecutiveLosses": loss_streak,
        }

    def log(self, level, message):
        self.logs.appendleft({"time": now_iso(), "level": level, "message": message})
        getattr(logger, "warning" if level == "WARN" else level.lower(), logger.info)("[autotrade] %s", message)

    # ---------- control ----------
    @property
    def running(self):
        return self.task is not None and not self.task.done()

    async def start(self, mode):
        if mode not in {"demo", "real", "both"}:
            raise ValueError("mode must be demo, real or both")
        if self.running:
            raise RuntimeError("AUTO_TRADE_ALREADY_RUNNING")
        modes = list(MODES) if mode == "both" else [mode]
        if (
            self.settings.get("signalSource") == "accumulator"
            and self.settings.get("accumulatorShadowValidationEnabled")
            and "real" in modes
        ):
            raise RuntimeError("ACCUMULATOR_SHADOW_VALIDATION_DEMO_ONLY")
        if "real" in modes and os.environ.get("AUTOTRADE_WEB_LIVE_ENABLED", "").strip().lower() != "true":
            raise RuntimeError("WEB_REAL_TRADING_DISABLED")
        creds = self.credentials()
        if self.settings["signalSource"] == "binary" and self.signal_service is None:
            raise RuntimeError("BINARY_SIGNAL_SERVICE_UNAVAILABLE")
        if self.settings["signalSource"] == "accumulator" and self.accumulator_signal_service is None:
            raise RuntimeError("ACCUMULATOR_SIGNAL_SERVICE_UNAVAILABLE")
        for m in modes:
            if not creds["app_id"] or not creds[m]:
                raise RuntimeError(f"DERIV_APP_ID / {'DERIV_API_TOKEN_1' if m == 'demo' else 'DERIV_API_TOKEN_2'} missing in .env")
        if mode == "both" and creds["demo"] == creds["real"]:
            raise RuntimeError("DEMO_AND_REAL_TOKEN_IDENTICAL: set DERIV_DEMO_TOKEN and DERIV_LIVE_TOKEN (or TOKEN_1 demo / TOKEN_2 real) in .env")
        self.mode = mode
        self.stop_requested = False
        self.kill_requested = False
        self.stop_reason = None
        self.executors = {m: DerivExecutor(creds["app_id"], creds[m], m) for m in modes}
        self.phase = "STARTING"
        self.task = asyncio.create_task(self._run(), name=f"autotrade-{mode}")
        stake_rule = f"martingale ×{self.settings['martingaleMultiplier']:g} up to {self.settings['martingaleMaxSteps']} steps" if self.settings.get("martingaleEnabled") else "flat stake"
        self.log("INFO", f"Auto trade started in {mode.upper()} mode · {' + '.join(modes)} account(s) · base stake {self.settings['baseStake']:.2f} · {stake_rule} · min confidence {self.settings['minSignalConfidence']}% · max latency {self.settings['maxSignalLatencyMs']} ms")

    async def _close_executors(self):
        for executor in list(self.executors.values()):
            await executor.close()

    async def stop(self, reason="Stopped by user"):
        self.stop_reason = reason
        self.stop_requested = True
        if not self.running:
            await self._close_executors()
            self.phase = "IDLE"
            self.current_trade = None
            self.log("INFO", reason)
        else:
            self.phase = "STOPPING"
            self.log("INFO", f"{reason}; waiting for any open contract to settle")

    async def shutdown(self):
        await self.stop("Backend shutdown")
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 15)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()
        await self.feed.close()

    def _halt(self, reason):
        self.stop_reason = reason
        self.phase = "STOPPED"
        self.log("WARN", reason)
        raise asyncio.CancelledError

    # ---------- main loop ----------
    async def _run(self):
        try:
            while not self.stop_requested:
                try:
                    for executor in self.executors.values():
                        await executor.ensure()
                    await self._reconcile_open_trades()
                    await self._cycle()
                except (DerivRequestError, OSError, asyncio.TimeoutError, ValueError) as exc:
                    self.phase = "ERROR"
                    self.log("ERROR", f"Cycle failed: {exc}")
                    await self._close_executors()
                    await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        finally:
            tasks = list(self.stream_tasks | self.accumulator_tasks)
            if tasks:
                self.phase = "STOPPING"
                self.log("INFO", f"Waiting for {len(tasks)} open contract(s) to settle")
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._close_executors()
            if self.phase not in {"STOPPED"} or self.stop_reason in {"Stopped by user", None}:
                self.phase = "IDLE"
            self.task = None
            self.current_trade = None

    async def _risk_gate(self):
        """Hard limits evaluated per account before every scan. Returns the modes allowed to trade now (or None while all cool down)."""
        today = datetime.now(timezone.utc).date().isoformat()
        s = self.settings
        active = []
        for mode in self.modes:
            state = await self.get_state(mode)
            ex = self.executors.get(mode)
            if state["day"] != today or not state.get("dayStartBalance"):
                state.update(day=today, dailyPnl=0.0, dailyTrades=0, consecutiveLosses=0, cooldownUntil=0.0, dayStartBalance=ex.balance if ex else None)
                await self.save_state(state)
            if state["dailyTrades"] >= s["maxDailyTrades"]:
                self._halt(f"{mode.upper()}: daily trade limit reached ({state['dailyTrades']}/{s['maxDailyTrades']}); trading stopped for today")
            daily_limit = self.effective_daily_loss_limit(state, mode)
            if daily_limit and state["dailyPnl"] <= -daily_limit:
                self._halt(f"{mode.upper()}: daily loss limit {daily_limit:.2f} reached ({state['dailyPnl']:.2f}); trading stopped for today")
            if s["dailyProfitTarget"] and state["dailyPnl"] >= s["dailyProfitTarget"]:
                self._halt(f"{mode.upper()}: daily profit target reached (+{state['dailyPnl']:.2f}); trading stopped")
            if state["cooldownUntil"] > time.time():
                continue
            if state["cooldownUntil"] and state["consecutiveLosses"] >= s["maxConsecutiveLosses"]:
                await self.db.autotrade_state.update_one({"id": mode}, {"$set": {"consecutiveLosses": 0, "cooldownUntil": 0.0}})
                self.log("INFO", f"{mode.upper()}: {s['streakCooldownMinutes']} min cooldown over · loss streak reset · trading resumes")
            active.append(mode)
        if not active:
            self.phase = "COOLDOWN"
            await asyncio.sleep(5)
            return None
        return active

    async def _cycle(self):
        active_modes = await self._risk_gate()
        if active_modes is None or self.stop_requested:
            return
        if self.settings["signalSource"] == "binary":
            await self._stream_cycle(active_modes)
        else:
            await self._accumulator_cycle(active_modes)
            if not self.stop_requested:
                await asyncio.sleep(max(1, int(self.settings.get("tradeIntervalSeconds", 6))))

    async def _mark_accumulator_signal(self, signal_id, result, reason, mode, **extra):
        state = {"mode": mode, "result": result, "reason": reason, "at": now_iso(), **extra}
        await self.db.accumulator_signals.update_one(
            {"id": signal_id},
            {"$set": {f"autoTradeByMode.{mode}": state, "autoTrade": state}},
        )

    async def _mark_accumulator_shadow(self, signal_id, result, reason, **extra):
        state = {"mode": "demo", "result": result, "reason": reason, "at": now_iso(), **extra}
        await self.db.accumulator_signals.update_one(
            {"id": signal_id},
            {"$set": {"shadowValidation.demo": state}},
        )

    async def _accumulator_cycle(self, active_modes):
        if not self.settings.get("accumulatorEnabled") or self.accumulator_signal_service is None:
            return
        if not self.feed_connected():
            return
        board = await self.accumulator_signal_service.live(200)
        supported = self.accumulator_signal_service.universe
        max_tasks = int(self.settings["maxConcurrentTrades"]) * len(active_modes)
        for signal in board.get("items", []):
            signal_id = signal.get("id")
            symbol = signal.get("symbol")
            validation = signal.get("validationReport")
            if not isinstance(validation, dict):
                continue
            if (
                signal.get("validationTier") != "LIVE_VALIDATED"
                or signal.get("strategyTier") != "LIVE_VALIDATED"
                or validation.get("status") != "VALIDATED"
                or validation.get("readyForLive") is not True
                or validation.get("source") != signal.get("source")
                or validation.get("symbol") != symbol
                or validation.get("timeframe") != signal.get("timeframe")
                or validation.get("validationMethod") != "DEMO_SHADOW"
                or validation.get("strategyVersion") != ACCUMULATOR_SHADOW_STRATEGY_VERSION
                or float(validation.get("growthRatePercent") or 0) != ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT
            ):
                continue
            if (
                not signal_id or not symbol or signal.get("status") != "PENDING"
                or symbol not in supported
            ):
                continue
            for mode in active_modes:
                key = (mode, signal_id)
                if key in self.submitted_accumulator_signals or (signal.get("autoTradeByMode") or {}).get(mode):
                    continue
                if len(self.stream_tasks) + len(self.accumulator_tasks) >= max_tasks:
                    return
                if self.pair_open(symbol) is False:
                    await self._mark_accumulator_signal(signal_id, "SKIPPED", "market closed on Deriv", mode)
                    continue
                score = float(signal.get("stabilityScore") or 0)
                if score < float(self.settings["minSignalConfidence"]):
                    await self._mark_accumulator_signal(signal_id, "SKIPPED", "stability score below configured minimum", mode)
                    continue
                growth_rate = signal.get("growthRate")
                if self.settings["accumulatorGrowthRateMode"] == "manual":
                    requested = float(self.settings["accumulatorGrowthRate"])
                    rates = signal.get("availableGrowthRates") or [growth_rate]
                    selected = next(
                        (rate for rate in rates if abs(accumulator_rate_percent(rate) - requested) < 0.001),
                        None,
                    )
                    if selected is None:
                        await self._mark_accumulator_signal(signal_id, "SKIPPED", f"Deriv does not offer {requested:g}% growth for this symbol", mode)
                        continue
                    growth_rate = selected
                if growth_rate is None:
                    await self._mark_accumulator_signal(signal_id, "SKIPPED", "Deriv growth-rate choices unavailable", mode)
                    continue
                if not is_supported_accumulator_growth_rate(growth_rate):
                    await self._mark_accumulator_signal(
                        signal_id, "SKIPPED", "growth rate is outside the configured 4-5% band", mode,
                    )
                    continue
                if (
                    float(validation.get("takeProfitPercent") or 0) != float(self.settings["accumulatorTakeProfitPercent"])
                    or int(validation.get("maxHoldTicks") or 0) != int(self.settings["accumulatorMaxHoldTicks"])
                    or int(validation.get("maxHoldMinutes") or 0) != int(self.settings["accumulatorMaxHoldMinutes"])
                ):
                    await self._mark_accumulator_signal(
                        signal_id, "SKIPPED", "ACCU risk settings differ from the demo-validated policy", mode,
                    )
                    continue
                validated_rate = next(
                    (
                        rate for rate in signal.get("availableGrowthRates") or [growth_rate]
                        if abs(accumulator_rate_percent(rate) - ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT) < 0.001
                    ),
                    None,
                )
                if validated_rate is None:
                    await self._mark_accumulator_signal(
                        signal_id, "SKIPPED", "Deriv no longer offers the demo-validated 4% growth rate", mode,
                    )
                    continue
                if (
                    self.settings["accumulatorGrowthRateMode"] == "manual"
                    and abs(float(self.settings["accumulatorGrowthRate"]) - ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT) >= 0.001
                ):
                    await self._mark_accumulator_signal(
                        signal_id, "SKIPPED", "Manual growth rate differs from the demo-validated 4% policy", mode,
                    )
                    continue
                safety_stake_factor = 1.0
                if self.settings.get("riskSafetyEnabled"):
                    stats = await self.accumulator_pair_stats(mode, symbol, limit=10)
                    win_rate = stats["winRate"]
                    if stats["consecutiveLosses"] >= 3 or (
                        stats["samples"] >= 5 and win_rate is not None and win_rate < 0.4
                    ):
                        await self._mark_accumulator_signal(
                            signal_id, "SKIPPED", "Risk Safety paused this pair based on recent losses", mode,
                        )
                        continue
                    strong_history = stats["samples"] >= 5 and win_rate is not None and win_rate >= 0.6
                    growth_rate = validated_rate
                    safety_stake_factor = 1.0 if strong_history and stats["consecutiveLosses"] == 0 else 0.5
                else:
                    growth_rate = validated_rate
                self.submitted_accumulator_signals.add(key)
                await self._mark_accumulator_signal(signal_id, "PENDING_ORDER", "order queued", mode)
                verdict = {
                    "symbol": symbol,
                    "direction": "ACCU",
                    "contractType": "ACCU",
                    "growthRate": float(growth_rate),
                    "growthRatePercent": accumulator_rate_percent(growth_rate),
                    "riskSafetyStakeFactor": safety_stake_factor,
                    "availableGrowthRates": signal.get("availableGrowthRates") or [],
                    "takeProfitPercent": float(self.settings["accumulatorTakeProfitPercent"]),
                    "maxHoldTicks": int(self.settings["accumulatorMaxHoldTicks"]),
                    "maxHoldMinutes": int(self.settings["accumulatorMaxHoldMinutes"]),
                    "score": score,
                    "duration": int(self.settings["accumulatorMaxHoldMinutes"]),
                    "durationUnit": "m",
                    "bucket": "accumulator:1m",
                    "reason": signal.get("reason", "volatility heuristic"),
                    "label": signal.get("label") or symbol,
                    "signalId": signal_id,
                    "signalPrice": signal.get("signalPrice"),
                    "signal": signal,
                }
                task = asyncio.create_task(
                    self._accumulator_task(verdict, mode),
                    name=f"accumulator-{mode}-{signal_id}",
                )
                self.accumulator_tasks.add(task)
                task.add_done_callback(lambda completed, key=key: self._release_accumulator_task(completed, key))

        if (
            self.settings.get("accumulatorShadowValidationEnabled")
            and "demo" in active_modes
        ):
            for signal in board.get("paperCandidates", []):
                signal_id = signal.get("id")
                symbol = signal.get("symbol")
                shadow_state = (signal.get("shadowValidation") or {}).get("demo")
                if (
                    not signal_id or not symbol or symbol not in supported
                    or signal.get("status") != "PENDING"
                    or signal.get("validationTier") != "PAPER_SHADOW"
                    or not isinstance(signal.get("validationReport"), dict)
                    or shadow_state
                ):
                    continue
                key = ("demo", signal_id)
                if key in self.submitted_accumulator_signals:
                    continue
                if len(self.stream_tasks) + len(self.accumulator_tasks) >= max_tasks:
                    return
                if self.pair_open(symbol) is False:
                    await self._mark_accumulator_shadow(signal_id, "SKIPPED", "market closed on Deriv")
                    continue
                growth_rate = next(
                    (
                        rate for rate in signal.get("availableGrowthRates", [])
                        if abs(accumulator_rate_percent(rate) - ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT) < 0.001
                    ),
                    None,
                )
                if growth_rate is None:
                    await self._mark_accumulator_shadow(
                        signal_id, "SKIPPED", "Deriv does not offer the shadow policy's 4% growth rate",
                    )
                    continue
                self.submitted_accumulator_signals.add(key)
                await self._mark_accumulator_shadow(
                    signal_id, "PENDING_ORDER", "demo-only ACCU shadow validation order queued",
                )
                policy = {
                    "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
                    "growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
                    "takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
                    "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                    "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
                }
                verdict = {
                    "symbol": symbol,
                    "direction": "ACCU",
                    "contractType": "ACCU",
                    "growthRate": float(growth_rate),
                    "growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
                    "riskSafetyStakeFactor": 1.0,
                    "availableGrowthRates": signal.get("availableGrowthRates") or [],
                    "takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
                    "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
                    "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
                    "duration": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
                    "durationUnit": "m",
                    "bucket": "accumulator:shadow",
                    "reason": signal.get("reason", "demo-only shadow validation"),
                    "label": signal.get("label") or symbol,
                    "signalId": signal_id,
                    "signalPrice": signal.get("signalPrice"),
                    "signal": signal,
                    "validationPolicy": policy,
                    "shadowValidation": True,
                }
                task = asyncio.create_task(
                    self._accumulator_task(verdict, "demo"),
                    name=f"accumulator-shadow-demo-{signal_id}",
                )
                self.accumulator_tasks.add(task)
                task.add_done_callback(lambda completed, key=key: self._release_accumulator_task(completed, key))
        if not board.get("items") and board.get("error"):
            self.log("WARN", f"Accumulator signal feed unavailable: {board['error']}")

    def _release_accumulator_task(self, task, key):
        self.accumulator_tasks.discard(task)
        self.submitted_accumulator_signals.discard(key)

    async def _accumulator_task(self, verdict, mode):
        source = "accumulator_shadow" if verdict.get("shadowValidation") else "accumulator"
        try:
            await self._place_contract(verdict, source, mode)
        except asyncio.CancelledError:
            raise
        except DerivRequestError as exc:
            self.log("WARN", f"Deriv rejected ACCU {verdict['symbol']} ({mode}): {exc}")
            if verdict.get("shadowValidation"):
                await self._mark_accumulator_shadow(verdict["signalId"], "REJECTED", str(exc)[:160])
            else:
                await self._mark_accumulator_signal(verdict["signalId"], "REJECTED", str(exc)[:160], mode)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"ACCU trade failed {verdict['symbol']} ({mode}): {type(exc).__name__}: {str(exc)[:120]}")
            if verdict.get("shadowValidation"):
                await self._mark_accumulator_shadow(verdict["signalId"], "FAILED", str(exc)[:160])
            else:
                await self._mark_accumulator_signal(verdict["signalId"], "FAILED", str(exc)[:160], mode)

    # ---------- brain mode (optional indicator + statistics scanner) ----------
    async def _brain_cycle(self, active_modes):
        s = self.settings
        self.phase = "BRAIN_SCANNING"
        if not self.feed_connected():
            self.log("WARN", "Deriv feed disconnected/stale · no new trades until the backend feed is live again")
            await asyncio.sleep(5)
            return
        deriv_rows = await self.db.market_instruments.find(
            {"source": "deriv", "signalEligible": True, "marketDataEligible": True, "isOTC": {"$ne": True}, "is_otc": {"$ne": True}},
            {"_id": 0, "symbol": 1},
        ).to_list(500)
        available_symbols = {row["symbol"] for row in deriv_rows}
        configured = set(s.get("brainSymbols") or [])
        symbols = [
            sym for sym in sorted(configured & available_symbols if configured else available_symbols)
            if self.pair_open(sym) is not False
        ]
        if not symbols:
            self.log("WARN", "Brain: no open symbol to trade")
            await asyncio.sleep(10)
            return
        groups = tuple(s.get("enabledGroups") or ["RISEFALL"])
        manual_type = s.get("contractType") if s.get("tradeTypeMode") == "manual" else None
        allowed_types = {manual_type} if manual_type else None
        picks = []
        if len(symbols) > 20:
            start = self.brain_scan_offset % len(symbols)
            scan_symbols = (symbols + symbols)[start:start + 20]
            self.brain_scan_offset = (start + 20) % len(symbols)
        else:
            scan_symbols = symbols
        for symbol in scan_symbols:
            try:
                candidate, analysis = await self.brain.best(symbol, groups, allowed_types, barrier=int(s["digitBarrier"]) if manual_type in BARRIER_TYPES else None)
            except Exception as exc:  # noqa: BLE001
                self.log("WARN", f"Brain analysis failed for {symbol}: {type(exc).__name__}")
                continue
            if candidate is None:
                continue
            picks.append((candidate["confidence"], symbol, candidate, analysis))
        self.last_scan = [{"symbol": sym, "direction": cand["contractType"], "score": conf, "approved": conf >= s["minBrainConfidence"], "layers": [{"name": "brain confidence", "pass": conf >= s["minBrainConfidence"], "detail": f"{conf:.0f}% (min {s['minBrainConfidence']}%) · {cand['reason']}"}, {"name": "learned win rate", "pass": (cand["learned"]["winRate"] or 50) >= 50, "detail": f"{cand['learned']['winRate']}% over {cand['learned']['samples']}"}]} for conf, sym, cand, _ in sorted(picks, key=lambda p: -p[0])[:12]]
        self.last_scan_at = now_iso()
        picks = [p for p in picks if p[0] >= s["minBrainConfidence"]]
        if not picks:
            self.phase = "WAITING_SIGNAL"
            await asyncio.sleep(max(2, int(s.get("tradeIntervalSeconds", 6))))
            return
        picks.sort(key=lambda p: -p[0])
        conf, symbol, candidate, analysis = picks[0]
        verdict = {
            "symbol": symbol, "direction": candidate["contractType"], "contractType": candidate["contractType"], "barrier": candidate.get("barrier"),
            "score": conf, "duration": candidate["duration"], "durationUnit": candidate["durationUnit"], "bucket": candidate["bucket"],
            "reason": candidate["reason"], "label": candidate["label"], "learned": candidate["learned"],
        }
        try:
            await asyncio.gather(*(self._place_contract(verdict, "brain", mode) for mode in active_modes))
        except DerivRequestError as exc:
            self.log("WARN", f"Deriv rejected {candidate['label']} {symbol}: {exc}")
            await asyncio.sleep(3)
        await asyncio.sleep(max(1, int(s.get("tradeIntervalSeconds", 6)) // 2))

    # ---------- event-stream mirror: trade every system signal exactly as the Event stream shows it ----------
    async def _stream_cycle(self, active_modes):
        s = self.settings
        self.phase = "IN_TRADE" if self.stream_tasks else "WAITING_SIGNAL"
        now = market_now()
        board = await self.signal_service.live("deriv", 500)
        upcoming = []
        for signal in board.get("upcoming", []):
            if signal.get("status") != "PENDING" or signal["id"] in self.traded_signal_ids:
                continue
            if not await self.is_deriv_instrument_eligible(signal["symbol"]):
                self.traded_signal_ids.append(signal["id"])
                await self._mark_signal(signal["id"], "SKIPPED", "instrument is not an eligible real Deriv market")
                continue
            entry_delta = float(signal["entryEpoch"]) - now
            if entry_delta < -SIGNAL_ENTRY_MAX_AGE_SECONDS:
                self.traded_signal_ids.append(signal["id"])
                await self._mark_signal(signal["id"], "SKIPPED", "signal is older than the 5s entry window")
                self.log("WARN", f"Skip {signal.get('label')} {signal.get('timeframe')}: signal older than 5s entry window")
                continue
            if entry_delta <= 60:
                upcoming.append(signal)
        upcoming.sort(key=lambda x: (float(x["entryEpoch"]), -float(x.get("confidence") or 0)))
        max_tasks = int(s.get("maxConcurrentTrades", 3)) * len(active_modes)
        for signal in upcoming:
            if len(self.stream_tasks) + len(self.accumulator_tasks) >= max_tasks:
                self.log("INFO", f"Defer {signal.get('label')} {signal.get('timeframe')}: waiting for an auto-trade slot")
                break
            if not self.feed_connected():
                self.log("WARN", "Deriv feed disconnected/stale · no new trades until the backend feed is live again")
                break
            self.traded_signal_ids.append(signal["id"])
            conf = float(signal.get("confidence") or 0)
            if signal["symbol"] in self.untradable_symbols:
                await self._mark_signal(signal["id"], "SKIPPED", "Deriv rejected the configured stream contract")
                continue
            if self.pair_open(signal["symbol"]) is False:
                await self._mark_signal(signal["id"], "SKIPPED", "market closed on Deriv")
                self.log("WARN", f"Skip {signal.get('label')} · market closed on Deriv")
                continue
            if conf < s["minSignalConfidence"]:
                await self._mark_signal(signal["id"], "SKIPPED", f"confidence {conf:.0f}% < {s['minSignalConfidence']}%")
                self.log("INFO", f"Skip {signal.get('label')} {signal['timeframe']} {signal['direction']}: confidence {conf:.0f}% < {s['minSignalConfidence']}%")
                continue
            if signal.get("timeframe") not in s["systemTimeframes"]:
                await self._mark_signal(signal["id"], "SKIPPED", "timeframe not enabled")
                continue
            if not self.pair_promoted(signal["symbol"], signal["timeframe"]):
                await self._mark_signal(signal["id"], "SKIPPED", "pair not promoted (walk-forward gate)")
                continue
            minutes = max(1, int(signal.get("timeframeSeconds") or 60) // 60)
            contract_type, multiplier = stream_contract(signal["symbol"], signal["direction"])
            verdict = {
                "symbol": signal["symbol"], "direction": signal["direction"], "contractType": contract_type, "multiplier": multiplier, "barrier": None,
                "score": conf, "duration": minutes, "durationUnit": "m", "bucket": f"stream:{signal['timeframe']}",
                "reason": f"system signal {conf:.0f}% · {len(signal.get('agreeing') or [])} indicators · {signal.get('validationTier') or 'tier n/a'}",
                "label": "Rise" if signal["direction"] == "CALL" else "Fall", "learned": {}, "signalId": signal["id"],
                "signalPrice": signal.get("signalPrice"), "signalPriceEpoch": signal.get("signalPriceEpoch"),
                "entryEpoch": float(signal["entryEpoch"]), "expiryEpoch": float(signal.get("expiryEpoch") or signal["entryEpoch"] + minutes * 60),
                "signal": {"id": signal["id"], "symbol": signal["symbol"], "direction": signal["direction"], "timeframe": signal["timeframe"], "confidence": conf,
                           "entryEpoch": float(signal["entryEpoch"]), "expiryEpoch": float(signal.get("expiryEpoch") or 0), "generatedAt": signal.get("generatedAt"),
                           "signalPrice": signal.get("signalPrice"), "signalPriceEpoch": signal.get("signalPriceEpoch")},
            }
            self.last_scan = [{"symbol": signal["symbol"], "direction": signal["direction"], "score": conf, "approved": True, "layers": [{"name": "event stream signal", "pass": True, "detail": verdict["reason"]}]}] + self.last_scan[:11]
            self.last_scan_at = now_iso()
            self.log("INFO", f"Mirror {signal.get('label')} {signal['timeframe']} {signal['direction']} · entry in {float(signal['entryEpoch']) - now:.0f}s · {' + '.join(active_modes)}")
            for mode in active_modes:
                task = asyncio.create_task(self._stream_task(verdict, mode), name=f"stream-{mode}-{signal['id']}")
                self.stream_tasks.add(task)
                task.add_done_callback(self.stream_tasks.discard)
        await asyncio.sleep(1)

    async def _mark_signal(self, signal_id, result, reason, mode=None, **extra):
        await self.db.live_signals.update_one({"id": signal_id}, {"$set": {"autoTrade": {"mode": mode or self.mode, "result": result, "profit": 0.0, "stake": 0.0, "reason": reason, **extra}}})

    async def _mark_trade_signal(self, verdict, result, reason, mode, **extra):
        if verdict.get("shadowValidation"):
            await self._mark_accumulator_shadow(verdict["signalId"], result, reason, **extra)
            return
        if verdict.get("contractType") == "ACCU":
            await self._mark_accumulator_signal(verdict["signalId"], result, reason, mode, **extra)
        elif verdict.get("signalId"):
            await self._mark_signal(verdict["signalId"], result, reason, mode, **extra)

    async def _stream_task(self, verdict, mode):
        try:
            await self._place_contract(verdict, "stream", mode)
        except asyncio.CancelledError:
            if self.stop_reason and not self.stop_requested:
                self.stop_requested = True
        except DerivRequestError as exc:
            self.log("WARN", f"Deriv rejected {verdict['label']} {verdict['symbol']} ({mode}): {exc}")
            if "TradingDurationNotAllowed" in str(exc):
                self.untradable_symbols.add(verdict["symbol"])
            await self._mark_signal(verdict["signalId"], "REJECTED", str(exc)[:160], mode)
        except Exception as exc:  # noqa: BLE001
            self.log("ERROR", f"Stream trade failed {verdict['symbol']} ({mode}): {type(exc).__name__}: {str(exc)[:120]}")

    # ---------- order placement (shared by stream + brain) ----------
    async def _place_contract(self, verdict, source="stream", mode=None):
        mode = mode or self.modes[0]
        if source == "accumulator_shadow":
            policy = verdict.get("validationPolicy") or {}
            if mode != "demo" or not self.settings.get("accumulatorShadowValidationEnabled"):
                raise ValueError("Accumulator shadow validation is restricted to the enabled Deriv Demo account")
            if (
                policy.get("strategyVersion") != ACCUMULATOR_SHADOW_STRATEGY_VERSION
                or int(policy.get("growthRatePercent") or 0) != ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT
                or int(policy.get("takeProfitPercent") or 0) != ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT
                or int(policy.get("maxHoldTicks") or 0) != ACCUMULATOR_SHADOW_MAX_HOLD_TICKS
                or int(policy.get("maxHoldMinutes") or 0) != ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES
            ):
                raise ValueError("Accumulator shadow validation policy is invalid")
        s, ex = self.settings, self.executors[mode]
        is_accumulator = verdict["contractType"] == "ACCU"
        growth_rate = verdict.get("growthRate") if is_accumulator else None
        if is_accumulator and not is_supported_accumulator_growth_rate(growth_rate):
            raise ValueError("Accumulator growth rate must be between 4% and 5%")
        self.phase = "PLACING_TRADE"
        state = await self.get_state(mode)
        balance = await ex.refresh_balance()
        duration, unit = int(verdict["duration"]), verdict["durationUnit"]
        multiplier = verdict.get("multiplier")
        limit_order = None
        signal_price = verdict.get("signalPrice") or (verdict.get("signal") or {}).get("signalPrice")
        execution_quote = None
        preflight_slippage_bps = None
        martingale_step = 0
        if source == "accumulator_shadow":
            stake, martingale_step = float(s["baseStake"]), 0
        elif s.get("stakeMode") == "auto" and source == "brain" and self.brain is not None:
            stake = self.brain.auto_stake(balance, verdict["score"], float(s["autoStakePercent"]), float(s["maxStake"]))
        else:
            stake, martingale_step = self.stake_for(state)
        if is_accumulator and s.get("riskSafetyEnabled") and source != "accumulator_shadow":
            stake = accumulator_stake_with_safety(
                stake, s["baseStake"], verdict.get("riskSafetyStakeFactor", 0.5), state,
            )
        if is_accumulator:
            limit_order = {"take_profit": round(stake * float(verdict["takeProfitPercent"]) / 100, 2)}
        if stake > s["maxStake"] or stake > balance:
            self._halt(f"{mode.upper()}: stake {stake:.2f} exceeds max stake {s['maxStake']:.2f} or balance {balance:.2f}; bot stopped")
        balance_cap = balance * s["maxStakeBalancePercent"] / 100
        if stake > balance_cap:
            self._halt(f"{mode.upper()}: stake {stake:.2f} exceeds {s['maxStakeBalancePercent']:g}% of balance ({balance_cap:.2f}); bot stopped")
        daily_limit = self.effective_daily_loss_limit(state, mode)
        if daily_limit and -state["dailyPnl"] + stake > daily_limit:
            self._halt(f"{mode.upper()}: stake {stake:.2f} would breach daily loss limit {daily_limit:.2f}; bot stopped")
        if state["cooldownUntil"] > time.time() or state["consecutiveLosses"] >= s["maxConsecutiveLosses"]:
            await self._mark_trade_signal(verdict, "SKIPPED", "loss-streak cooldown active", mode)
            return
        entry_epoch = verdict.get("entryEpoch")
        proposal = None
        if entry_epoch:
            initial_age = market_now() - float(entry_epoch)
            if initial_age > SIGNAL_ENTRY_MAX_AGE_SECONDS:
                await self._mark_signal(verdict["signalId"], "SKIPPED", "signal is older than the 5s entry window", mode)
                self.log("WARN", f"Skip {verdict['label']} {verdict['symbol']} ({mode}): signal older than 5s entry window")
                return
            if initial_age * 1000 > float(s["maxSignalLatencyMs"]):
                await self._cancel_late(verdict, source, round(initial_age * 1000, 1), mode)
                return
            # proposal first (stake-basis ask price is fixed), then sleep until the signal's entry epoch
            proposal = await ex.proposal(verdict["symbol"], verdict["contractType"], stake, duration, unit, barrier=verdict.get("barrier"), multiplier=multiplier, growth_rate=growth_rate, limit_order=limit_order)
            wait = float(entry_epoch) - market_now()
            if wait > ENTRY_PREFLIGHT_SECONDS:
                await asyncio.sleep(min(wait - ENTRY_PREFLIGHT_SECONDS, 60))
            # re-read the risk state: another concurrent contract may have settled a loss meanwhile
            state = await self.get_state(mode)
            daily_limit = self.effective_daily_loss_limit(state, mode)
            if state["cooldownUntil"] > time.time() or state["consecutiveLosses"] >= s["maxConsecutiveLosses"] or (daily_limit and -state["dailyPnl"] + stake > daily_limit):
                await self._mark_trade_signal(verdict, "SKIPPED", "risk gate closed while waiting for entry", mode)
                self.log("WARN", f"Skip {verdict['label']} {verdict['symbol']} ({mode}): risk gate closed while waiting for entry")
                return
            if not self.feed_connected():
                await self._mark_trade_signal(verdict, "CANCELLED", "Deriv feed stale at entry", mode)
                self.log("WARN", f"Cancel {verdict['label']} {verdict['symbol']} ({mode}): feed stale at entry")
                return
            if multiplier is not None:
                if not isinstance(signal_price, (int, float)) or not math.isfinite(float(signal_price)) or float(signal_price) <= 0:
                    reason = "multiplier slippage guard has no signal reference price"
                    await self._mark_signal(verdict["signalId"], "SKIPPED", reason, mode)
                    self.log("WARN", f"Skip {verdict['label']} {verdict['symbol']} ({mode}): {reason}")
                    return
                quote = await self.db.market_instruments.find_one(
                    {"source": "deriv", "symbol": verdict["symbol"]},
                    {"_id": 0, "latestPrice": 1, "latestEpoch": 1},
                )
                quote_epoch = float((quote or {}).get("latestEpoch") or 0)
                execution_quote = float((quote or {}).get("latestPrice") or 0)
                quote_age = market_now() - quote_epoch
                if execution_quote <= 0 or quote_age > 3 or quote_age < -2:
                    reason = "multiplier slippage guard has no fresh Deriv quote"
                    await self._mark_signal(verdict["signalId"], "SKIPPED", reason, mode, quoteAgeSeconds=round(quote_age, 3))
                    self.log("WARN", f"Skip {verdict['label']} {verdict['symbol']} ({mode}): {reason}")
                    return
                preflight_slippage_bps = abs(execution_quote - float(signal_price)) / float(signal_price) * 10000
                if preflight_slippage_bps > float(s["maxSignalSlippageBps"]):
                    reason = f"preflight slippage {preflight_slippage_bps:.1f} bps > {s['maxSignalSlippageBps']:.1f} bps"
                    await self._mark_signal(verdict["signalId"], "SKIPPED", reason, mode, slippageBps=round(preflight_slippage_bps, 2))
                    self.log("WARN", f"Skip {verdict['label']} {verdict['symbol']} ({mode}): {reason}")
                    return
            remaining_to_entry = float(entry_epoch) - market_now()
            if remaining_to_entry > 0:
                await asyncio.sleep(remaining_to_entry)
            late_ms = (market_now() - float(entry_epoch)) * 1000.0
            if late_ms > float(s["maxSignalLatencyMs"]):
                await self._cancel_late(verdict, source, round(late_ms, 1), mode)
                return
        if is_accumulator:
            if not isinstance(signal_price, (int, float)) or not math.isfinite(float(signal_price)) or float(signal_price) <= 0:
                reason = "Accumulator signal has no valid Deriv reference price"
                await self._mark_trade_signal(verdict, "SKIPPED", reason, mode)
                return
            quote = await self.db.market_instruments.find_one(
                {"source": "deriv", "symbol": verdict["symbol"]},
                {"_id": 0, "latestPrice": 1, "latestEpoch": 1},
            )
            quote_epoch = float((quote or {}).get("latestEpoch") or 0)
            execution_quote = float((quote or {}).get("latestPrice") or 0)
            quote_age = market_now() - quote_epoch
            if execution_quote <= 0 or quote_age > 3 or quote_age < -2:
                reason = "Accumulator entry has no fresh Deriv quote"
                await self._mark_trade_signal(verdict, "SKIPPED", reason, mode, quoteAgeSeconds=round(quote_age, 3))
                return
            preflight_slippage_bps = abs(execution_quote - float(signal_price)) / float(signal_price) * 10000
            if preflight_slippage_bps > float(s["maxSignalSlippageBps"]):
                reason = f"preflight slippage {preflight_slippage_bps:.1f} bps > {s['maxSignalSlippageBps']:.1f} bps"
                await self._mark_trade_signal(verdict, "SKIPPED", reason, mode, slippageBps=round(preflight_slippage_bps, 2))
                return
        if proposal is None:
            proposal = await ex.proposal(verdict["symbol"], verdict["contractType"], stake, duration, unit, barrier=verdict.get("barrier"), multiplier=multiplier, growth_rate=growth_rate, limit_order=limit_order)
        ask = float(proposal["ask_price"])
        rate = (float(proposal.get("payout") or 0) - ask) / ask if ask and multiplier is None and not is_accumulator else 0.0
        expiry_seconds = int(verdict["maxHoldMinutes"]) * 60 if is_accumulator else duration * (60 if unit == "m" else 3)
        close_at_epoch = float(verdict.get("expiryEpoch") or entry_epoch + expiry_seconds) if entry_epoch else None
        if is_accumulator:
            close_at_epoch = market_now() + expiry_seconds
        request_id = f"{mode}:{source}:{verdict.get('signalId') or verdict['symbol'] + ':' + verdict['contractType'] + ':' + str(int(market_now()))}"
        trade_id = str(uuid.uuid4())
        base = {
            "id": trade_id, "requestId": request_id, "mode": mode, "accountId": ex.account_id, "symbol": verdict["symbol"], "direction": verdict.get("direction", verdict["contractType"]),
            "contractType": verdict["contractType"], "multiplier": multiplier,
            "executionType": "ACCUMULATOR" if is_accumulator else "MULTIPLIER" if multiplier is not None else "RISE_FALL",
            "growthRate": growth_rate, "growthRatePercent": verdict.get("growthRatePercent"),
            "accumulatorTakeProfitPercent": verdict.get("takeProfitPercent") if is_accumulator else None,
            "accumulatorMaxHoldTicks": verdict.get("maxHoldTicks") if is_accumulator else None,
            "closeAtEpoch": close_at_epoch, "barrier": verdict.get("barrier"), "label": verdict["label"], "stake": ask, "payout": float(proposal.get("payout") or 0),
            "signalPrice": signal_price, "preflightQuote": execution_quote, "preflightSlippageBps": round(preflight_slippage_bps, 2) if preflight_slippage_bps is not None else None,
            "maxSignalSlippageBps": s["maxSignalSlippageBps"], "multiplierStopLossPercent": s["multiplierStopLossPercent"],
            "multiplierTakeProfitPercent": s["multiplierTakeProfitPercent"],
            "riseFallStopLossPercent": s["riseFallStopLossPercent"], "riseFallTakeProfitPercent": s["riseFallTakeProfitPercent"],
            "riseFallTrailingEnabled": s["riseFallTrailingEnabled"], "riseFallTrailingPullbackPercent": s["riseFallTrailingPullbackPercent"],
            "payoutRate": round(rate, 4), "score": verdict["score"], "reason": verdict["reason"], "bucket": verdict["bucket"],
            "status": "PENDING_ORDER", "result": None, "profit": None, "openedAt": now_iso(), "closedAt": None,
            "duration": "ACCU" if is_accumulator else f"{duration}{unit}", "source": source, "signalId": verdict.get("signalId"), "entryEpoch": entry_epoch,
            "stakeMode": "flat" if source == "accumulator_shadow" else ("martingale" if s.get("martingaleEnabled") else "flat") if source == "stream" or s.get("stakeMode") != "auto" else "auto", "martingaleStep": martingale_step,
            "signal": verdict.get("signal"),
            "validationPolicy": verdict.get("validationPolicy"),
        }
        try:
            await self.db.autotrade_trades.insert_one(dict(base))
        except DuplicateKeyError:
            self.log("WARN", f"Duplicate order blocked · request {request_id}")
            return
        order_sent_at = market_now()
        try:
            if entry_epoch:
                # direct buy with full contract parameters: a proposal id fetched before the entry wait expires on Deriv
                contract = await ex.buy_parameters(
                    verdict["symbol"], verdict["contractType"], stake, duration, unit,
                    barrier=verdict.get("barrier"), multiplier=multiplier,
                    growth_rate=growth_rate, limit_order=limit_order,
                )
            else:
                contract = await ex.buy(proposal)
        except Exception as exc:
            await self.db.autotrade_trades.update_one({"id": trade_id}, {"$set": {"status": "FAILED", "result": "FAILED", "closedAt": now_iso(), "error": f"{type(exc).__name__}: {str(exc)[:200]}"}})
            raise
        order_ack_at = market_now()
        if contract.get("buy_price") is not None:
            ask = float(contract["buy_price"]) or ask
        if close_at_epoch is None:
            close_at_epoch = market_now() + expiry_seconds
        actual_entry_price = contract.get("entry_spot", contract.get("spot"))
        actual_slippage_bps = None
        if signal_price and isinstance(actual_entry_price, (int, float)) and float(signal_price) > 0:
            actual_slippage_bps = abs(float(actual_entry_price) - float(signal_price)) / float(signal_price) * 10000
        if contract.get("payout") is not None and ask:
            rate = (float(contract["payout"]) - ask) / ask
        match = None
        if entry_epoch:
            sig = verdict.get("signal") or {}
            match = {
                "sameDirection": sig.get("direction", verdict.get("direction", verdict["contractType"])) == verdict.get("direction", verdict["contractType"]),
                "sameExpiry": abs(float(verdict.get("expiryEpoch") or 0) - (close_at_epoch if close_at_epoch is not None else float(entry_epoch) + expiry_seconds)) < 1.0,
                "executionType": "MULTIPLIER" if multiplier is not None else "RISE_FALL",
                "signalPrice": signal_price, "preflightQuote": execution_quote,
                "preflightSlippageBps": round(preflight_slippage_bps, 2) if preflight_slippage_bps is not None else None,
                "actualEntryPrice": actual_entry_price,
                "actualSlippageBps": round(actual_slippage_bps, 2) if actual_slippage_bps is not None else None,
                "orderLatencyMs": round((order_sent_at - float(entry_epoch)) * 1000.0, 1),
                "ackLatencyMs": round((order_ack_at - float(entry_epoch)) * 1000.0, 1),
                "maxLatencyMs": s["maxSignalLatencyMs"],
                "orderSentAt": order_sent_at, "orderAckAt": order_ack_at,
            }
        await self.db.autotrade_state.update_one({"id": mode}, {"$inc": {"dailyTrades": 1}}, upsert=True)
        trade = {**base, "contractId": contract["contract_id"], "status": "OPEN", "match": match}
        trade.pop("_id", None)
        self.tracked_trade_ids.add(trade_id)
        await self.db.autotrade_trades.update_one({"id": trade_id}, {"$set": {"contractId": contract["contract_id"], "status": "OPEN", "match": match, "stake": ask, "payout": float(contract.get("payout") or base["payout"]), "payoutRate": round(rate, 4), "closeAtEpoch": close_at_epoch}})
        trade.update(stake=ask, payout=float(contract.get("payout") or base["payout"]), payoutRate=round(rate, 4))
        self.current_trade = trade
        self.phase = "IN_TRADE"
        execution = f" · ACCU growth {verdict['growthRatePercent']:g}%" if is_accumulator else f" · {verdict['contractType']} x{multiplier}" if multiplier is not None else ""
        slippage_log = f" · preflight slippage {preflight_slippage_bps:.1f} bps" if preflight_slippage_bps is not None else ""
        self.log("INFO", f"BUY {mode.upper()} {verdict['label']} {verdict['symbol']}{execution} stake {ask:.2f}{f' (martingale step {martingale_step})' if martingale_step else ''} · {verdict['score']:.0f}% · latency {match['orderLatencyMs'] if match else '—'} ms{slippage_log} · contract {contract['contract_id']}")
        timeout = expiry_seconds + 90
        try:
            if is_accumulator:
                result = await ex.wait_accumulator(
                    contract["contract_id"], expiry_seconds,
                    max_hold_ticks=int(verdict.get("maxHoldTicks") or ACCUMULATOR_SHADOW_MAX_HOLD_TICKS),
                    now_fn=market_now,
                )
            elif multiplier is not None:
                result = await wait_multiplier_exit(ex, contract["contract_id"], close_at_epoch, ask, s["multiplierStopLossPercent"], s["multiplierTakeProfitPercent"])
            else:
                result = await wait_rise_fall_exit(ex, contract["contract_id"], close_at_epoch, ask, s["riseFallStopLossPercent"], s["riseFallTakeProfitPercent"], s["riseFallTrailingEnabled"], s["riseFallTrailingPullbackPercent"])
            outcome, profit = await self._settle(trade, result, rate)
        finally:
            self.tracked_trade_ids.discard(trade_id)
        if verdict.get("signalId"):
            if source == "accumulator_shadow":
                await self._mark_accumulator_shadow(
                    verdict["signalId"], outcome, "demo ACCU contract settled",
                    tradeId=trade_id, profit=profit, stake=ask,
                )
            else:
                target = self.db.accumulator_signals if is_accumulator else self.db.live_signals
                await target.update_one({"id": verdict["signalId"]}, {"$set": {"autoTrade": {"mode": mode, "tradeId": trade_id, "result": outcome, "profit": profit, "stake": ask, "match": match}, f"autoTradeByMode.{mode}": {"tradeId": trade_id, "result": outcome, "profit": profit, "stake": ask}}})
        if self.brain is not None and not is_accumulator:
            await self.brain.record_outcome(verdict["symbol"], verdict.get("direction", verdict["contractType"]), verdict["bucket"], outcome, profit, mode)

    async def _cancel_late(self, verdict, source, latency_ms, mode=None):
        mode = mode or self.modes[0]
        reason = f"order would leave {latency_ms:.0f} ms after entry (limit {self.settings['maxSignalLatencyMs']} ms)"
        await self.db.autotrade_trades.insert_one({
            "id": str(uuid.uuid4()), "mode": mode, "symbol": verdict["symbol"], "direction": verdict["contractType"], "contractType": verdict["contractType"],
            "label": verdict["label"], "stake": 0.0, "payout": 0.0, "status": "CANCELLED", "result": "CANCELLED_LATE", "profit": 0.0, "openedAt": now_iso(), "closedAt": now_iso(),
            "duration": f"{verdict['duration']}{verdict['durationUnit']}", "source": source, "signalId": verdict.get("signalId"), "entryEpoch": verdict.get("entryEpoch"),
            "signal": verdict.get("signal"), "match": {"orderLatencyMs": latency_ms, "maxLatencyMs": self.settings["maxSignalLatencyMs"], "withinLimit": False}, "reason": reason,
        })
        await self.db.autotrade_state.update_one({"id": mode}, {"$inc": {"cancelledLate": 1}}, upsert=True)
        if verdict.get("signalId"):
            await self._mark_signal(verdict["signalId"], "CANCELLED_LATE", reason, mode, latencyMs=latency_ms)
        self.log("WARN", f"CANCELLED {mode.upper()} {verdict['label']} {verdict['symbol']}: {reason}")

    async def _settle(self, trade, result, rate):
        s = self.settings
        risk_state_id = trade.get("riskStateId") or trade["mode"]
        is_shadow = risk_state_id == "demo_shadow"
        max_losses = s["shadowMaxConsecutiveLosses"] if is_shadow else s["maxConsecutiveLosses"]
        cooldown_minutes = s["shadowCooldownMinutes"] if is_shadow else s["streakCooldownMinutes"]
        profit = round(float(result["profit"]), 2)
        outcome = "WIN" if profit > 0 else "LOSS" if profit < 0 else "TIE"
        inc = {"dailyPnl": profit, "totalPnl": profit, "wins" if outcome == "WIN" else "losses" if outcome == "LOSS" else "ties": 1}
        update = {"$inc": inc, "$set": {"lastPayoutRate": round(rate, 4)}}
        if outcome == "WIN":
            update["$set"]["consecutiveLosses"] = 0
        elif outcome == "LOSS":
            inc["consecutiveLosses"] = 1
        state = await self.db.autotrade_state.find_one_and_update({"id": risk_state_id}, update, upsert=True, return_document=True)
        state = {**empty_state(risk_state_id), **(state or {})}
        await self.db.autotrade_trades.update_one({"id": trade["id"]}, {"$set": {
            "status": "CLOSED", "result": outcome, "profit": profit, "closedAt": now_iso(),
            "entrySpot": result.get("entry_spot"), "exitSpot": result.get("exit_spot"), "exitReason": result.get("exitReason"),
        }})
        self.current_trade = None
        account_label = "DEMO_SHADOW" if is_shadow else trade["mode"].upper()
        self.log("INFO" if outcome != "LOSS" else "WARN", f"{outcome} {account_label} {trade['symbol']} P/L {profit:+.2f} · streak {state['consecutiveLosses']}/{max_losses}")
        if outcome == "LOSS" and state["consecutiveLosses"] >= max_losses and state["cooldownUntil"] <= time.time():
            until = time.time() + cooldown_minutes * 60
            await self.db.autotrade_state.update_one({"id": risk_state_id}, {"$set": {"cooldownUntil": until}, "$inc": {"streakPauses": 1}})
            self.log("WARN", f"{account_label}: {state['consecutiveLosses']} losses in a row · auto trade paused for {cooldown_minutes} min")
            if not is_shadow:
                self.phase = "COOLDOWN"
        daily_limit = float(s["shadowDailyLossLimit"]) if is_shadow else self.effective_daily_loss_limit(state, trade["mode"])
        if daily_limit and state["dailyPnl"] <= -daily_limit:
            if not is_shadow:
                self._halt(f"{trade['mode'].upper()}: daily loss limit {daily_limit:.2f} reached ({state['dailyPnl']:.2f}); trading stopped for today")
        return outcome, profit

    # ---------- optional layer scan (read-only analysis, used by /evaluate and /scan) ----------
    async def _candles(self, symbol):
        frames = await asyncio.gather(self.feed.candles(symbol, 60, 150), self.feed.candles(symbol, 300, 60), self.feed.candles(symbol, 900, 60))
        return {tf: rows[:-1] for tf, rows in zip(("1m", "5m", "15m"), frames)}

    async def evaluate_symbol(self, mode, symbol):
        candles = await self._candles(symbol)
        return evaluate(symbol, candles, self.settings, await self.pair_stats(mode, symbol))

    async def scan(self, mode):
        semaphore = asyncio.Semaphore(4)

        async def one(symbol):
            async with semaphore:
                try:
                    return await self.evaluate_symbol(mode, symbol)
                except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the scan
                    return {"symbol": symbol, "approved": False, "direction": None, "score": 0, "layers": [{"name": "data", "pass": False, "detail": str(exc)[:160]}]}

        verdicts = await asyncio.gather(*(one(symbol) for symbol in self.settings["symbols"]))
        self.last_scan = [{k: v.get(k) for k in ("symbol", "direction", "score", "approved", "layers")} for v in verdicts]
        self.last_scan_at = now_iso()
        return verdicts

    # ---------- status ----------
    async def system_accuracy(self, hours=24):
        rows = await self.db.live_signals.find({"status": {"$in": ["WIN", "LOSS"]}, "generatedAt": {"$gte": time.time() - hours * 3600}}, {"_id": 0, "status": 1}).to_list(5000)
        wins = sum(r["status"] == "WIN" for r in rows)
        return {"wins": wins, "losses": len(rows) - wins, "winRate": round(100.0 * wins / len(rows), 1) if rows else None}

    async def status(self):
        primary = self.modes[0]
        state = await self.get_state(primary)
        states = {m: await self.get_state(m) for m in MODES}
        shadow_state = await self.get_state("demo_shadow")
        creds = self.credentials()
        open_trades = await self.db.autotrade_trades.find({"mode": {"$in": self.modes}, "status": {"$in": ["OPEN", "PENDING_ORDER"]}}, {"_id": 0}).sort("openedAt", -1).limit(20).to_list(20)
        try:
            from promotion_gate import promoted_symbols
            promoted = promoted_symbols()
        except ImportError:
            promoted = []
        s = self.settings
        ex = self.executor
        accounts = {m: {"id": e.account_id, "balance": e.balance, "currency": e.currency} for m, e in self.executors.items()}
        combined_wins = sum(states[m]["wins"] for m in self.modes)
        combined_losses = sum(states[m]["losses"] for m in self.modes)
        return {
            "openTrades": open_trades, "effectiveDailyLossLimit": self.effective_daily_loss_limit(state, primary), "feedConnected": self.feed_connected(), "feed": self.feed_status(),
            "promotedSymbols": promoted, "killed": self.kill_requested,
            "running": self.running, "mode": self.mode, "modes": self.modes, "phase": self.phase if self.running or self.phase == "STOPPED" else "IDLE",
            "stopReason": self.stop_reason, "settings": s, "state": state, "states": states, "shadowState": shadow_state, "accounts": accounts,
            "riskRules": {"stake": "martingale" if s.get("martingaleEnabled") else "flat", "martingale": bool(s.get("martingaleEnabled")), "martingaleMultiplier": s["martingaleMultiplier"], "martingaleMaxSteps": s["martingaleMaxSteps"],
                          "maxConsecutiveLosses": s["maxConsecutiveLosses"], "streakCooldownMinutes": s["streakCooldownMinutes"],
                          "dailyLossLimit": self.effective_daily_loss_limit(state, primary), "maxSignalLatencyMs": s["maxSignalLatencyMs"], "cooldownActive": state["cooldownUntil"] > time.time(),
                          "cooldownUntil": state["cooldownUntil"] or None, "minSignalConfidence": s["minSignalConfidence"]},
            "account": {"id": ex.account_id if ex else None, "balance": ex.balance if ex else None, "currency": ex.currency if ex else "USD"},
            "lastScan": self.last_scan, "lastScanAt": self.last_scan_at, "currentTrade": self.current_trade,
            "systemAccuracy": await self.system_accuracy() if self.signal_service is not None else None,
            "logs": list(self.logs)[:80],
            "brain": {"enabled": self.brain is not None, "learning": self.brain.learning_report()[:20] if self.brain is not None else []},
            "winRate": round(100.0 * combined_wins / (combined_wins + combined_losses), 1) if (combined_wins + combined_losses) else None,
            "credentials": {"appId": bool(creds["app_id"]), "demoToken": bool(creds["demo"]), "realToken": bool(creds["real"]), "distinctTokens": creds["demo"] != creds["real"]},
            "webLiveEnabled": (
                os.environ.get("AUTOTRADE_WEB_LIVE_ENABLED", "").strip().lower() == "true"
                and bool(os.environ.get("AUTOTRADE_WEB_REAL_CONTROL_KEY", "").strip())
            ),
        }
