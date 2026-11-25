import asyncio
import json
import math
import os
from pathlib import Path

import httpx
import websockets
from dotenv import dotenv_values

from .accu_simulator import (
    read_barrier_distance,
    read_barrier_reference_spot,
    read_payout,
    replay_ticks,
)
from .tick_recorder import TickStore


LEGACY_WS_TEMPLATE = "wss://ws.derivws.com/websockets/v3?app_id={app_id}"
REST_BASE = "https://api.derivws.com/trading/v1/options"


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def is_settled_contract(contract):
    status = str(contract.get("status") or "").lower()
    return bool(contract.get("is_sold") or contract.get("is_expired")) or status in {
        "won", "lost", "sold",
    }


def is_accu_transaction(transaction):
    contract_type = str(transaction.get("contract_type") or "").upper()
    shortcode = str(transaction.get("shortcode") or "").upper()
    return contract_type == "ACCU" or shortcode.startswith("ACCU_")


class DemoTradeCalibration:
    def __init__(self, app_id=None, token=None, tick_store=None):
        env_path = Path(__file__).resolve().parents[1] / ".env"
        env = dotenv_values(env_path)
        self.app_id = str(app_id or os.environ.get("DERIV_APP_ID") or env.get("DERIV_APP_ID") or "")
        self.token = str(
            token
            or os.environ.get("DERIV_DEMO_TOKEN")
            or os.environ.get("DERIV_API_TOKEN_1")
            or env.get("DERIV_DEMO_TOKEN")
            or env.get("DERIV_API_TOKEN_1")
            or ""
        )
        self.tick_store = tick_store or TickStore()
        if not self.app_id or not self.token:
            raise RuntimeError("DERIV_APP_ID and a demo Deriv token are required")

    async def read_demo_accu_trades(self, minimum=50):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Deriv-App-ID": self.app_id,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            accounts_response = await client.get(
                f"{REST_BASE}/accounts",
                headers=headers,
            )
            accounts_response.raise_for_status()
            accounts = accounts_response.json().get("data") or []
            demo = next(
                (row for row in accounts
                 if row.get("account_type") == "demo"
                 and row.get("status") == "active"),
                None,
            )
            if not demo:
                raise RuntimeError("NO_ACTIVE_DERIV_DEMO_ACCOUNT")
            otp_response = await client.post(
                f"{REST_BASE}/accounts/{demo['account_id']}/otp",
                headers=headers,
            )
            otp_response.raise_for_status()
            ws_url = (otp_response.json().get("data") or {}).get("url")
        if not ws_url:
            raise RuntimeError("DERIV_OTP_URL_MISSING")

        async with websockets.connect(
            ws_url,
            open_timeout=15,
            close_timeout=5,
            ping_interval=20,
        ) as ws:
            trades = []
            seen_contract_ids = set()
            offset = 0
            while len(trades) < minimum:
                rid = offset + 100
                await ws.send(json.dumps({
                    "statement": 1,
                    "description": 1,
                    "limit": 100,
                    "offset": offset,
                    "req_id": rid,
                }))
                while True:
                    response = json.loads(await ws.recv())
                    if response.get("req_id") == rid:
                        break
                rows = (response.get("statement") or {}).get("transactions") or []
                if not rows:
                    break
                for row in rows:
                    if not is_accu_transaction(row):
                        continue
                    contract_id = row.get("contract_id")
                    if contract_id is None or str(contract_id) in seen_contract_ids:
                        continue
                    seen_contract_ids.add(str(contract_id))
                    crid = int(contract_id) + 100000
                    await ws.send(json.dumps({
                        "proposal_open_contract": 1,
                        "contract_id": int(contract_id),
                        "subscribe": 1,
                        "req_id": crid,
                    }))
                    while True:
                        detail = json.loads(await ws.recv())
                        if detail.get("req_id") == crid:
                            break
                    contract = detail.get("proposal_open_contract")
                    if isinstance(contract, dict) and is_settled_contract(contract):
                        trades.append({**row, "_contract": contract})
                        if len(trades) >= minimum:
                            break
                if len(rows) < 100:
                    break
                offset += 100
            return trades[:minimum]

    def compare(self, trades):
        mismatches = 0
        compared = 0
        results = []
        for trade in trades:
            contract = trade["_contract"]
            symbol = str(contract.get("underlying_symbol") or trade.get("underlying_symbol") or "")
            entry_epoch = int(
                contract.get("entry_spot_time")
                or contract.get("date_start")
                or contract.get("purchase_time")
                or trade.get("transaction_time")
                or 0
            )
            entry_quote = contract.get("entry_spot") or contract.get("buy_spot") or contract.get("spot")
            try:
                barrier_distance = read_barrier_distance(contract)
                barrier_reference_spot = read_barrier_reference_spot(contract)
                payout = read_payout(contract)
                growth = contract.get("growth_rate")
            except (KeyError, ValueError):
                continue
            if entry_quote is None or not growth:
                continue
            entry_quote = float(entry_quote)
            exit_epoch = int(contract.get("exit_spot_time") or contract.get("date_expiry") or entry_epoch)
            ticks = [
                tick for tick in self.tick_store.history(symbol)
                if entry_epoch <= int(tick["epoch"]) <= exit_epoch
            ]
            if not ticks or any(tick.get("has_gap") for tick in ticks):
                continue
            buy_price = _number(contract.get("buy_price"))
            if buy_price is None or buy_price <= 0:
                continue
            outcome = replay_ticks(
                ticks,
                entry_epoch,
                entry_quote,
                growth,
                barrier_distance,
                take_profit=contract.get("take_profit") or (
                    (contract.get("limit_order") or {}).get("take_profit")
                ),
                max_ticks=contract.get("tick_count"),
                barrier_reference_spot=barrier_reference_spot,
                stake=buy_price,
            )
            if outcome["outcome"] == "NO_TICKS":
                continue
            actual_profit_value = _number(contract.get("profit"))
            if actual_profit_value is None:
                continue
            actual_profit = actual_profit_value
            actual_outcome = "WIN" if actual_profit > 0 else "LOSS"
            simulated_outcome = "WIN" if outcome["outcome"] in {"TP", "MAX_TICKS"} else "LOSS"
            mismatch = actual_outcome != simulated_outcome
            mismatches += int(mismatch)
            compared += 1
            results.append({
                "symbol": symbol,
                "contract_id": trade.get("contract_id"),
                "entry_epoch": entry_epoch,
                "actual": actual_outcome,
                "simulated": simulated_outcome,
                "mismatch": mismatch,
                "provider_payout": payout,
                "actual_profit": actual_profit,
                "barrier_distance": barrier_distance,
                "barrier_reference_spot": barrier_reference_spot,
            })
        minimum_required = 50
        mismatch_rate = (
            mismatches / compared
            if compared >= minimum_required
            else None
        )
        status = (
            "INSUFFICIENT_DATA" if len(trades) < minimum_required or compared < minimum_required
            else "STOPPED" if mismatch_rate > 0.02
            else "CALIBRATED"
        )
        return {
            "minimum_required": minimum_required,
            "compared": compared,
            "mismatches": mismatches,
            "mismatch_rate": mismatch_rate,
            "stop": status == "STOPPED",
            "status": status,
            "results": results,
        }
