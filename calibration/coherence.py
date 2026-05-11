"""
Phase 70: Market Coherence Index (MCI)
=======================================
Source: A4 (Calibration Surface Analysis)

MCI measures how well a market's prices are internally calibrated.
A well-calibrated market has:
  - Low antisymmetry violation: C(K,τ) ≈ -C(1-K,τ)
  - Small calibration errors across the surface
  - Sufficient liquidity and trade volume

MCI ∈ [0, 1]:
  - MCI > 0.7 → well-calibrated, high confidence
  - MCI 0.4-0.7 → moderate, use with caution
  - MCI < 0.4 → poorly calibrated, consider skipping

Usage:
    from calibration.coherence import compute_mci, MCIResult

    mci = compute_mci(surface_2d, recent_trades_n=500)
    if mci.score < MCI_MINIMUM:
        skip("LOW_MCI")

Integration:
    - Engine: checked before entering a trade. Low MCI → skip or reduce size
    - signal_fusion.py: MCI as a signal weight modifier
    - Telegram: /mci command shows current market coherence

ENV:
    MCI_ENABLED=true
    MCI_MINIMUM=0.40           # Below this: skip trade
    MCI_SIZE_PENALTY=0.60      # Low MCI → reduce size to 60%
    MCI_ANTISYM_WEIGHT=0.35    # Weight of antisymmetry component
    MCI_COVERAGE_WEIGHT=0.25   # Weight of surface coverage component
    MCI_ERROR_WEIGHT=0.25      # Weight of calibration error component
    MCI_VOLUME_WEIGHT=0.15     # Weight of volume/liquidity component
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

logger = logging.getLogger("polypaper.calibration.coherence")

# ── ENV ──
_ENABLED = os.getenv("MCI_ENABLED", "true").lower() == "true"
_MINIMUM = float(os.getenv("MCI_MINIMUM", "0.40"))
_SIZE_PENALTY = float(os.getenv("MCI_SIZE_PENALTY", "0.60"))
_ANTISYM_W = float(os.getenv("MCI_ANTISYM_WEIGHT", "0.35"))
_COVERAGE_W = float(os.getenv("MCI_COVERAGE_WEIGHT", "0.25"))
_ERROR_W = float(os.getenv("MCI_ERROR_WEIGHT", "0.25"))
_VOLUME_W = float(os.getenv("MCI_VOLUME_WEIGHT", "0.15"))


@dataclass
class MCIResult:
    """Market Coherence Index breakdown."""

    score: float = 0.5  # Final MCI [0, 1]
    antisym_score: float = 0.5  # Antisymmetry quality [0, 1]
    coverage_score: float = 0.5  # Surface coverage [0, 1]
    error_score: float = 0.5  # Mean calibration error [0, 1]
    volume_score: float = 0.5  # Volume/liquidity [0, 1]
    should_trade: bool = True  # MCI >= minimum
    size_multiplier: float = 1.0  # Position size modifier
    n_pairs_checked: int = 0  # Number of antisymmetry pairs
    mean_abs_error: float = 0.0  # Mean |δ| across surface
    reason: str = ""


def compute_mci(
    surface=None,
    recent_trades_n: int = 0,
    min_cells: int = 10,
) -> MCIResult:
    """
    Compute Market Coherence Index from a 2D calibration surface.

    Args:
        surface: CalibrationSurface from surface_2d.py (or None)
        recent_trades_n: Number of recent trades in the market (for volume scoring)
        min_cells: Minimum populated cells needed for meaningful MCI

    Returns:
        MCIResult with decomposed components.
    """
    if not _ENABLED:
        return MCIResult(score=1.0, reason="disabled", should_trade=True)

    if surface is None or not getattr(surface, "built", False):
        return MCIResult(
            score=0.5,
            reason="no_surface",
            should_trade=True,
            size_multiplier=1.0,
        )

    cells = surface.cells
    if len(cells) < min_cells:
        return MCIResult(
            score=0.5,
            reason=f"sparse({len(cells)}<{min_cells})",
            should_trade=True,
            size_multiplier=0.9,
        )

    # ── 1. Antisymmetry Score ──
    # Check C(K,τ) ≈ -C(1-K,τ) for all pairs
    antisym_violations = []
    n_pairs = 0
    for (pb, tb), cell in cells.items():
        mirror_bin = round(1.0 - pb - 0.05, 2)
        mirror_bin = max(0.05, min(0.90, mirror_bin))
        mirror = cells.get((mirror_bin, tb))
        if mirror is not None and cell.n_trades >= 10 and mirror.n_trades >= 10:
            violation = abs(cell.delta + mirror.delta)
            antisym_violations.append(violation)
            n_pairs += 1

    if antisym_violations:
        mean_violation = sum(antisym_violations) / len(antisym_violations)
        # Score: 1.0 if mean_violation=0, 0.0 if mean_violation>=0.10
        antisym_score = max(0.0, 1.0 - mean_violation / 0.10)
    else:
        antisym_score = 0.5  # No pairs to check

    # ── 2. Coverage Score ──
    # What fraction of the grid is populated?
    # Price: 18 bins (0.05-0.90), Time: N bins
    from calibration.surface_2d import _N_TIME_BINS

    max_cells = 18 * _N_TIME_BINS
    coverage_ratio = min(1.0, len(cells) / max(1, max_cells))
    # Non-linear: 80% coverage → score 1.0, 20% → 0.25
    coverage_score = min(1.0, coverage_ratio / 0.8)

    # ── 3. Calibration Error Score ──
    # Lower mean |δ| across populated cells = better calibrated
    deltas = [abs(c.delta) for c in cells.values() if c.n_trades >= 5]
    if deltas:
        mean_abs_error = sum(deltas) / len(deltas)
        # Score: 1.0 if error=0, 0.0 if error>=0.15
        error_score = max(0.0, 1.0 - mean_abs_error / 0.15)
    else:
        mean_abs_error = 0.0
        error_score = 0.5

    # ── 4. Volume Score ──
    # More total trades = better statistical significance
    total_n = surface.total_trades
    # Log-scale: 100 trades → 0.3, 1000 → 0.6, 10000 → 0.9, 100000 → 1.0
    if total_n > 0:
        volume_score = min(1.0, math.log10(max(1, total_n)) / 5.0)
    else:
        volume_score = 0.0

    # Supplement with recent market volume
    if recent_trades_n > 0:
        recent_vol = min(1.0, math.log10(max(1, recent_trades_n)) / 3.0)
        volume_score = volume_score * 0.7 + recent_vol * 0.3

    # ── Combine ──
    score = (
        antisym_score * _ANTISYM_W
        + coverage_score * _COVERAGE_W
        + error_score * _ERROR_W
        + volume_score * _VOLUME_W
    )
    score = max(0.0, min(1.0, score))

    # Determine trading action
    should_trade = score >= _MINIMUM
    if score >= 0.7:
        size_multiplier = 1.0
    elif score >= _MINIMUM:
        # Linear interpolation: MCI=0.4 → 0.6x, MCI=0.7 → 1.0x
        size_multiplier = _SIZE_PENALTY + (1.0 - _SIZE_PENALTY) * (
            (score - _MINIMUM) / max(0.01, 0.7 - _MINIMUM)
        )
    else:
        size_multiplier = _SIZE_PENALTY * 0.5  # Very low

    reason_parts = []
    if antisym_score < 0.4:
        reason_parts.append(f"antisym_low({antisym_score:.2f})")
    if coverage_score < 0.3:
        reason_parts.append(f"sparse({coverage_score:.2f})")
    if error_score < 0.4:
        reason_parts.append(f"high_err({mean_abs_error:.4f})")
    if not should_trade:
        reason_parts.append(f"SKIP(mci={score:.2f}<{_MINIMUM})")

    return MCIResult(
        score=round(score, 3),
        antisym_score=round(antisym_score, 3),
        coverage_score=round(coverage_score, 3),
        error_score=round(error_score, 3),
        volume_score=round(volume_score, 3),
        should_trade=should_trade,
        size_multiplier=round(size_multiplier, 3),
        n_pairs_checked=n_pairs,
        mean_abs_error=round(mean_abs_error, 6),
        reason=" ".join(reason_parts) if reason_parts else "ok",
    )


def format_mci_telegram(mci: MCIResult) -> str:
    """Format MCI result for Telegram."""
    icon = "🟢" if mci.score >= 0.7 else "🟡" if mci.score >= 0.4 else "🔴"
    lines = [
        f"{icon} <b>Market Coherence Index: {mci.score:.2f}</b>",
        f"Antisymmetry: {mci.antisym_score:.2f} | " f"Coverage: {mci.coverage_score:.2f}",
        f"Calibration: {mci.error_score:.2f} | " f"Volume: {mci.volume_score:.2f}",
        f"Mean |δ|: {mci.mean_abs_error:.4f} | " f"Pairs: {mci.n_pairs_checked}",
    ]
    if not mci.should_trade:
        lines.append("⛔ <b>Trade etme — MCI çok düşük</b>")
    elif mci.size_multiplier < 1.0:
        lines.append(f"⚠️ Size penalty: {mci.size_multiplier:.0%}")
    else:
        lines.append("✅ <b>Kalibrasyon iyi</b>")
    return "\n".join(lines)
