import asyncio
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import websockets
from dotenv import dotenv_values

logger = logging.getLogger(__name__)

PUBLIC_WS = os.environ.get(
    "DERIV_LAB_WS_URL",
    "wss://api.derivws.com/trading/v1/options/ws/public",
)


def default_db_path():
    return Path(__file__).resolve().parents[2] / "data" / "accu_lab.sqlite3"


class TickStore:
    def __init__(self, path=None):
        self.path = Path(path or default_db_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ticks (
                symbol TEXT NOT NULL,
                epoch INTEGER NOT NULL,
                quote REAL NOT NULL,
                source TEXT NOT NULL,
                has_gap INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(symbol, epoch)
            );
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_epoch ON ticks(symbol, epoch);

            CREATE TABLE IF NOT EXISTS symbols (
                symbol TEXT PRIMARY KEY,
                label TEXT,
                discovered_at INTEGER,
                growth_rates TEXT,
                active INTEGER DEFAULT 1
            );
            """
        )
        self.conn.commit()
        self._last_epoch = {}

    def add(self, symbol, epoch, quote, source="live", gap_seconds=None):
        epoch = int(epoch)
        quote = float(quote)
        gap = 0
        prior = self._last_epoch.get(symbol)
        if prior is None:
            row = self.conn.execute(
                "SELECT epoch FROM ticks WHERE symbol=? ORDER BY epoch DESC LIMIT 1",
                (symbol,),
            ).fetchone()
            prior = int(row[0]) if row else None
        if prior is not None:
            threshold = float(gap_seconds or 5)
            gap = int(epoch - prior > threshold)
        self._last_epoch[symbol] = epoch
        self.conn.execute(
            "INSERT OR REPLACE INTO ticks(symbol, epoch, quote, source, has_gap) "
            "VALUES(?, ?, ?, ?, ?)",
            (symbol, epoch, quote, source, gap),
        )
        self.conn.commit()
        return gap

    def history(self, symbol):
        return [
            {"epoch": epoch, "quote": quote, "source": source, "has_gap": bool(has_gap)}
            for epoch, quote, source, has_gap in self.conn.execute(
                "SELECT epoch, quote, source, has_gap FROM ticks WHERE symbol=? "
                "ORDER BY epoch ASC",
                (symbol,),
            ).fetchall()
        ]

    def close(self):
        self.conn.close()


class AccuTickRecorder:
    def __init__(self, store=None, app_id=None):
        self.store = store or TickStore()
        env_path = Path(__file__).resolve().parents[1] / ".env"
        env = dotenv_values(env_path)
        self.app_id = str(app_id or os.environ.get("DERIV_APP_ID") or env.get("DERIV_APP_ID") or "")
        if not self.app_id:
            raise RuntimeError("DERIV_APP_ID is required")
        self.symbols = []
        self.running = False

    def ws_url(self):
        separator = "&" if "?" in PUBLIC_WS else "?"
        return f"{PUBLIC_WS}{separator}app_id={self.app_id}"

    async def discover(self, ws):
        await ws.send(json.dumps({"active_symbols": "brief", "req_id": 1}))
        active = json.loads(await ws.recv())
        active_rows = active.get("active_symbols") or []
        for index, row in enumerate(active_rows, start=2):
            symbol = str(row.get("underlying_symbol") or row.get("symbol") or "")
            if not symbol:
                continue
            await ws.send(json.dumps({"contracts_for": symbol, "req_id": index}))
            response = json.loads(await ws.recv())
            available = (response.get("contracts_for") or {}).get("available") or []
            accu = next((item for item in available if item.get("contract_type") == "ACCU"), None)
            if not accu:
                continue
            rates = accu.get("growth_rate_range") or []
            self.symbols.append(symbol)
            self.store.conn.execute(
                "INSERT OR REPLACE INTO symbols(symbol,label,discovered_at,growth_rates,active)"
                " VALUES(?,?,?,?,1)",
                (
                    symbol,
                    row.get("underlying_symbol_name") or symbol,
                    int(time.time()),
                    json.dumps(rates),
                ),
            )
        self.store.conn.commit()
        self.symbols = sorted(set(self.symbols))

    async def record_history(self, ws):
        for index, symbol in enumerate(self.symbols, start=100):
            await ws.send(json.dumps({
                "ticks_history": symbol,
                "end": "latest",
                "count": 10000,
                "style": "ticks",
                "req_id": index,
            }))
            while True:
                message = json.loads(await ws.recv())
                if message.get("req_id") == index:
                    break
            history = message.get("history") or {}
            epochs = history.get("times") or []
            quotes = history.get("prices") or []
            for epoch, quote in zip(epochs, quotes):
                self.store.add(symbol, epoch, quote, source="ticks_history")

    async def subscribe_live(self, ws):
        for index, symbol in enumerate(self.symbols, start=1000):
            await ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": index}))

    async def run(self, duration_seconds=None):
        self.running = True
        async with websockets.connect(self.ws_url(), open_timeout=15, close_timeout=5, ping_interval=20) as ws:
            await self.discover(ws)
            await self.record_history(ws)
            await self.subscribe_live(ws)
            deadline = time.monotonic() + duration_seconds if duration_seconds else None
            while self.running:
                if deadline and time.monotonic() >= deadline:
                    break
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                message = json.loads(raw)
                tick = message.get("tick")
                if tick:
                    self.store.add(
                        str(tick["symbol"]),
                        tick["epoch"],
                        tick["quote"],
                        source="live",
                    )
        self.running = False
