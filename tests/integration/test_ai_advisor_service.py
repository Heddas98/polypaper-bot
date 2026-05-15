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


# ── P1-02 Wave 2a (2026-05-11) — prompts + ModelRouter extracted ─────


def test_prompts_module_importable():
    """Wave 2a: prompts moved from core/ai_brain.py to services/ai_advisor/."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.prompts import (
        BRAIN_SYSTEM,
        MISTAKE_SYSTEM,
        TRADE_SYSTEM,
    )

    # Sanity: non-empty strings, BRAIN_SYSTEM has the expected fingerprint.
    assert isinstance(BRAIN_SYSTEM, str) and len(BRAIN_SYSTEM) > 1000
    assert "PolyPaper Bot" in BRAIN_SYSTEM
    assert "AKSIYON TIPLERI" in BRAIN_SYSTEM
    assert isinstance(TRADE_SYSTEM, str) and len(TRADE_SYSTEM) > 10
    assert isinstance(MISTAKE_SYSTEM, str) and "mistake_type" in MISTAKE_SYSTEM


def test_router_module_importable():
    """Wave 2a: ModelRouter moved from core/ai_brain.py to services/ai_advisor/."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.router import ModelRouter

    # Routing dispatch + default fallback are intact.
    assert ModelRouter.get("brain_cycle") == ("claude", "claude-sonnet-4-6")
    assert ModelRouter.get("market_scan")[0] == "groq"
    assert ModelRouter.get("__unknown_task__")[0] == "groq"
    assert isinstance(ModelRouter.FALLBACK_CHAIN, list)
    assert len(ModelRouter.FALLBACK_CHAIN) >= 1


def test_core_ai_brain_shim_aliases_still_work():
    """Wave 2a: `from core.ai_brain import BRAIN_SYSTEM` / ModelRouter still works.

    Existing call sites (engine, tests, etc.) keep their imports unchanged
    because core/ai_brain.py re-exports the names from services/ai_advisor/.
    """
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from core.ai_brain import BRAIN_SYSTEM as shim_brain, ModelRouter as shim_router
    from services.ai_advisor.prompts import BRAIN_SYSTEM as canonical_brain
    from services.ai_advisor.router import ModelRouter as canonical_router

    # Same object, not a copy — proves import-shim, no string duplication.
    assert shim_brain is canonical_brain
    assert shim_router is canonical_router


def test_stats_exposes_router_config(client):
    """Wave 2a: /stats now reports loaded prompts + ModelRouter task list."""
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.json()
    assert "prompts_loaded" in data
    assert "BRAIN_SYSTEM" in data["prompts_loaded"]
    assert data["brain_system_chars"] > 1000
    assert "routing" in data
    assert "brain_cycle" in data["routing"]["tasks"]
    assert isinstance(data["routing"]["fallback_chain"], list)


# ── P1-02 Wave 2b (2026-05-11) — LLM HTTP wrappers extracted ─────────


def test_llm_clients_module_importable():
    """Wave 2b: HTTP wrappers + LLMRateLimitError moved out of core/ai_brain."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.llm_clients import (
        LLMRateLimitError,
        build_chat_payload,
        build_claude_payload,
        do_claude_call,
        do_groq_call,
        do_openrouter_call,
        parse_retry_after,
    )

    # All callable / class.
    assert callable(do_claude_call)
    assert callable(do_groq_call)
    assert callable(do_openrouter_call)
    assert callable(build_claude_payload)
    assert callable(build_chat_payload)
    assert callable(parse_retry_after)
    assert issubclass(LLMRateLimitError, Exception)


def test_llm_rate_limit_error_carries_state():
    """Wave 2b: LLMRateLimitError exposes provider + retry_after."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.llm_clients import LLMRateLimitError

    err = LLMRateLimitError("claude", 42.5)
    assert err.provider == "claude"
    assert err.retry_after == 42.5
    assert "claude" in str(err)
    assert "42.5" in str(err) or "42." in str(err)


def test_parse_retry_after_variants():
    """Wave 2b: parse_retry_after handles integer-as-string, garbage, missing."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from services.ai_advisor.llm_clients import parse_retry_after

    # Numeric string → that number, min 1.0.
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("0") == 1.0  # bumped to floor
    assert parse_retry_after("0.5") == 1.0  # bumped to floor
    # None / empty / garbage → default fallback (from env or 60).
    default = parse_retry_after(None)
    assert default >= 1.0
    assert parse_retry_after("") == default
    assert parse_retry_after("not-a-number") == default
    # Explicit default override.
    assert parse_retry_after(None, default=99.0) == 99.0


def test_build_claude_payload_shape():
    """Wave 2b: claude payload has system list with ephemeral cache_control."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    import json as _json

    from services.ai_advisor.llm_clients import build_claude_payload

    p = build_claude_payload("sys-prompt", "user-text", model="claude-foo", max_tokens=500)
    d = _json.loads(p)
    assert d["model"] == "claude-foo"
    assert d["max_tokens"] == 500
    assert d["system"][0]["text"] == "sys-prompt"
    assert d["system"][0]["cache_control"]["type"] == "ephemeral"
    assert d["messages"][0]["role"] == "user"
    assert d["messages"][0]["content"] == "user-text"


