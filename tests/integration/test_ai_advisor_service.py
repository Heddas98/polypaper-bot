"""
P1-02-e (2026-05-11) Wave 1 — AI Advisor service smoke test.

Uses fastapi.TestClient to exercise the service in-process — no separate
uvicorn process needed for unit-level verification.

Marked `integration` so it's excluded from the sandbox-default run; Heddas
Windows side picks it up in the full pytest pass.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

try:
    from fastapi.testclient import TestClient
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False


@pytest.fixture
def client():
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.app import app
    return TestClient(app)


def test_health_returns_200_with_expected_keys(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "uptime_s" in data
    assert "llm_clients" in data
    assert data["wave"] == 1
    # llm_clients keys: anthropic + groq + openrouter
    assert "anthropic" in data["llm_clients"]
    assert "groq" in data["llm_clients"]
    assert "openrouter" in data["llm_clients"]
    # Each value is "configured" or "missing"
    for v in data["llm_clients"].values():
        assert v in ("configured", "missing")


def test_stats_returns_200(client):
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.json()
    assert data["wave"] == 1
    assert "request_count" in data
    assert "uptime_s" in data


def test_suggest_stub_returns_hold(client):
    payload = {
        "market": {
            "slug": "btc-up-or-down-on-may-11-2026",
            "asset": "BTC",
            "timeframe": "24h",
            "up_odds": 0.55,
            "down_odds": 0.45,
        },
        "strategy": {
            "label": "M_BTC_5m_any_0.92",
            "asset": "BTC",
            "timeframe": "5m",
            "threshold": 0.92,
            "recent_trades": 35,
            "recent_wins": 31,
            "recent_pnl": 12.50,
        },
        "correlation_id": "test-001",
        "cycle": 42,
    }
    r = client.post("/suggest", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["stub_mode"] is True
    assert data["model_used"] is None
    assert len(data["suggestions"]) == 1
    sug = data["suggestions"][0]
    assert sug["action"] == "HOLD"
    assert sug["confidence"] == 0.0
    assert "stub Wave 1" in sug["reason"]
    assert "M_BTC_5m_any_0.92" in sug["reason"]
    assert sug["payload"]["correlation_id"] == "test-001"


def test_suggest_validates_slug_required(client):
    # Empty slug → pydantic min_length=1 fails → 422
    payload = {"market": {"slug": "", "asset": "BTC", "timeframe": "5m"}}
    r = client.post("/suggest", json=payload)
    assert r.status_code == 422


def test_suggest_invalid_odds_rejected(client):
    payload = {
        "market": {
            "slug": "btc-foo",
            "asset": "BTC",
            "timeframe": "5m",
            "up_odds": 1.5,  # >1.0, pydantic le=1.0 rejects
        },
    }
    r = client.post("/suggest", json=payload)
    assert r.status_code == 422


def test_request_counter_increments(client):
    """Two consecutive /suggest calls bump the counter."""
    payload = {
        "market": {"slug": "test-slug", "asset": "BTC", "timeframe": "5m"},
    }
    r1 = client.post("/suggest", json=payload)
    assert r1.status_code == 200
    count1 = r1.json()["suggestions"][0]["payload"]["request_count"]
    r2 = client.post("/suggest", json=payload)
    count2 = r2.json()["suggestions"][0]["payload"]["request_count"]
    assert count2 == count1 + 1
