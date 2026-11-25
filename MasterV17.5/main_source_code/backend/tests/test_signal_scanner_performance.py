import asyncio
import sys
from types import SimpleNamespace
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from signal_service import (
    SIGNAL_TIMEFRAMES,
    SignalService,
    evaluation_priority,
    evaluation_window,
    is_real_deriv_forex,
    signal_target_metadata,
)  # noqa: E402


def test_backend_fixed_time_signal_universe_is_real_deriv_forex_only():
    forex = {
        "source": "deriv", "symbol": "frxEURUSD", "market": "forex",
        "marketDataEligible": True,
    }
    assert is_real_deriv_forex(forex)
    assert not is_real_deriv_forex({
        **forex, "symbol": "R_100", "market": "synthetic_index",
    })
    assert not is_real_deriv_forex({
        **forex, "source": "market-qx-observer-v2",
    })
    assert not is_real_deriv_forex({**forex, "is_otc": True})


def test_backend_fixed_time_signals_are_limited_to_one_and_five_minutes():
    assert SIGNAL_TIMEFRAMES == ["1m", "5m"]
    service = object.__new__(SignalService)
    service.settings = {"timeframes": ["1m"], "sources": ["market-qx-observer-v2"]}
    service._evaluated = {}

    settings = service.apply_settings({"timeframes": ["15m"], "sources": ["deriv"]})

    assert settings["timeframes"] == ["1m", "5m"]
    assert settings["sources"] == ["deriv"]


def test_fresh_signal_instruments_exclude_non_forex_and_observer_markets():
    rows = [
        {
            "source": "deriv", "symbol": "frxEURUSD", "market": "forex",
            "marketDataEligible": True,
        },
        {
            "source": "deriv", "symbol": "R_100", "market": "synthetic_index",
            "marketDataEligible": True,
        },
        {
            "source": "market-qx-observer-v2", "symbol": "EURUSD",
            "market": "forex", "marketDataEligible": True,
        },
    ]

    class Cursor:
        async def to_list(self, *_):
            return rows

    class Collection:
        query = None

        def find(self, query, *_):
            self.query = query
            return Cursor()

    async def verify():
        instruments = Collection()
        service = object.__new__(SignalService)
        service.db = SimpleNamespace(market_instruments=instruments)
        found = await service.fresh_instruments()
        assert [row["symbol"] for row in found] == ["frxEURUSD"]
        assert instruments.query["source"] == "deriv"
        assert instruments.query["market"] == "forex"

    asyncio.run(verify())


def test_emit_refuses_non_forex_or_non_fixed_time_signal_modes():
    service = object.__new__(SignalService)
    forex = {
        "source": "deriv", "symbol": "frxEURUSD", "market": "forex",
        "marketDataEligible": True,
    }
    synthetic = {
        "source": "deriv", "symbol": "R_100", "market": "synthetic_index",
        "marketDataEligible": True,
    }

    async def verify():
        assert not await service.emit(synthetic, "1m", 100, {"direction": "CALL"}, "STANDARD")
        assert not await service.emit(forex, "15m", 100, {"direction": "CALL"}, "STANDARD")

    asyncio.run(verify())


def test_scanner_filters_out_of_window_targets_and_keeps_pre_signal_window():
    assert not evaluation_window("deriv", "1m", 121, 0.5)
    assert evaluation_window("deriv", "1m", 145, 0.5)
    assert evaluation_window("deriv", "1m", 169, 0.5)
    assert evaluation_window("deriv", "5m", 1488, 0.5)
    assert not evaluation_window("deriv", "5m", 1400, 0.5)


def test_scanner_prioritizes_imminent_one_minute_deriv_targets():
    now = 169
    urgent = evaluation_priority("deriv", "R_100", "1m", now)
    background = evaluation_priority("deriv", "R_100", "5m", now)
    assert urgent[0] < background[0]


def test_signal_payload_lists_expiry_and_verified_platforms():
    deriv = signal_target_metadata("deriv", "1m", "DERIV_PROVIDER", ["1m", "5m"])
    deriv_five_minute = signal_target_metadata("deriv", "5m", "DERIV_PROVIDER", ["1m", "5m"])
    observed = signal_target_metadata("market-qx-observer-v2", "5m", "OBSERVED_UNVERIFIED", ["1m", "5m"])
    cross_validated = signal_target_metadata("market-qx-observer-v2", "5m", "CROSS_VALIDATED", ["1m", "5m"])
    assert deriv == {"targetExpiry": "1m", "expiryOptions": ["1m", "5m"], "platformCompatibility": ["deriv", "generic_binary_options"]}
    assert deriv_five_minute["targetExpiry"] == "5m"
    assert deriv_five_minute["expiryOptions"] == ["1m", "5m"]
    assert observed["platformCompatibility"] == ["generic_binary_options"]
    assert cross_validated["platformCompatibility"] == ["deriv", "generic_binary_options"]


def test_cycle_candle_cache_coalesces_identical_concurrent_reads():
    class Store:
        calls = 0

        async def candles(self, source, symbol, timeframe, limit):
            self.calls += 1
            await asyncio.sleep(0.01)
            return [{
                "epoch": 60, "open": 1, "high": 2, "low": 1, "close": 2,
                "completeness": "PROVIDER_OHLC",
            }]

    service = object.__new__(SignalService)
    service.store = Store()
    service._cycle_candle_cache = {}

    async def read_together():
        return await asyncio.gather(*(
            service.closed_candles("deriv", "R_100", "1m", 200)
            for _ in range(12)
        ))

    results = asyncio.run(read_together())
    assert service.store.calls == 1
    assert all(len(rows) == 1 for rows in results)


def test_current_candle_gap_retries_once_before_skipping_entry(monkeypatch):
    service = object.__new__(SignalService)
    service._gap_retry_entry = {}
    service._insufficient_retry = {}
    service._evaluated = {}
    service.active_market_evaluations = 0
    service.store = SimpleNamespace(deriv_feed=None)
    service._maybe_emit_pre_signal = lambda *args: asyncio.sleep(0)
    service._is_blacklisted = lambda *args, **kwargs: asyncio.sleep(0, result=False)
    service.assess = lambda *args: asyncio.sleep(
        0, result=(None, 'GAP_BEFORE_CURRENT_CANDLE', 120)
    )
    service._scan = lambda *args: None
    monkeypatch.setattr('signal_service.server_clock.now', lambda: 90)
    instrument = {'source': 'market-qx-observer-v2', 'symbol': 'EURUSD'}
    semaphore = asyncio.Semaphore(1)

    async def run():
        first = await service._evaluate_cycle_market(instrument, '1m', 90, semaphore, 0.5)
        assert first == {'evaluated': 1, 'candidate': None}
        assert service._evaluated == {}
        assert service._insufficient_retry[('market-qx-observer-v2', 'EURUSD', '1m')] == 95

        waiting = await service._evaluate_cycle_market(instrument, '1m', 90, semaphore, 0.5)
        assert waiting == {'evaluated': 0, 'candidate': None}

        monkeypatch.setattr('signal_service.server_clock.now', lambda: 95)
        second = await service._evaluate_cycle_market(instrument, '1m', 95, semaphore, 0.5)
        assert second == {'evaluated': 1, 'candidate': None}
        assert service._evaluated[('market-qx-observer-v2', 'EURUSD', '1m')] == 120
        assert ('market-qx-observer-v2', 'EURUSD', '1m') not in service._insufficient_retry

    asyncio.run(run())
