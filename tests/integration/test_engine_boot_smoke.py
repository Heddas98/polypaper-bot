"""Integration smoke test for TradingEngine construction (Epic 9 T9.8 part 1).

Goal: pin the construction-level invariants that must hold AFTER `__init__`
returns, BEFORE `engine.start()` is awaited. Full async boot (DB migrations,
WS handshake, plugin hot-reload) is Windows-only territory (sandbox cannot
run asyncio+aiosqlite+py-clob-client reliably) — captured as T9.8-Windows
backlog in TASKS.md.

Scope (pure construction; no await):
  * TradingEngine() instantiates with minimal AsyncMock db + MagicMock
    settings/scanner/odds_feed (no real DB, no network).
  * `_running` starts False.
  * `brain_flags` has the canonical 5-key set (T6.3 doctrine):
      {ai_brain, autopilot, candle_collector,
       regime_detection, thompson_sampling}
  * All 5 flags default True at construction time (UI state = ON until
    user toggles or DB boot-restore flips).
  * Sibling modules attached: risk (RiskManager), plugins (StrategyRegistry),
    lifecycle (StrategyLifecycle), optimizer (AutoOptimizer), selector,
    regime, drift, signals, kill_switch.
  * `_max_moves` starts empty dict (per-instance, Sprint 2 S2-03 fix —
    class-attr bug regression guard).
  * `_pending` starts empty list (Epic 5 T5.3 reservation list invariant).
  * `_open_positions` starts empty set.
  * `stop()` on non-running engine is a safe no-op (doesn't raise).

Out-of-scope (→ Windows backlog T9.8-REG):
  * `await engine.start()` — DB migration path + WS handshake
  * Real aiosqlite connection
  * plugin hot-reload / HyperOpt restore
  * Full cycle loop tick
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# Canonical 6-flag set pinned by T6.3 (brain_flags parity doctrine).
# Any drift here → RED. Adding a 6th flag requires ALL of:
#   (a) engine init dict update
#   (b) ai_handler UI button + valid_features allow-list entry
#   (c) DB boot-sync restore path update
#   (d) update of this canonical set constant.
CANONICAL_BRAIN_FLAGS = {
    "ai_brain",
    "autopilot",
    "candle_collector",
    "regime_detection",
    "thompson_sampling",
}


def _make_engine():
    """Construct TradingEngine with minimal mocks suitable for __init__ only.

    Returns (engine, db_mock) so tests can inspect mock call records.
    """
    from core.engine import TradingEngine

    settings = MagicMock()
    settings.paper_balance = 10000.0

    db = AsyncMock()
    db.conn = AsyncMock()
    db.conn.execute_fetchall = AsyncMock(return_value=[])
    db.get_setting = AsyncMock(return_value=None)

    scanner = MagicMock()
    odds_feed = MagicMock()

    engine = TradingEngine(settings, db, scanner, odds_feed)
    return engine, db


# ═══ 1. Construction succeeds ══════════════════════════════════════════


class TestConstruction:
    def test_instantiates_without_raise(self):
        engine, _ = _make_engine()
        assert engine is not None

    def test_running_starts_false(self):
        engine, _ = _make_engine()
        assert engine._running is False

    def test_cycle_counter_starts_zero(self):
        engine, _ = _make_engine()
        assert engine._cycle == 0


# ═══ 2. Brain flags — canonical 5-key set ══════════════════════════════


class TestBrainFlags:
    """T6.3 doctrine — canonical brain_flags set at boot."""

    def test_canonical_five_keys(self):
        engine, _ = _make_engine()
        assert set(engine.brain_flags.keys()) == CANONICAL_BRAIN_FLAGS, (
            "brain_flags key-set drifted — update T6.3 doctrine: UI panel, "
            "valid_features allow-list, boot-restore, CANONICAL_BRAIN_FLAGS "
            "must all agree."
        )

    def test_all_default_true(self):
        """UI shows all 5 as AÇIK until user toggle or DB boot-restore."""
        engine, _ = _make_engine()
        for k in CANONICAL_BRAIN_FLAGS:
            assert engine.brain_flags[k] is True, (
                f"brain_flags['{k}'] default drifted from True — UI will "
                f"show KAPALI at fresh install (ghost-on-boot regression)."
            )

    def test_no_extra_keys(self):
        """Guard against silent addition of a 7th ghost flag."""
        engine, _ = _make_engine()
        extras = set(engine.brain_flags.keys()) - CANONICAL_BRAIN_FLAGS
        assert not extras, (
            f"Unexpected brain_flags key(s) {extras!r} — if intentional, "
            f"update CANONICAL_BRAIN_FLAGS + UI + allow-list together."
        )


# ═══ 3. Sibling modules attached ═══════════════════════════════════════


class TestSiblings:
    """Contract: subsystems must exist on self.* after __init__."""

    @pytest.mark.parametrize(
        "attr",
        [
            "risk",
            "kill_switch",
            "selector",
            "regime",
            "drift",
            "signals",
            "plugins",
            "optimizer",
            "lifecycle",
        ],
    )
    def test_sibling_attached(self, attr):
        engine, _ = _make_engine()
        assert hasattr(engine, attr), (
            f"TradingEngine.{attr} missing after __init__ — upstream refactor "
            f"likely dropped a subsystem init."
        )
        assert getattr(engine, attr) is not None


# ═══ 4. Collection invariants (empty on boot) ══════════════════════════


class TestBootCollections:
    def test_max_moves_empty(self):
        """Sprint 2 S2-03: per-instance dict, not class-attr."""
        engine, _ = _make_engine()
        assert engine._max_moves == {}
        # Separate instance must have separate dict (class-attr regression)
        e2, _ = _make_engine()
        engine._max_moves["x"] = (0.1, 0.05)
        assert "x" not in e2._max_moves

    def test_pending_empty(self):
        """T5.3 reservation list starts empty."""
        engine, _ = _make_engine()
        assert engine._pending == []

    def test_open_positions_empty(self):
        engine, _ = _make_engine()
        assert engine._open_positions == set()

    def test_settled_slugs_empty(self):
        engine, _ = _make_engine()
        assert engine._settled_slugs == {}


# ═══ 5. stop() on non-running engine = no-op ═══════════════════════════


class TestStopNoOp:
    """engine.stop() must be safe to call when never started."""

    def test_stop_without_start_does_not_raise(self):
        engine, _ = _make_engine()
        # Constructed but never started — stop() should be idempotent.
        asyncio.run(engine.stop())
        assert engine._running is False

    def test_stop_twice_idempotent(self):
        engine, _ = _make_engine()
        asyncio.run(engine.stop())
        asyncio.run(engine.stop())  # should not raise
        assert engine._running is False
