"""
PolyPaper Bot - Streak Reversal Strategy
Based on PolyBackTest analysis of 9,521 BTC 5m markets.

Key findings:
  After 3+ same direction: 53.1% reversal
  After 5+ same direction: 54% reversal
  After 7+ same direction: 70% reversal
  Best on SOL 15m (least efficient market)

Strategy: Track consecutive market outcomes in same direction.
When streak reaches threshold, bet the opposite direction.

Requires: market outcome history (from cache or Gamma API).

Parameters:
  min_streak: minimum consecutive same-direction to trigger (default 5)
  confidence_map: {streak_length: win_rate}
  entry_pct: when in market to enter (default 0.15)
"""
from backtest.strategies.base import (
    BaseBacktestStrategy, StrategyRegistryV2,
    MarketData, OrderbookSnapshot, Signal, Resolution, Direction
)
from typing import Optional

# Streak → reversal probability from tweet data
DEFAULT_CONFIDENCE_MAP = {
    3: 0.531,   # 53.1% reversal after 3 in a row
    4: 0.535,
    5: 0.540,   # 54% reversal after 5+
    6: 0.580,
    7: 0.700,   # 70% reversal after 7+
    8: 0.720,
    9: 0.750,
    10: 0.780,
}


@StrategyRegistryV2.register
class StreakReversalStrategy(BaseBacktestStrategy):
    """Bet against streaks — mean reversion after N consecutive same-direction."""

    name = "streak_reversal"
    version = "1.0"
    description = "Mean reversion after N+ consecutive same-direction markets"

    def __init__(self):
        self.params = {
            "min_streak": 5,
            "confidence_map": DEFAULT_CONFIDENCE_MAP,
            "entry_pct": 0.15,
        }
        self._market: Optional[MarketData] = None
        self._signal_emitted = False
        self._snapshots_seen = 0

        # Streak tracking (persists across markets)
        self._streak_direction: str = ""  # "UP" or "DOWN"
        self._streak_count: int = 0
        self._should_bet: bool = False
        self._bet_direction: str = ""
        self._bet_confidence: float = 0.0

    def on_market_open(self, market: MarketData) -> None:
        self._market = market
        self._signal_emitted = False
        self._snapshots_seen = 0

        # Check if streak qualifies for a bet
        min_streak = self.params.get("min_streak", 5)
        conf_map = self.params.get("confidence_map", DEFAULT_CONFIDENCE_MAP)

        if self._streak_count >= min_streak:
            # Bet OPPOSITE of streak direction
            self._should_bet = True
            self._bet_direction = "DOWN" if self._streak_direction == "UP" else "UP"
            # Get confidence from map (use highest matching)
            self._bet_confidence = 0.5
            for streak_len, conf in sorted(conf_map.items()):
                if self._streak_count >= streak_len:
                    self._bet_confidence = conf
        else:
            self._should_bet = False

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        self._snapshots_seen += 1

        if self._signal_emitted or not self._should_bet:
            return None

        # Wait until entry_pct
        entry_pct = self.params.get("entry_pct", 0.15)
        if snapshot.elapsed_pct < entry_pct:
            return None

        direction = self._bet_direction
        if direction == "UP":
            entry_price = snapshot.up_best_ask if snapshot.up_best_ask > 0 else 0.50
        else:
            entry_price = snapshot.down_best_ask if snapshot.down_best_ask > 0 else 0.50

        self._signal_emitted = True
        d = Direction.UP if direction == "UP" else Direction.DOWN
        return Signal(
            direction=d,
            confidence=self._bet_confidence,
            entry_price=entry_price,
            reason=f"Streak reversal: {self._streak_count}× {self._streak_direction} "
                   f"→ bet {direction} (conf={self._bet_confidence:.1%})",
            metadata={
                "streak_count": self._streak_count,
                "streak_dir": self._streak_direction,
            },
        )

    def on_market_close(self, market: MarketData,
                        result: Resolution) -> None:
        """Update streak tracker with this market's outcome."""
        winner = result.winner.value.upper()  # "UP" or "DOWN"

        if winner == self._streak_direction:
            self._streak_count += 1
        else:
            self._streak_direction = winner
            self._streak_count = 1
