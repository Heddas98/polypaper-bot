"""
PolyPaper Bot - Orderbook Imbalance Strategy (Backtest v2)
Bid/ask depth asimetrisi → yön sinyali.

Tweet verisi: %57.6 hit rate.
Market açılışından 5 saniye sonra depth ölçümü.
Bid tarafı ağır olan yöne bet.

Parameters:
  - imbalance_threshold: min bid/ask ratio (default 1.30)
  - min_elapsed_sec: wait at least N seconds for depth to form (default 5)
  - max_elapsed_pct: max market progress for entry (default 0.25)
  - min_depth: minimum total depth to consider (default 100 USD)
"""
from typing import Optional
from backtest.strategies.base import (
    BaseBacktestStrategy, StrategyRegistryV2,
    MarketData, OrderbookSnapshot, Signal, Direction,
)


@StrategyRegistryV2.register
class OrderbookImbalanceStrategy(BaseBacktestStrategy):
    name = "orderbook_imbalance"
    version = "1.0"
    description = "Orderbook bid/ask depth imbalance → directional signal"

    def __init__(self, imbalance_threshold: float = 1.30,
                 min_elapsed_sec: float = 5.0,
                 max_elapsed_pct: float = 0.25,
                 min_depth: float = 100.0):
        self.imbalance_threshold = imbalance_threshold
        self.min_elapsed_sec = min_elapsed_sec
        self.max_elapsed_pct = max_elapsed_pct
        self.min_depth = min_depth

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Wait for orderbook to form
        if snap.elapsed_seconds < self.min_elapsed_sec:
            return None

        # Only trade early
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Need depth data
        up_bid = snap.up_bid_depth
        up_ask = snap.up_ask_depth
        down_bid = snap.down_bid_depth
        down_ask = snap.down_ask_depth

        # Check UP token: heavy bid = people want to buy UP = UP signal
        if up_bid > self.min_depth and up_ask > 0:
            up_ratio = up_bid / up_ask
            if up_ratio >= self.imbalance_threshold:
                confidence = min(0.90, 0.55 + (up_ratio - 1.0) * 0.15)
                entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
                return self.make_signal(
                    "up", confidence, entry,
                    reason=f"ob_imbalance: UP bid/ask={up_ratio:.2f} "
                           f"(bid={up_bid:.0f} ask={up_ask:.0f})",
                    up_ratio=up_ratio,
                )

        # Check DOWN token: heavy bid = people want to buy DOWN = DOWN signal
        if down_bid > self.min_depth and down_ask > 0:
            down_ratio = down_bid / down_ask
            if down_ratio >= self.imbalance_threshold:
                confidence = min(0.90, 0.55 + (down_ratio - 1.0) * 0.15)
                entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
                return self.make_signal(
                    "down", confidence, entry,
                    reason=f"ob_imbalance: DOWN bid/ask={down_ratio:.2f} "
                           f"(bid={down_bid:.0f} ask={down_ask:.0f})",
                    down_ratio=down_ratio,
                )

        # Also check cross-token: if UP ask is thin and DOWN is normal
        # → UP token is being bought aggressively
        total_bid = up_bid + down_bid
        total_ask = up_ask + down_ask
        if total_bid > self.min_depth and total_ask > 0:
            net_imbalance = (up_bid - down_bid) / (total_bid + 1)
            if abs(net_imbalance) > 0.3:
                direction = "up" if net_imbalance > 0 else "down"
                confidence = min(0.85, 0.50 + abs(net_imbalance) * 0.5)
                if direction == "up":
                    entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
                else:
                    entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
                return self.make_signal(
                    direction, confidence, entry,
                    reason=f"ob_imbalance: net={net_imbalance:.2f}",
                    net_imbalance=net_imbalance,
                )

        return None
