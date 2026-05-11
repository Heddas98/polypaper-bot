"""Unit tests for Epic 6 T6.3e / Epic 9 T9.7 — market_recorder UI↔engine
full round-trip parity.

Background
----------
`test_brain_flags_parity.py` + `test_boot_sibling_sync.py` cover the
generic ghost-doctrine invariants. This file pins the **explicit**
market_recorder toggle contract end-to-end:

  UI (callback_data "brain_toggle_market_recorder")
   → ai_handler.brain_toggle_handler
   → engine.brain_flags["market_recorder"] flip
   → engine.market_recorder._enabled ← new_state
   → db.set_setting("brain_flags.market_recorder", "1"|"0")
   → on boot, engine.start() sibling-sync restores sibling._enabled

Pre-T6.3e the callback handler only flipped the brain_flags dict and
forgot to propagate to `mr._enabled`, so the user saw "KAPALI" but the
recorder kept writing. This test pins each step structurally (AST +
regex) so a regression in any layer fails loudly.

Out-of-scope (already covered elsewhere):
  * boot sibling sync — `test_boot_sibling_sync.py`
  * reverse-ghost (UI exposes the flag) — `test_brain_flags_parity.py`
  * canonical 6-flag set — `test_brain_flags_parity.py`
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AI_HANDLER_PY = REPO_ROOT / "telegram_bot" / "handlers" / "ai_handler.py"
ENGINE_PY = REPO_ROOT / "core" / "engine.py"
MAIN_PY = REPO_ROOT / "main.py"


def _ai_handler_src() -> str:
    return AI_HANDLER_PY.read_text(encoding="utf-8")


def _engine_src() -> str:
    return ENGINE_PY.read_text(encoding="utf-8")


# ═══ 1. UI layer: button + callback_data ═══════════════════════════════


class TestUiLayer:
    """The AI Brain panel must render a button with callback_data
    'brain_toggle_market_recorder'."""

    def test_callback_data_string_present(self):
        src = _ai_handler_src()
        assert "brain_toggle_market_recorder" in src, (
            "T6.3e regression: the AI Brain panel no longer declares the "
            "callback_data literal 'brain_toggle_market_recorder' — users "
            "cannot toggle the recorder from Telegram UI."
        )

    def test_recorder_button_label_present(self):
        """Button label '📹 Recorder' must render in the button grid."""
        src = _ai_handler_src()
        assert "Recorder" in src, "T6.3e regression: '📹 Recorder' button removed from panel."

    def test_status_line_uses_fmt_flag(self):
        """Status line must use fmt_flag('market_recorder'), not hardcoded."""
        src = _ai_handler_src()
        assert re.search(r"fmt_flag\s*\(\s*['\"]market_recorder['\"]\s*\)", src), (
            "Status line must call fmt_flag('market_recorder') so UI state "
            "mirrors engine.brain_flags live, not a hardcoded value."
        )


# ═══ 2. Handler: valid_features allow-list ═════════════════════════════


class TestHandlerAllowList:
    """brain_toggle_handler must accept 'market_recorder' in valid_features."""

    def test_market_recorder_in_valid_features(self):
        src = _ai_handler_src()
        # Locate the valid_features set and confirm market_recorder literal
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if (
                        isinstance(tgt, ast.Name)
                        and tgt.id == "valid_features"
                        and isinstance(node.value, ast.Set)
                    ):
                        members = {
                            e.value
                            for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
                        if "market_recorder" in members:
                            found = True
                            break
        assert found, (
            "valid_features set no longer contains 'market_recorder' — "
            "unknown-feature branch will reject the UI toggle and the "
            "admin sees a generic error (T6.3e reverse-ghost regression)."
        )


# ═══ 3. Handler: sibling _enabled propagation ══════════════════════════


class TestSiblingPropagation:
    """The `if feature == "market_recorder":` branch must set mr._enabled."""

    def test_market_recorder_branch_writes_enabled(self):
        src = _ai_handler_src()
        # Must exist the conditional block that writes mr._enabled = new_state
        # Be lenient on whitespace but strict on presence.
        pattern = re.compile(
            r"if\s+feature\s*==\s*['\"]market_recorder['\"]\s*:"
            r"[\s\S]{0,250}?"
            r"mr\._enabled\s*=\s*new_state",
            re.MULTILINE,
        )
        assert pattern.search(src), (
            "T6.3e regression: the handler branch that propagates the "
            "toggle to engine.market_recorder._enabled is missing or broken. "
            "Without it, the UI flips brain_flags but the recorder keeps "
            "running (silent ghost)."
        )

    def test_db_setting_key_pinned(self):
        """Persistence key must be 'brain_flags.market_recorder'.

        The boot loader in engine.start() reads settings with exactly this
        prefix — any drift here silently disables persistence.
        """
        src = _ai_handler_src()
        assert re.search(r"f['\"]brain_flags\.\{feature\}['\"]", src), (
            "DB persistence key template 'brain_flags.{feature}' drifted. "
            "Boot restore path in engine.start() will silently lose the "
            "user's toggle across restart."
        )


# ═══ 4. Engine init: flag dict still declares the key ══════════════════


class TestEngineInit:
    """core/engine.py init block must still declare market_recorder=True."""

    def test_init_dict_declares_market_recorder(self):
        src = _engine_src()
        # Match inside the init dict: "market_recorder": True,
        assert re.search(r'["\']market_recorder["\']\s*:\s*True', src), (
            "Engine init dict no longer seeds brain_flags['market_recorder']. "
            "Pre-T6.3e state returns: flag looks missing in UI → disabled "
            "toggle."
        )


# ═══ 5. Semantic simulation — toggle propagates to sibling ═════════════


class _StubMarketRecorder:
    def __init__(self, initial: bool = True):
        self._enabled = initial


class _StubEngine:
    """Minimal replica of the handler's surface touched by the toggle."""

    def __init__(self, recorder, flag_initial: bool = True):
        self.brain_flags = {"market_recorder": flag_initial}
        self.market_recorder = recorder
        self._db_writes: list[tuple[str, str]] = []

    async def db_set_setting(self, key: str, val: str):
        self._db_writes.append((key, val))


