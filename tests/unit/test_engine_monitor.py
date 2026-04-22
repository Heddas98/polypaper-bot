"""Unit tests for core/engine_monitor.py pure-logic surface (Epic 9 T9.6 P2).

Coverage gap baseline (2026-04-22): `engine_monitor.py` 0% / 222 stmts.
Phase 51 P51-02 mixin carved from engine.py monolith + Phase 60 Smart
Exit + Sprint 2 S2-03 _max_moves state fix.

Scope (pure logic + ENV helpers only):
  1. `_track_max_moves` favorable/adverse dispatch (Phase 60)
  2. `_pop_max_moves` dict pop semantics
  3. `_smart_exit_enabled` runtime ENV read (Phase 60 /filters toggle)
  4. `_remaining_edge_min` runtime ENV read + fallback

Out-of-scope (→ T9.8 integration smoke):
  * `_monitor` / `_check` / `_smart_exit_check` — DB-heavy async paths
  * `_update_disposition` — DB write + schema check
"""
from __future__ import annotations

import pytest

from core import engine_monitor as em_mod
from core.engine_monitor import EngineMonitorMixin


class MonitorHarness(EngineMonitorMixin):
    """Minimal stub with _max_moves dict (Sprint 2 S2-03 contract)."""

    def __init__(self):
        self._max_moves = {}  # Sprint 2 S2-03: initialized per-instance


# ═══ _track_max_moves: Phase 60 favorable/adverse dispatch ═════════════

class TestTrackMaxMoves:
    def test_first_favorable_move_sets_fav(self):
        h = MonitorHarness()
        # entry 0.50, cur 0.55 → move = +0.05 (favorable)
        h._track_max_moves("exec1", entry=0.50, cur=0.55)
        fav, adv = h._max_moves["exec1"]
        assert fav == pytest.approx(0.05, abs=0.001)
        assert adv == 0.0

    def test_first_adverse_move_sets_adv(self):
        h = MonitorHarness()
        # entry 0.50, cur 0.45 → move = -0.05 (adverse)
        h._track_max_moves("exec1", entry=0.50, cur=0.45)
        # adv stored as most-negative number
        assert h._max_moves["exec1"][0] == 0.0  # fav unchanged
        assert h._max_moves["exec1"][1] == pytest.approx(-0.05, abs=0.001)

    def test_favorable_only_grows_monotonically(self):
        """Sprint 2 S2-03: max favorable is peak, not latest."""
        h = MonitorHarness()
        h._track_max_moves("exec1", 0.50, 0.55)  # +0.05
        h._track_max_moves("exec1", 0.50, 0.53)  # +0.03 — smaller
        # Fav stays at peak 0.05, not overwritten
        assert h._max_moves["exec1"][0] == pytest.approx(0.05, abs=0.001)

    def test_adverse_tracks_worst(self):
        """Adverse = MIN over time (most negative)."""
        h = MonitorHarness()
        h._track_max_moves("exec1", 0.50, 0.47)  # -0.03
        h._track_max_moves("exec1", 0.50, 0.40)  # -0.10 — worse
        # Adv = -0.10 (further from 0)
        assert h._max_moves["exec1"][1] == pytest.approx(-0.10, abs=0.001)
        # Second call: move -0.03 doesn't replace -0.10
        h._track_max_moves("exec1", 0.50, 0.48)  # -0.02, above -0.10
        assert h._max_moves["exec1"][1] == pytest.approx(-0.10, abs=0.001)

    def test_cur_none_noop(self):
        """Defensive: None cur returns early without mutation."""
        h = MonitorHarness()
        h._track_max_moves("exec1", 0.50, None)
        assert "exec1" not in h._max_moves

    def test_entry_zero_noop(self):
        """Defensive: entry <= 0 returns early (no divide-by-zero risk downstream)."""
        h = MonitorHarness()
        h._track_max_moves("exec1", 0.0, 0.55)
        assert "exec1" not in h._max_moves
        h._track_max_moves("exec2", -0.01, 0.55)
        assert "exec2" not in h._max_moves


# ═══ _pop_max_moves: dict pop semantics ═════════════════════════════════

class TestPopMaxMoves:
    def test_pop_returns_tracked_tuple(self):
        h = MonitorHarness()
        h._track_max_moves("exec1", 0.50, 0.55)
        result = h._pop_max_moves("exec1")
        assert result is not None
        fav, adv = result
        assert fav == pytest.approx(0.05, abs=0.001)
        assert adv == 0.0
        # And is removed from dict
        assert "exec1" not in h._max_moves

    def test_pop_missing_returns_none(self):
        """Missing exec_id must return None, not raise KeyError."""
        h = MonitorHarness()
        assert h._pop_max_moves("not-tracked") is None


# ═══ ENV helpers: runtime read, no import-time freeze ═══════════════════

class TestSmartExitEnabled:
    """Phase 60: `_smart_exit_enabled` re-reads env on every call.

    /filters panel toggles SMART_EXIT_ENABLED at runtime — if it froze
    to a module-top constant, the Telegram admin toggle would silently
    no-op (same ghost-toggle class as T6.1/T6.4/T7.6 A5).
    """

    def test_default_true(self, monkeypatch):
        monkeypatch.delenv("SMART_EXIT_ENABLED", raising=False)
        assert em_mod._smart_exit_enabled() is True

    def test_explicit_false(self, monkeypatch):
        monkeypatch.setenv("SMART_EXIT_ENABLED", "false")
        assert em_mod._smart_exit_enabled() is False

    def test_case_insensitive_true(self, monkeypatch):
        monkeypatch.setenv("SMART_EXIT_ENABLED", "TRUE")
        assert em_mod._smart_exit_enabled() is True

    def test_runtime_rread(self, monkeypatch):
        """CRITICAL: 2 sequential calls must see fresh values — no freeze."""
        monkeypatch.setenv("SMART_EXIT_ENABLED", "true")
        assert em_mod._smart_exit_enabled() is True
        monkeypatch.setenv("SMART_EXIT_ENABLED", "false")
        assert em_mod._smart_exit_enabled() is False


class TestRemainingEdgeMin:
    """Phase 60: `_remaining_edge_min` — exit threshold when δ(cur) < X."""

    def test_default(self, monkeypatch):
        monkeypatch.delenv("REMAINING_EDGE_MIN", raising=False)
        assert em_mod._remaining_edge_min() == 0.05

    def test_override(self, monkeypatch):
        monkeypatch.setenv("REMAINING_EDGE_MIN", "0.12")
        assert em_mod._remaining_edge_min() == 0.12

    def test_runtime_rread(self, monkeypatch):
        """/filters tuning must take effect on next call, no freeze."""
        monkeypatch.setenv("REMAINING_EDGE_MIN", "0.03")
        assert em_mod._remaining_edge_min() == pytest.approx(0.03)
        monkeypatch.setenv("REMAINING_EDGE_MIN", "0.10")
        assert em_mod._remaining_edge_min() == pytest.approx(0.10)

    def test_invalid_raises(self, monkeypatch):
        """Note: `_remaining_edge_min` uses raw `float()` — malformed ENV
        raises ValueError. Documented here so future refactors (adding a
        try/except like live_trader helpers) don't silently change semantics.
        """
        monkeypatch.setenv("REMAINING_EDGE_MIN", "not-a-number")
        with pytest.raises(ValueError):
            em_mod._remaining_edge_min()