def test_build_chat_payload_shape():
    """Wave 2b: chat payload (Groq/OpenRouter) has system + user messages."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    import json as _json

    from services.ai_advisor.llm_clients import build_chat_payload

    p = build_chat_payload("sys", "u", model="llama-3.3-70b-versatile")
    d = _json.loads(p)
    assert d["model"] == "llama-3.3-70b-versatile"
    assert d["messages"][0]["role"] == "system"
    assert d["messages"][0]["content"] == "sys"
    assert d["messages"][1]["role"] == "user"
    assert d["messages"][1]["content"] == "u"
    assert "temperature" in d
    assert d["max_tokens"] >= 1


def test_core_ai_brain_llm_rate_limit_shim_identity():
    """Wave 2b: `from core.ai_brain import LLMRateLimitError` is canonical class.

    Same identity check as Wave 2a prompts/router shim. Existing code that
    `from core.ai_brain import LLMRateLimitError` keeps catching the right
    type from `services.ai_advisor.llm_clients`.
    """
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from core.ai_brain import LLMRateLimitError as shim_err
    from services.ai_advisor.llm_clients import LLMRateLimitError as canonical_err

    assert shim_err is canonical_err


def test_do_claude_call_handles_non_429_error_softly():
    """Wave 2b: any non-429 httpx error → None (soft failure)."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from unittest.mock import patch

    from services.ai_advisor.llm_clients import do_claude_call

    # httpx.post raises ConnectionError → soft None.
    with patch(
        "services.ai_advisor.llm_clients.httpx.post",
        side_effect=ConnectionError("boom"),
    ):
        result = do_claude_call("{}", "sk-test")
    assert result is None


def test_do_groq_call_429_raises_rate_limit():
    """Wave 2b: 429 response → LLMRateLimitError with retry_after."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from unittest.mock import MagicMock, patch

    from services.ai_advisor.llm_clients import LLMRateLimitError, do_groq_call

    fake_response = MagicMock()
    fake_response.status_code = 429
    fake_response.headers = {"Retry-After": "45"}
    with patch(
        "services.ai_advisor.llm_clients.httpx.post",
        return_value=fake_response,
    ):
        try:
            do_groq_call("{}", "gsk-test")
            raise AssertionError("expected LLMRateLimitError")
        except LLMRateLimitError as e:
            assert e.provider == "groq"
            assert e.retry_after == 45.0


def test_do_openrouter_call_success_returns_text():
    """Wave 2b: 200 response with choices array → message content text."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed (install requirements-dev.txt)")
    from unittest.mock import MagicMock, patch

    from services.ai_advisor.llm_clients import do_openrouter_call

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "hello from openrouter"}}]
    }
    with patch(
        "services.ai_advisor.llm_clients.httpx.post",
        return_value=fake_response,
    ):
        result = do_openrouter_call("{}", "or-test")
    assert result == "hello from openrouter"


# ── P1-02 Wave 2c (2026-05-11) — /suggest real LLM wiring ────────────


