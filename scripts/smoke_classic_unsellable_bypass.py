"""
scripts/smoke_classic_unsellable_bypass.py — Sprint 5 HOTFIX smoke.

Verifies that core/engine_signals.py contains the Phase 66 UNSELLABLE
classic-bypass hotfix (CLASSIC_RESPECT_UNSELLABLE env + bypass flag +
combined gate condition).

Exit 0 if all markers present, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TARGET = _ROOT / "core" / "engine_signals.py"


REQUIRED_MARKERS: list[str] = [
    # Legacy v2 env override (still honored as narrower opt-in on top of v3)
    'CLASSIC_RESPECT_UNSELLABLE',
    # v3 FREE-MODE unifies the bypass
    '_unsellable_bypass = _classic_free or (',
    'ctx.get("stype") == "classic"',
    # The guard must wrap the UNSELLABLE gate
    'UNSELLABLE_CHECK_ENABLED',
    'not _unsellable_bypass',
    # Call to the gate itself (still reachable for non-classic)
    'check_unsellable_risk',
]


def main() -> int:
    if not _TARGET.exists():
        print(f"[classic-unsellable-smoke] FAIL: {_TARGET} yok")
        return 1

    src = _TARGET.read_text(encoding="utf-8")

    # 1. Syntax check
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"[classic-unsellable-smoke] FAIL: SyntaxError: {e}")
        return 1

    # 2. Marker check
    missing = [m for m in REQUIRED_MARKERS if m not in src]
    if missing:
        print(f"[classic-unsellable-smoke] FAIL: {len(missing)} marker eksik:")
        for m in missing:
            print(f"    - {m}")
        return 1

    # 3. Sanity: v3 free-mode path present
    if 'CLASSIC_BYPASS_ALL_GATES' in src:
        print(f"[classic-unsellable-smoke] OK: tum markerlar ({len(REQUIRED_MARKERS)}) hazir")
        print("           - v3 FREE-MODE unifikasyonu aktif (CLASSIC_BYPASS_ALL_GATES=true)")
        print("           - v2 CLASSIC_RESPECT_UNSELLABLE=true ise FREE-MODE on olsa bile")
        print("             UNSELLABLE gate'i respect edilir (narrower opt-in)")
        print("           - Phase 66 UNSELLABLE (EXTREME_ODDS/NEAR_CLOSE/THIN_BOOK) default bypass")
        return 0

    print("[classic-unsellable-smoke] FAIL: v3 free-mode marker not found")
    return 1


if __name__ == "__main__":
    sys.exit(main())