def _run_toggle_once(engine: _StubEngine) -> bool:
    """Reproduce the ai_handler toggle logic (the subset that affects
    market_recorder). Returns the new toggle state."""
    feature = "market_recorder"
    engine.brain_flags[feature] = not engine.brain_flags.get(feature, True)
    new_state = engine.brain_flags[feature]
    # DB persist
    # (async in prod; sync here for unit test)
    engine._db_writes.append((f"brain_flags.{feature}", "1" if new_state else "0"))
    # Sibling sync
    if feature == "market_recorder":
        mr = getattr(engine, "market_recorder", None)
        if mr:
            mr._enabled = new_state
    return new_state


class TestToggleSim:
    """Behaviour simulation — mimics the ai_handler flow end-to-end."""

    def test_first_toggle_off(self):
        recorder = _StubMarketRecorder(initial=True)
        engine = _StubEngine(recorder, flag_initial=True)
        state = _run_toggle_once(engine)
        assert state is False
        assert engine.brain_flags["market_recorder"] is False
        assert (
            recorder._enabled is False
        ), "Sibling did not propagate — silent ghost (T6.3e regression)."

    def test_second_toggle_back_on(self):
        recorder = _StubMarketRecorder(initial=True)
        engine = _StubEngine(recorder, flag_initial=True)
        _run_toggle_once(engine)  # → False
        state = _run_toggle_once(engine)  # → True
        assert state is True
        assert recorder._enabled is True

    def test_db_persistence_key_and_value(self):
        """Both 0 and 1 must be written with the canonical key prefix."""
        recorder = _StubMarketRecorder(initial=True)
        engine = _StubEngine(recorder, flag_initial=True)
        _run_toggle_once(engine)  # → False
        _run_toggle_once(engine)  # → True
        assert ("brain_flags.market_recorder", "0") in engine._db_writes
        assert ("brain_flags.market_recorder", "1") in engine._db_writes

    def test_missing_recorder_tolerated(self):
        """If engine has no .market_recorder attached yet, toggle still
        updates brain_flags + DB without crashing."""
        engine = _StubEngine(recorder=None, flag_initial=True)
        state = _run_toggle_once(engine)
        assert state is False
        assert engine.brain_flags["market_recorder"] is False
        # No crash on missing sibling
