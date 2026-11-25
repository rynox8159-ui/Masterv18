# MasterV17.2 regression: health, brain API, autotrade settings/status/accounts, demo run, telemetry SSE
import json
import os
import time
import pytest
import requests
from pathlib import Path
from dotenv import dotenv_values

BASE_URL = os.environ.get("BACKEND_TEST_URL", "http://localhost:8001").rstrip("/")
BACKEND_DIR = Path(__file__).resolve().parents[1]
KEY = os.environ.get("BACKEND_API_KEY", "").strip() or str(
    dotenv_values(BACKEND_DIR / ".env").get("BACKEND_API_KEY") or ""
).strip()
H = {"X-API-Key": KEY, "Content-Type": "application/json"}
RESTORE = {"tradeTypeMode": "auto", "contractType": "CALL", "stakeMode": "manual", "baseStake": 1,
           "enabledGroups": ["RISEFALL", "DIGITS"], "minBrainConfidence": 62}


# Health
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok"
    assert d["version"] == "MasterV17.3"
    assert d["deriv"]["state"] == "DATA_RECEIVING"
    assert d["deriv"]["pairs"] > 0


# Brain API
def test_brain_signal():
    r = requests.get(f"{BASE_URL}/api/v1/brain/signal", params={"symbol": "R_100"}, headers=H, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["riseFall"]["direction"] in ("CALL", "PUT", "NO_SIGNAL")
    assert isinstance(d["digits"]["samples"], int)
    assert isinstance(d["candidates"], list) and d["candidates"]
    for c in d["candidates"]:
        for k in ("contractType", "confidence", "probability", "edge", "label"):
            assert k in c, k
        assert 0 <= c["confidence"] <= 99


def test_brain_requires_key():
    r = requests.get(f"{BASE_URL}/api/v1/brain/signal", params={"symbol": "R_100"}, timeout=15)
    assert r.status_code in (401, 403)


def test_brain_overview_sorted():
    r = requests.get(f"{BASE_URL}/api/v1/brain/overview", params={"limit": 5}, headers=H, timeout=30)
    assert r.status_code == 200, r.text
    d = r.json()
    items = d["items"]
    assert len(items) <= 5
    assert "tracked" in d
    confs = [(i.get("best") or {}).get("confidence", 0) for i in items]
    assert confs == sorted(confs, reverse=True)


def test_brain_learning():
    r = requests.get(f"{BASE_URL}/api/v1/brain/learning", headers=H, timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["items"], list)
    assert "phase2Types" in d


# Autotrade settings validation
class TestSettings:
    def teardown_class(cls):
        requests.post(f"{BASE_URL}/api/v1/autotrade/settings", headers=H, json=RESTORE, timeout=15)

    def test_valid_settings(self):
        p = {"tradeTypeMode": "manual", "contractType": "DIGITOVER", "digitBarrier": 3, "stakeMode": "auto",
             "autoStakePercent": 1, "minBrainConfidence": 62, "enabledGroups": ["RISEFALL"]}
        r = requests.post(f"{BASE_URL}/api/v1/autotrade/settings", headers=H, json=p, timeout=15)
        assert r.status_code == 200, r.text
        s = requests.get(f"{BASE_URL}/api/v1/autotrade/status", headers=H, timeout=15).json()["settings"]
        for k, v in p.items():
            assert s[k] == v, k

    @pytest.mark.parametrize("bad", [{"contractType": "FOO"}, {"digitBarrier": 12}, {"enabledGroups": []},
                                     {"tradeTypeMode": "x"}, {"digitBarrier": None}, {"minBrainConfidence": "abc"}])
    def test_invalid_settings(self, bad):
        r = requests.post(f"{BASE_URL}/api/v1/autotrade/settings", headers=H, json=bad, timeout=15)
        assert 400 <= r.status_code < 500, (bad, r.status_code, r.text)

    def test_restore(self):
        r = requests.post(f"{BASE_URL}/api/v1/autotrade/settings", headers=H, json=RESTORE, timeout=15)
        assert r.status_code == 200
        s = r.json()
        assert s["tradeTypeMode"] == "auto" and s["stakeMode"] == "manual" and s["contractType"] == "CALL"


# Autotrade status/accounts
def test_status():
    d = requests.get(f"{BASE_URL}/api/v1/autotrade/status", headers=H, timeout=15).json()
    assert d["settings"]["signalSource"] == "accumulator"
    assert "winRate" in d and (d["winRate"] is None or isinstance(d["winRate"], (int, float)))
    assert d["brain"]["enabled"] is True
    assert isinstance(d["brain"]["learning"], list)
    assert d["credentials"]["demoToken"] is True
    assert d["feedConnected"] is True


def test_accounts():
    r = requests.get(f"{BASE_URL}/api/v1/autotrade/accounts", headers=H, timeout=20)
    assert r.status_code == 200
    types = json.dumps(r.json()).lower()
    assert "demo" in types and "real" in types


def test_real_start_without_confirmation_rejected():
    r = requests.post(f"{BASE_URL}/api/v1/autotrade/start", headers=H, json={"mode": "real"}, timeout=15)
    assert r.status_code in (400, 401, 403, 422)


# Demo run (once)
def test_demo_run_and_stop():
    r = requests.post(f"{BASE_URL}/api/v1/autotrade/start", headers=H, json={"mode": "demo"}, timeout=30)
    try:
        assert r.status_code == 200, r.text
        time.sleep(40)
        d = requests.get(f"{BASE_URL}/api/v1/autotrade/status", headers=H, timeout=15).json()
        print("STATUS phase:", d.get("phase"), "running:", d.get("running"), "msg:", d.get("message") or d.get("lastMessage"))
        assert d["running"] is True or "loss" in json.dumps(d).lower()
        assert d.get("phase") in ("STARTING", "WAITING_SIGNAL", "PLACING_TRADE", "IN_TRADE", "COOLDOWN", "STOPPED")
        t = requests.get(f"{BASE_URL}/api/v1/autotrade/trades", params={"mode": "demo", "limit": 5}, headers=H, timeout=15).json()
        items = t.get("items", t) if isinstance(t, dict) else t
        print("TRADES:", json.dumps(items)[:800])
        for tr in items:
            if tr.get("source") == "brain":
                assert tr.get("label") and tr.get("contractType")
    finally:
        s = requests.post(f"{BASE_URL}/api/v1/autotrade/stop", headers=H, json={}, timeout=30)
        assert s.status_code == 200
        time.sleep(2)
        d = requests.get(f"{BASE_URL}/api/v1/autotrade/status", headers=H, timeout=15).json()
        assert d["running"] is False


# Telemetry SSE
def test_telemetry_sse():
    with requests.get(f"{BASE_URL}/api/telemetry/stream", params={"source": "deriv", "symbol": "R_100", "timeframe": "1m"},
                      headers={"X-API-Key": KEY}, stream=True, timeout=40) as r:
        assert r.status_code == 200
        event, data = None, None
        for line in r.iter_lines(decode_unicode=True):
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "telemetry":
                data = json.loads(line.split(":", 1)[1])
                break
        assert data is not None
        assert isinstance(data["evidence"]["brain"], bool)
        assert data["evidence"]["brainPick"] is None or isinstance(data["evidence"]["brainPick"], dict)
        assert len(data["candles"]) > 50
        assert isinstance(data["price"], (int, float))
