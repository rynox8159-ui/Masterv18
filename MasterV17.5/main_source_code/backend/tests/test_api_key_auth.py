import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from api_key_auth import (
    API_KEY,
    SESSION_COOKIE,
    api_key_middleware,
    auth_router,
    create_session_token,
    session_token_is_valid,
    configured_credential,
)


CREDENTIAL = "server-only-test-credential-0123456789abcdef"


def make_client():
    app = FastAPI()
    app.middleware("http")(api_key_middleware)
    app.include_router(auth_router())

    @app.get("/api/private")
    async def private():
        return {"ok": True}

    return TestClient(app)


def test_session_token_is_signed_and_expires():
    token = create_session_token(CREDENTIAL, now=1000)
    assert session_token_is_valid(token, CREDENTIAL, now=1001)
    assert not session_token_is_valid(token, "different-server-credential-0123456789", now=1001)
    assert not session_token_is_valid(token, CREDENTIAL, now=1000 + 8 * 60 * 60 + 1)
    assert not session_token_is_valid(f"{token}x", CREDENTIAL, now=1001)


def test_api_key_is_importable_without_configured_credential(monkeypatch):
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    assert isinstance(API_KEY, str)
    assert configured_credential() == ""


def test_protected_routes_fail_closed_without_server_credential(monkeypatch):
    monkeypatch.delenv("BACKEND_API_KEY", raising=False)
    response = make_client().get("/api/private")
    assert response.status_code == 503
    assert response.json()["detail"] == "AUTH_NOT_CONFIGURED"


def test_session_login_sets_httponly_cookie_and_logout_revokes_browser_session(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", CREDENTIAL)
    client = make_client()

    rejected = client.post("/api/v1/auth/session", json={"credential": "wrong-credential-value-0123456789"})
    assert rejected.status_code == 401
    assert not client.cookies.get(SESSION_COOKIE)
    assert client.get("/api/private").status_code == 401

    accepted = client.post("/api/v1/auth/session", json={"credential": CREDENTIAL})
    assert accepted.status_code == 200
    cookie = accepted.headers["set-cookie"]
    assert SESSION_COOKIE in cookie and "httponly" in cookie.lower()
    assert "max-age=28800" in cookie.lower()
    assert client.get("/api/private").json() == {"ok": True}

    logout = client.delete("/api/v1/auth/session")
    assert logout.status_code == 200
    assert client.get("/api/private").status_code == 401


def test_bootstrap_route_is_not_registered(monkeypatch):
    monkeypatch.setenv("BACKEND_API_KEY", CREDENTIAL)
    response = make_client().get("/api/v1/auth/bootstrap")
    assert response.status_code in {401, 404}
    assert "apiKey" not in response.text
