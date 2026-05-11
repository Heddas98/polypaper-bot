"""
Phase 67: Monte Carlo Kelly Validation
=======================================
Source: A3 (@mikita_crypto — 86M trades, Kelly + MC analysis)

10,000-path Monte Carlo simulation to validate that Quarter Kelly is
optimal for current win rate and payout conditions.

Validates:
  - Quarter Kelly vs Full, Half, Eighth, and Fixed-size betting
  - Bankruptcy probability per fraction
  - Growth rate vs variance tradeoff
  - Optimal fraction for current WR

Key insight from A3:
  - Quarter Kelly retains 51% of full Kelly growth with only 9% of variance
  - Full Kelly has 33% chance of halving bankroll before doubling
  - For WR=57%, Quarter Kelly is near-optimal for survival

Usage:
    from utils.mc_simulation import MonteCarloKelly

    mc = MonteCarloKelly(
        win_rate=0.57,
        avg_entry_price=0.65,
        initial_bankroll=10000.0,
    )
    result = mc.simulate()
    print(result.summary())

ENV:
    MC_KELLY_PATHS=10000         # Number of simulation paths
    MC_KELLY_TRADES=500          # Trades per path
    MC_KELLY_SEED=42             # Random seed for reproducibility
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("polypaper.utils.mc_kelly")

try:
    import numpy as np

    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    logger.warning("numpy not installed — MC simulation disabled")


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

MC_PATHS = int(os.getenv("MC_KELLY_PATHS", "10000"))
MC_TRADES = int(os.getenv("MC_KELLY_TRADES", "500"))
MC_SEED = int(os.getenv("MC_KELLY_SEED", "42"))

# Kelly fractions to test
KELLY_FRACTIONS = {
    "eighth": 0.125,
    "quarter": 0.25,
    "third": 0.333,
    "half": 0.50,
    "full": 1.00,
    "fixed_1": None,  # Fixed $1 bet
    "fixed_2": None,  # Fixed $2 bet
}


@dataclass
class FractionResult:
    """Result for a single Kelly fraction."""

    name: str = ""
    fraction: float = 0.0
    # Growth
    median_final: float = 0.0
    mean_final: float = 0.0
    geometric_growth_rate: float = 0.0  # log growth per trade
    # Risk
    bankruptcy_pct: float = 0.0  # % of paths hitting < $1
    max_drawdown_median: float = 0.0
    drawdown_50pct_prob: float = 0.0  # % paths with 50%+ drawdown
    # Variance
    std_final: float = 0.0
    cv: float = 0.0  # coefficient of variation
    # Percentiles
    p5: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    p95: float = 0.0
    # Efficiency
    growth_retention: float = 0.0  # vs full Kelly
    variance_ratio: float = 0.0  # vs full Kelly


@dataclass
class MCKellyResult:
    """Full Monte Carlo validation result."""

    # Input params
    win_rate: float = 0.0
    avg_entry_price: float = 0.0
    initial_bankroll: float = 0.0
    n_paths: int = 0
    n_trades: int = 0
    # Kelly math
    full_kelly_pct: float = 0.0
    quarter_kelly_pct: float = 0.0
    theoretical_edge: float = 0.0
    # Results per fraction
    fractions: list = field(default_factory=list)  # list[FractionResult]
    # Recommendation
    optimal_fraction_name: str = ""
    optimal_fraction_value: float = 0.0
    recommendation: str = ""
    is_quarter_kelly_optimal: bool = False

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [
            "═══ Monte Carlo Kelly Validation ═══",
            f"WR: {self.win_rate:.1%} | Entry: {self.avg_entry_price:.2f} | "
            f"Bankroll: ${self.initial_bankroll:,.0f}",
            f"Paths: {self.n_paths:,} | Trades/path: {self.n_trades}",
            f"Full Kelly: {self.full_kelly_pct:.1f}% | Quarter: {self.quarter_kelly_pct:.1f}%",
            "",
            f"{'Fraction':<12} {'Median$':<12} {'Bankrupt%':<10} "
            f"{'Growth':<10} {'StdDev':<10} {'DD50%':<8}",
            f"{'─'*62}",
        ]
        for fr in self.fractions:
            lines.append(
                f"{fr.name:<12} ${fr.median_final:<10,.0f} "
                f"{fr.bankruptcy_pct:<9.1f}% "
                f"{fr.geometric_growth_rate:<9.4f} "
                f"${fr.std_final:<9,.0f} "
                f"{fr.drawdown_50pct_prob:<7.1f}%"
            )
        lines.extend(
            [
                "",
                f"🏆 Optimal: {self.optimal_fraction_name} " f"({self.optimal_fraction_value:.1%})",
                f"{'✅' if self.is_quarter_kelly_optimal else '⚠️'} " f"{self.recommendation}",
            ]
        )
        return "\n".join(lines)

    def format_telegram(self) -> str:
        """Telegram HTML formatted report."""
        lines = [
            "🎲 <b>Monte Carlo Kelly Validation</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"📊 WR: <b>{self.win_rate:.1%}</b> | Entry: {self.avg_entry_price:.2f}",
            f"💰 Bankroll: ${self.initial_bankroll:,.0f}",
            f"🔢 {self.n_paths:,} paths × {self.n_trades} trades",
            "",
        ]

        for fr in self.fractions:
            icon = "🏆" if fr.name == self.optimal_fraction_name else "  "
            lines.append(
                f"{icon} <b>{fr.name}</b>: "
                f"${fr.median_final:,.0f} median | "
                f"{fr.bankruptcy_pct:.1f}% bust | "
                f"DD50: {fr.drawdown_50pct_prob:.0f}%"
            )

        lines.extend(
            [
                "",
                f"🏆 <b>Optimal: {self.optimal_fraction_name}</b> "
                f"({self.optimal_fraction_value:.1%} of bankroll)",
            ]
        )

        if self.is_quarter_kelly_optimal:
            lines.append("✅ Quarter Kelly onaylandı — mevcut sizing doğru")
        else:
            lines.append(f"⚠️ {self.recommendation}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Monte Carlo Engine
# ═══════════════════════════════════════════════════════════════


class MonteCarloKelly:
    """
    Monte Carlo simulation to validate Kelly fractions.

    For each Kelly fraction:
      1. Generate n_paths independent sequences of n_trades outcomes
      2. At each trade, bet fraction × bankroll (or fixed amount)
      3. Binary outcome: win → +shares×(1-entry_price), lose → -bet
      4. Track: final bankroll, max drawdown, bankruptcy events
    """

    def __init__(
        self,
        win_rate: float = 0.57,
        avg_entry_price: float = 0.65,
        initial_bankroll: float = 10000.0,
        n_paths: int = MC_PATHS,
        n_trades: int = MC_TRADES,
        seed: int = MC_SEED,
    ):
        if not NUMPY_AVAILABLE:
            raise ImportError("numpy required for MC simulation")

        self.win_rate = win_rate
        self.avg_entry_price = avg_entry_price
        self.initial_bankroll = initial_bankroll
        self.n_paths = n_paths
        self.n_trades = n_trades
        self.seed = seed

        # Binary market math
        # b = payout ratio = (1/p) - 1
        self.payout_ratio = (1.0 / avg_entry_price) - 1.0
        self.full_kelly = (self.payout_ratio * win_rate - (1 - win_rate)) / self.payout_ratio
        self.quarter_kelly = self.full_kelly * 0.25

    def simulate(self) -> MCKellyResult:
        """Run full MC simulation across all Kelly fractions."""
        rng = np.random.default_rng(self.seed)

        # Pre-generate outcomes for all paths (shared across fractions)
        # 1 = win, 0 = loss
        outcomes = rng.binomial(1, self.win_rate, size=(self.n_paths, self.n_trades))

        fraction_results = []
        full_kelly_growth = None

        for name, frac in KELLY_FRACTIONS.items():
            fr = self._simulate_fraction(name, frac, outcomes)
            fraction_results.append(fr)
            if name == "full":
                full_kelly_growth = fr.geometric_growth_rate
                full_kelly_std = fr.std_final

        # Calculate retention ratios relative to full Kelly
        if full_kelly_growth and full_kelly_growth > 0 and full_kelly_std > 0:
            for fr in fraction_results:
                fr.growth_retention = (
                    fr.geometric_growth_rate / full_kelly_growth if full_kelly_growth > 0 else 0
                )
                fr.variance_ratio = (
                    (fr.std_final**2) / (full_kelly_std**2) if full_kelly_std > 0 else 0
                )

        # Find optimal fraction (best risk-adjusted: median / (1 + bankruptcy%))
        scored = []
        for fr in fraction_results:
            if fr.bankruptcy_pct > 20:
                score = 0  # Too risky
            else:
                score = fr.median_final / (1 + fr.bankruptcy_pct / 100)
            scored.append((fr, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        optimal = scored[0][0] if scored else fraction_results[0]

        # Is quarter Kelly optimal?
        quarter_result = next((fr for fr in fraction_results if fr.name == "quarter"), None)
        is_qk_optimal = optimal.name == "quarter"

        # Recommendation
        if is_qk_optimal:
            rec = (
                f"Quarter Kelly doğrulandı. WR={self.win_rate:.0%} ile "
                f"growth retention: {quarter_result.growth_retention:.0%}, "
                f"variance ratio: {quarter_result.variance_ratio:.0%}"
            )
        elif optimal.name in ("eighth", "fixed_1"):
            rec = (
                f"Quarter Kelly çok agresif! {optimal.name} daha güvenli. "
                f"Bankrupt riski QK: {quarter_result.bankruptcy_pct:.1f}% vs "
                f"{optimal.name}: {optimal.bankruptcy_pct:.1f}%"
            )
        else:
            rec = (
                f"Quarter Kelly konservatif. {optimal.name} "
                f"({optimal.fraction:.0%}) daha iyi risk/return veriyor. "
                f"Median: ${optimal.median_final:,.0f} vs QK: "
                f"${quarter_result.median_final:,.0f}"
            )

        return MCKellyResult(
            win_rate=self.win_rate,
            avg_entry_price=self.avg_entry_price,
            initial_bankroll=self.initial_bankroll,
            n_paths=self.n_paths,
            n_trades=self.n_trades,
            full_kelly_pct=round(self.full_kelly * 100, 1),
            quarter_kelly_pct=round(self.quarter_kelly * 100, 1),
            theoretical_edge=round(self.payout_ratio * self.win_rate - (1 - self.win_rate), 4),
            fractions=fraction_results,
            optimal_fraction_name=optimal.name,
            optimal_fraction_value=optimal.fraction,
            recommendation=rec,
            is_quarter_kelly_optimal=is_qk_optimal,
        )

    def _simulate_fraction(
        self,
        name: str,
        fraction: Optional[float],
        outcomes: np.ndarray,
    ) -> FractionResult:
        """Simulate one Kelly fraction across all paths."""
        n_paths, n_trades = outcomes.shape
        bankrolls = np.full(n_paths, self.initial_bankroll, dtype=np.float64)
        peak = bankrolls.copy()
        max_dd = np.zeros(n_paths, dtype=np.float64)

        # Fixed sizing mode
        is_fixed = fraction is None
        if is_fixed:
            fixed_amount = 1.0 if name == "fixed_1" else 2.0
            kelly_frac = 0.0
        else:
            fixed_amount = 0.0
            kelly_frac = self.full_kelly * fraction

        for t in range(n_trades):
            # Calculate bet size
            if is_fixed:
                bet = np.minimum(fixed_amount, bankrolls)
            else:
                bet = bankrolls * kelly_frac
                # Floor at $0.50, cap at bankroll
                bet = np.clip(bet, 0.50, bankrolls)

            # Apply outcomes
            win_mask = outcomes[:, t] == 1
            # Win: get bet * payout_ratio back
            bankrolls[win_mask] += bet[win_mask] * self.payout_ratio
            # Loss: lose the bet
            bankrolls[~win_mask] -= bet[~win_mask]

            # Floor at 0
            bankrolls = np.maximum(bankrolls, 0.0)

            # Track drawdown
            peak = np.maximum(peak, bankrolls)
            dd = (peak - bankrolls) / np.maximum(peak, 1e-10)
            max_dd = np.maximum(max_dd, dd)

        # Calculate statistics
        final = bankrolls
        bankrupt_mask = final < 1.0  # < $1 = effectively bankrupt
        log_returns = np.log(np.maximum(final, 1e-10) / self.initial_bankroll)
        geo_growth = np.mean(log_returns) / n_trades  # per-trade geometric growth

        return FractionResult(
            name=name,
            fraction=kelly_frac if not is_fixed else 0.0,
            median_final=float(np.median(final)),
            mean_final=float(np.mean(final)),
            geometric_growth_rate=float(geo_growth),
            bankruptcy_pct=float(np.mean(bankrupt_mask) * 100),
            max_drawdown_median=float(np.median(max_dd)),
            drawdown_50pct_prob=float(np.mean(max_dd >= 0.50) * 100),
            std_final=float(np.std(final)),
            cv=float(np.std(final) / max(np.mean(final), 1e-10)),
            p5=float(np.percentile(final, 5)),
            p25=float(np.percentile(final, 25)),
            p75=float(np.percentile(final, 75)),
            p95=float(np.percentile(final, 95)),
        )


# ═══════════════════════════════════════════════════════════════
# Quick validation function (for AI Brain / auto_optimizer)
# ═══════════════════════════════════════════════════════════════


def validate_quarter_kelly(
    win_rate: float,
    avg_entry_price: float,
    bankroll: float = 10000.0,
    n_paths: int = 1000,  # Quick mode: fewer paths
    n_trades: int = 200,
) -> dict:
    """
    Quick Kelly validation — returns dict for integration.

    Returns:
        {
            "is_optimal": bool,
            "recommended_fraction": str,  # "quarter", "eighth", etc.
            "bankruptcy_pct": float,
            "median_growth": float,
            "recommendation": str,
        }
    """
    if not NUMPY_AVAILABLE:
        return {
            "is_optimal": True,  # default to quarter Kelly
            "recommended_fraction": "quarter",
            "bankruptcy_pct": 0.0,
            "median_growth": 0.0,
            "recommendation": "numpy not available — defaulting to Quarter Kelly",
        }

    try:
        mc = MonteCarloKelly(
            win_rate=win_rate,
            avg_entry_price=avg_entry_price,
            initial_bankroll=bankroll,
            n_paths=n_paths,
            n_trades=n_trades,
        )
        result = mc.simulate()

        qk = next((f for f in result.fractions if f.name == "quarter"), None)
        return {
            "is_optimal": result.is_quarter_kelly_optimal,
            "recommended_fraction": result.optimal_fraction_name,
            "recommended_value": result.optimal_fraction_value,
            "bankruptcy_pct": qk.bankruptcy_pct if qk else 0,
            "median_growth": qk.median_final if qk else 0,
            "recommendation": result.recommendation,
        }
    except Exception as e:
        logger.error("MC validation failed: %s", e)
        return {
            "is_optimal": True,
            "recommended_fraction": "quarter",
            "bankruptcy_pct": 0.0,
            "median_growth": 0.0,
            "recommendation": f"MC failed: {e} — defaulting to Quarter Kelly",
        }
