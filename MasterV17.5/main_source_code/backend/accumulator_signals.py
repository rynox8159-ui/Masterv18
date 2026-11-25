"""Discover Deriv-supported Accumulators and publish transparent setup observations."""
import asyncio
import logging
import re
import time

from deriv_service import DerivRequestError, is_real_market_instrument
from market_config import FRESHNESS
from server_clock import now as market_now
from ds.accumulator_strategy import (
    ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
    ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
    ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
    ACCUMULATOR_SHADOW_MIN_SAMPLES,
    ACCUMULATOR_SHADOW_STRATEGY_VERSION,
    ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
    accumulator_shadow_report,
    evaluate_accumulator,
    growth_rate_percent,
)

logger = logging.getLogger(__name__)
OTC_MARKER = re.compile(r"(?:_OTC|OTC)$", re.IGNORECASE)
UNIVERSE_REFRESH_SECONDS = 300
SIGNAL_MAX_AGE_SECONDS = 60


class AccumulatorSignalService:
    def __init__(self, store, deriv, *, interval_seconds=5):
        self.store = store
        self.db = store.db
        self.deriv = deriv
        self.interval_seconds = max(1, interval_seconds)
        self.task = None
        self.error = "DERIV_MARKET_DATA_DISABLED" if deriv is None else None
        self.scanned = 0
        self.universe = {}
        self.universe_updated_at = 0.0
        self.last_evaluated = {}
        self.last_signal_at = None
        self.last_scan_at = None
        self.last_candidate_count = 0
        self.candidate_timestamps = {}
        self.scan_diagnostics = {
            "supportedPairs": 0,
            "freshQuotes": 0,
            "closedCandleWindows": 0,
            "recentCandidates": 0,
        }

    async def refresh_universe(self, force=False):
        now = market_now()
        if not force and now - self.universe_updated_at < UNIVERSE_REFRESH_SECONDS:
            return self.universe
        if self.deriv is None:
            raise RuntimeError("DERIV_MARKET_DATA_DISABLED")
        response = await self.deriv.request({"active_symbols": "brief"})
        symbols = response.get("active_symbols")
        if not isinstance(symbols, list):
            raise RuntimeError("DERIV_ACCU_SYMBOL_RESPONSE_INVALID")
        universe = {}
        for item in symbols:
            symbol = str(item.get("underlying_symbol") or item.get("symbol") or "").strip()
            if not symbol or OTC_MARKER.search(symbol) or not is_real_market_instrument(item):
                continue
            if str(item.get("exchange_is_open", 1)).lower() in {"0", "false"} or str(item.get("is_trading_suspended", 0)).lower() in {"1", "true"}:
                continue
            try:
                contracts_response = await self.deriv.request({"contracts_for": symbol})
            except (DerivRequestError, OSError, asyncio.TimeoutError) as exc:
                logger.warning("Could not verify ACCU contracts for %s: %s", symbol, exc)
                continue
            available = (contracts_response.get("contracts_for") or {}).get("available") or []
            accu = next((row for row in available if row.get("contract_type") == "ACCU"), None)
            if not accu:
                continue
            rates = accu.get("growth_rate_range")
            if not isinstance(rates, list) or not rates:
                continue
            universe[symbol] = {
                "label": item.get("underlying_symbol_name") or symbol,
                "market": item.get("market"),
                "growthRates": rates,
            }
        self.universe = universe
        self.universe_updated_at = now
        return universe

    async def scan_once(self):
        universe = await self.refresh_universe()
        now = market_now()
        items = []
        self.candidate_timestamps = {
            symbol: generated_at
            for symbol, generated_at in self.candidate_timestamps.items()
            if now - generated_at <= SIGNAL_MAX_AGE_SECONDS
        }
        self.scanned = len(universe)
        diagnostics = {
            "supportedPairs": len(universe),
            "freshQuotes": 0,
            "closedCandleWindows": 0,
        }
        for symbol, metadata in universe.items():
            instrument = await self.db.market_instruments.find_one(
                {"source": "deriv", "symbol": symbol},
                {
                    "_id": 0, "marketDataEligible": 1, "latestEpoch": 1,
                    "latestPrice": 1, "market": 1, "submarket": 1,
                    "exchangeIsOpen": 1, "available": 1,
                    "is_otc": 1, "isOTC": 1, "label": 1, "source": 1,
                    "symbol": 1,
                },
            )
            if (
                not instrument
                or instrument.get("marketDataEligible") is False
                or instrument.get("exchangeIsOpen") is False
                or instrument.get("available") is False
                or not is_real_market_instrument(instrument)
                or instrument.get("latestEpoch") is None
                or now - float(instrument["latestEpoch"]) > FRESHNESS
                or instrument.get("latestPrice") is None
            ):
                continue
            diagnostics["freshQuotes"] += 1
            closed_before = int(now // 60) * 60 - 60
            candles = await self.db.market_candles.find(
                {
                    "source": "deriv",
                    "symbol": symbol,
                    "timeframe": "1m",
                    "completeness": "PROVIDER_OHLC",
                    "epoch": {"$lte": closed_before},
                },
                {"_id": 0},
            ).sort("epoch", -1).limit(30).to_list(30)
            candles.reverse()
            if len(candles) < 30:
                continue
            diagnostics["closedCandleWindows"] += 1
            candle_epoch = candles[-1].get("epoch")
            if self.last_evaluated.get(symbol) == candle_epoch:
                continue
            recommendation = evaluate_accumulator(
                symbol, candles, metadata["growthRates"],
            )
            if recommendation is None:
                self.last_evaluated[symbol] = candle_epoch
                continue
            validation = await self._shadow_validation_report(symbol)
            validated_rate = None
            for rate in metadata["growthRates"]:
                try:
                    if growth_rate_percent(rate) == ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT:
                        validated_rate = float(rate)
                        break
                except (TypeError, ValueError):
                    continue
            if validated_rate is not None and validation["readyForLive"]:
                recommendation["growthRate"] = validated_rate
                recommendation["growthRatePercent"] = ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT
                recommendation["strategyTier"] = "LIVE_VALIDATED"
                validation["growthRate"] = validated_rate
                validation["growthRatePercent"] = ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT
                validation_tier = "LIVE_VALIDATED"
            else:
                if validated_rate is None:
                    validation["reason"] = "DERIV_4_PERCENT_RATE_UNAVAILABLE"
                validation["readyForLive"] = False
                validation["status"] = "SHADOW_VALIDATION_PENDING"
                validation_tier = "PAPER_SHADOW"
            generated_at = now
            self.candidate_timestamps[symbol] = generated_at
            signal = {
                "id": f"accu:{symbol}:{candle_epoch}",
                "signalMode": "DERIV_ACCU",
                "source": "deriv",
                "symbol": symbol,
                "label": metadata["label"],
                "market": metadata.get("market"),
                "timeframe": "1m",
                "generatedAt": generated_at,
                "entryEpoch": generated_at,
                "expiresAt": generated_at + SIGNAL_MAX_AGE_SECONDS,
                "signalPrice": float(instrument["latestPrice"]),
                "status": "PENDING",
                "validationTier": validation_tier,
                "validationReport": validation,
                "autoTradeByMode": {},
                "availableGrowthRates": metadata["growthRates"],
                **recommendation,
            }
            result = await self.db.accumulator_signals.update_one(
                {"id": signal["id"]},
                {"$setOnInsert": signal},
                upsert=True,
            )
            if result.upserted_id is not None:
                validation_label = (
                    f"demo-validated over {validation['samples']} settled ACCU trades"
                    if validation_tier == "LIVE_VALIDATED"
                    else f"awaiting demo validation ({validation['samples']}/{validation['minSamples']} settled trades)"
                )
                await self.store.event(
                    "SIGNAL", "Accumulator",
                    f"{metadata['label']} ({symbol}) · ACCU growth {recommendation['growthRatePercent']:g}% · "
                    f"TP {recommendation['takeProfitPercent']:g}% of stake · "
                    f"max hold {recommendation['maxHoldTicks']} ticks / {ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES} minutes · "
                    f"{validation_label} · {recommendation['reason']}",
                )
                self.last_signal_at = generated_at
            self.last_evaluated[symbol] = candle_epoch
            items.append(signal)
        self.last_candidate_count = len(self.candidate_timestamps)
        self.last_scan_at = now
        diagnostics["recentCandidates"] = self.last_candidate_count
        self.scan_diagnostics = diagnostics
        return items

    async def _shadow_validation_report(self, symbol):
        query = {
            "mode": "demo",
            "source": "accumulator_shadow",
            "symbol": symbol,
            "executionType": "ACCUMULATOR",
            "status": "CLOSED",
            "result": {"$in": ["WIN", "LOSS"]},
            "validationPolicy.strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
            "validationPolicy.growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
            "validationPolicy.takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
            "validationPolicy.maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
            "validationPolicy.maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
        }
        rows = []
        trades = getattr(self.db, "autotrade_trades", None)
        if trades is not None:
            rows = await trades.find(
                query,
                {"_id": 0, "profit": 1, "stake": 1, "closedAt": 1},
            ).sort("closedAt", -1).limit(ACCUMULATOR_SHADOW_MIN_SAMPLES).to_list(
                ACCUMULATOR_SHADOW_MIN_SAMPLES
            )
            rows.reverse()
        report = accumulator_shadow_report(rows)
        return {
            **report,
            "source": "deriv",
            "symbol": symbol,
            "timeframe": "1m",
            "validationMethod": "DEMO_SHADOW",
            "strategyVersion": ACCUMULATOR_SHADOW_STRATEGY_VERSION,
            "growthRatePercent": ACCUMULATOR_SHADOW_GROWTH_RATE_PERCENT,
            "takeProfitPercent": ACCUMULATOR_SHADOW_TAKE_PROFIT_PERCENT,
            "maxHoldTicks": ACCUMULATOR_SHADOW_MAX_HOLD_TICKS,
            "maxHoldMinutes": ACCUMULATOR_SHADOW_MAX_HOLD_MINUTES,
        }

    async def live(self, limit=100):
        bounded_limit = max(1, min(int(limit), 500))
        rows = await self.db.accumulator_signals.find(
            {
                "status": "PENDING",
                "expiresAt": {"$gte": market_now()},
                "validationTier": "LIVE_VALIDATED",
                "strategyTier": "LIVE_VALIDATED",
                "validationReport.status": "VALIDATED",
                "validationReport.readyForLive": True,
                "validationReport.source": "deriv",
            },
            {"_id": 0},
        ).sort("generatedAt", -1).limit(bounded_limit).to_list(bounded_limit)
        paper_candidates = await self.db.accumulator_signals.find(
            {
                "expiresAt": {"$gte": market_now()},
                "status": "PENDING",
                "validationTier": "PAPER_SHADOW",
                "validationReport.readyForLive": False,
            },
            {"_id": 0},
        ).sort("generatedAt", -1).limit(bounded_limit).to_list(bounded_limit)
        return {
            "items": rows,
            "paperCandidates": paper_candidates,
            "supportedSymbols": [
                {
                    "symbol": symbol,
                    "label": row["label"],
                    "market": row.get("market"),
                    "growthRates": row.get("growthRates", []),
                }
                for symbol, row in sorted(self.universe.items())
            ],
            "scanned": self.scanned,
            "universeUpdatedAt": self.universe_updated_at or None,
            "lastSignalAt": self.last_signal_at,
            "lastScanAt": self.last_scan_at,
            "unvalidatedCandidates": sum(
                market_now() - generated_at <= SIGNAL_MAX_AGE_SECONDS
                for generated_at in self.candidate_timestamps.values()
            ),
            "scanDiagnostics": dict(self.scan_diagnostics),
            "error": self.error,
            "now": market_now(),
        }

    async def run(self):
        while True:
            try:
                await self.scan_once()
                if self.error:
                    logger.info("Accumulator signal scanner recovered")
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
                if error != self.error:
                    logger.warning("Accumulator signal scan failed: %s", error)
                self.error = error
            await asyncio.sleep(self.interval_seconds)

    def start(self):
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="accumulator-signals")

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.task = None
