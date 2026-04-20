"""
Phase 70: EV Threshold Validation
===================================
Source: A7 (only 12.3% of trades have positive EV)

Measures and gates trades based on Expected Value calculation.
EV = (win_probability × payout) - (loss_probability × cost) - fees

A7 finding: Only ~12.3% of Polymarket trades are EV+. Our system should:
1. Compute EV for each proposed trade
2. Track what % of our trades are EV+ (compare with 12.3% baseline)
3. Filter out EV- trades (below threshold)
4. Report EV stats via Telegram

EV Formula for binary market:
    EV = P(win) × (1/price - 1) × stake - P(loss) × stake - fee
    Simplified: EV = (model_wr / price - 1) × stake - fee_pct × stake

Where model_wr comes from Bayesian posterior (Phase 66) or calibrated odds.

ENV:
    EV_THRESHOLD_ENABLED=true
    EV_MINIMUM=0.005             # Minimum EV per dollar (0.5¢ per $1)
    EV_STRICT_MODE=false         # If true: skip ALL EV- trades. If false: just log + reduce size
    EV_SIZE_PENALTY=0.50         # Reduce size to 50% for marginal EV (0-minimum range)
    EV_FEE_OVERRIDE=0.0          # Override fee if > 0, else use computed fee
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("polypaper.calibration.ev_threshold")

# ── ENV ──
_ENABLED = os.getenv("EV_THRESHOLD_ENABLED", "true").lower() == "true"
_EV_MIN = float(os.getenv("EV_MINIMUM", "0.005"))
_STRICT = os.getenv("EV_STRICT_MODE", "false").lower() == "true"
_SIZE_PENALTY = float(os.getenv("EV_SIZE_PENALTY", "0.50"))
_FEE_OVERRIDE = float(os.getenv("EV_FEE_OVERRIDE", "0.0"))


@dataclass
class EVResult:
    """Expected Value calculation result."""
    ev_per_dollar: float = 0.0     # EV per $1 stake
    ev_positive: bool = False      # EV > 0
    ev_above_threshold: bool = False  # EV > minimum
    should_trade: bool = True      # Pass EV gate?
    size_multiplier: float = 1.0   # Position size modifier
    model_wr: float = 0.0         # Model win rate used
    market_price: float = 0.0     # Market implied probability
    edge: float = 0.0             # model_wr - market_price
    fee_pct: float = 0.0          # Fee applied
    reason: str = ""


def compute_ev(
    model_wr: float,
    market_price: float,
    fee_pct: float = 0.02,
    is_maker: bool = False,
) -> EVResult:
    """
    Compute Expected Value for a binary market trade.

    Args:
        model_wr: Our estimated win probability (0-1)
        market_price: Market price / implied probability (0-1)
        fee_pct: Fee percentage (default 2% taker)
        is_maker: If True, use maker rebate instead of fee

    Returns:
        EVResult with EV breakdown.
    """
    if not _ENABLED:
        return EVResult(
            should_trade=True, reason="disabled",
            model_wr=model_wr, market_price=market_price,
        )

    # Validate inputs
    if market_price <= 0.01 or market_price >= 0.99:
        return EVResult(
            should_trade=True, reason="extreme_price",
            model_wr=model_wr, market_price=market_price,
        )

    if model_wr <= 0.0 or model_wr >= 1.0:
        return EVResult(
            should_trade=True, reason="invalid_model_wr",
            model_wr=model_wr, market_price=market_price,
        )

    # Fee handling
    if _FEE_OVERRIDE > 0:
        effective_fee = _FEE_OVERRIDE
    elif is_maker:
        # Makers get rebate — effective fee is negative (benefit)
        effective_fee = -0.005  # ~0.5% rebate
    else:
        effective_fee = fee_pct

    # EV calculation for binary market:
    # If we buy YES at price P, we get $1 on win, lose $P on loss
    # EV = P(win) × ($1 - P) - P(loss) × P - fee × P
    # Simplified per dollar of stake:
    # stake = $1 → shares = 1/P → EV = P(win)/P - 1 - fee
    #
    # More precisely:
    # payout_on_win = (1/price) × stake - stake = (1/price - 1) × stake
    # loss_on_loss = stake
    # EV = model_wr × payout_on_win - (1-model_wr) × loss_on_loss - fee × stake
    # Per dollar: EV_per_dollar = model_wr × (1/price - 1) - (1 - model_wr) - fee

    ev_per_dollar = (
        model_wr * (1.0 / market_price - 1.0) -
        (1.0 - model_wr) -
        effective_fee
    )

    edge = model_wr - market_price
    ev_positive = ev_per_dollar > 0
    ev_above_threshold = ev_per_dollar >= _EV_MIN

    # Trading decision
    if ev_above_threshold:
        should_trade = True
        size_mult = 1.0
        reason = f"ev_ok({ev_per_dollar:+.4f})"
    elif ev_positive and not _STRICT:
        # Marginal EV — reduce size but allow
        should_trade = True
        size_mult = _SIZE_PENALTY
        reason = f"ev_marginal({ev_per_dollar:+.4f})"
    elif not ev_positive and _STRICT:
        should_trade = False
        size_mult = 0.0
        reason = f"ev_negative_strict({ev_per_dollar:+.4f})"
    elif not ev_positive:
        # Non-strict: allow but heavily penalize
        should_trade = True
        size_mult = _SIZE_PENALTY * 0.5
        reason = f"ev_negative({ev_per_dollar:+.4f})"
    else:
        should_trade = not _STRICT
        size_mult = _SIZE_PENALTY
        reason = f"ev_below_threshold({ev_per_dollar:+.4f})"

    return EVResult(
        ev_per_dollar=round(ev_per_dollar, 6),
        ev_positive=ev_positive,
        ev_above_threshold=ev_above_threshold,
        should_trade=should_trade,
        size_multiplier=round(size_mult, 3),
        model_wr=round(model_wr, 4),
        market_price=round(market_price, 4),
        edge=round(edge, 4),
        fee_pct=round(effective_fee, 4),
        reason=reason,
    )


class EVTracker:
    """Track EV statistics over time. Compares with A7 baseline (12.3%)."""

    BASELINE_EV_POSITIVE_PCT = 12.3  # A7 finding

    def __init__(self):
        self._total = 0
        self._ev_positive = 0
        self._ev_above_threshold = 0
        self._total_ev = 0.0

    def record(self, ev_result: EVResult) -> None:
        """Record an EV computation."""
        self._total += 1
        if ev_result.ev_positive:
            self._ev_positive += 1
        if ev_result.ev_above_threshold:
            self._ev_above_threshold += 1
        self._total_ev += ev_result.ev_per_dollar

    @property
    def ev_positive_pct(self) -> float:
        """Percentage of trades with positive EV."""
        if self._total == 0:
            return 0.0
        return (self._ev_positive / self._total) * 100.0

    @property
    def ev_threshold_pct(self) -> float:
        """Percentage of trades above EV threshold."""
        if self._total == 0:
            return 0.0
        return (self._ev_above_threshold / self._total) * 100.0

    @property
    def mean_ev(self) -> float:
        """Mean EV per dollar across all recorded trades."""
        if self._total == 0:
            return 0.0
        return self._total_ev / self._total

    @property
    def beats_baseline(self) -> bool:
        """Are we better than the A7 baseline (12.3% EV+)?"""
        return self.ev_positive_pct > self.BASELINE_EV_POSITIVE_PCT

    def format_telegram(self) -> str:
        """Format EV stats for Telegram."""
        icon = "🟢" if self.beats_baseline else "🔴"
        lines = [
            f"{icon} <b>EV Threshold Stats</b>",
            f"Total trades evaluated: <b>{self._total}</b>",
            f"EV+ trades: <b>{self._ev_positive}</b> "
            f"({self.ev_positive_pct:.1f}%)",
            f"Above threshold: <b>{self._ev_above_threshold}</b> "
            f"({self.ev_threshold_pct:.1f}%)",
            f"Mean EV/dollar: <b>{self.mean_ev:+.4f}</b>",
            "",
            f"A7 baseline: 12.3% EV+",
        ]
        if self.beats_baseline:
            lines.append("✅ <b>Baseline'ın üstünde!</b>")
        else:
            lines.append("⚠️ Baseline'ın altında — filtreleme artır")
        return "\n".join(lines)

    def reset(self) -> None:
        """Reset counters."""
        self._total = 0
        self._ev_positive = 0
        self._ev_above_threshold = 0
        self._total_ev = 0.0
