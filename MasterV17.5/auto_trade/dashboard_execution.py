"""Deriv Options API client (new api.derivws.com platform: PAT token + App ID -> OTP -> WebSocket)."""
import asyncio
import inspect
import json
import logging
import time

import httpx
import websockets

logger = logging.getLogger(__name__)

REST_BASE = "https://api.derivws.com/trading/v1/options"
PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"


class DerivRequestError(RuntimeError):
    pass


def _headers(app_id, token):
    return {"Authorization": f"Bearer {token}", "Deriv-App-ID": app_id, "Content-Type": "application/json"}


async def list_accounts(app_id, token):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{REST_BASE}/accounts", headers=_headers(app_id, token))
    if response.status_code >= 400:
        raise DerivRequestError(f"ACCOUNTS_HTTP_{response.status_code}: {response.text[:200]}")
    return response.json().get("data") or []


async def request_otp_url(app_id, token, account_id):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(f"{REST_BASE}/accounts/{account_id}/otp", headers=_headers(app_id, token))
    if response.status_code >= 400:
        raise DerivRequestError(f"OTP_HTTP_{response.status_code}: {response.text[:200]}")
    url = (response.json().get("data") or {}).get("url")
    if not url:
        raise DerivRequestError("OTP response did not contain a websocket URL")
    return url


class DerivConnection:
    """One websocket with a background reader that routes responses by req_id."""

    def __init__(self, url):
        self.url = url
        self.ws = None
        self.reader = None
        self.pinger = None
        self.queues = {}
        self.request_id = 0

    async def open(self):
        self.ws = await websockets.connect(self.url, open_timeout=15, close_timeout=5, ping_interval=20, ping_timeout=20, max_size=4_000_000)
        self.reader = asyncio.create_task(self._read())
        self.pinger = asyncio.create_task(self._ping())

    async def _ping(self):
        # Deriv closes sockets after 120s without a client request.
        while True:
            await asyncio.sleep(12)
            try:
                await self.ws.send(json.dumps({"ping": 1}))
            except (websockets.exceptions.WebSocketException, AttributeError):
                return

    @property
    def closed(self):
        return self.ws is None or self.reader is None or self.reader.done()

    async def _read(self):
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                queue = self.queues.get(message.get("req_id"))
                if queue is not None:
                    queue.put_nowait(message)
        except websockets.exceptions.WebSocketException as exc:
            logger.warning("Deriv websocket closed: %s", exc)
        finally:
            for queue in list(self.queues.values()):
                queue.put_nowait({"error": {"code": "DISCONNECTED", "message": "Deriv connection closed"}})

    def _register(self):
        self.request_id += 1
        queue = asyncio.Queue()
        self.queues[self.request_id] = queue
        return self.request_id, queue

    async def request(self, payload, timeout=15):
        rid, queue = self._register()
        try:
            await self.ws.send(json.dumps({**payload, "req_id": rid}))
            message = await asyncio.wait_for(queue.get(), timeout=timeout)
        finally:
            self.queues.pop(rid, None)
        if message.get("error"):
            error = message["error"]
            raise DerivRequestError(f"{error.get('code', 'DERIV_ERROR')}: {error.get('message', 'request rejected')}")
        return message

    async def subscribe(self, payload):
        rid, queue = self._register()
        await self.ws.send(json.dumps({**payload, "subscribe": 1, "req_id": rid}))
        return rid, queue

    def release(self, rid):
        self.queues.pop(rid, None)

    async def close(self):
        if self.ws is not None:
            await self.ws.close()
        if self.reader is not None:
            self.reader.cancel()
        if self.pinger is not None:
            self.pinger.cancel()
        self.ws = None