def test_real_llm_disabled_by_default(client, monkeypatch):
    """Wave 2c: AI_ADVISOR_REAL_LLM unset → stub HOLD (zero cost)."""
    monkeypatch.delenv("AI_ADVISOR_REAL_LLM", raising=False)
    payload = {"market": {"slug": "btc-test", "asset": "BTC", "timeframe": "5m"}}
    r = client.post("/suggest", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["stub_mode"] is True
    assert data["model_used"] is None
    sug = data["suggestions"][0]
    assert sug["action"] == "HOLD"
    assert sug["confidence"] == 0.0


def test_real_llm_flag_value_variants(monkeypatch):
    """Wave 2c: real-LLM flag accepts 1/true/yes/on (case-insensitive)."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed")
    from services.ai_advisor.app import _real_llm_enabled

    for truthy in ("true", "TRUE", "1", "yes", "on", "True"):
        monkeypatch.setenv("AI_ADVISOR_REAL_LLM", truthy)
        assert _real_llm_enabled() is True, f"{truthy!r} should be truthy"
    for falsy in ("false", "0", "no", "off", "", "garbage"):
        monkeypatch.setenv("AI_ADVISOR_REAL_LLM", falsy)
        assert _real_llm_enabled() is False, f"{falsy!r} should be falsy"


def test_build_user_prompt_renders_market_and_strategy():
    """Wave 2c: prompt rendering surfaces all key MarketContext + StrategyContext fields."""
    if not _FASTAPI_AVAILABLE:
        pytest.skip("fastapi not installed")
    from services.ai_advisor.app import _build_user_prompt
    from services.ai_advisor.models import (
        MarketContext,
        StrategyContext,
        SuggestRequest,
    )

    req = SuggestRequest(
        market=MarketContext(
            slug="btc-up-or-down-may-11",
            asset="BTC",
            timeframe="5m",
            up_odds=0.55,
            down_odds=0.45,
            spread=0.02,
            minutes_remaining=12.5,
            total_minutes=30.0,
        ),
        strategy=StrategyContext(
            label="M_BTC_5m_any_0.92",
            asset="BTC",
            timeframe="5m",
            threshold=0.92,
            recent_trades=35,
            recent_wins=22,
            recent_pnl=12.50,
        ),
        correlation_id="wave2c-001",
        cycle=42,
    )
    text = _build_user_prompt(req)
    # Market fields rendered.
    assert "btc-up-or-down-may-11" in text
    assert "asset=BTC timeframe=5m" in text
    assert "up=0.55" in text and "down=0.45" in text
    assert "spread=0.0200" in text
    assert "12.5m left" in text and "30.0m" in text
    # Strategy fields rendered with WR calculation.
    assert "M_BTC_5m_any_0.92" in text
    assert "n=35 wins=22" in text
    assert "WR=62.9%" in text  # 22/35 = 62.857...
    assert "PnL=+12.50" in text
    assert "correlation_id=wave2c-001" in text
    assert "cycle=42" in text


def test_real_llm_success_path(client, monkeypatch):
    """Wave 2c: real-LLM mode + successful provider → non-stub response."""
    from unittest.mock import patch

    monkeypatch.setenv("AI_ADVISOR_REAL_LLM", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake-test")

    # Mock do_claude_call (ModelRouter brain_cycle → claude primary).
    with patch(
        "services.ai_advisor.app.do_claude_call",
        return_value='{"actions": [], "market_view": "sideways", "reasoning": "test"}',
    ):
        payload = {
            "market": {"slug": "btc-test", "asset": "BTC", "timeframe": "5m"},
        }
        r = client.post("/suggest", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["stub_mode"] is False
    assert data["model_used"] is not None
    assert "claude" in data["model_used"] or "sonnet" in data["model_used"]
    sug = data["suggestions"][0]
    assert "Wave 2c real-LLM" in sug["reason"]
    assert "sideways" in sug["reason"]
    assert sug["payload"]["provider"] == "claude"


def test_real_llm_all_providers_fail_degrades_to_stub(client, monkeypatch):
    """Wave 2c: all providers return None → graceful stub fallback (no 500)."""
    from unittest.mock import patch

    monkeypatch.setenv("AI_ADVISOR_REAL_LLM", "true")
    # No API keys → providers should return None up-front. Even with keys,
    # patched calls return None.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")
    monkeypatch.setenv("GROK_API_KEY", "")

    with (
        patch("services.ai_advisor.app.do_claude_call", return_value=None),
        patch("services.ai_advisor.app.do_groq_call", return_value=None),
        patch("services.ai_advisor.app.do_openrouter_call", return_value=None),
    ):
        payload = {"market": {"slug": "btc-test", "asset": "BTC", "timeframe": "5m"}}
        r = client.post("/suggest", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["stub_mode"] is True
    assert "all-providers-failed" in data["suggestions"][0]["reason"]


def test_real_llm_429_skips_to_next_provider(client, monkeypatch):
    """Wave 2c: 429 from primary → fall back to next provider in chain."""
    from unittest.mock import patch

    from services.ai_advisor.llm_clients import LLMRateLimitError

    monkeypatch.setenv("AI_ADVISOR_REAL_LLM", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-fake")

    # Claude raises 429 → groq returns text.
    with (
        patch(
            "services.ai_advisor.app.do_claude_call",
            side_effect=LLMRateLimitError("claude", 30.0),
        ),
        patch("services.ai_advisor.app.do_groq_call", return_value="groq fallback ok"),
    ):
        payload = {"market": {"slug": "btc-test", "asset": "BTC", "timeframe": "5m"}}
        r = client.post("/suggest", json=payload)
    assert r.status_code == 200
    data = r.json()
    assert data["stub_mode"] is False
    sug = data["suggestions"][0]
    assert "groq fallback ok" in sug["reason"]
    assert sug["payload"]["provider"] == "groq"


def test_stats_wave_2c_block_present(client):
    """Wave 2c: /stats exposes real_llm_enabled + providers_available."""
    r = client.get("/stats")
    assert r.status_code == 200
    data = r.json()
    assert "wave_2c" in data
    assert "real_llm_enabled" in data["wave_2c"]
    assert "providers_available" in data["wave_2c"]
    pa = data["wave_2c"]["providers_available"]
    assert set(pa.keys()) == {"anthropic", "groq", "openrouter"}
    for v in pa.values():
        assert isinstance(v, bool)
