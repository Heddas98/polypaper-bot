"""
Phase 69: Strategy Reputation Scoring
======================================
Source: AI-Trader (agent reputation system)

Thompson Sampling alpha/beta gives exploration/exploitation balance.
Reputation adds two extra dimensions:
  1. Streak-based: Hot/cold streaks affect confidence
  2. Market-type-based: Some strategies work better in certain conditions

Reputation score is a multiplier on trade_amount:
  - reputation > 1.0 → size up (hot streak, favorable conditions)
  - reputation < 1.0 → size down (cold streak, unfavorable conditions)
  - reputation = 1.0 → neutral

ENV:
    REPUTATION_ENABLED=true
    REPUTATION_STREAK_WEIGHT=0.30       # Weight of streak component
    REPUTATION_MARKET_WEIGHT=0.30       # Weight of market-type component
    REPUTATION_HISTORICAL_WEIGHT=0.40   # Weight of historical WR
    REPUTATION_MIN=0.5                  # Floor: never size below 50%
    REPUTATION_MAX=1.5                  # Cap: never size above 150%
"""
from __future__ import annotations

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger("polypaper.utils.reputation")

_REP_ENABLED = os.getenv("REPUTATION_ENABLED", "true").lower() == "true"
_STREAK_W = float(os.getenv("REPUTATION_STREAK_WEIGHT", "0.30"))
_MARKET_W = float(os.getenv("REPUTATION_MARKET_WEIGHT", "0.30"))
_HIST_W = float(os.getenv("REPUTATION_HISTORICAL_WEIGHT", "0.40"))
_REP_MIN = float(os.getenv("REPUTATION_MIN", "0.5"))
_REP_MAX = float(os.getenv("REPUTATION_MAX", "1.5"))


@dataclass
class ReputationScore:
    """Strategy reputation breakdown."""
    strategy_id: str = ""
    overall: float = 1.0          # Final multiplier
    streak_score: float = 1.0     # Based on recent win/loss streak
    market_score: float = 1.0     # Based on market conditions
    historical_score: float = 1.0 # Based on overall WR
    is_hot: bool = False          # 3+ win streak
    is_cold: bool = False         # 3+ loss streak
    reason: str = ""


def compute_reputation(
    strategy_id: str,
    recent_results: list[bool],     # True=win, False=loss, most recent first
    win_rate: float,                # Overall WR (0-1)
    total_trades: int,
    # Market condition signals
    is_trending: bool = False,      # From regime detection
    is_weekend: bool = False,       # Weekend = less competition
    hour_utc: int = 12,
    strategy_type: str = "fusion",
) -> ReputationScore:
    """
    Compute reputation multiplier for a strategy.

    Returns ReputationScore with overall multiplier [0.5, 1.5].
    """
    if not _REP_ENABLED:
        return ReputationScore(strategy_id=strategy_id, reason="disabled")

    # ── 1. Streak Score ──
    streak_score = 1.0
    is_hot = False
    is_cold = False

    if recent_results:
        # Count consecutive wins/losses from most recent
        streak_type = recent_results[0]
        streak_len = 0
        for r in recent_results:
            if r == streak_type:
                streak_len += 1
            else:
                break

        if streak_type and streak_len >= 3:
            # Win streak
            is_hot = True
            streak_score = 1.0 + min(streak_len * 0.05, 0.25)  # Max 1.25
        elif not streak_type and streak_len >= 3:
            # Loss streak
            is_cold = True
            streak_score = 1.0 - min(streak_len * 0.08, 0.35)  # Min 0.65
        elif streak_len >= 2:
            streak_score = 1.05 if streak_type else 0.95

    # ── 2. Market Condition Score ──
    market_score = 1.0

    # Weekend bonus (less competition, more mispricing)
    if is_weekend:
        market_score *= 1.1

    # Night bonus (same logic)
    if hour_utc >= 2 and hour_utc < 6:
        market_score *= 1.05

    # Strategy-type × condition matching
    if is_trending:
        # Trending market: momentum good, contrarian bad
        if strategy_type in ("momentum", "fusion"):
            market_score *= 1.1
        elif strategy_type == "contrarian":
            market_score *= 0.85
    else:
        # Ranging market: contrarian good, momentum bad
        if strategy_type == "contrarian":
            market_score *= 1.1
        elif strategy_type == "momentum":
            market_score *= 0.90

    # ── 3. Historical Score ──
    historical_score = 1.0
    if total_trades >= 10:
        if win_rate >= 0.65:
            historical_score = 1.2
        elif win_rate >= 0.58:
            historical_score = 1.1
        elif win_rate >= 0.52:
            historical_score = 1.0
        elif win_rate >= 0.48:
            historical_score = 0.85
        else:
            historical_score = 0.7

    # ── Combine ──
    overall = (
        streak_score * _STREAK_W +
        market_score * _MARKET_W +
        historical_score * _HIST_W
    )

    # Clamp
    overall = max(_REP_MIN, min(_REP_MAX, overall))

    reason_parts = []
    if is_hot:
        reason_parts.append(f"🔥hot({streak_score:.2f})")
    elif is_cold:
        reason_parts.append(f"❄️cold({streak_score:.2f})")
    if market_score != 1.0:
        reason_parts.append(f"mkt({market_score:.2f})")
    if historical_score != 1.0:
        reason_parts.append(f"hist({historical_score:.2f})")

    return ReputationScore(
        strategy_id=strategy_id,
        overall=round(overall, 3),
        streak_score=round(streak_score, 3),
        market_score=round(market_score, 3),
        historical_score=round(historical_score, 3),
        is_hot=is_hot,
        is_cold=is_cold,
        reason=" ".join(reason_parts) if reason_parts else "neutral",
    )
