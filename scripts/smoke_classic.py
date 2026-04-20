"""
scripts/smoke_classic.py — Phase 82e Sprint 4.6 Classic strategy smoke test.

Verifies:
  1) ClassicStrategy class importable + registered in StrategyRegistry.
  2) evaluate() fires UP correctly above trigger.
  3) evaluate() fires DOWN correctly above trigger.
  4) evaluate() stays silent below trigger.
  5) direction_filter="up" respected (blocks DOWN even if down>=trigger).
  6) direction_filter="down" respected (blocks UP even if up>=trigger).
  7) direction_filter="any" allows either side.
  8) QUICK_STRATEGY_TYPES includes "classic".
  9) strategy_builder edit_stype menu contains classic button.

Run: py -3.11 scripts\\smoke_classic.py

Exit 0 on success, 1 on any assertion failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    # ── Test 1: import + registration
    print("[1] Import + registry")
    try:
        from core.strategy_plugins import (
            ClassicStrategy, StrategyRegistry, MarketSnapshot, StrategySignal
        )
    except Exception as e:
        print(f"  FAIL: import error: {e}")
        return 1
    reg = StrategyRegistry()
    plugin = reg.get("classic")
    if plugin is None:
        print("  FAIL: 'classic' not registered in StrategyRegistry")
        return 1
    if not isinstance(plugin, ClassicStrategy):
        print(f"  FAIL: registered plugin type = {type(plugin).__name__}")
        return 1
    print(f"  OK: classic registered, name={plugin.name}, origin={plugin.origin}")

    # ── Test 2: UP fires when up_odds >= threshold
    print("[2] UP fire (up=0.60, thr=0.55, dir=any)")
    snap = MarketSnapshot(up_odds=0.60, down_odds=0.40, threshold=0.55,
                          direction_filter="any", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.direction != "up" or not sig.should_trade:
        print(f"  FAIL: expected UP trade; got dir={sig.direction} trade={sig.should_trade} reason={sig.reason!r}")
        return 1
    if abs(sig.confidence - 0.75) > 0.001:
        print(f"  FAIL: confidence != 0.75, got {sig.confidence}")
        return 1
    print(f"  OK: {sig.reason}")

    # ── Test 3: DOWN fires when down_odds >= threshold
    print("[3] DOWN fire (down=0.70, thr=0.60, dir=any)")
    snap = MarketSnapshot(up_odds=0.30, down_odds=0.70, threshold=0.60,
                          direction_filter="any", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.direction != "down" or not sig.should_trade:
        print(f"  FAIL: expected DOWN trade; got dir={sig.direction} trade={sig.should_trade} reason={sig.reason!r}")
        return 1
    print(f"  OK: {sig.reason}")

    # ── Test 4: silent below trigger
    print("[4] Silent below trigger (up=0.40, down=0.40, thr=0.55)")
    snap = MarketSnapshot(up_odds=0.40, down_odds=0.40, threshold=0.55,
                          direction_filter="any", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.should_trade or sig.direction is not None:
        print(f"  FAIL: expected no trade; got dir={sig.direction} trade={sig.should_trade}")
        return 1
    if "not-at-trigger" not in sig.reason:
        print(f"  FAIL: reason missing 'not-at-trigger': {sig.reason!r}")
        return 1
    print(f"  OK: {sig.reason}")

    # ── Test 5: direction_filter="up" blocks DOWN
    print("[5] dir=up blocks DOWN (down=0.80, thr=0.55, dir=up)")
    snap = MarketSnapshot(up_odds=0.20, down_odds=0.80, threshold=0.55,
                          direction_filter="up", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.should_trade:
        print(f"  FAIL: expected blocked; got trade dir={sig.direction}")
        return 1
    print(f"  OK: blocked, reason={sig.reason}")

    # ── Test 6: direction_filter="down" blocks UP
    print("[6] dir=down blocks UP (up=0.80, thr=0.55, dir=down)")
    snap = MarketSnapshot(up_odds=0.80, down_odds=0.20, threshold=0.55,
                          direction_filter="down", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.should_trade:
        print(f"  FAIL: expected blocked; got trade dir={sig.direction}")
        return 1
    print(f"  OK: blocked, reason={sig.reason}")

    # ── Test 7: "any" direction allows either — UP wins at tie
    print("[7] dir=any, both above, UP preference (up=0.80, down=0.80)")
    snap = MarketSnapshot(up_odds=0.80, down_odds=0.80, threshold=0.55,
                          direction_filter="any", odds_series=[],
                          minutes_remaining=3.0, total_minutes=5.0)
    sig = plugin.evaluate(snap)
    if sig.direction != "up":
        print(f"  FAIL: expected UP at tie (code order); got {sig.direction}")
        return 1
    print(f"  OK: UP preferred at tie")

    # ── Test 8: QUICK_STRATEGY_TYPES whitelist contains classic
    print("[8] QUICK_STRATEGY_TYPES includes 'classic'")
    try:
        from telegram_bot.handlers.strategies import QUICK_STRATEGY_TYPES
    except Exception as e:
        print(f"  FAIL: import error: {e}")
        return 1
    if "classic" not in QUICK_STRATEGY_TYPES:
        print(f"  FAIL: QUICK_STRATEGY_TYPES={QUICK_STRATEGY_TYPES}")
        return 1
    print(f"  OK: QUICK_STRATEGY_TYPES={QUICK_STRATEGY_TYPES}")

    # ── Test 9: strategy_builder edit_stype menu has classic button
    print("[9] strategy_builder.py edit_stype menu has classic button")
    try:
        sb_src = (_ROOT / "telegram_bot" / "handlers" / "strategy_builder.py").read_text(encoding="utf-8")
    except Exception as e:
        print(f"  FAIL: read error: {e}")
        return 1
    if "sb_set_stype_classic" not in sb_src:
        print("  FAIL: 'sb_set_stype_classic' callback not found in strategy_builder.py")
        return 1
    print("  OK: classic button present in edit_stype menu")

    # ── Test 10: Classic NOT in PARAM_SPACES (hyperopt skip-intended)
    print("[10] PARAM_SPACES does NOT include classic (no tunable params)")
    try:
        from backtest.hyperopt import PARAM_SPACES
    except Exception as e:
        print(f"  FAIL: import error: {e}")
        return 1
    if "classic" in PARAM_SPACES:
        print("  FAIL: 'classic' should NOT be in PARAM_SPACES — it has no algorithmic params")
        return 1
    print(f"  OK: 'classic' correctly absent from PARAM_SPACES")

    print()
    print("ALL SMOKE CHECKS PASSED (10/10)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
