"""Unit tests for Epic 6 T6.3e-fix-2 — boot sibling-gate sync.

When engine.start() loads brain_flags from DB, sibling-gated flags
(candle_collector) must have their `_enabled` attribute sync'd to match
the loaded dict. Otherwise a user who toggled the flag OFF via UI would
see it silently resurrect on bot restart, because the CandleCollector
constructor defaults `_enabled = True` and the boot loader previously
only wrote to the brain_flags dict.

2026-05-22: market_recorder removed (component deleted) — the sibling
gate now covers candle_collector only.

These tests are AST-based so they run without a full engine bootstrap
(which requires httpx + websockets + live DB). The goal is to pin the
structural contract: the sync block must exist, read brain_flags and
write sibling._enabled.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PY = REPO_ROOT / "core" / "engine.py"


def _engine_source() -> str:
    return ENGINE_PY.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════
# Structural contracts
# ═══════════════════════════════════════════════════════════════════════


def test_sibling_gates_list_present():
    """Engine boot loader must declare the sibling-gate list."""
    src = _engine_source()
    assert "_sibling_gates" in src, (
        "Boot loader missing `_sibling_gates` list — T6.3e-fix-2 "
        "regression. Toggling Candles OFF will not survive restart."
    )


def test_sibling_gates_cover_candle():
    """candle_collector must be in the sibling-gate list."""
    src = _engine_source()
    assert re.search(
        r'_sibling_gates\s*=\s*\[[^\]]*"candle_collector"[^\]]*\]', src, re.DOTALL
    ), "candle_collector missing from _sibling_gates"


def test_sync_reads_brain_flags_and_writes_enabled():
    """Sync block must: get flag value from brain_flags + set _enabled."""
    src = _engine_source()
    assert re.search(
        r"self\.brain_flags\.get\(flag_key", src
    ), "Sync block should READ from self.brain_flags via flag_key"
    assert re.search(
        r"sibling\._enabled\s*=\s*desired", src
    ), "Sync block should WRITE sibling._enabled from desired value"


def test_sync_block_runs_in_start_method():
    """The sync block must be inside engine.start() (post DB load)."""
    tree = ast.parse(_engine_source())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start":
            body_src = ast.get_source_segment(_engine_source(), node) or ""
            if "_sibling_gates" in body_src and "sibling._enabled" in body_src:
                found = True
                break
    assert found, (
        "Sibling-sync block must live inside engine.start() (after brain "
        "flags load from DB). Moving it outside breaks the boot ordering."
    )


# ═══════════════════════════════════════════════════════════════════════
# Semantic simulation — mimic the boot loader logic on a stub engine
# ═══════════════════════════════════════════════════════════════════════


class _StubSibling:
    def __init__(self, initial=True):
        self._enabled = initial


class _StubEngine:
    """Mimics the parts of TradingEngine the sync block touches."""

    def __init__(self, brain_flags, candle=None):
        self.brain_flags = dict(brain_flags)
        self.candle_collector = candle

    def run_sibling_sync(self):
        """Copy-paste of the production logic (T6.3e-fix-2)."""
        _sibling_gates = [
            ("candle_collector", "candle_collector"),
        ]
        events = []
        for flag_key, attr_name in _sibling_gates:
            sibling = getattr(self, attr_name, None)
            if sibling is not None and hasattr(sibling, "_enabled"):
                desired = self.brain_flags.get(flag_key, True)
                if sibling._enabled != desired:
                    sibling._enabled = desired
                    events.append((attr_name, desired))
        return events


def test_sim_off_flag_silences_sibling():
    """Classic bug scenario: brain_flags OFF → sibling must flip to OFF."""
    candle = _StubSibling(initial=True)  # constructor default
    eng = _StubEngine(brain_flags={"candle_collector": False}, candle=candle)
    events = eng.run_sibling_sync()
    assert candle._enabled is False, "Candle should flip to OFF"
    assert ("candle_collector", False) in events


def test_sim_already_matching_is_noop():
    """If sibling already matches brain_flags, no write/event."""
    candle = _StubSibling(initial=True)
    eng = _StubEngine(brain_flags={"candle_collector": True}, candle=candle)
    events = eng.run_sibling_sync()
    assert events == [], "No-op sync should emit no events"


def test_sim_missing_sibling_tolerated():
    """If the sibling object is None (not attached), sync must not crash."""
    eng = _StubEngine(brain_flags={"candle_collector": False}, candle=None)
    events = eng.run_sibling_sync()
    assert events == []


def test_sim_default_true_when_flag_absent():
    """Missing flag key (shouldn't happen in prod) defaults to True."""
    candle = _StubSibling(initial=False)  # previously turned off
    eng = _StubEngine(brain_flags={}, candle=candle)  # empty — edge case
    events = eng.run_sibling_sync()
    # Missing key → default True → sibling False ≠ True → flip to True
    assert candle._enabled is True
    assert ("candle_collector", True) in events
