"""Unit tests for Epic 6 T6.3 — brain_flags UI ↔ engine parity.

Every entry in `engine.brain_flags` represents a toggle that a Telegram
admin can flip in the AI Brain panel. For the toggle to be meaningful,
two things must be true:

  1. Every flag declared in `engine.brain_flags` must be exposed in the
     AI Brain panel UI (ai_handler.py `valid_features` set + keyboard
     layout) — otherwise the flag is a **reverse ghost** (engine listens
     but UI never flips it).

  2. Every flag declared in `engine.brain_flags` must be READ by at
     least one module outside the handler layer and outside the engine
     init (where the dict is populated). Otherwise the flag is a **true
     ghost** — the UI shows "AÇIK/KAPALI" but engine decisions ignore it.

T6.2 audit (2026-04-21) found:
  - drift_monitor, autopilot, kelly_sizing → TRUE GHOSTS
  - market_recorder → REVERSE GHOST

These tests fail under the pre-T6.3 code. They go GREEN only after
T6.3b/c/d/e fixes land.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PY = REPO_ROOT / "core" / "engine.py"
AI_HANDLER_PY = REPO_ROOT / "telegram_bot" / "handlers" / "ai_handler.py"


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _extract_brain_flags_keys() -> set[str]:
    """Parse core/engine.py and return the set of keys in brain_flags init.

    Looks for ``self.brain_flags = { "key": ... }`` and pulls every string
    literal key. Uses AST to avoid regex fragility.
    """
    src = ENGINE_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # looking for self.brain_flags = { ... }
        for tgt in node.targets:
            if (isinstance(tgt, ast.Attribute)
                    and tgt.attr == "brain_flags"
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "self"
                    and isinstance(node.value, ast.Dict)):
                keys: set[str] = set()
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
                return keys
    raise AssertionError("Could not locate self.brain_flags init in core/engine.py")


def _extract_valid_features() -> set[str]:
    """Parse ai_handler.py and return the brain_toggle valid_features set.

    Finds ``valid_features = { "ai_brain", ... }`` and extracts literals.
    """
    src = AI_HANDLER_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Name)
                    and tgt.id == "valid_features"
                    and isinstance(node.value, ast.Set)):
                out: set[str] = set()
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        out.add(elt.value)
                return out
    raise AssertionError(
        "Could not locate valid_features set in ai_handler.py")


def _flag_is_consumed_in_engine(flag_key: str) -> list[str]:
    """Return list of file:line hits where the flag is consumed.

    "Consumed" = referenced as a brain_flags key, OR a sibling runtime
    gate attribute that the toggle explicitly syncs. Excludes:
      - core/engine.py init line (where the dict is populated)
      - telegram_bot/handlers/ai_handler.py (the UI itself)
      - tests/ (self)
      - _archive/
    """
    hits: list[str] = []
    patterns = [
        rf'brain_flags\[["\']{flag_key}["\']\]',
        rf'brain_flags\.get\(["\']{flag_key}["\']',
    ]
    # For flags whose runtime gate lives on a sibling object's _enabled
    # attribute, the module file itself is an acceptable consumer —
    # e.g. data/candle_collector.py has `if not self._enabled:` loops that
    # gate the collector, and the UI toggle sets `cc._enabled`. That IS a
    # wired toggle, even though `brain_flags['candle_collector']` is not
    # directly referenced anywhere.
    #
    # For such flags, accept "self._enabled" reads (not writes!) inside
    # files whose path basename == flag_key.py.
    sibling_enabled_flags = {"candle_collector", "market_recorder"}

    # Walk all .py files except excluded dirs
    for py in REPO_ROOT.rglob("*.py"):
        parts = set(py.parts)
        if "_archive" in parts or "tests" in parts or "__pycache__" in parts:
            continue
        # Skip the UI handler and the engine init line
        rel = py.relative_to(REPO_ROOT).as_posix()
        if rel == "telegram_bot/handlers/ai_handler.py":
            continue
        try:
            content = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Is this the sibling-gate module for a sibling-enabled flag?
        is_sibling_module = (
            flag_key in sibling_enabled_flags
            and py.stem == flag_key
        )

        for lineno, line in enumerate(content.splitlines(), 1):
            # Skip the engine.py init dict line (where the dict is defined)
            if rel == "core/engine.py":
                # Engine init block reads like:  "drift_monitor": True,
                if re.match(rf'\s*["\']{flag_key}["\']\s*:\s*(True|False)',
                            line):
                    continue

            # Direct brain_flags usage — strongest signal
            for pat in patterns:
                if re.search(pat, line):
                    hits.append(f"{rel}:{lineno}")
                    break
            else:
                # Sibling-gate match: `if not self._enabled` or similar
                # READ (not assignment) inside the named module.
                if is_sibling_module:
                    # Match read-style: "self._enabled" appearing in a
                    # conditional or expression, but NOT as the LHS of an
                    # assignment (`self._enabled = ...`).
                    if re.search(r"self\._enabled\b", line) and \
                            not re.search(r"self\._enabled\s*=\s*[^=]",
                                          line):
                        hits.append(f"{rel}:{lineno} (sibling _enabled gate)")
    return hits


# ═══════════════════════════════════════════════════════════════════════
# Source-of-truth tests
# ═══════════════════════════════════════════════════════════════════════

def test_brain_flags_dict_parseable():
    """Sanity: AST extraction finds a non-empty brain_flags dict."""
    flags = _extract_brain_flags_keys()
    assert len(flags) >= 4, f"brain_flags dict looks too small: {flags}"
    assert "ai_brain" in flags, "ai_brain missing — audit premise broken"


def test_valid_features_set_parseable():
    """Sanity: AST extraction finds the ai_handler valid_features set."""
    feats = _extract_valid_features()
    assert len(feats) >= 4
    assert "ai_brain" in feats


# ═══════════════════════════════════════════════════════════════════════
# Reverse-ghost test — every engine flag must appear in UI valid_features
# ═══════════════════════════════════════════════════════════════════════

def test_no_reverse_ghost_flags():
    """Every brain_flags key MUST be toggleable from the UI.

    Fails pre-T6.3e: market_recorder is in brain_flags but not in
    ai_handler.valid_features → engine listens, UI never flips.
    """
    engine_flags = _extract_brain_flags_keys()
    ui_flags = _extract_valid_features()
    reverse_ghosts = engine_flags - ui_flags
    assert not reverse_ghosts, (
        f"Reverse ghosts — engine has these flags but UI doesn't expose "
        f"them: {sorted(reverse_ghosts)}")


# ═══════════════════════════════════════════════════════════════════════
# True-ghost tests — every UI flag must be consumed somewhere
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("flag", [
    "ai_brain",
    "thompson_sampling",
    "regime_detection",
    "candle_collector",
    "market_recorder",
])
def test_known_good_flag_has_engine_consumer(flag):
    """Regression anchor: flags that were wired correctly pre-T6.3."""
    hits = _flag_is_consumed_in_engine(flag)
    assert hits, (
        f"[REGRESSION] {flag} used to be consumed somewhere outside "
        f"engine init + ai_handler UI — now it isn't.")


@pytest.mark.parametrize("flag", [
    "drift_monitor",
    "autopilot",
    "kelly_sizing",
])
def test_no_true_ghost_flags(flag):
    """Every flag in brain_flags must have at least one engine consumer.

    Fails pre-T6.3:
      - drift_monitor: no module, no reader → fail
      - autopilot: AutoPilot class doesn't check the flag → fail
      - kelly_sizing: engine reads engine._kelly_mode instead → fail

    Post-T6.3:
      - drift_monitor will be REMOVED from brain_flags (not fixed in-place),
        so test_brain_flags_init_matches_expected_set (below) will no longer
        include it and this parametrize entry should also be updated.
      - autopilot: AutoPilot.generate_actions gains a flag gate → pass
      - kelly_sizing: will be REMOVED; UI toggle retargets _kelly_mode.
    """
    engine_flags = _extract_brain_flags_keys()
    if flag not in engine_flags:
        pytest.skip(f"{flag} intentionally removed from brain_flags — OK")
    hits = _flag_is_consumed_in_engine(flag)
    assert hits, (
        f"GHOST — brain_flags['{flag}'] is toggleable in the AI Brain UI "
        f"but nothing in the engine reads it. Either wire it to an engine "
        f"consumer (T6.3 Option A) or remove it from brain_flags + UI "
        f"(T6.3 Option B).")


# ═══════════════════════════════════════════════════════════════════════
# Whole-surface invariant (post-T6.3 shape)
# ═══════════════════════════════════════════════════════════════════════

def test_brain_flags_init_matches_expected_set():
    """Pin the post-T6.3 brain_flags shape.

    Pre-T6.3 dict had 8 keys (incl. 3 ghosts). Post-T6.3 dict should be:
      ai_brain, thompson_sampling, regime_detection, autopilot,
      candle_collector, market_recorder
    (drift_monitor + kelly_sizing removed; autopilot gated not removed.)
    """
    expected = {
        "ai_brain",
        "thompson_sampling",
        "regime_detection",
        "autopilot",
        "candle_collector",
        "market_recorder",
    }
    actual = _extract_brain_flags_keys()
    # Intentionally a strict equality check — we want to catch any silent
    # re-addition of ghosts in the future.
    assert actual == expected, (
        f"brain_flags dict drifted from the T6.3 canonical set.\n"
        f"  Missing from expected: {expected - actual}\n"
        f"  Unexpected extras:     {actual - expected}")
