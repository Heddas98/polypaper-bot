"""Unit tests for auto_optimizer.py pure-logic helpers (Epic 9 T9.6 P2).

Coverage gap baseline (2026-04-22): `auto_optimizer.py` 9.2% / 381 stmts.
T6.1 + T6.4 + T7.6 B8 all touched this module to eliminate "ghost toggle"
bugs where module-top ENV constants were frozen at import. Every runtime
threshold helper in here is tested as runtime-readable.

Scope (pure logic only):
  1. `_get_pnl_pause_threshold`   (T6.1 default drift -8.0)
  2. `_get_rolling_wr_window`     (T7.6 B8)
  3. `_get_rolling_wr_kill_threshold` (T7.6 B8)
  4. `_is_protected_type`         (Phase 82e hotfix)
  5. `_adaptive_pnl_threshold`    (Phase 56 P1-05 trade-count-scaled base)

Out-of-scope (→ T9.8):
  * `AutoOptimizer.run_check`, `_startup_health_check`, `_check_*` —
    all DB-heavy async orchestration paths.
  * `_startup_auto_resume` — DB read/write.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import auto_optimizer as ao


# ═══ _get_pnl_pause_threshold ═══════════════════════════════════════════

class TestPnlPauseThreshold:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("PNL_PAUSE_THRESHOLD", raising=False)
        assert ao._get_pnl_pause_threshold() == -8.0

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-3.0")
        assert ao._get_pnl_pause_threshold() == -3.0

    def test_malformed_falls_back(self, monkeypatch):
        """T6.1 guard: bad ENV must NOT crash auto-pause loop."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "garbage")
        assert ao._get_pnl_pause_threshold() == -8.0

    def test_runtime_reread(self, monkeypatch):
        """Ghost-toggle guard: /env_toggle reflects on next call."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-5.0")
        assert ao._get_pnl_pause_threshold() == -5.0
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-12.0")
        assert ao._get_pnl_pause_threshold() == -12.0


# ═══ _get_rolling_wr_window / _get_rolling_wr_kill_threshold (T7.6 B8) ═══

class TestRollingWrWindow:
    def test_default_20(self, monkeypatch):
        monkeypatch.delenv("ROLLING_WR_WINDOW", raising=False)
        assert ao._get_rolling_wr_window() == 20

    def test_override_int(self, monkeypatch):
        monkeypatch.setenv("ROLLING_WR_WINDOW", "50")
        assert ao._get_rolling_wr_window() == 50

    def test_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("ROLLING_WR_WINDOW", "abc")
        assert ao._get_rolling_wr_window() == 20

    def test_runtime_reread(self, monkeypatch):
        monkeypatch.setenv("ROLLING_WR_WINDOW", "10")
        assert ao._get_rolling_wr_window() == 10
        monkeypatch.setenv("ROLLING_WR_WINDOW", "30")
        assert ao._get_rolling_wr_window() == 30


class TestRollingWrKillThreshold:
    def test_default_40(self, monkeypatch):
        monkeypatch.delenv("ROLLING_WR_KILL", raising=False)
        assert ao._get_rolling_wr_kill_threshold() == 40.0

    def test_override(self, monkeypatch):
        monkeypatch.setenv("ROLLING_WR_KILL", "45.5")
        assert ao._get_rolling_wr_kill_threshold() == 45.5

    def test_malformed_falls_back(self, monkeypatch):
        monkeypatch.setenv("ROLLING_WR_KILL", "NaNish")
        assert ao._get_rolling_wr_kill_threshold() == 40.0


# ═══ _is_protected_type (Phase 82e hotfix) ═════════════════════════════

class TestIsProtectedType:
    def test_classic_is_protected(self):
        """Default PROTECTED_STRATEGY_TYPES = {'classic'}."""
        s = SimpleNamespace(strategy_type="classic")
        assert ao._is_protected_type(s) is True

    def test_case_insensitive(self):
        s = SimpleNamespace(strategy_type="Classic")
        assert ao._is_protected_type(s) is True
        s2 = SimpleNamespace(strategy_type="CLASSIC")
        assert ao._is_protected_type(s2) is True

    def test_momentum_not_protected(self):
        s = SimpleNamespace(strategy_type="momentum")
        assert ao._is_protected_type(s) is False

    def test_missing_strategy_type_attr(self):
        """No attr → getattr returns '' → not protected, not crash."""
        s = SimpleNamespace()
        assert ao._is_protected_type(s) is False

    def test_none_strategy_type(self):
        """Explicit None → or '' → not protected."""
        s = SimpleNamespace(strategy_type=None)
        assert ao._is_protected_type(s) is False

    def test_non_string_strategy_type_safe(self):
        """T8.1 narrow: int/non-str must NOT crash — returns False."""
        s = SimpleNamespace(strategy_type=42)
        # AttributeError path in narrowed except → False
        assert ao._is_protected_type(s) is False


# ═══ _adaptive_pnl_threshold (Phase 56 P1-05) ══════════════════════════

class TestAdaptivePnlThreshold:
    """More trades → more lenient (looser) threshold.

    Formula: `base - (trades // step_size * step)` clamped to FLOOR.
    With defaults base=-8, step=0.5, per_step=20, floor=-10.
    """

    def test_zero_trades_returns_base(self, monkeypatch):
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(0) == -8.0

    def test_below_step_returns_base(self, monkeypatch):
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(19) == -8.0

    def test_one_step_loosens(self, monkeypatch):
        """20 trades = 1 step → -8.0 - 0.5 = -8.5."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(20) == pytest.approx(-8.5)

    def test_floor_clamp(self, monkeypatch):
        """1000 trades → would be very low, floored at -10.0."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(1000) == -10.0

    def test_adaptive_disabled_returns_base(self, monkeypatch):
        """ADAPTIVE_PNL_ENABLED=False → flat base, regardless of trades."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", False)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(500) == -8.0

    def test_zero_step_size_guard(self, monkeypatch):
        """Defensive: ADAPTIVE_PNL_TRADES_PER_STEP=0 must NOT divide-by-zero."""
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-8.0")
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 0)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -10.0)
        assert ao._adaptive_pnl_threshold(100) == -8.0

    def test_runtime_base_reread(self, monkeypatch):
        """T6.1 doctrine: base re-read per call, not frozen at import."""
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_ENABLED", True)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_STEP", 0.5)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_TRADES_PER_STEP", 20)
        monkeypatch.setattr(ao, "ADAPTIVE_PNL_FLOOR", -20.0)  # generous floor
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-5.0")
        assert ao._adaptive_pnl_threshold(0) == -5.0
        monkeypatch.setenv("PNL_PAUSE_THRESHOLD", "-10.0")
        assert ao._adaptive_pnl_threshold(0) == -10.0
