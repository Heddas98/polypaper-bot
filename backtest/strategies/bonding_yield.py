"""
BondingYield Strategy — Phase 76
Targets high-probability contracts (90-99c) for small, near-certain returns.
Like buying a bond: low yield (1-10%), very high probability (90-99%).

Key insight from X articles: contracts at 95c+ resolve YES 94-98% of the time.
Profit = (1.00 - entry_price) * shares - fees

ENV:
  BONDING_MIN_PRICE=0.90         # Min price to enter (90c)
  BONDING_MAX_PRICE=0.99         # Max price (99c)
  BONDING_MIN_YIELD=0.01         # Min expected yield (1%)
  BONDING_TIME_WEIGHT=true       # Prefer closer-to-resolution
  BONDING_MAX_HOURS_LEFT=48      # Max hours until resolution
  BONDING_CONFIDENCE_BASE=0.80   # Base confidence for qualifying contracts
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

BONDING_MIN_PRICE     = float(os.getenv("BONDING_MIN_PRICE", "0.90"))
BONDING_MAX_PRICE     = float(os.getenv("BONDING_MAX_PRICE", "0.99"))
BONDING_MIN_YIELD     = float(os.getenv("BONDING_MIN_YIELD", "0.01"))
BONDING_TIME_WEIGHT   = os.getenv("BONDING_TIME_WEIGHT", "true").lower() == "true"
BONDING_MAX_HOURS     = float(os.getenv("BONDING_MAX_HOURS_LEFT", "48"))
BONDING_CONF_BASE     = float(os.getenv("BONDING_CONFIDENCE_BASE", "0.80"))


@dataclass
class BondingSignal:
    direction: Optional[str] = None
    confidence: float = 0.0
    should_trade: bool = False
    reason: str = ""
    expected_yield: float = 0.0
    entry_price: float = 0.0
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class BondingYieldStrategy:
    """
    Buy high-probability contracts near resolution for bond-like returns.

    Logic:
    1. Check if UP or DOWN odds are in [MIN_PRICE, MAX_PRICE] range
    2. Calculate expected yield = (1.0 - price)
    3. Prefer contracts closer to resolution (time decay = profit accelerator)
    4. Higher confidence for higher prices (95c > 90c)
    """

    name = "BondingYield"

    def evaluate(self, snapshot) -> BondingSignal:
        """
        Args:
            snapshot: MarketSnapshot with up_odds, down_odds, minutes_remaining, etc.
        """
        up = snapshot.up_odds
        down = snapshot.down_odds
        mins_remaining = getattr(snapshot, 'minutes_remaining', 5.0)
        total_mins = getattr(snapshot, 'total_minutes', 5.0)
        spread = getattr(snapshot, 'spread', 0.02)

        # Check both directions
        candidates = []

        if BONDING_MIN_PRICE <= up <= BONDING_MAX_PRICE:
            exp_yield = 1.0 - up - 0.02  # minus ~2% fee estimate
            if exp_yield >= BONDING_MIN_YIELD:
                candidates.append(("up", up, exp_yield))

        if BONDING_MIN_PRICE <= down <= BONDING_MAX_PRICE:
            exp_yield = 1.0 - down - 0.02
            if exp_yield >= BONDING_MIN_YIELD:
                candidates.append(("down", down, exp_yield))

        if not candidates:
            return BondingSignal(reason="no qualifying contracts in bonding range")

        # Pick best candidate (highest yield)
        candidates.sort(key=lambda x: x[2], reverse=True)
        direction, price, exp_yield = candidates[0]

        # Time check: prefer closer to resolution
        hours_left = mins_remaining / 60.0
        if BONDING_TIME_WEIGHT and hours_left > BONDING_MAX_HOURS:
            return BondingSignal(
                reason=f"too far from resolution ({hours_left:.1f}h > {BONDING_MAX_HOURS}h)"
            )

        # Confidence: higher price = higher confidence
        # 90c -> 0.80, 95c -> 0.90, 99c -> 0.98
        confidence = BONDING_CONF_BASE + (price - BONDING_MIN_PRICE) * 2.0
        confidence = min(confidence, 0.99)

        # Time boost: closer to resolution = more confident
        if BONDING_TIME_WEIGHT and total_mins > 0:
            time_factor = 1.0 - (mins_remaining / total_mins)
            confidence = min(confidence + time_factor * 0.1, 0.99)

        # Spread check: tight spread needed for small yields
        if spread > exp_yield * 0.5:
            return BondingSignal(
                reason=f"spread too wide ({spread:.3f}) for yield ({exp_yield:.3f})"
            )

        return BondingSignal(
            direction=direction,
            confidence=round(confidence, 4),
            should_trade=True,
            reason=f"bonding {direction} @ {price:.2f}c, yield={exp_yield:.1%}",
            expected_yield=round(exp_yield, 4),
            entry_price=round(price, 4),
            metadata={
                "strategy": "BondingYield",
                "entry_price": price,
                "expected_yield": exp_yield,
                "hours_remaining": round(hours_left, 1),
            }
        )


def create_strategy():
    return BondingYieldStrategy()
