"""
PolyPaper Bot - Cross-Coin Correlation Strategy (Backtest v2)
BTC→ETH/SOL korelasyon sinyali. Divergence anlarını exploit et.

Tweet verisi:
  - BTC-ETH: %82.5 aynı yön
  - BTC-SOL: %79.5 aynı yön
  - Divergence olduğunda → realignment beklentisi ile bet

Bu strateji bir "referans coin" market'inin sonucunu alıp,
"hedef coin" market'inde aynı yöne bet yapar.

Parameters:
  - reference_coin: hangi coin'in sonucunu referans al (default "BTC")
  - min_confidence: min entry confidence (default 0.60)
  - max_elapsed_pct: max entry timing (default 0.40)
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
class CrossCoinStrategy(BaseBacktestStrategy):
    name = "cross_coin"
    version = "1.0"
    description = "Cross-coin correlation: BTC direction → ETH/SOL alignment"

    def __init__(
        self,
        reference_coin: str = "BTC",
        min_confidence: float = 0.60,
        max_elapsed_pct: float = 0.40,
        btc_move_threshold: float = 0.1,
    ):
        self.reference_coin = reference_coin.upper()
        self.min_confidence = min_confidence
        self.max_elapsed_pct = max_elapsed_pct
        self.btc_move_threshold = btc_move_threshold
        self._btc_open_price = 0.0
        # Last known BTC direction from metadata
        self._ref_direction: Optional[str] = None

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        self._btc_open_price = 0.0
        self._ref_direction = None

        # Check if reference direction is in metadata
        # (set by engine when running multi-coin backtest)
        ref_dir = market.metadata.get("ref_coin_direction")
        if ref_dir:
            self._ref_direction = ref_dir

        # Skip if this IS the reference coin
        if market.coin.upper() == self.reference_coin:
            self._signal_emitted = True  # prevent signal emission

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Don't enter too late
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Method 1: Use reference direction from metadata
        if self._ref_direction:
            direction = self._ref_direction.lower()
            if direction == "up":
                entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
            else:
                entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
            return self.make_signal(
                direction,
                self.min_confidence,
                entry,
                reason=f"cross_coin: {self.reference_coin}→{direction.upper()} "
                f"(correlation alignment)",
            )

        # Method 2: Use BTC price movement as proxy
        if snap.binance_price > 0:
            if self._btc_open_price == 0:
                self._btc_open_price = snap.binance_price
                return None

            pct_change = ((snap.binance_price - self._btc_open_price) / self._btc_open_price) * 100

            if abs(pct_change) >= self.btc_move_threshold:
                direction = "up" if pct_change > 0 else "down"
                confidence = min(0.85, self.min_confidence + abs(pct_change) * 0.1)
                if direction == "up":
                    entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
                else:
                    entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
                return self.make_signal(
                    direction,
                    confidence,
                    entry,
                    reason=f"cross_coin: BTC {pct_change:+.3f}% "
                    f"→ {direction.upper()} alignment",
                    btc_pct_change=pct_change,
                )

        return None