class DerivExecutor:
    """Authenticated Deriv account (demo or real) used for proposals, buys and contract tracking."""

    def __init__(self, app_id, token, account_mode):
        if account_mode not in {"demo", "real"}:
            raise ValueError("account_mode must be 'demo' or 'real'")
        self.app_id = app_id
        self.token = token
        self.account_mode = account_mode
        self.account_id = None
        self.currency = "USD"
        self.balance = None
        self.conn = None

    async def connect(self):
        accounts = await list_accounts(self.app_id, self.token)
        account = next((a for a in accounts if a.get("account_type") == self.account_mode and a.get("status") == "active"), None)
        if not account:
            raise DerivRequestError(f"No active Deriv {self.account_mode} options account for this token")
        self.account_id = account["account_id"]
        self.currency = account.get("currency") or "USD"
        url = await request_otp_url(self.app_id, self.token, self.account_id)
        self.conn = DerivConnection(url)
        await self.conn.open()
        await self.refresh_balance()
        logger.info("Deriv %s account %s connected (balance %.2f %s)", self.account_mode, self.account_id, self.balance, self.currency)
        return self

    async def ensure(self):
        if self.conn is None or self.conn.closed:
            if self.conn is not None:
                await self.conn.close()
            await self.connect()

    async def refresh_balance(self):
        response = await self.conn.request({"balance": 1})
        balance = response.get("balance") or {}
        self.balance = float(balance.get("balance") or 0)
        self.currency = str(balance.get("currency") or self.currency)
        return self.balance

    async def _request(self, payload, *, timeout=None):
        method = self.conn.request
        if timeout is None:
            try:
                return await method(payload)
            except TypeError as exc:
                if "unexpected keyword argument 'timeout'" not in str(exc):
                    raise
                return await method(payload)
        try:
            if "timeout" in inspect.signature(method).parameters:
                return await method(payload, timeout=timeout)
        except (TypeError, ValueError):
            pass
        try:
            return await method(payload)
        except TypeError as exc:
            if "timeout" not in str(exc):
                raise
            return await method(payload)

    async def proposal(self, symbol, direction, stake, duration, duration_unit="m", barrier=None, multiplier=None, growth_rate=None, limit_order=None):
        payload = {
            "proposal": 1,
            "amount": round(float(stake), 2),
            "basis": "stake",
            "contract_type": direction,
            "currency": self.currency,
            "underlying_symbol": symbol,
        }
        if direction == "ACCU":
            if growth_rate is None:
                raise ValueError("Accumulator proposals require growth_rate")
            payload["growth_rate"] = float(growth_rate)
            take_profit = (limit_order or {}).get("take_profit")
            if take_profit is not None:
                payload["limit_order"] = {"take_profit": float(take_profit)}
        elif multiplier is not None:
            payload["multiplier"] = int(multiplier)
        else:
            payload["duration"] = int(duration)
            payload["duration_unit"] = duration_unit
        if barrier is not None:
            payload["barrier"] = str(barrier)
        response = await self._request(payload)
        proposal = response.get("proposal")
        if not isinstance(proposal, dict) or not proposal.get("id"):
            raise DerivRequestError("Deriv returned a malformed proposal")
        return proposal

    async def buy(self, proposal):
        response = await self._request({"buy": proposal["id"], "price": float(proposal["ask_price"])}, timeout=20)
        contract = response.get("buy") or {}
        if not contract.get("contract_id"):
            raise DerivRequestError("Deriv buy response is missing its contract ID")
        return contract

    async def buy_parameters(self, symbol, direction, stake, duration, duration_unit="m", barrier=None, multiplier=None, growth_rate=None, limit_order=None):
        """V17.3: one-shot buy with the full contract description (no proposal id that can expire while we wait for the entry epoch)."""
        parameters = {
            "amount": round(float(stake), 2), "basis": "stake", "contract_type": direction, "currency": self.currency,
            "underlying_symbol": symbol,
        }
        if direction == "ACCU":
            if growth_rate is None:
                raise ValueError("Accumulator buys require growth_rate")
            parameters["growth_rate"] = float(growth_rate)
            take_profit = (limit_order or {}).get("take_profit")
            if take_profit is not None:
                parameters["limit_order"] = {"take_profit": float(take_profit)}
        elif multiplier is not None:
            parameters["multiplier"] = int(multiplier)
        else:
            parameters["duration"] = int(duration)
            parameters["duration_unit"] = duration_unit
        if barrier is not None:
            parameters["barrier"] = str(barrier)
        response = await self._request({"buy": 1, "price": round(float(stake), 2), "parameters": parameters}, timeout=20)
        contract = response.get("buy") or {}
        if not contract.get("contract_id"):
            raise DerivRequestError("Deriv buy response is missing its contract ID")
        return contract

    async def sell(self, contract_id):
        response = await self._request({"sell": int(contract_id), "price": 0}, timeout=20)
        return response.get("sell") or {}

    async def wait_contract(self, contract_id, timeout):
        rid, queue = await self.conn.subscribe({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        deadline = time.time() + timeout
        contract = {}
        try:
            while not contract.get("is_sold"):
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise DerivRequestError("Timed out waiting for contract settlement")
                message = await asyncio.wait_for(queue.get(), timeout=remaining)
                if message.get("error"):
                    error = message["error"]
                    raise DerivRequestError(f"{error.get('code')}: {error.get('message')}")
                contract = message.get("proposal_open_contract") or contract
        finally:
            self.conn.release(rid)
        if "profit" in contract:
            profit = float(contract["profit"])
        else:
            profit = float(contract.get("sell_price") or 0) - float(contract.get("buy_price") or 0)
        return {**contract, "profit": profit}

    async def wait_accumulator(self, contract_id, max_hold_seconds, max_hold_ticks=5, now_fn=time.time):
        """Monitor Deriv-managed ACCU take profit and enforce tick-count and time caps."""
        await self.ensure()
        rid, queue = await self.conn.subscribe({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        deadline = now_fn() + max(1.0, float(max_hold_seconds))
        next_sell_attempt = 0.0
        contract = {}
        try:
            while True:
                remaining = deadline - now_fn()
                timeout = min(5.0, max(0.2, remaining)) if remaining > 0 else 1.0
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    message = {}
                if message.get("error"):
                    error = message["error"]
                    raise DerivRequestError(f"{error.get('code', 'DERIV_ERROR')}: {error.get('message', 'request rejected')}")
                contract = message.get("proposal_open_contract") or contract
                if contract.get("is_sold"):
                    return {**contract, "profit": self._contract_profit(contract), "exitReason": "DERIV_LIMIT_OR_SETTLEMENT"}
                ticks_held = int(contract.get("tick_count") or 0)
                exit_reason = (
                    "MAX_HOLD_TICKS" if ticks_held >= max(1, int(max_hold_ticks))
                    else "MAX_HOLD_TIME" if now_fn() >= deadline
                    else None
                )
                if exit_reason and now_fn() >= next_sell_attempt:
                    try:
                        sold = await self.sell(contract_id)
                        sold_for = sold.get("sold_for", sold.get("sell_price"))
                        if sold_for is None:
                            raise DerivRequestError("Accumulator sell response is missing its sale price")
                        buy_price = float(sold.get("buy_price") or contract.get("buy_price") or 0)
                        return {**contract, **sold, "profit": round(float(sold_for) - buy_price, 2), "exitReason": exit_reason}
                    except (DerivRequestError, OSError, asyncio.TimeoutError) as exc:
                        logger.warning("Accumulator max-hold sell rejected for contract %s; retrying: %s", contract_id, exc)
                        next_sell_attempt = now_fn() + 5.0
        finally:
            self.conn.release(rid)

    async def wait_multiplier(self, contract_id, close_epoch, stake, stop_loss_percent, take_profit_percent, now_fn=time.time):
        """Monitor a multiplier and sell at signal expiry or configured stake-relative risk thresholds."""
        await self.ensure()
        rid, queue = await self.conn.subscribe({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        contract = {}
        exit_reason = "SIGNAL_EXPIRY"
        try:
            while True:
                remaining = float(close_epoch) - now_fn()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=min(10.0, max(0.2, remaining)))
                except asyncio.TimeoutError:
                    continue
                if message.get("error"):
                    error = message["error"]
                    raise DerivRequestError(f"{error.get('code', 'DERIV_ERROR')}: {error.get('message', 'request rejected')}")
                contract = message.get("proposal_open_contract") or contract
                if contract.get("is_sold"):
                    profit = float(contract.get("profit") or 0.0)
                    return {**contract, "profit": profit, "exitReason": "DERIV_SETTLEMENT"}
                floating_profit = float(contract.get("profit") or 0.0)
                if stop_loss_percent > 0 and floating_profit <= -float(stake) * float(stop_loss_percent) / 100:
                    exit_reason = "STOP_LOSS"
                    break
                if take_profit_percent > 0 and floating_profit >= float(stake) * float(take_profit_percent) / 100:
                    exit_reason = "TAKE_PROFIT"
                    break

            sold = await self.sell(contract_id)
            sold_for = sold.get("sold_for", sold.get("sell_price"))
            if sold_for is None:
                raise DerivRequestError("Multiplier sell response is missing its sale price")
            buy_price = float(sold.get("buy_price") or contract.get("buy_price") or stake)
            return {**contract, **sold, "profit": round(float(sold_for) - buy_price, 2), "exitReason": exit_reason}
        finally:
            self.conn.release(rid)

    async def wait_rise_fall(
        self,
        contract_id,
        close_epoch,
        stake,
        stop_loss_percent,
        take_profit_percent,
        trailing_enabled=False,
        trailing_pullback_percent=5.0,
        now_fn=time.time,
    ):
        """Monitor a Rise/Fall contract and opportunistically sell at deterministic thresholds.

        A rejected early sell is non-fatal: the same open-contract stream remains
        active and the contract is allowed to settle normally at expiry.
        """
        await self.ensure()
        rid, queue = await self.conn.subscribe({"proposal_open_contract": 1, "contract_id": int(contract_id)})
        contract = {}
        peak_profit = None
        trailing_active = False
        exit_reason = None
        try:
            while True:
                remaining = float(close_epoch) - now_fn()
                if remaining <= 0:
                    break
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=min(10.0, max(0.2, remaining)))
                except asyncio.TimeoutError:
                    continue
                if message.get("error"):
                    error = message["error"]
                    raise DerivRequestError(f"{error.get('code', 'DERIV_ERROR')}: {error.get('message', 'request rejected')}")
                contract = message.get("proposal_open_contract") or contract
                if contract.get("is_sold"):
                    return {**contract, "profit": self._contract_profit(contract), "exitReason": "DERIV_SETTLEMENT"}
                if "profit" not in contract:
                    continue
                floating_profit = float(contract["profit"])
                stake_value = max(float(stake), 0.01)
                if stop_loss_percent > 0 and floating_profit <= -stake_value * float(stop_loss_percent) / 100:
                    exit_reason = "STOP_LOSS"
                elif not trailing_active and take_profit_percent > 0 and floating_profit >= stake_value * float(take_profit_percent) / 100:
                    if trailing_enabled and trailing_pullback_percent > 0:
                        trailing_active = True
                        peak_profit = floating_profit
                        logger.info("Rise/Fall contract %s trailing profit armed at %.2f", contract_id, floating_profit)
                        continue
                    exit_reason = "TAKE_PROFIT"
                elif trailing_active:
                    peak_profit = max(float(peak_profit or floating_profit), floating_profit)
                    pullback = float(peak_profit) * float(trailing_pullback_percent) / 100
                    if floating_profit <= float(peak_profit) - pullback:
                        exit_reason = "TRAILING_PROFIT"
                if exit_reason:
                    try:
                        sold = await self.sell(contract_id)
                        sold_for = sold.get("sold_for", sold.get("sell_price"))
                        if sold_for is None:
                            raise DerivRequestError("Early sell response is missing its sale price")
                        buy_price = float(sold.get("buy_price") or contract.get("buy_price") or stake)
                        return {
                            **contract, **sold,
                            "profit": round(float(sold_for) - buy_price, 2),
                            "exitReason": exit_reason,
                        }
                    except (DerivRequestError, OSError, asyncio.TimeoutError) as exc:
                        logger.warning("Early Rise/Fall sell rejected for contract %s; waiting for expiry: %s", contract_id, exc)
                        exit_reason = None
                        if trailing_active:
                            continue
            return await self.wait_contract(contract_id, max(30.0, float(close_epoch) - now_fn() + 30.0))
        finally:
            self.conn.release(rid)

    @staticmethod
    def _contract_profit(contract):
        if "profit" in contract:
            return float(contract["profit"])
        return float(contract.get("sell_price") or 0) - float(contract.get("buy_price") or 0)

    async def close(self):
        if self.conn is not None:
            await self.conn.close()
            self.conn = None


class PublicFeed:
    """Shared unauthenticated connection used for candle history."""

    def __init__(self):
        self.conn = None
        self.lock = asyncio.Lock()

    async def _ensure(self):
        async with self.lock:
            if self.conn is None or self.conn.closed:
                self.conn = DerivConnection(PUBLIC_WS)
                await self.conn.open()

    async def candles(self, symbol, granularity, count=200):
        await self._ensure()
        response = await self.conn.request({"ticks_history": symbol, "style": "candles", "granularity": int(granularity), "count": int(count), "end": "latest"}, timeout=20)
        return [{"epoch": int(c["epoch"]), "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]), "close": float(c["close"])} for c in response.get("candles") or []]

    async def close(self):
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
