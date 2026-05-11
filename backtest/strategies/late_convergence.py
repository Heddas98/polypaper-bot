"""
PolyPaper Bot - Late Convergence Strategy
Based on PolyBackTest analysis: THE highest WR strategy found.

Key findings (6,140 BTC 5m markets):
  Minute 0:30 → 55.8% win rate
  Minute 1:00 → 60.2%
  Minute 2:00 → 72.1%
  Minute 3:00 → 85.4%
  Minute 4:00 → 98.9% win rate (!)
  Minute 4:30 → 96.4%

  With $30+ BTC move requirement:
  <120s remaining + $30+ move → 96.4% WR

Strategy: Wait until late in the market window.
Check which direction BTC is clearly moving. Bet that direction.
The later you wait, the higher the WR but the higher the entry price (lower EV/trade).

Parameters:
  min_elapsed_pct: minimum % of market window elapsed (default 0.80 = minute 4 of 5m)
  min_price_move: minimum BTC price change to confirm direction (default 0)
  max_entry_price: don't buy above this price (EV guard) (default 0.95)
"""

from typing import Optional

from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Resolution,
    Signal,
    StrategyRegistryV2,
)


@StrategyRegistryV2.register
class LateConvergenceStrategy(BaseBacktestStrategy):
    """Wait until market direction is clear, then bet with momentum."""

    name = "late_convergence"
    version = "1.0"
    description = "Bet dominant direction in final portion of market window (96%+ WR)"

    def __init__(self):
        self.params = {
            "min_elapsed_pct": 0.80,  # 80% of window = minute 4 of 5m
            "min_price_move": 0.0,  # minimum Binance price change ($)
            "max_entry_price": 0.95,  # don't buy above 95c
            "min_spread_threshold": 0.02,  # need at least 2c spread from 50/50
        }
        self._market: Optional[MarketData] = None
        self._signal_emitted = False
        self._snapshots_seen = 0
        self._first_binance_price = 0.0

    def on_market_open(self, market: MarketData) -> None:
        self._market = market
        self._signal_emitted = False
        self._snapshots_seen = 0
        self._first_binance_price = 0.0

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        self._snapshots_seen += 1

        # Record first Binance price
        if self._first_binance_price == 0 and snapshot.binance_price > 0:
            self._first_binance_price = snapshot.binance_price

        if self._signal_emitted:
            return None

        # Wait until late in market
        min_pct = self.params.get("min_elapsed_pct", 0.80)
        if snapshot.elapsed_pct < min_pct:
            return None

        # Determine dominant direction from orderbook prices
        up_price = snapshot.up_best_bid if snapshot.up_best_bid > 0 else snapshot.up_best_ask
        down_price = (
            snapshot.down_best_bid if snapshot.down_best_bid > 0 else snapshot.down_best_ask
        )

        if up_price <= 0 and down_price <= 0:
            return None

        # Check if there's a clear direction
        min_spread = self.params.get("min_spread_threshold", 0.02)

        # Direction determination: which side is clearly winning?
        if up_price > 0.5 + min_spread:
            direction = "UP"
            entry_price = up_price
            dominant_price = up_price
        elif down_price > 0.5 + min_spread:
            direction = "DOWN"
            entry_price = down_price
            dominant_price = down_price
        elif up_price > down_price:
            direction = "UP"
            entry_price = snapshot.up_best_ask if snapshot.up_best_ask > 0 else up_price
            dominant_price = up_price
        else:
            direction = "DOWN"
            entry_price = snapshot.down_best_ask if snapshot.down_best_ask > 0 else down_price
            dominant_price = down_price

        # Max entry price guard (EV protection)
        max_entry = self.params.get("max_entry_price", 0.95)
        if entry_price > max_entry:
            return None

        # Optional: Check Binance price move
        min_move = self.params.get("min_price_move", 0)
        if min_move > 0 and self._first_binance_price > 0:
            btc_change = abs(snapshot.binance_price - self._first_binance_price)
            if btc_change < min_move:
                return None

        # Calculate confidence based on how late we are and price clarity
        # Later = higher confidence, clearer price = higher confidence
        time_conf = min(0.99, 0.55 + (snapshot.elapsed_pct - 0.5) * 0.88)
        price_conf = abs(dominant_price - 0.5) * 2  # 0→1 based on distance from 50/50
        confidence = min(0.99, (time_conf + price_conf) / 2)

        self._signal_emitted = True
        d = Direction.UP if direction == "UP" else Direction.DOWN
        return Signal(
            direction=d,
            confidence=confidence,
            entry_price=entry_price,
            reason=f"Late convergence: {direction} dominant @ "
            f"{dominant_price:.2f} ({snapshot.elapsed_pct:.0%} elapsed)",
            metadata={
                "elapsed_pct": snapshot.elapsed_pct,
                "dominant_price": dominant_price,
                "btc_change": snapshot.binance_price - self._first_binance_price
                if self._first_binance_price > 0
                else 0,
            },
        )

    def on_market_close(self, market: MarketData, result: Resolution) -> None:
        pass
