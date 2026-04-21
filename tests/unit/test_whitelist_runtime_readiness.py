"""Unit tests for Epic 6 T6.4 — env-whitelist ghost-induction guard.

Background
----------
T6.1 fixed the PNL_PAUSE_THRESHOLD silent ghost: the constant was captured
at module-import into `MIN_PNL_FOR_PAUSE`, so `/env_toggle PNL_PAUSE_THRESHOLD
-8.0` patched os.environ but the optimizer kept using the import-time value.

Every key in `config.env_whitelist.ENV_WHITELIST` is user-toggleable at
runtime via the `/env_toggle` admin command — it writes `os.environ[KEY]=val`
and expects consumers to re-read on the next call. A production module
that captures a whitelisted env var at module-top (e.g. `MYCONST =
os.getenv("KEY", "default")`) will ghost the toggle.

T6.4 Invariant
--------------
For every env key in `ENV_WHITELIST`, at least one reference in production
code (core/, telegram_bot/) MUST be a runtime re-read — meaning it lives
inside a function/method body, NOT at module scope as an import-time
assignment. Scripts and tests are exempt (they run one-shot, not inside
the always-running engine).

Auto-optimizer module-top constants that are NOT in whitelist (e.g.
MIN_TRADES_BEFORE_PAUSE, ROLLING_WR_WINDOW, ROLLING_WR_KILL, ADAPTIVE_PNL_*)
are fine — they're configured via .env + bot restart, which is standard
Python practice. The guard below only fires if someone adds one of these
to the whitelist without first adding a `_get_*()` runtime helper.

This prevents future Epic-6-style audits from re-discovering the same
pattern: one catch-all test replaces the ad-hoc grep/audit cycle.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]

# Directories scanned for runtime-read evidence. Excludes tests, scripts,
# _archive, and everything else that runs one-shot or outside the engine.
PROD_DIRS = ("core", "telegram_bot", "db")


def _load_whitelist():
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from config.env_whitelist import ENV_WHITELIST  # type: ignore
    return ENV_WHITELIST


def _iter_prod_py_files():
    for d in PROD_DIRS:
        root = REPO_ROOT / d
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            # skip __pycache__
            if "__pycache__" in p.parts:
                continue
            yield p


def _find_env_sites(key: str):
    """Return list of (path, line, is_module_top) for os.getenv("key")."""
    pattern = re.compile(
        rf'os\.getenv\s*\(\s*["\']{re.escape(key)}["\']')
    sites = []
    for p in _iter_prod_py_files():
        try:
            src = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue

        # Build a set of line numbers that fall inside function/method bodies
        runtime_line_set = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", None) or start
                for ln in range(start, end + 1):
                    runtime_line_set.add(ln)

        for i, line in enumerate(src.splitlines(), 1):
            if pattern.search(line):
                sites.append((str(p.relative_to(REPO_ROOT)), i,
                              i not in runtime_line_set))
    return sites


# ═══════════════════════════════════════════════════════════════════════
# Baseline sanity — whitelist is non-empty
# ═══════════════════════════════════════════════════════════════════════

def test_whitelist_nonempty():
    wl = _load_whitelist()
    assert len(wl) > 0, "ENV_WHITELIST is empty — did the import break?"


# ═══════════════════════════════════════════════════════════════════════
# Core invariant — every whitelist key has at least one runtime read
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("key", sorted(_load_whitelist().keys()))
def test_whitelist_key_is_runtime_read(key):
    """Every whitelist-toggleable key must be read at runtime somewhere."""
    sites = _find_env_sites(key)
    assert sites, (
        f"Whitelist key {key!r} has NO os.getenv() references in "
        f"production dirs {PROD_DIRS}. Either the key is dead (remove "
        "from whitelist) or it's read some other way (not supported).")
    runtime_sites = [s for s in sites if not s[2]]
    module_top_sites = [s for s in sites if s[2]]
    assert runtime_sites, (
        f"Whitelist key {key!r} is ONLY read at module-top level in "
        f"production code. Sites: {module_top_sites}. This is a silent "
        f"ghost — /env_toggle {key} will patch os.environ but the cached "
        "module-top value wins. Apply T6.1 pattern: move the read into a "
        "helper like `_get_{}()` so it re-reads per call.".format(
            key.lower().replace(".", "_")))


# ═══════════════════════════════════════════════════════════════════════
# Regression pins — keys with documented helpers stay honored
# ═══════════════════════════════════════════════════════════════════════

def test_pnl_pause_threshold_has_helper():
    """T6.1 landmark: PNL_PAUSE_THRESHOLD must keep its helper."""
    src = (REPO_ROOT / "core" / "auto_optimizer.py").read_text(encoding="utf-8")
    assert "_get_pnl_pause_threshold" in src, (
        "T6.1 regression: `_get_pnl_pause_threshold()` helper removed from "
        "core/auto_optimizer.py — silent ghost returns.")
    # Helper must be called, not just defined (dead helpers don't help)
    tree = ast.parse(src)
    call_sites = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_get_pnl_pause_threshold"
    ]
    assert call_sites, (
        "_get_pnl_pause_threshold defined but never called — T6.1 regression.")


# ═══════════════════════════════════════════════════════════════════════
# Future-proof — auto_optimizer module-top constants must NOT silently
# join the whitelist without a runtime helper
# ═══════════════════════════════════════════════════════════════════════

# These env var names are captured at module-top in auto_optimizer.py.
# If any of them is ever added to ENV_WHITELIST without also refactoring
# into a _get_*() helper, /env_toggle will ghost them — the test
# `test_whitelist_key_is_runtime_read` above will catch it, but pin the
# list here so the intent is explicit and reviewable.
AUTO_OPTIMIZER_MODULE_TOP_ENV_VARS = {
    "MIN_TRADES_BEFORE_PAUSE",
    "ROLLING_WR_WINDOW",
    "ROLLING_WR_KILL",
    "ADAPTIVE_PNL_ENABLED",
    "ADAPTIVE_PNL_STEP",
    "ADAPTIVE_PNL_TRADES_PER_STEP",
    "ADAPTIVE_PNL_FLOOR",
    "PROTECTED_STRATEGY_TYPES",
}


def test_auto_optimizer_module_top_vars_not_in_whitelist():
    """Safety pin: if these env vars migrate into whitelist, CI must fail
    until someone first adds the runtime helper."""
    wl_keys = set(_load_whitelist().keys())
    overlap = AUTO_OPTIMIZER_MODULE_TOP_ENV_VARS & wl_keys
    assert not overlap, (
        f"These auto_optimizer env vars are now in ENV_WHITELIST but still "
        f"captured at module-top (silent ghost risk): {sorted(overlap)}. "
        f"Apply T6.1 pattern: convert to a `_get_*()` runtime helper in "
        "core/auto_optimizer.py BEFORE adding to whitelist. See commit "
        "log for T6.1 (PNL_PAUSE_THRESHOLD) as the reference example.")
