"""
PolyPaper Bot - Taker Flow Strategy (Backtest v2)
Binance agresif taker hacmi dominansı → yön sinyali.

Tweet verisi: %62.7 WR, 729 trade.
İlk 15 saniyedeki taker flow en prediktif.
Buy volume > sell volume → UP bet (ve tersi).

Parameters:
  - flow_ratio_threshold: min ratio to trigger (default 1.15)
  - min_volume: min total taker volume in window (default 50000 USD)
  - max_elapsed_pct: max market progress for entry (default 0.30)
"""

from typing import Optional

from backtest.strategies.base import (
    BaseBacktestStrategy,
    MarketData,
    OrderbookSnapshot,
    Signal,
    StrategyRegistryV2,
)


@StrategyRegistryV2.register
class TakerFlowStrategy(BaseBacktestStrategy):
    name = "taker_flow"
    version = "1.0"
    description = "Binance taker buy/sell imbalance → directional signal"

    def __init__(
        self,
        flow_ratio_threshold: float = 1.15,
        min_volume: float = 50000,
        max_elapsed_pct: float = 0.30,
    ):
        self.flow_ratio_threshold = flow_ratio_threshold
        self.min_volume = min_volume
        self.max_elapsed_pct = max_elapsed_pct
        self._cum_buy = 0.0
        self._cum_sell = 0.0

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        self._cum_buy = 0.0
        self._cum_sell = 0.0

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Only trade in early portion of market
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Accumulate taker flow
        if snap.taker_buy_volume > 0:
            self._cum_buy = snap.taker_buy_volume
        if snap.taker_sell_volume > 0:
            self._cum_sell = snap.taker_sell_volume

        total = self._cum_buy + self._cum_sell
        if total < self.min_volume:
            return None

        # Calculate flow ratio
        if self._cum_sell > 0 and self._cum_buy > 0:
            buy_ratio = self._cum_buy / self._cum_sell
            sell_ratio = self._cum_sell / self._cum_buy
        else:
            return None

        # Buy dominance → UP
        if buy_ratio >= self.flow_ratio_threshold:
            confidence = min(0.95, 0.55 + (buy_ratio - 1.0) * 0.3)
            entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
            return self.make_signal(
                "up",
                confidence,
                entry,
                reason=f"taker_flow: buy_ratio={buy_ratio:.2f} "
                f"(buy={self._cum_buy:.0f} sell={self._cum_sell:.0f})",
                buy_ratio=buy_ratio,
            )

        # Sell dominance → DOWN
        if sell_ratio >= self.flow_ratio_threshold:
            confidence = min(0.95, 0.55 + (sell_ratio - 1.0) * 0.3)
            entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
            return self.make_signal(
                "down",
                confidence,
                entry,
                reason=f"taker_flow: sell_ratio={sell_ratio:.2f} "
                f"(buy={self._cum_buy:.0f} sell={self._cum_sell:.0f})",
                sell_ratio=sell_ratio,
            )

        return None
