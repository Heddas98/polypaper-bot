"""Unit tests for T11.6 — user-facing exception render policy.

Verifies the `render_user_exception` helper:
  1. Default mode (DEBUG_SHOW_EXC unset/false): generic, type-only
  2. Debug mode: full str(e) truncated to 200 chars, HTML-escaped
  3. HTML escape applied (no injection via exc str)
  4. Label prefix respected
  5. Runtime env re-read (T6.1 doctrine) — no restart needed to flip
"""
from __future__ import annotations

from telegram_bot.handlers._exc_render import render_user_exception


def test_default_mode_no_exc_str_leak(monkeypatch):
    """DEBUG_SHOW_EXC off -> user sees type only, never str(e) content."""
    monkeypatch.delenv("DEBUG_SHOW_EXC", raising=False)
    exc = ValueError("sensitive internal path /data_store/polypaper.db")
    out = render_user_exception(exc, "❌ Test error")
    assert "sensitive" not in out
    assert "data_store" not in out
    assert "polypaper.db" not in out
    assert "ValueError" in out
    assert "type=" in out
    assert "beklenmeyen hata" in out.lower()


def test_default_mode_no_prefix():
    """No prefix → generic warning line."""
    exc = TypeError("internal")
    out = render_user_exception(exc)
    assert "internal" not in out
    assert "TypeError" in out


def test_debug_mode_shows_exc_str(monkeypatch):
    """DEBUG_SHOW_EXC=true -> shows str(e) (admin diagnostic)."""
    monkeypatch.setenv("DEBUG_SHOW_EXC", "true")
    exc = RuntimeError("diagnostic detail")
    out = render_user_exception(exc, "❌ Label")
    assert "RuntimeError" in out
    assert "diagnostic detail" in out


def test_debug_mode_truncates_long_exc_str(monkeypatch):
    monkeypatch.setenv("DEBUG_SHOW_EXC", "true")
    exc = RuntimeError("x" * 1000)
    out = render_user_exception(exc)
    # Should not contain all 1000 'x's (truncated at 200 chars)
    x_count = out.count("x")
    assert x_count <= 210, f"truncation failed, got {x_count} 'x's"


def test_html_escape_applied_on_type(monkeypatch):
    """Exception type should be HTML-safe."""
    monkeypatch.delenv("DEBUG_SHOW_EXC", raising=False)

    # Custom exception with a weird name (simulating user-controlled input
    # shouldn't land in type name but pin the escape contract)
    class MyErr(Exception):
        pass

    out = render_user_exception(MyErr("x"))
    assert "<code>MyErr</code>" in out  # code tag applied


def test_html_escape_applied_on_debug_str(monkeypatch):
    """Exception str() must be HTML-escaped so angle brackets don't break
    Telegram HTML parse."""
    monkeypatch.setenv("DEBUG_SHOW_EXC", "true")
    exc = ValueError("<script>alert(1)</script> & \"quoted\"")
    out = render_user_exception(exc)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&amp;" in out
    assert "&quot;" in out


def test_runtime_env_reread(monkeypatch):
    """T6.1 parity: /envt DEBUG_SHOW_EXC flips behaviour without restart.

    Two sequential calls with different env values must return different
    outputs (no import-time freeze).
    """
    exc = KeyError("secret")

    monkeypatch.setenv("DEBUG_SHOW_EXC", "false")
    out_off = render_user_exception(exc)
    assert "secret" not in out_off

    monkeypatch.setenv("DEBUG_SHOW_EXC", "true")
    out_on = render_user_exception(exc)
    assert "secret" in out_on


def test_label_prefix_preserved(monkeypatch):
    """Prefix ("❌ ...") appears at the start of the rendered string."""
    monkeypatch.delenv("DEBUG_SHOW_EXC", raising=False)
    out = render_user_exception(ValueError("x"), "❌ EV stats hatası")
    assert out.startswith("❌ EV stats hatası")


def test_empty_prefix_falls_back():
    """Empty string prefix -> generic branch (no empty label leak)."""
    out = render_user_exception(ValueError("x"), "")
    assert out.startswith("⚠️")
