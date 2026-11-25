"""Auto-trade API regression: both supported Deriv signal profiles can be selected."""
import os
from pathlib import Path

import requests
from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("BACKEND_TEST_URL", "http://localhost:8001").rstrip("/")
API_KEY = os.environ.get("BACKEND_API_KEY", "").strip() or str(
    dotenv_values(BACKEND_DIR / ".env").get("BACKEND_API_KEY") or ""
).strip()
HEADERS = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def test_autotrade_supports_accumulator_and_binary_signals():
    response = requests.get(f"{BASE_URL}/api/v1/autotrade/status", headers=HEADERS, timeout=20)
    assert response.status_code == 200, response.text
    assert response.json()["settings"]["signalSource"] == "accumulator"

    for source in ("accumulator", "binary"):
        response = requests.post(
            f"{BASE_URL}/api/v1/autotrade/settings",
            headers=HEADERS,
            json={"signalSource": source},
            timeout=20,
        )
        assert response.status_code == 200, response.text
        assert response.json()["signalSource"] == source

    response = requests.post(
        f"{BASE_URL}/api/v1/autotrade/settings",
        headers=HEADERS,
        json={"signalSource": "brain"},
        timeout=20,
    )
    assert 400 <= response.status_code < 500, response.text

    requests.post(
        f"{BASE_URL}/api/v1/autotrade/settings",
        headers=HEADERS,
        json={"signalSource": "accumulator"},
        timeout=20,
    )
