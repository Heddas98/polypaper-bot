"""Unit tests for ai_brain.py T8.2 LLM rate-limit helpers (Epic 9 T9.6 P2).

Coverage gap baseline (2026-04-22): `ai_brain.py` 6.0% / 1120 stmts.
Epic 8 T8.2 (commit `c967726`) introduced 429 rate-limit guard with
Retry-After parsing + per-provider cooldown window + MIN_COST anti-
bypass. Post-closure (`69553c4`) made BACKOFF/MIN_COST runtime-read
via `_get_llm_ratelimit_backoff()` / `_get_llm_ratelimit_min_cost()`
so `/env_toggle` applies without restart (T6.1/T7.6 A5 pattern).

Scope (pure logic — no network, no DB writes):
  1. `_get_llm_ratelimit_backoff` — runtime ENV read + fallback
  2. `_get_llm_ratelimit_min_cost` — runtime ENV read + fallback
  3. `AIBrain._parse_retry_after` — numeric seconds | date | malformed
  4. `AIBrain._rate_limit_active` — time-window predicate

Out-of-scope (→ T9.8 integration):
  * `_call_claude` / `_call_groq` / `_call_openrouter` — network I/O
  * `_handle_rate_limit` — DB write via `_save_budget`
  * `start` / `_cycle` — 10-min loop orchestration
"""
from __future__ import annotations

import time

import pytest

from core import ai_brain as ab_mod


# ═══ Module-level ENV helpers: runtime re-read guards ═══════════════════

class TestLlmRateLimitBackoff:
    """Phase post-closure: /env_toggle knob must apply without restart."""

    def test_default_60(self, monkeypatch):
        monkeypatch.delenv("LLM_RATELIMIT_BACKOFF_SEC", raising=False)
        assert ab_mod._get_llm_ratelimit_backoff() == 60.0

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "120")
        assert ab_mod._get_llm_ratelimit_backoff() == 120.0

    def test_malformed_falls_back(self, monkeypatch):
        """Invalid input must NOT raise — graceful default 60s."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "not-a-number")
        assert ab_mod._get_llm_ratelimit_backoff() == 60.0

    def test_runtime_reread(self, monkeypatch):
        """CRITICAL: two calls see fresh values — no import-time freeze."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "30")
        assert ab_mod._get_llm_ratelimit_backoff() == 30.0
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "90")
        assert ab_mod._get_llm_ratelimit_backoff() == 90.0


class TestLlmRateLimitMinCost:
    """MIN_COST anti-bypass: charged to budget per 429 to prevent infinite retry."""

    def test_default_small(self, monkeypatch):
        monkeypatch.delenv("LLM_RATELIMIT_MIN_COST", raising=False)
        assert ab_mod._get_llm_ratelimit_min_cost() == pytest.approx(0.001)

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_MIN_COST", "0.05")
        assert ab_mod._get_llm_ratelimit_min_cost() == pytest.approx(0.05)

    def test_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_MIN_COST", "bad")
        assert ab_mod._get_llm_ratelimit_min_cost() == pytest.approx(0.001)

    def test_runtime_reread(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_MIN_COST", "0.01")
        assert ab_mod._get_llm_ratelimit_min_cost() == pytest.approx(0.01)
        monkeypatch.setenv("LLM_RATELIMIT_MIN_COST", "0.02")
        assert ab_mod._get_llm_ratelimit_min_cost() == pytest.approx(0.02)


# ═══ AIBrain method-level helpers ══════════════════════════════════════

def _make_brain():
    """Minimal AIBrain — only uses state touched by pure-logic methods."""
    # AIBrain.__init__ sets _rate_limited_until dict + tries to import
    # BrierTracker; we pass db=None because none of the tested methods
    # actually use self.db. BrierTracker import may succeed but __init__
    # will log if db None — that's fine (no bubbled exception).
    return ab_mod.AIBrain(db=None)


class TestParseRetryAfter:
    """Providers send Retry-After as seconds ("30") or HTTP-date.
    We only need a conservative number; malformed → ENV fallback."""

    def test_numeric_seconds(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "60")
        b = _make_brain()
        assert b._parse_retry_after("30") == 30.0

    def test_numeric_float(self, monkeypatch):
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "60")
        b = _make_brain()
        assert b._parse_retry_after("45.5") == 45.5

    def test_minimum_1_second(self, monkeypatch):
        """max(1.0, …) floors absurdly small retry to avoid spin-loop."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "60")
        b = _make_brain()
        assert b._parse_retry_after("0") == 1.0
        assert b._parse_retry_after("0.1") == 1.0

    def test_none_header_uses_backoff_default(self, monkeypatch):
        """No header → pull fresh default via ENV helper."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "123")
        b = _make_brain()
        assert b._parse_retry_after(None) == 123.0

    def test_empty_string_header_uses_backoff_default(self, monkeypatch):
        """Empty string → treated as missing header."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "77")
        b = _make_brain()
        assert b._parse_retry_after("") == 77.0

    def test_malformed_header_uses_default(self, monkeypatch):
        """Wed-22-Oct-2026 date not yet supported → fall back."""
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "60")
        b = _make_brain()
        assert b._parse_retry_after("Wed, 21 Oct 2026 07:28:00 GMT") == 60.0

    def test_default_arg_override(self):
        """Explicit default arg bypasses ENV read."""
        b = _make_brain()
        assert b._parse_retry_after(None, default=99.0) == 99.0

    def test_runtime_default_reread(self, monkeypatch):
        """Sequential calls see fresh ENV (T8.2 post-closure guarantee)."""
        b = _make_brain()
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "10")
        assert b._parse_retry_after(None) == 10.0
        monkeypatch.setenv("LLM_RATELIMIT_BACKOFF_SEC", "200")
        assert b._parse_retry_after(None) == 200.0


class TestRateLimitActive:
    """_rate_limit_active(provider) is a time-window predicate."""

    def test_fresh_brain_all_providers_inactive(self):
        b = _make_brain()
        assert b._rate_limit_active("claude") is False
        assert b._rate_limit_active("groq") is False
        assert b._rate_limit_active("openrouter") is False

    def test_future_timestamp_is_active(self):
        b = _make_brain()
        b._rate_limited_until["claude"] = time.time() + 60
        assert b._rate_limit_active("claude") is True

    def test_past_timestamp_is_inactive(self):
        b = _make_brain()
        b._rate_limited_until["claude"] = time.time() - 1
        assert b._rate_limit_active("claude") is False

    def test_unknown_provider_defaults_inactive(self):
        """Unseen key → dict.get(provider, 0.0) → 0.0 → never active."""
        b = _make_brain()
        assert b._rate_limit_active("nonexistent") is False

    def test_per_provider_isolation(self):
        """Setting claude cooldown must NOT affect groq / openrouter."""
        b = _make_brain()
        b._rate_limited_until["claude"] = time.time() + 60
        assert b._rate_limit_active("claude") is True
        assert b._rate_limit_active("groq") is False
        assert b._rate_limit_active("openrouter") is False
