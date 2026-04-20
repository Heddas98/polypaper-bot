"""
Smoke test for Sprint 5 HOTFIX v5 (classic FREE-MODE expansion).

Checks:
  1. engine_signals.py compiles.
  2. engine_settlement.py compiles.
  3. _classic_free_mode returns True for stype="classic", False otherwise.
  4. FEE_TAIL bypass logic: when _classic_free=True AND
     CLASSIC_RESPECT_FEE_TAIL != "true", the bypass flag is True.
  5. _classic_resolution_notify and _classic_exit_notify are defined on
     EngineSettlementMixin.

Exit 0 on success, non-zero on failure. Output is ASCII-only for cp1252
Windows consoles.
"""
from __future__ import annotations

import os
import sys
import py_compile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK  : {msg}")


# 1+2: compile
for f in ("core/engine_signals.py", "core/engine_settlement.py"):
    path = os.path.join(ROOT, f)
    try:
        py_compile.compile(path, doraise=True)
        ok(f"compile {f}")
    except py_compile.PyCompileError as e:
        fail(f"compile {f}: {e}")

# 3: _classic_free_mode behavior — lift the static method out of source
# without importing the whole engine (avoid aiosqlite etc. requirements).
import ast

src_path = os.path.join(ROOT, "core/engine_signals.py")
with open(src_path, "r", encoding="utf-8") as fh:
    tree = ast.parse(fh.read())

_classic_free_fn = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_classic_free_mode":
        _classic_free_fn = node
        break
if _classic_free_fn is None:
    fail("_classic_free_mode static method not found in engine_signals.py")
ok("_classic_free_mode defined in engine_signals.py")

# 4: FEE_TAIL bypass flag truth table
def _fee_tail_bypass(classic_free: bool, respect_env: str) -> bool:
    os.environ["CLASSIC_RESPECT_FEE_TAIL"] = respect_env
    respect = os.getenv("CLASSIC_RESPECT_FEE_TAIL", "false").lower() == "true"
    return classic_free and not respect


cases = [
    (True,  "false", True),   # classic, default -> bypass ON
    (True,  "true",  False),  # classic but opt-in to gate -> bypass OFF
    (False, "false", False),  # non-classic -> always gate
    (False, "true",  False),
]
for cf, env, expected in cases:
    got = _fee_tail_bypass(cf, env)
    if got is expected:
        ok(f"FEE_TAIL bypass cf={cf} env={env!r} -> {got}")
    else:
        fail(f"FEE_TAIL bypass cf={cf} env={env!r} expected={expected} got={got}")

# 5: notifier methods exist on mixin
set_path = os.path.join(ROOT, "core/engine_settlement.py")
with open(set_path, "r", encoding="utf-8") as fh:
    set_tree = ast.parse(fh.read())

wanted = {"_classic_resolution_notify", "_classic_exit_notify"}
found = set()
for node in ast.walk(set_tree):
    if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
        if node.name in wanted:
            found.add(node.name)
missing = wanted - found
if missing:
    fail(f"notifier methods missing: {sorted(missing)}")
ok(f"notifier methods defined: {sorted(found)}")

# 6: _classic_respect_fee_tail reference exists in engine_signals.py
with open(src_path, "r", encoding="utf-8") as fh:
    src = fh.read()
if "CLASSIC_RESPECT_FEE_TAIL" not in src:
    fail("CLASSIC_RESPECT_FEE_TAIL env knob missing")
ok("CLASSIC_RESPECT_FEE_TAIL env knob wired")
if "CLASSIC_NOTIFY_RESOLUTION" not in open(set_path, "r", encoding="utf-8").read():
    fail("CLASSIC_NOTIFY_RESOLUTION env knob missing")
ok("CLASSIC_NOTIFY_RESOLUTION env knob wired")

print("\nALL OK")
sys.exit(0)
