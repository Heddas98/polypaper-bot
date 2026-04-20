"""
scripts/smoke_classic_free_mode.py — Sprint 5 HOTFIX v3 smoke.

Verifies that core/engine_signals.py contains the Phase 82e Sprint 5
HOTFIX v3 Classic FREE-MODE wraps for 14 strategic gates.

Bypassed gates (_classic_free=True skips the skip-return):
  _eval_market_checks: TOO_LATE, WHIPSAW
  _eval_signal: REGIME, TS_SKIP, ZONE_BLOCKED (Sprint 5 FINAL)
  _eval_signal_boosters: ORACLE_PARITY, BECKER veto/flip, EVENT_WAVES_QUALITY
  _eval_gates: EDGE_GATE, LOW_EDGE_VS_FEE, BRIER_ALARM, UNSELLABLE
  _eval_sizing: KELLY_NO_EDGE, LOW_CONVICTION, EV_NEGATIVE, CAPITAL_BUDGET

Exit 0 if all markers present, 1 otherwise.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TARGET = _ROOT / "core" / "engine_signals.py"


REQUIRED_MARKERS: list[str] = [
    # Helper method exists
    'def _classic_free_mode(',
    'CLASSIC_BYPASS_ALL_GATES',
    # _eval_market_checks: TOO_LATE + WHIPSAW bypass
    'if not _classic_free and (minutes_remaining < mbe or minutes_remaining < 0):',
    'if last_slug == slug and not _classic_free:',
    # _eval_signal: REGIME + TS_SKIP bypass
    'if not _classic_free and self.regime.should_skip(stype):',
    'if not _classic_free and not self.selector.should_trade(',
    # _eval_signal_boosters: ORACLE_PARITY + BECKER_DECISION + EVENT_WAVES
    'if (not _classic_free and clo is not None',
    'and decision_mode in ("veto", "flip")',  # BECKER block (wrapped by _classic_free)
    'if not _classic_free and os.getenv("EVENT_WAVES_ENABLED"',
    # _eval_gates: EDGE_GATE + FEE_GATE + BRIER + UNSELLABLE
    'if not _classic_free and signal_score < min_sig:',
    'if not _classic_free and _fee_gate_enabled:',
    'if not _classic_free and _brier_enabled and self.BRIER_GAP_MAX > 0:',
    '_unsellable_bypass = _classic_free or (',
    # _eval_sizing: KELLY + CONVICTION + EV + CAPITAL_BUDGET
    'if kelly.get("skip") and not _classic_free:',
    'if conviction < _conv_min and not _classic_free:',
    'if not ev_result.should_trade and not _classic_free:',
    'if not _ca_result["allowed"] and not _classic_free:',
]


def main() -> int:
    if not _TARGET.exists():
        print(f"[classic-free-mode-smoke] FAIL: {_TARGET} yok")
        return 1

    src = _TARGET.read_text(encoding="utf-8")

    # 1. Syntax check
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"[classic-free-mode-smoke] FAIL: SyntaxError: {e}")
        return 1

    # 2. Marker check
    missing = [m for m in REQUIRED_MARKERS if m not in src]
    if missing:
        print(f"[classic-free-mode-smoke] FAIL: {len(missing)} marker eksik:")
        for m in missing:
            print(f"    - {m}")
        return 1

    print(f"[classic-free-mode-smoke] OK: tum markerlar ({len(REQUIRED_MARKERS)}) hazir")
    print("           - stype=='classic' ve CLASSIC_BYPASS_ALL_GATES!=false ise")
    print("             14 strateji gate'i atlanir: TOO_LATE, WHIPSAW, REGIME,")
    print("             TS_SKIP, ORACLE_PARITY, BECKER_VETO/FLIP, EVENT_WAVES,")
    print("             EDGE_GATE, LOW_EDGE_VS_FEE, BRIER_ALARM, UNSELLABLE,")
    print("             KELLY_NO_EDGE, LOW_CONVICTION, EV_NEGATIVE, CAPITAL_BUDGET")
    print("           - Hard safety korunur: MARKET_HALT, NO_LIQ, BAD_PRICE,")
    print("             RISK, MIN_SIZE/SHARES, FEE_TAIL, STP, TOKEN_CAP")
    print("           - Opt-out: set CLASSIC_BYPASS_ALL_GATES=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
