"""
PolyPaper Bot - Funding Rate Strategy (Backtest v2)
Binance perpetual futures funding rate → yön sinyali.

Tweet verisi: Sonuçlar mixed ama bazı eşiklerde profitable.
Pozitif funding + eşik üstü → longs overleveraged → SHORT → DOWN bet.
Negatif funding → shorts overleveraged → LONG → UP bet.

Kontra-intuitif: Yüksek pozitif funding = çok long pozisyon açık
= mean reversion DOWN beklentisi.

Parameters:
  - rate_threshold: min abs funding rate to trigger (default 0.0005)
  - contrarian: True=fade funding, False=follow funding (default True)
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
class FundingRateStrategy(BaseBacktestStrategy):
    name = "funding_rate"
    version = "1.0"
    description = "Binance funding rate → contrarian directional signal"

    def __init__(
        self, rate_threshold: float = 0.0005, contrarian: bool = True, max_elapsed_pct: float = 0.20
    ):
        self.rate_threshold = rate_threshold
        self.contrarian = contrarian
        self.max_elapsed_pct = max_elapsed_pct
        self._funding_rate: Optional[float] = None

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        # Funding rate should be in metadata (set by data collector)
        self._funding_rate = market.metadata.get("funding_rate")

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Only enter early
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Need funding rate data
        rate = self._funding_rate
        if rate is None:
            return None

        if abs(rate) < self.rate_threshold:
            return None

        # Determine direction
        if self.contrarian:
            # High positive funding → overleveraged longs → fade → DOWN
            direction = "down" if rate > 0 else "up"
        else:
            # Follow the funding direction
            direction = "up" if rate > 0 else "down"

        confidence = min(0.80, 0.55 + abs(rate) * 200)

        if direction == "up":
            entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
        else:
            entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5

        mode = "contrarian" if self.contrarian else "momentum"
        return self.make_signal(
            direction,
            confidence,
            entry,
            reason=f"funding_rate: rate={rate:.6f} ({mode}) → {direction.upper()}",
            funding_rate=rate,
        )
