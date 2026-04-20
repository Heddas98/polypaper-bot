"""
PolyPaper Bot - Hour Edge Strategy
Based on PolyBackTest analysis of 9,075 BTC 5m markets + 744 1h markets.

Key findings:
  6am UTC:  57.8% UP win rate (384 trades) — BTC 5m
  10pm UTC: 68% DOWN (31 markets) — BTC 1h
  17h UTC:  65% DOWN — BTC 1h
  9am ET (14h UTC): 81.8% UP on 15m (22 trading days)

Strategy: At specific hours, bet the historically dominant direction.
Simple, no orderbook data needed, just time of day.

Parameters:
  edges: dict of {hour_utc: ("up"|"down", win_rate)}
  min_win_rate: minimum historical WR to act (default 55%)
  entry_pct: when in market window to enter (0.0-1.0, default 0.1 = early)
"""
from backtest.strategies.base import (
    BaseBacktestStrategy, StrategyRegistryV2,
    MarketData, OrderbookSnapshot, Signal, Resolution, Direction
)
from typing import Optional

# Default hour edges from PolyBackTest tweet analysis
DEFAULT_EDGES = {
    # BTC 5m edges
    6:  ("up", 0.578),     # 57.8% UP, 384 trades
    # BTC 1h edges
    22: ("down", 0.680),   # 68% DOWN, 31 markets
    17: ("down", 0.650),   # 65% DOWN
    # BTC 15m edge (9am ET = 14h UTC in summer, 13h winter)
    14: ("up", 0.818),     # 81.8% UP, 22 trading days (15m)
}


@StrategyRegistryV2.register
class HourEdgeStrategy(BaseBacktestStrategy):
    """Bet based on hour-of-day directional bias."""

    name = "hour_edge"
    version = "1.0"
    description = "Hour-based directional edge from PolyBackTest data"

    def __init__(self):
        self.params = {
            "edges": DEFAULT_EDGES,
            "min_win_rate": 0.55,
            "entry_pct": 0.1,  # enter at 10% of market window
        }
        self._market: Optional[MarketData] = None
        self._edge: Optional[tuple] = None
        self._signal_emitted = False
        self._snapshots_seen = 0

    def on_market_open(self, market: MarketData) -> None:
        self._market = market
        self._signal_emitted = False
        self._snapshots_seen = 0

        # Check if this hour has an edge
        edges = self.params.get("edges", DEFAULT_EDGES)
        min_wr = self.params.get("min_win_rate", 0.55)

        edge = edges.get(market.hour_utc)
        if edge and edge[1] >= min_wr:
            self._edge = edge
        else:
            self._edge = None

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        self._snapshots_seen += 1

        if self._signal_emitted or not self._edge:
            return None

        # Wait until entry_pct of market window
        entry_pct = self.params.get("entry_pct", 0.1)
        if snapshot.elapsed_pct < entry_pct:
            return None

        direction, win_rate = self._edge

        # Determine entry price
        if direction == "up":
            entry_price = snapshot.up_best_ask if snapshot.up_best_ask > 0 else 0.50
        else:
            entry_price = snapshot.down_best_ask if snapshot.down_best_ask > 0 else 0.50

        self._signal_emitted = True
        d = Direction.UP if direction == "up" else Direction.DOWN
        return Signal(
            direction=d,
            confidence=win_rate,
            entry_price=entry_price,
            reason=f"Hour {self._market.hour_utc} edge: {direction.upper()} "
                   f"WR={win_rate:.1%}",
            metadata={"hour": self._market.hour_utc, "hist_wr": win_rate},
        )

    def on_market_close(self, market: MarketData,
                        result: Resolution) -> None:
        pass
