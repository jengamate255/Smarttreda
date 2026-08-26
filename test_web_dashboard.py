"""Smoke tests for the dashboard API (item #14).

Run: python -m pytest test_web_dashboard.py -v
These hit the real FastAPI app without starting uvicorn. Endpoints that
require a live MT5 terminal are skipped when no terminal responds.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mt5_mcp"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
os.environ.setdefault("MT5_MCP_ACCOUNT", "PROP")

TOKEN = os.getenv("WEB_AUTH_TOKEN", "")
HEADERS = {"X-Auth-Token": TOKEN} if TOKEN else {}

import pytest
from fastapi.testclient import TestClient

from web_dashboard import app

client = TestClient(app)


def _mt5_up() -> bool:
    try:
        from client import get_client

        get_client().ensure_connected()
        return True
    except Exception:
        return False


MT5 = pytest.mark.skipif(not _mt5_up(), reason="MT5 terminal not connected")


# ------------------------------------------------------------------ auth/authz

def test_api_requires_token_when_configured():
    if not TOKEN:
        pytest.skip("WEB_AUTH_TOKEN not set")
    r = client.get("/api/account")
    assert r.status_code == 401


def test_static_not_gated():
    r = client.get("/static/index.html")
    assert r.status_code == 200


@MT5
def test_account_shape():
    r = client.get("/api/account", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "login" in body and "balance" in body


def test_accounts_list():
    r = client.get("/api/accounts", headers=HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_switch_unknown_login_404():
    r = client.post("/api/account/switch", json={"login": 1}, headers=HEADERS)
    assert r.status_code == 404


def test_signals_forex_disabled():
    r = client.get("/api/signals?symbol=EURUSD", headers=HEADERS)
    body = r.json()
    assert body["enabled"] is False


@MT5
def test_ai_detection_shape():
    r = client.get("/api/ai-detection?symbol=EURUSD&timeframe=H1", headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert "patterns" in body and "support_resistance" in body


def test_journal_equity_curve_contract():
    r = client.get("/api/journal/equity-curve?days=7", headers=HEADERS)
    body = r.json()
    assert "error" in body or (
        isinstance(body.get("points"), list)
        and isinstance(body.get("net_pnl"), (int, float))
    )


def test_journal_distribution_contract():
    r = client.get("/api/journal/distribution?days=7", headers=HEADERS)
    body = r.json()
    assert "error" in body or ("pnl_histogram" in body and "total_trades" in body)


def test_backtests_router():
    r = client.get("/api/backtests", headers=HEADERS)
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert {"month", "forward", "all"} <= set(groups)


def test_alerts_crud_roundtrip():
    r = client.post(
        "/api/alerts",
        json={"symbol": "EURUSD", "price": 0.01, "condition": "below", "name": "t"},
        headers=HEADERS,
    )
    assert r.status_code == 200
    alert_id = r.json()["alert"]["id"]
    tog = client.post(f"/api/alerts/{alert_id}/toggle", headers=HEADERS)
    assert tog.status_code == 200 and tog.json()["alert"]["active"] is False
    dele = client.delete(f"/api/alerts/{alert_id}", headers=HEADERS)
    assert dele.status_code == 200


def test_client_error_accepts_batch():
    r = client.post(
        "/api/client-error",
        json={"errors": [{"sessionId": "t", "message": "m", "source": "x", "lineno": 1}]},
        headers=HEADERS,
    )
    assert r.status_code == 200 and r.json()["received"] == 1
