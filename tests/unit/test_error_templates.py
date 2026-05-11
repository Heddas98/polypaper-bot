"""Unit tests for telegram_bot/templates/errors.py (Phase 47f.10 P2#12)."""

from __future__ import annotations

from telegram_bot.templates.errors import ERR, fmt_error


class TestErrorKeys:
    def test_required_keys_present(self):
        required = [
            "DB_UNAVAILABLE",
            "ENGINE_NOT_READY",
            "ADMIN_ONLY",
            "USAGE",
            "MISSING_ARG",
            "BAD_NUMBER",
            "NOT_FOUND",
            "AMBIGUOUS_PREFIX",
            "NO_RESULTS",
            "ALREADY_RUNNING",
            "ALREADY_STOPPED",
            "GATE_FAIL",
            "RISK_HALTED",
            "RISK_DAILY_LIMIT",
            "NETWORK_ERROR",
            "API_ERROR",
            "UNEXPECTED",
        ]
        for key in required:
            assert key in ERR, f"missing error template: {key}"
            assert isinstance(ERR[key], str)
            assert len(ERR[key]) > 0


class TestFmtError:
    def test_returns_string(self):
        msg = fmt_error("DB_UNAVAILABLE")
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_unknown_key_falls_back_safely(self):
        # Should not raise — returns a safe placeholder or the key itself
        msg = fmt_error("NONEXISTENT_KEY")
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_accepts_kwargs(self):
        # Even if the template has no placeholders, extra kwargs must not crash
        msg = fmt_error("DB_UNAVAILABLE", extra="ignored")
        assert isinstance(msg, str)
