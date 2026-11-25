# V17.3 regression: health, autotrade status/settings/start/stop/trades, ML pipeline
import os
import time

import pytest
import requests
from pathlib import Path
from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("BACKEND_TEST_URL", "").rstrip("/") or "http://localhost:8001"
API_KEY = os.environ.get("BACKEND_API_KEY", "").strip() or str(
    dotenv_values(BACKEND_DIR / ".env").get("BACKEND_API_KEY") or ""
).strip()
REAL_KEY = "rk_Nw04tdJiI7gIgmA2D9RG8Odm2zD7e5TS"
H = {"X-API-Key": API_KEY, "Content-Type": "application/json"}
DEFAULTS = {"martingaleEnabled": False, "martingaleMultiplier": 2, "martingaleMaxSteps": 3, "minSignalConfidence": 0}
T = 60


def get(path, **kw):
    return requests.get(f"{BASE_URL}{path}", headers=H, timeout=T, **kw)


def post(path, body=None, headers=None):
    return requests.post(f"{BASE_URL}{path}", headers={**H, **(headers or {})}, json=body if body is not None else {}, timeout=T)


@pytest.fixture(scope="module", autouse=True)
def restore():
    yield
    post("/api/v1/autotrade/settings", DEFAULTS)
    if get("/api/v1/autotrade/status").json().get("running"):
        post("/api/v1/autotrade/stop")


# health
def test_health_version():
    r = requests.get(f"{BASE_URL}/api/health", timeout=T)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok" and d["version"] == "MasterV17.3"


# autotrade status shape
def test_status_shape():
    r = get("/api/v1/autotrade/status")
    assert r.status_code == 200
    d = r.json()
    assert d["running"] is False
    assert d["mode"] in {"demo", "real", "both"} and isinstance(d["modes"], list)
    assert {"demo", "real"} <= set(d["states"].keys())
    assert "accounts" in d
    s = d["settings"]
    assert s["minSignalConfidence"] == 0
    assert isinstance(s["martingaleEnabled"], bool)
    for k in ("martingaleMultiplier", "martingaleMaxSteps", "maxSignalLatencyMs"):
        assert k in s
    assert "distinctTokens" in d["credentials"]


# settings validation
@pytest.mark.parametrize("body", [
    {"martingaleEnabled": True, "martingaleMultiplier": 2.5, "martingaleMaxSteps": 4},
    {"minSignalConfidence": 0},
    {"minSignalConfidence": 80},
])
def test_settings_accept(body):
    r = post("/api/v1/autotrade/settings", body)
    assert r.status_code == 200, r.text
    d = r.json()
    for k, v in body.items():
        assert d[k] == v
    s = get("/api/v1/autotrade/status").json()["settings"]
    for k, v in body.items():
        assert s[k] == v


@pytest.mark.parametrize("body", [{"martingaleMultiplier": 9}, {"martingaleMaxSteps": 0}, {"minSignalConfidence": 120}])
def test_settings_reject(body):
    r = post("/api/v1/autotrade/settings", body)
    assert r.status_code == 422, r.text


def test_settings_restore_defaults():
    r = post("/api/v1/autotrade/settings", DEFAULTS)
    assert r.status_code == 200
    d = r.json()
    assert d["martingaleEnabled"] is False and d["martingaleMultiplier"] == 2 and d["martingaleMaxSteps"] == 3 and d["minSignalConfidence"] == 0


# real/both guards (never succeed)
def test_start_both_without_key_403():
    r = post("/api/v1/autotrade/start", {"mode": "both"})
    assert r.status_code == 403, r.text


def test_start_both_key_no_confirm_400():
    r = post("/api/v1/autotrade/start", {"mode": "both"}, headers={"X-Autotrade-Real-Key": REAL_KEY})
    assert r.status_code == 400, r.text
    assert "CONFIRMATION" in r.text


def test_start_real_without_key_403():
    r = post("/api/v1/autotrade/start", {"mode": "real", "confirmReal": True, "confirmText": "REAL"})
    assert r.status_code == 403


# demo start / stop
def test_demo_start_stop():
    r = post("/api/v1/autotrade/start", {"mode": "demo"})
    assert r.status_code == 200, r.text
    assert r.json()["running"] is True
    time.sleep(8)
    d = get("/api/v1/autotrade/status").json()
    print("phase", d["phase"], "account", d.get("account"), "accounts", d.get("accounts"))
    assert d["running"] is True
    assert d["phase"] in {"WAITING_SIGNAL", "IN_TRADE", "PLACING_TRADE"}
    assert "demo" in d["accounts"]
    assert float(d["account"]["balance"]) > 0
    r = post("/api/v1/autotrade/stop")
    assert r.status_code == 200
    for _ in range(15):
        d = get("/api/v1/autotrade/status").json()
        if not d["running"]:
            break
        time.sleep(1)
    assert d["running"] is False
    assert d["phase"] in {"IDLE", "STOPPED"}


# trades
@pytest.mark.parametrize("mode", ["both", "demo"])
def test_trades(mode):
    r = get("/api/v1/autotrade/trades", params={"mode": mode})
    assert r.status_code == 200
    assert isinstance(r.json()["items"], list)


# ML pipeline
def test_pipeline_status():
    d = get("/api/v1/ml/pipeline").json()
    assert d["parallelMarkets"] >= 1
    assert d["incrementalSeconds"] >= 300
    assert "runs" in d["incremental"] and "markets" in d["incremental"]
    assert d["dailyRetrainSeconds"] == 21600


def test_retrain_active():
    was_running = get("/api/v1/ml/pipeline").json()["running"]
    r = post("/api/v1/ml/retrain-active", {"hours": 24})
    assert r.status_code in (200, 409), r.text
    if r.status_code == 200 and not was_running:
        assert r.json()["phase"] == "RETRAIN_ACTIVE"
        s = post("/api/v1/ml/pipeline/stop")
        assert s.status_code == 200
