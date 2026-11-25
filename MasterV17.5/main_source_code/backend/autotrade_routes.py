"""Auto-trade control API used by the /autotrade page."""
import hmac
import os
import sys
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from auto_trade.dashboard_engine import AutoTradeEngine  # noqa: E402
from auto_trade.dashboard_execution import DerivRequestError, list_accounts  # noqa: E402


class StartBody(BaseModel):
    mode: Literal["demo", "real", "both"] = "demo"
    confirmReal: bool = False
    confirmText: str = ""


class EvaluateBody(BaseModel):
    symbol: str
    mode: Literal["demo", "real"] = "demo"
    llm: bool = False


class CredentialsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appId: str | None = Field(default=None, max_length=20)
    demoToken: str | None = Field(default=None, max_length=256)
    realToken: str | None = Field(default=None, max_length=256)


def autotrade_router(engine: AutoTradeEngine):
    router = APIRouter(prefix="/api/v1/autotrade", tags=["auto-trade"])

    @router.get("/status")
    async def status():
        return await engine.status()

    @router.get("/accounts")
    async def accounts():
        creds = engine.credentials()
        if not creds["app_id"] or not creds["demo"]:
            raise HTTPException(400, "Enter Deriv credentials in Auto-trade settings or configure them in backend .env")
        try:
            rows = await list_accounts(creds["app_id"], creds["demo"] or creds["real"])
        except DerivRequestError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"items": [{"accountId": r.get("account_id"), "type": r.get("account_type"), "balance": float(r.get("balance") or 0), "currency": r.get("currency"), "status": r.get("status")} for r in rows]}

    @router.post("/credentials")
    async def credentials(body: CredentialsBody):
        changes = {
            "app_id": body.appId,
            "demo": body.demoToken,
            "real": body.realToken,
        }
        try:
            return engine.update_credentials({key: value for key, value in changes.items() if value is not None})
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/settings")
    async def settings(body: dict):
        try:
            return await engine.update_settings(body)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/start")
    async def start(body: StartBody, request: Request):
        real_involved = body.mode in {"real", "both"}
        if real_involved:
            configured_key = os.environ.get("AUTOTRADE_WEB_REAL_CONTROL_KEY", "").strip()
            if (
                os.environ.get("AUTOTRADE_WEB_LIVE_ENABLED", "").strip().lower() != "true"
                or not configured_key
            ):
                raise HTTPException(403, "WEB_REAL_TRADING_DISABLED")
            supplied_key = request.headers.get("x-autotrade-real-key", "").strip()
            if not hmac.compare_digest(supplied_key, configured_key):
                raise HTTPException(403, "AUTOTRADE_REAL_CONTROL_KEY_REQUIRED")
        if real_involved and not (body.confirmReal and body.confirmText.strip().upper() == "REAL"):
            raise HTTPException(400, "REAL_MODE_CONFIRMATION_REQUIRED")
        if real_involved:
            settings = engine.settings
            problems = []
            if float(settings.get("dailyLossLimit", 0)) <= 0:
                problems.append("dailyLossLimit")
            if float(settings.get("maxStake", 0)) <= 0:
                problems.append("maxStake")
            if int(settings.get("maxConsecutiveLosses", 0)) <= 0:
                problems.append("maxConsecutiveLosses")
            if int(settings.get("maxDailyTrades", 0)) <= 0:
                problems.append("maxDailyTrades")
            if problems:
                raise HTTPException(400, f"REAL_SAFETY_LIMITS_REQUIRED: set {', '.join(problems)} in Settings before real trading")
        try:
            await engine.start(body.mode)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return await engine.status()

    @router.post("/stop")
    async def stop():
        await engine.stop()
        return await engine.status()

    @router.post("/kill")
    async def kill():
        result = await engine.kill()
        return {**(await engine.status()), "kill": result}

    @router.post("/reset-cycle")
    async def reset_cycle(mode: Literal["demo", "real", "both"] = "demo"):
        return await engine.reset_cycle(mode)

    @router.get("/trades")
    async def trades(mode: Literal["demo", "real", "both"] = "demo", limit: int = 50, status: str | None = None):
        query = {"mode": {"$in": ["demo", "real"]} if mode == "both" else mode}
        if status == "open":
            query["status"] = {"$in": ["OPEN", "PENDING_ORDER"]}
        elif status == "closed":
            query["status"] = {"$nin": ["OPEN", "PENDING_ORDER"]}
        rows = await engine.db.autotrade_trades.find(query, {"_id": 0}).sort("openedAt", -1).limit(max(1, min(limit, 500))).to_list(500)
        return {"items": rows}

    @router.post("/evaluate")
    async def evaluate(body: EvaluateBody):
        try:
            verdict = await engine.evaluate_symbol(body.mode, body.symbol)
        except DerivRequestError as exc:
            raise HTTPException(502, str(exc)) from exc
        return verdict

    @router.post("/scan")
    async def scan(mode: Literal["demo", "real"] = "demo"):
        verdicts = await engine.scan(mode)
        return {"items": engine.last_scan, "at": engine.last_scan_at, "approved": sum(v["approved"] for v in verdicts)}

    return router
