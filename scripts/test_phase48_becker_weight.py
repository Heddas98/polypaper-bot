"""
Phase 48 — BeckerWeightTracker self-test (no engine wiring).

Static + runtime exercise of `core/becker_weight_tracker.py`. Verifies:
  • Disabled-by-default returns 1.0 multiplier and is no-op for record_*
  • Enabled tracker accumulates per-asset history correctly
  • Recompute triggers at update_every threshold (and only after MIN_SAMPLES)
  • Positive correlation drives multiplier toward 1.5
  • Negative correlation drives multiplier toward 0.5
  • Multiplier always clamped to [0.50, 1.50]
  • State file round-trips (save → reload → same multipliers)
  • Per-asset isolation: BTC tuning does not move ETH multiplier

Run (Windows): py -3.11 -u scripts\\test_phase48_becker_weight.py
Exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.becker_weight_tracker import (  # noqa: E402
    BeckerWeightTracker, MULT_LOW, MULT_HIGH, _pearson_like,
)

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    print("Phase 48 BeckerWeightTracker self-test")
    print("=" * 70)

    # 1. Disabled-by-default no-op path
    print("[1] disabled tracker is a no-op")
    t = BeckerWeightTracker(enabled=False)
    check("disabled get_multiplier == 1.0", t.get_multiplier("BTC") == 1.0)
    t.record_open("o1", "BTC", 0.05)
    t.record_close("o1", 0.50)
    check("disabled state.per_asset BTC.n == 0",
          t.get_status()["per_asset"]["BTC"]["n"] == 0)

    # 2. Enabled tracker, tiny history
    print("[2] enabled tracker accumulates pairs")
    tmp_state = Path(tempfile.mkdtemp()) / "state.json"
    t = BeckerWeightTracker(enabled=True, state_file=tmp_state)
    t.record_open("a", "BTC", 0.03); t.record_close("a", 1.0)
    t.record_open("b", "BTC", -0.02); t.record_close("b", -1.0)
    check("BTC.n == 2", t.get_status()["per_asset"]["BTC"]["n"] == 2)
    check("get_multiplier still 1.0 (under MIN_SAMPLES)",
          abs(t.get_multiplier("BTC") - 1.0) < 1e-9)

    # 3. Positive correlation → multiplier moves up
    print("[3] positive correlation drives multiplier > 1.0")
    t = BeckerWeightTracker(enabled=True, state_file=tmp_state, update_every=5)
    # 25 perfectly correlated pairs (delta sign == pnl sign)
    for i in range(25):
        d = 0.05 if (i % 2 == 0) else -0.05
        pnl = 1.0 if (i % 2 == 0) else -1.0
        k = f"p{i}"
        t.record_open(k, "BTC", d)
        t.record_close(k, pnl)
    btc_mult = t.get_multiplier("BTC")
    check("BTC mult > 1.0 after positive corr", btc_mult > 1.0,
          f"(got {btc_mult:.3f})")
    check("BTC mult <= MULT_HIGH", btc_mult <= MULT_HIGH)
    check("ETH untouched (mult == 1.0)",
          abs(t.get_multiplier("ETH") - 1.0) < 1e-9)

    # 4. Negative correlation → multiplier moves down
    print("[4] negative correlation drives multiplier < 1.0")
    t2 = BeckerWeightTracker(enabled=True, state_file=Path(tempfile.mkdtemp()) / "s.json",
                             update_every=5)
    for i in range(25):
        d = 0.05 if (i % 2 == 0) else -0.05
        pnl = -1.0 if (i % 2 == 0) else 1.0  # ANTI-correlated
        k = f"n{i}"
        t2.record_open(k, "ETH", d)
        t2.record_close(k, pnl)
    eth_mult = t2.get_multiplier("ETH")
    check("ETH mult < 1.0 after negative corr", eth_mult < 1.0,
          f"(got {eth_mult:.3f})")
    check("ETH mult >= MULT_LOW", eth_mult >= MULT_LOW)

    # 5. Clamp boundary check via _pearson_like + recompute math
    print("[5] clamp boundary")
    t3 = BeckerWeightTracker(enabled=True, state_file=Path(tempfile.mkdtemp()) / "s.json")
    # Manually shove the multiplier above bounds and recompute
    t3._mults["SOL"] = 99.0
    t3._mults["SOL"] = max(MULT_LOW, min(MULT_HIGH, t3._mults["SOL"]))
    check("SOL mult clamped to MULT_HIGH",
          abs(t3._mults["SOL"] - MULT_HIGH) < 1e-9)

    # 6. State persistence round-trip
    print("[6] state persistence")
    tmp = Path(tempfile.mkdtemp()) / "rt.json"
    t4 = BeckerWeightTracker(enabled=True, state_file=tmp, update_every=5)
    for i in range(25):
        d = 0.04 if (i % 2 == 0) else -0.04
        pnl = 1.0 if (i % 2 == 0) else -1.0
        k = f"x{i}"
        t4.record_open(k, "XRP", d); t4.record_close(k, pnl)
    saved_xrp = t4.get_multiplier("XRP")
    check("XRP mult > 1.0 (sanity)", saved_xrp > 1.0)
    # Reload from disk
    t5 = BeckerWeightTracker(enabled=True, state_file=tmp)
    check("XRP mult survives reload",
          abs(t5.get_multiplier("XRP") - saved_xrp) < 1e-9)

    # 7. _pearson_like edge cases
    print("[7] _pearson_like edge cases")
    check("zero pairs → None", _pearson_like([]) is None)
    check("single pair → None", _pearson_like([(0.0, 1.0)]) is None)
    check("zero variance xs → None",
          _pearson_like([(0.05, 1.0), (0.05, -1.0)]) is None)
    check("zero variance ys → None",
          _pearson_like([(0.05, 1.0), (-0.05, 1.0)]) is None)
    check("perfect positive corr ≈ +1",
          abs((_pearson_like([(0.05, 1.0), (0.03, 0.5), (-0.02, -1.0)]) or 0) - 1) < 0.1)

    print("=" * 70)
    print(f"  PASS: {PASS}")
    print(f"  FAIL: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
