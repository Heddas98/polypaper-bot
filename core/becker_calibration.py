"""
Phase 47f - pure δ(p) lookup + boost helpers for Becker calibration.

Kept import-light (stdlib only) so the test harness can validate the logic
without pulling the full engine dependency graph.
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

Curve = Sequence[Tuple[float, float]]  # list of (bin_low, delta_at_midpoint)


def becker_delta(curve: Curve, price: Optional[float]) -> Optional[float]:
    """Return empirical δ = actual_wr - price at the given market price.

    `curve` is a list of (bin_low, delta_at_midpoint) entries on a 5% grid,
    as produced by BeckerLoader.calibration_curve() + the engine's init-time
    transform. Delta at midpoint is `actual_wr - (bin_low + 0.025)`. To get
    the true delta relative to an arbitrary price inside the bin we just
    reconstruct actual_wr and subtract the live price (local-linear
    assumption — bins are 5% wide, price movement within a bin is small).

    Returns None when the curve is empty or the price is outside [0.05, 0.95].
    """
    if not curve or price is None or price < 0.05 or price > 0.95:
        return None
    bin_low = max(0.05, min(0.95, (int(float(price) * 20) / 20)))
    best: Optional[Tuple[float, float]] = None
    for bl, d in curve:
        if abs(bl - bin_low) < 1e-6:
            best = (bl, d)
            break
    if best is None:
        best = min(curve, key=lambda r: abs(r[0] - bin_low))
    bin_mid = best[0] + 0.025
    actual_wr = bin_mid + best[1]
    return actual_wr - float(price)


def becker_boost(delta: Optional[float], weight: float, clamp: float) -> float:
    """Turn a raw δ(p) into a signal_score additive boost.

    boost = clamp(delta * weight, ±clamp). Returns 0.0 when delta is None.
    """
    if delta is None:
        return 0.0
    return max(min(float(delta) * float(weight), float(clamp)), -float(clamp))
