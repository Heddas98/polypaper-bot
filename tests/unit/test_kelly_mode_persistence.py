"""Unit tests for Epic 6 T6.5 — Kelly mode DB persistence.

Prior to T6.5, toggling Kelly mode via either the `/kelly_toggle` command
or the AI Brain panel's 📈 Kelly button was purely an in-memory change on
`engine._kelly_mode`. A bot restart reset Kelly back to True regardless
of the user's last preference.

T6.5 adds a `engine.kelly_mode` bot_setting row that both writers persist
and the boot loader reads back. Contract:

1. DB key: exactly `engine.kelly_mode`, values `"1"` / `"0"` (matches
   the brain_flags persistence style).
2. Boot loader: in `engine.start()`, AFTER the brain_flags DB load
   block, read `engine.kelly_mode` and set `self._kelly_mode` if a
   persisted value exists. Missing key → leave constructor default
   (True) untouched.
3. Write from `/kelly_toggle`: `kelly_toggle_command` must persist
   the new value via `set_setting("engine.kelly_mode", ...)`.
4. Write from AI Brain panel: `brain_toggle_callback`'s kelly_sizing
   virtual-flag branch must persist the same key/value.

These tests are AST-based + semantic simulation to run without
full httpx/websockets engine bootstrap.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PY = REPO_ROOT / "core" / "engine.py"
STRATEGIES_PY = REPO_ROOT / "telegram_bot" / "handlers" / "strategies.py"
AI_HANDLER_PY = REPO_ROOT / "telegram_bot" / "handlers" / "ai_handler.py"

KELLY_DB_KEY = "engine.kelly_mode"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════
# Structural contracts — BOOT loader (engine.start)
# ═══════════════════════════════════════════════════════════════════════


def test_engine_start_reads_kelly_mode_from_db():
    """engine.start() must read `engine.kelly_mode` setting at boot."""
    src = _read(ENGINE_PY)
    # Look for get_setting call with the canonical key anywhere in the file
    assert re.search(rf'get_setting\s*\(\s*["\']{re.escape(KELLY_DB_KEY)}["\']', src), (
        f'Boot loader missing `get_setting("{KELLY_DB_KEY}")` — Kelly '
        "mode will not survive restart."
    )


def test_engine_start_sets_kelly_mode_from_db_value():
    """After DB read, `_kelly_mode` must be assigned from persisted value."""
    src = _read(ENGINE_PY)
    # Expect a write to self._kelly_mode tied to the persisted string.
    # Accept either `== "1"` comparison or truthy-string coercion, as
    # long as the write exists somewhere near the key read.
    idx = src.find(KELLY_DB_KEY)
    assert idx >= 0, f"{KELLY_DB_KEY} missing from engine.py"
    window = src[max(0, idx - 200) : idx + 600]
    assert re.search(r"self\._kelly_mode\s*=", window), (
        "No assignment to `self._kelly_mode` found near the "
        f"`{KELLY_DB_KEY}` read — boot loader incomplete."
    )


def test_kelly_boot_read_inside_start_method():
    """The Kelly DB read must be inside engine.start() (not module-top)."""
    tree = ast.parse(_read(ENGINE_PY))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "start":
            body_src = ast.get_source_segment(_read(ENGINE_PY), node) or ""
            if KELLY_DB_KEY in body_src and "_kelly_mode" in body_src:
                found = True
                break
    assert found, (
        "Kelly boot read must live inside engine.start() so it runs "
        "after the DB connection is established and brain_flags load."
    )


# ═══════════════════════════════════════════════════════════════════════
# Structural contracts — WRITERS
# ═══════════════════════════════════════════════════════════════════════


def test_kelly_toggle_command_persists_to_db():
    """/kelly_toggle handler must call set_setting with canonical key."""
    src = _read(STRATEGIES_PY)
    # Find the kelly_toggle_command function body
    tree = ast.parse(src)
    func_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "kelly_toggle_command":
            func_src = ast.get_source_segment(src, node) or ""
            break
    assert func_src, "kelly_toggle_command not found in strategies.py"
    assert re.search(rf'set_setting\s*\(\s*["\']{re.escape(KELLY_DB_KEY)}["\']', func_src), (
        "kelly_toggle_command must persist the new state via "
        f'set_setting("{KELLY_DB_KEY}", ...) — otherwise the '
        "Kelly toggle is in-memory-only and lost on restart."
    )


def test_brain_toggle_callback_persists_kelly():
    """AI Brain panel kelly_sizing branch must also persist the flag."""
    src = _read(AI_HANDLER_PY)
    tree = ast.parse(src)
    func_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "brain_toggle_callback":
            func_src = ast.get_source_segment(src, node) or ""
            break
    assert func_src, "brain_toggle_callback not found in ai_handler.py"
    # The kelly_sizing branch should include the same DB persistence.
    assert (
        "kelly_sizing" in func_src
    ), "kelly_sizing virtual-flag branch missing from brain_toggle_callback"
    assert re.search(rf'set_setting\s*\(\s*["\']{re.escape(KELLY_DB_KEY)}["\']', func_src), (
        "brain_toggle_callback kelly_sizing branch must persist via "
        f'set_setting("{KELLY_DB_KEY}", ...). Divergence between the '
        "two writers (command vs panel) is a classic ghost-toggle source."
    )


# ═══════════════════════════════════════════════════════════════════════
# Semantic simulation — mimic the full persistence round trip
# ═══════════════════════════════════════════════════════════════════════


class _StubDB:
    """In-memory stand-in for the bot_settings table."""

    def __init__(self, initial=None):
        self._store: dict[str, str] = dict(initial or {})

    async def get_setting(self, key: str, default=None):
        return self._store.get(key, default)

    async def set_setting(self, key: str, value: str):
        self._store[key] = value


class _StubEngine:
    def __init__(self, db, initial_kelly=True):
        self.db = db
        self._kelly_mode: bool = initial_kelly


async def _simulate_boot_load(engine):
    """Mirrors the production boot logic: read key, set attr if present."""
    saved = await engine.db.get_setting(KELLY_DB_KEY)
    if saved is not None:
        engine._kelly_mode = saved == "1"


async def _simulate_toggle_and_persist(engine):
    """Mirrors /kelly_toggle + brain panel writers."""
    engine._kelly_mode = not engine._kelly_mode
    await engine.db.set_setting(KELLY_DB_KEY, "1" if engine._kelly_mode else "0")


@pytest.mark.asyncio
async def test_sim_boot_honors_persisted_off():
    """User last turned Kelly OFF → DB has "0" → boot must respect."""
    db = _StubDB({KELLY_DB_KEY: "0"})
    engine = _StubEngine(db, initial_kelly=True)  # constructor default True
    await _simulate_boot_load(engine)
    assert engine._kelly_mode is False


@pytest.mark.asyncio
async def test_sim_boot_honors_persisted_on():
    """Persisted "1" → boot stays ON (explicit even if already default)."""
    db = _StubDB({KELLY_DB_KEY: "1"})
    engine = _StubEngine(db, initial_kelly=False)
    await _simulate_boot_load(engine)
    assert engine._kelly_mode is True


@pytest.mark.asyncio
async def test_sim_boot_missing_key_keeps_default():
    """No DB row → constructor default wins (do not force-True on None)."""
    db = _StubDB({})
    engine = _StubEngine(db, initial_kelly=True)
    await _simulate_boot_load(engine)
    assert engine._kelly_mode is True  # default preserved


@pytest.mark.asyncio
async def test_sim_toggle_persists_new_value():
    """Toggle must update both in-memory and DB."""
    db = _StubDB({})
    engine = _StubEngine(db, initial_kelly=True)
    await _simulate_toggle_and_persist(engine)
    assert engine._kelly_mode is False
    assert db._store[KELLY_DB_KEY] == "0"


@pytest.mark.asyncio
async def test_sim_round_trip():
    """Full cycle: toggle → new engine boot → state preserved."""
    db = _StubDB({})
    # First session: user disables Kelly
    engine_a = _StubEngine(db, initial_kelly=True)
    await _simulate_toggle_and_persist(engine_a)
    assert engine_a._kelly_mode is False
    assert db._store[KELLY_DB_KEY] == "0"
    # Bot restart simulated: fresh engine, same DB
    engine_b = _StubEngine(db, initial_kelly=True)  # default True
    await _simulate_boot_load(engine_b)
    assert engine_b._kelly_mode is False, (
        "After restart, Kelly should remain OFF — regression would " "force-reset to default True."
    )


@pytest.mark.asyncio
async def test_sim_double_toggle_round_trip():
    """Off → On → Off must persist correctly across each step."""
    db = _StubDB({})
    engine = _StubEngine(db, initial_kelly=True)
    await _simulate_toggle_and_persist(engine)  # True → False
    assert db._store[KELLY_DB_KEY] == "0"
    await _simulate_toggle_and_persist(engine)  # False → True
    assert db._store[KELLY_DB_KEY] == "1"
    assert engine._kelly_mode is True
