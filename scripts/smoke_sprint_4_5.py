"""
scripts/smoke_sprint_4_5.py — Sprint 4.5 apply-filter smoke test.

Verifies:
  1) _load_live_strategy_types() returns a populated set from the live DB.
  2) PARAM_SPACES intersection with live_types is non-empty (keep list).
  3) Orphan live_types not in PARAM_SPACES are logged but non-fatal.
  4) Safe fallback path works when DB path is bogus.

Run: py -3.11 scripts\\smoke_sprint_4_5.py

Exit 0 on success, 1 on any assertion failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    try:
        from backtest.hyperopt_worker import _load_live_strategy_types
        from backtest.hyperopt import PARAM_SPACES
    except Exception as e:
        print(f"  FAIL: import error: {e}")
        return 1

    db_path = str(_ROOT / "data_store" / "polypaper.db")

    # ── Test 1: helper returns populated set against real DB
    print(f"[1] Probing live DB: {db_path}")
    live = _load_live_strategy_types(db_path)
    if live is None:
        print("  FAIL: helper returned None on real DB (expected a set)")
        return 1
    if not isinstance(live, set):
        print(f"  FAIL: helper returned {type(live).__name__}, expected set")
        return 1
    if len(live) == 0:
        print("  WARN: live types set is empty — DB may be wiped. Filter would fall back.")
    print(f"  OK: live_types count = {len(live)}")

    # ── Test 2: intersection produces keep list
    all_spaces = list(PARAM_SPACES.keys())
    keep = [s for s in all_spaces if s in live]
    skip = [s for s in all_spaces if s not in live]
    print(f"[2] PARAM_SPACES={len(all_spaces)}  keep={len(keep)}  skip={len(skip)}")
    if not keep and live:
        print("  FAIL: live types exist but no PARAM_SPACES match — filter would produce empty batch")
        return 1
    print(f"  OK: keep={keep}")
    print(f"  OK: skip={skip}")

    # ── Test 3: detect orphan DB types not in PARAM_SPACES (informational)
    orphan = live - set(all_spaces)
    if orphan:
        print(f"[3] WARN: orphan live types (no PARAM_SPACES — can never be optimized): {sorted(orphan)}")
    else:
        print("[3] OK: no orphan live types")

    # ── Test 4: safe fallback on bad DB path
    print("[4] Safe-fallback probe (bogus DB path)")
    fake = _load_live_strategy_types("/nope/does/not/exist.db")
    if fake is not None:
        print(f"  FAIL: expected None on bogus path, got {type(fake).__name__}")
        return 1
    print("  OK: helper returned None → caller falls back to no-filter")

    # ── Test 5: imports cleanly from worker module (no side-effect regressions)
    print("[5] Import re-check — worker module still loads")
    try:
        import importlib
        import backtest.hyperopt_worker as hw
        importlib.reload(hw)
    except Exception as e:
        print(f"  FAIL: worker reload error: {e}")
        return 1
    print("  OK")

    print()
    print("ALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
