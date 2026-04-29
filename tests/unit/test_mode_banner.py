"""Tests for telegram_bot/templates/mode_banner.py — Aşama 3.B."""
from __future__ import annotations

import os

import pytest

from telegram_bot.templates.mode_banner import (
    format_banner, format_mode_status_text,
    get_current_mode, is_paper_mode, is_real_mode,
)


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Each test gets a clean LIVE_ENABLED slot."""
    monkeypatch.delenv("LIVE_ENABLED", raising=False)
    yield


class TestGetCurrentMode:
    def test_default_paper_when_env_unset(self):
        assert get_current_mode() == "paper"

    def test_real_when_env_true(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "true")
        assert get_current_mode() == "real"

    def test_paper_when_env_false(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "false")
        assert get_current_mode() == "paper"

    def test_paper_when_env_garbage(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "yes")  # not "true"
        assert get_current_mode() == "paper"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "TRUE")
        assert get_current_mode() == "real"
        monkeypatch.setenv("LIVE_ENABLED", "True")
        assert get_current_mode() == "real"


class TestModePredicates:
    def test_is_paper_mode_default(self):
        assert is_paper_mode() is True
        assert is_real_mode() is False

    def test_is_real_mode_when_enabled(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "true")
        assert is_real_mode() is True
        assert is_paper_mode() is False


class TestFormatBanner:
    def test_paper_full_banner(self):
        b = format_banner("paper")
        assert "📋" in b
        assert "PAPER MODE" in b
        assert "Simülasyon" in b
        assert b.endswith("\n\n")

    def test_real_full_banner(self):
        b = format_banner("real")
        assert "💰" in b
        assert "REAL MODE" in b
        assert "Gerçek pUSD" in b

    def test_paper_compact(self):
        b = format_banner("paper", compact=True)
        assert "📋" in b
        assert "PAPER MODE" in b
        assert "Simülasyon" not in b  # compact omits subtitle
        assert "\n" not in b  # single line

    def test_real_compact(self):
        b = format_banner("real", compact=True)
        assert "💰" in b
        assert "REAL MODE" in b
        assert "\n" not in b

    def test_default_uses_env(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "true")
        b = format_banner()
        assert "REAL" in b

    def test_override_overrides_env(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "true")
        b = format_banner("paper")  # override
        assert "PAPER" in b
        assert "REAL" not in b


class TestFormatModeStatusText:
    def test_paper_describes_simulation(self):
        t = format_mode_status_text()
        assert "PAPER MODE" in t
        assert "virtual" in t.lower() or "simülasyon" in t.lower()
        assert "/strategies" in t  # paper command hint
        assert "/portfolio" not in t  # portfolio only meaningful in real

    def test_real_describes_real_money(self, monkeypatch):
        monkeypatch.setenv("LIVE_ENABLED", "true")
        t = format_mode_status_text()
        assert "REAL MODE" in t
        assert "Polymarket" in t
        assert "/portfolio" in t  # portfolio key in real
        assert "LIVE_BUDGET" in t  # risk control hint

    def test_html_safe_no_unescaped_ampersand(self, monkeypatch):
        for mode in ("paper", "real"):
            monkeypatch.setenv("LIVE_ENABLED", "true" if mode == "real" else "false")
            t = format_mode_status_text()
            # No naked & (would break Telegram HTML)
            # Only `&amp;` or no & at all
            for ch_idx, ch in enumerate(t):
                if ch == "&":
                    # must be part of an entity
                    rest = t[ch_idx:ch_idx + 6]
                    assert rest.startswith(("&amp;", "&lt;", "&gt;", "&quot;", "&#")), \
                        f"Unescaped & at pos {ch_idx}: '{rest}'"
