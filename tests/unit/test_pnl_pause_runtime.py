"""Unit tests for Epic 6 T6.1 — PNL_PAUSE_THRESHOLD runtime read.

Before T6.1, `PNL_PAUSE_THRESHOLD` was a module-top constant populated
once at import time:

    PNL_PAUSE_THRESHOLD = float(os.getenv("PNL_PAUSE_THRESHOLD", "-8.0"))

This meant `/env_toggle` could patch `os.environ` at runtime but the
auto-optimizer would still use the import-time value — a silent "ghost
toggle" where the Telegram admin thinks the threshold changed but the
engine keeps using the old one.

T6.1 Option A replaces the constant with `_get_pnl_pause_threshold()`,
a helper that re-reads env on every call. `_adaptive_pnl_threshold`
now calls the helper instead of reading a frozen constant.

T11.5 hygiene pass (2026-04-24): raw `os.environ[X] = val` replaced
with `monkeypatch.setenv(X, val)` for automatic teardown. This removes
cross-test state leak risk (the original FLAKY_AUDIT.md HIGH item).

These tests verify:
  1. Fresh env read on every call (no import-time freeze)
  2. Default fallback when env is unset
  3. Malformed env falls back to default without raising
  4. `_adaptive_pnl_threshold` picks up env changes on subsequent calls
"""
from __future__ import annotations

import pytest

import core.auto_optimizer as ao


@pytest.fixture
def adaptive_defaults(monkeypatch):
    """Pin adaptive knobs + ensure env starts clean. Auto-restored on teardown.

    T11.5: Uses monkeypatch.setattr for module attrs (auto-restored) and
    monkeypatch.delenv to start with a clean env slot. Individual tests
    then call monkeypatch.setenv(...) as needed.
    """
    monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
    monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
    monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
    monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
    monkeypatch.delenv("PNL_PAUSE_THRESHOLD", raising=False)


# ═══════════════════════════════════════════════════════════════════
# _get_pnl_pause_threshold — direct behaviour
# ═══════════════════════════════════════════════════════════════════

def test_get_threshold_reads_env_fresh(adaptive_defaults, monkeypatch):
    """Helper returns current env value, not a frozen import-time one."""
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-5.0")
    assert ao._get_pnl_pause_threshold() == -5.0

    # Flip the env mid-process (simulates /env_toggle patch)
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-12.0")
    assert ao._get_pnl_pause_threshold() == -12.0

    # And back again
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-3.0")
    assert ao._get_pnl_pause_threshold() == -3.0


def test_get_threshold_default_when_unset(adaptive_defaults):
    """Unset env → default -8.0 (Sprint 0 loosened value).

    `adaptive_defaults` fixture already `delenv`'d the key on setup.
    """
    assert ao._get_pnl_pause_threshold() == -8.0


def test_get_threshold_malformed_falls_back(adaptive_defaults, monkeypatch):
    """Garbage env value → fallback to -8.0, no ValueError escapes."""
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "not-a-number")
    assert ao._get_pnl_pause_threshold() == -8.0

    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "")
    assert ao._get_pnl_pause_threshold() == -8.0


# ═══════════════════════════════════════════════════════════════════
# _adaptive_pnl_threshold — now picks up env changes at runtime
# ═══════════════════════════════════════════════════════════════════

def test_adaptive_uses_runtime_base(adaptive_defaults, monkeypatch):
    """Flip env between calls → adaptive base changes too."""
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-3.0")
    # 0 trades → base
    assert ao._adaptive_pnl_threshold(0) == pytest.approx(-3.0)

    # Admin fires /env_toggle PNL_PAUSE_THRESHOLD=-6.0
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-6.0")
    # Same call after env flip — now returns -6.0 without restart
    assert ao._adaptive_pnl_threshold(0) == pytest.approx(-6.0)


def test_adaptive_step_applies_over_runtime_base(adaptive_defaults, monkeypatch):
    """Adaptive step math still works on top of the live env value."""
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-4.0")
    # 40 trades → 2 steps of -0.5 → -4.0 - 1.0 = -5.0
    assert ao._adaptive_pnl_threshold(40) == pytest.approx(-5.0)

    # Flip base to -2.0, same 40 trades → -2.0 - 1.0 = -3.0
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-2.0")
    assert ao._adaptive_pnl_threshold(40) == pytest.approx(-3.0)


def test_adaptive_floor_still_caps_after_runtime_base_change(
    adaptive_defaults, monkeypatch
):
    """Floor (-10.0) must cap regardless of runtime base value."""
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-3.0")
    # 500 trades → 25 steps → -3.0 - 12.5 = -15.5, capped at floor -10.0
    assert ao._adaptive_pnl_threshold(500) == pytest.approx(-10.0)

    # Swap base to -9.0 — still capped
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-9.0")
    assert ao._adaptive_pnl_threshold(500) == pytest.approx(-10.0)


def test_adaptive_disabled_returns_runtime_base(
    adaptive_defaults, monkeypatch
):
    """When adaptive is off, base must still be the live env value."""
    monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", False)
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-7.5")
    # Regardless of trade count, returns fresh base
    assert ao._adaptive_pnl_threshold(0) == pytest.approx(-7.5)
    assert ao._adaptive_pnl_threshold(9999) == pytest.approx(-7.5)


def test_ghost_toggle_regression(adaptive_defaults, monkeypatch):
    """Regression: the exact bug T6.1 fixed — env flip must take effect.

    Before T6.1, the module-top constant was frozen at import time, so
    patching os.environ mid-process had no effect on the optimizer's
    decisions. This test would have failed under the old code.
    """
    # Baseline — what /env_toggle would see on boot
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
    t1 = ao._adaptive_pnl_threshold(0)
    assert t1 == pytest.approx(-8.0)

    # Admin tightens threshold via /env_toggle
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-3.0")
    t2 = ao._adaptive_pnl_threshold(0)
    assert t2 == pytest.approx(-3.0), (
        "ghost-toggle regression — env change didn't take effect")

    # And loosens it to -7.0 (under floor=-10.0 cap)
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-7.0")
    t3 = ao._adaptive_pnl_threshold(0)
    assert t3 == pytest.approx(-7.0)

    # If admin pushes past floor (-12.0 with floor=-10.0) → floor caps it.
    # That's correct behaviour — verifies the floor guard still works even
    # with runtime base changes.
    monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-12.0")
    t4 = ao._adaptive_pnl_threshold(0)
    assert t4 == pytest.approx(-10.0)  # floor bites
