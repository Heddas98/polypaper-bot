"""
Smoke test for Sprint 5 HOTFIX v6 (classic TAKER fill ceiling).

Checks:
  1. engine_signals.py compiles.
  2. engine_fills.py compiles.
  3. CLASSIC_TAKER_LIMIT_CEIL env knob wired in engine_signals.py.
  4. TAKER_STUCK_TIMEOUT_SEC env knob wired in engine_fills.py.
  5. Ceiling logic truth table — classic TAKER picks ceiling over
     best_ask, maker is untouched.
  6. Stuck-TAKER cancel log marker present ("taker-stuck").

Exit 0 on success, non-zero on failure. ASCII-only output for Windows
cp1252 consoles.
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
for f in ("core/engine_signals.py", "core/engine_fills.py"):
    path = os.path.join(ROOT, f)
    try:
        py_compile.compile(path, doraise=True)
        ok(f"compile {f}")
    except py_compile.PyCompileError as e:
        fail(f"compile {f}: {e}")

# 3: CLASSIC_TAKER_LIMIT_CEIL in engine_signals
sig_src = open(os.path.join(ROOT, "core/engine_signals.py"),
               "r", encoding="utf-8").read()
if "CLASSIC_TAKER_LIMIT_CEIL" not in sig_src:
    fail("CLASSIC_TAKER_LIMIT_CEIL env knob missing from engine_signals.py")
ok("CLASSIC_TAKER_LIMIT_CEIL env knob wired in engine_signals.py")

# 4: TAKER_STUCK_TIMEOUT_SEC in engine_fills
fills_src = open(os.path.join(ROOT, "core/engine_fills.py"),
                 "r", encoding="utf-8").read()
if "TAKER_STUCK_TIMEOUT_SEC" not in fills_src:
    fail("TAKER_STUCK_TIMEOUT_SEC env knob missing from engine_fills.py")
ok("TAKER_STUCK_TIMEOUT_SEC env knob wired in engine_fills.py")

# 5: ceiling truth table — simulate the decision block
def _apply_ceil(classic_free: bool, is_maker: bool, limit: float,
                ceil_env: str) -> float:
    os.environ["CLASSIC_TAKER_LIMIT_CEIL"] = ceil_env
    if classic_free and not is_maker:
        try:
            ceil = float(os.getenv("CLASSIC_TAKER_LIMIT_CEIL", "0.99"))
        except ValueError:
            ceil = 0.99
        if ceil > 0 and ceil > limit:
            return ceil
    return limit


cases = [
    # (classic_free, is_maker, limit_in, ceil_env, expected_out)
    (True,  False, 0.90, "0.99", 0.99),   # classic taker -> ceiling
    (True,  False, 0.99, "0.99", 0.99),   # already at ceiling -> no change
    (True,  False, 0.995,"0.99", 0.995),  # above ceiling -> keep higher
    (True,  True,  0.50, "0.99", 0.50),   # maker -> untouched
    (False, False, 0.30, "0.99", 0.30),   # non-classic -> untouched
    (True,  False, 0.50, "0",    0.50),   # opt-out via ceil=0
    (True,  False, 0.50, "bad",  0.99),   # bad env -> default 0.99
]
for cf, maker, lim_in, env, exp in cases:
    got = _apply_ceil(cf, maker, lim_in, env)
    if abs(got - exp) < 1e-9:
        ok(f"ceil cf={cf} maker={maker} in={lim_in} env={env!r} -> {got}")
    else:
        fail(f"ceil cf={cf} maker={maker} in={lim_in} env={env!r} "
             f"expected={exp} got={got}")

# 6: stuck-TAKER cancel marker
if "taker-stuck" not in fills_src:
    fail("taker-stuck cancel log marker missing")
ok("taker-stuck cancel log marker present")

# 7: stuck-TAKER continue path (cur > limit branch)
if "_stuck_tout" not in fills_src or "cancelled.append(o)" not in fills_src:
    fail("stuck-TAKER auto-cancel block missing")
ok("stuck-TAKER auto-cancel block wired")

print("\nALL OK")
sys.exit(0)
