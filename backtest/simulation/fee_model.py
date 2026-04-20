"""
PolyPaper Bot - Backtest v2 Fee Model
Wraps core/fees_v2.py for backtest simulation.
Supports both standard and dynamic (15m) fee modes.

Phase 47f.9 (2026-04-09): unified to fees_v2 (Mart 2026 linear crypto curve)
to match live execution. Previously routed to core/fees.py quadratic (v1),
which under-counted taker fees by ~130% — backtest PnL was therefore
systematically over-optimistic vs live shadow trades.
  Maker: category rebate from realized taker pool (~20% for crypto)
  Taker: linear, rate=0.072 * (1-p)   (crypto category default)
"""
import logging
from enum import Enum
from core.fees_v2 import (
    polymarket_taker_fee_v2,
    polymarket_maker_rebate,
    DEFAULT_CATEGORY,
)

logger = logging.getLogger("polypaper.backtest.fee")


class FeeMode(Enum):
    STANDARD = "standard"          # Normal quadratic taker fee
    DYNAMIC_15M = "dynamic_15m"    # 15m markets: ~2x fee multiplier
    MAKER = "maker"                # 0% fee (limit orders)
    ZERO = "zero"                  # No fees (testing only)


class FeeCalculator:
    """Fee calculator for backtest simulation."""

    def __init__(self, mode: FeeMode = FeeMode.STANDARD,
                 category: str = DEFAULT_CATEGORY):
        self.mode = mode
        self.category = category
        # 15m markets historically carried roughly 2x the fee of 5m.
        self._dynamic_multiplier = 2.0

    def calculate_fee(self, price: float, amount_usd: float) -> float:
        """Calculate taker fee for a trade using the v2 linear curve."""
        if self.mode in (FeeMode.ZERO, FeeMode.MAKER):
            return 0.0
        base = polymarket_taker_fee_v2(price, amount_usd, self.category)
        if self.mode == FeeMode.DYNAMIC_15M:
            return round(base * self._dynamic_multiplier, 6)
        return base

    def fee_percent(self, price: float) -> float:
        """Effective taker fee as a percentage of notional."""
        if self.mode in (FeeMode.ZERO, FeeMode.MAKER):
            return 0.0
        # Use a $1 probe so the returned value is a pure ratio.
        pct = polymarket_taker_fee_v2(price, 1.0, self.category)
        if self.mode == FeeMode.DYNAMIC_15M:
            pct *= self._dynamic_multiplier
        return pct

    def calculate_ev(self, price: float, win_prob: float,
                     amount: float = 1.0) -> float:
        """Expected value after fees, using the v2 curve."""
        fee = self.calculate_fee(price, amount)
        shares = amount / price if price > 0 else 0
        ev_win = shares - amount - fee
        ev_lose = -amount - fee
        return round(win_prob * ev_win + (1 - win_prob) * ev_lose, 6)

    @staticmethod
    def for_market_type(market_type: str) -> "FeeCalculator":
        """Create appropriate fee calculator for market type."""
        # 15m markets historically carried a dynamic multiplier; keep the mode
        # wired but default off until we re-confirm it against live fills.
        if market_type in ("15m",) and False:
            return FeeCalculator(FeeMode.DYNAMIC_15M)
        return FeeCalculator(FeeMode.STANDARD)
