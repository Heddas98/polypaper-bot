"""
PolyPaper Bot - Phase 45 — Backtest v3 fee model
================================================

Drop-in replacement for FeeCalculator that wires the live engine's
fees_v2 (Mart 2026 linear, category-aware, maker rebate) into the
backtest loop. This is the *first* major piece of Phase 45 — the
remaining pieces (Becker calibration injection + maker rebate accounting
in the portfolio + replay-engine swap-in) layer on top of this primitive.

Why a separate module:
  - As of 2026-04-21 Epic 4 T4.1 the codebase has a SINGLE fee oracle:
    `core/fees_v2.py` (Mart 2026 linear, category-aware). The old
    `core/fees.py` v1 quadratic module + `category="legacy"` branch +
    `tests/unit/test_fees.py` were deleted. fees_v2 was validated against
    the live Polymarket Gamma feeSchedule (rate=0.072 / exp=1 /
    rebateRate=0.2 for crypto) so a synthetic regression test against
    a pre-March-2026 oracle would only constrain new development to
    match a deprecated curve.
  - Phase 38 also kept FeeMode.ZERO/MAKER as a no-op gate. The new V3
    mode adds:
        FeeMode.V3            → polymarket_taker_fee_v2 + crypto category
        FeeMode.V3_MAKER      → maker rebate path (negative fee = credit)
        FeeMode.V3_AUTO       → V3_MAKER while spread > MAKER_WIDE_SPREAD
                                else V3 (mirrors live engine logic).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from core.fees_v2 import (
    polymarket_taker_fee_v2,
    polymarket_maker_rebate,
    in_tail_zone,
    ev_after_fee_v2,
)

logger = logging.getLogger("polypaper.backtest.fee_v3")


class FeeModeV3(Enum):
    V3 = "v3"                    # Always taker, fees_v2 path
    V3_MAKER = "v3_maker"        # Always maker → negative fee (rebate)
    V3_AUTO = "v3_auto"          # Spread-based maker/taker switch


class FeeCalculatorV3:
    """Phase 45 — fees_v2 wired into the backtest portfolio."""

    def __init__(self, mode: FeeModeV3 = FeeModeV3.V3,
                 category: str = "crypto",
                 wide_spread: float = 0.04,
                 tail_skip: bool = True):
        self.mode = mode
        self.category = category
        self.wide_spread = wide_spread
        self.tail_skip = tail_skip
        self._maker_count = 0
        self._taker_count = 0
        self._maker_rebate_total = 0.0
        self._taker_fee_total = 0.0

    # ── decision: maker vs taker ─────────────────────────────────────
    def _is_maker(self, spread: Optional[float]) -> bool:
        if self.mode == FeeModeV3.V3:
            return False
        if self.mode == FeeModeV3.V3_MAKER:
            return True
        # V3_AUTO
        if spread is None:
            return False
        return spread > self.wide_spread

    # ── public API ───────────────────────────────────────────────────
    def should_skip_tail(self, price: float) -> bool:
        return self.tail_skip and in_tail_zone(price)

    def calculate_fee(self, price: float, amount_usd: float,
                      spread: Optional[float] = None) -> float:
        """Return fee in USDC. Negative fee = maker rebate credit."""
        taker_fee = polymarket_taker_fee_v2(price, amount_usd, self.category)
        if self._is_maker(spread):
            rebate = polymarket_maker_rebate(taker_fee, self.category)
            self._maker_count += 1
            self._maker_rebate_total += rebate
            return -rebate  # negative fee = credit to portfolio
        self._taker_count += 1
        self._taker_fee_total += taker_fee
        return taker_fee

    def calculate_ev(self, price: float, win_prob: float,
                     amount: float = 1.0,
                     spread: Optional[float] = None) -> float:
        """Expected value after fees, maker rebate aware."""
        is_maker = self._is_maker(spread)
        return ev_after_fee_v2(
            price=price, win_probability=win_prob, amount=amount,
            category=self.category, is_maker=is_maker,
        )

    def get_stats(self) -> dict:
        total = self._maker_count + self._taker_count
        return {
            "mode": self.mode.value,
            "category": self.category,
            "trade_count": total,
            "maker_count": self._maker_count,
            "taker_count": self._taker_count,
            "maker_pct": round(self._maker_count / total * 100, 2) if total else 0.0,
            "maker_rebate_total": round(self._maker_rebate_total, 4),
            "taker_fee_total": round(self._taker_fee_total, 4),
            "net_fee_paid": round(self._taker_fee_total - self._maker_rebate_total, 4),
        }

    @staticmethod
    def for_live_parity(category: str = "crypto") -> "FeeCalculatorV3":
        """Match live engine settings exactly (V3_AUTO + crypto)."""
        return FeeCalculatorV3(
            mode=FeeModeV3.V3_AUTO, category=category, wide_spread=0.04,
        )
