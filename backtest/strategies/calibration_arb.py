"""
PolyPaper Bot - Calibration Arb Strategy (Backtest v2)
Fiyat-olasılık sapma tespiti.

Tweet verisi: Token $0.70 ise UP gerçekten %70 kazanıyor → oldukça doğru.
$0.50 civarı en verimli bölge.

Strateji: Eğer token fiyatı beklenen olasılıktan sapıyorsa,
"doğru" olasılığa doğru bet yap.

Parameters:
  - deviation_threshold: min price-probability deviation (default 0.08)
  - target_zone_low: only trade in this price zone (default 0.35)
  - target_zone_high: (default 0.65)
"""
from typing import Optional
from backtest.strategies.base import (
    BaseBacktestStrategy, StrategyRegistryV2,
    MarketData, OrderbookSnapshot, Signal, Direction,
)


@StrategyRegistryV2.register
class CalibrationArbStrategy(BaseBacktestStrategy):
    name = "calibration_arb"
    version = "1.0"
    description = "Price-probability miscalibration detection"

    def __init__(self, deviation_threshold: float = 0.08,
                 target_zone_low: float = 0.35,
                 target_zone_high: float = 0.65,
                 min_elapsed_pct: float = 0.10,
                 max_elapsed_pct: float = 0.60):
        self.deviation_threshold = deviation_threshold
        self.target_zone_low = target_zone_low
        self.target_zone_high = target_zone_high
        self.min_elapsed_pct = min_elapsed_pct
        self.max_elapsed_pct = max_elapsed_pct
        self._price_history: list[float] = []

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        self._price_history = []

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Track UP token mid price
        if snap.up_best_bid > 0 and snap.up_best_ask > 0:
            mid = (snap.up_best_bid + snap.up_best_ask) / 2
            self._price_history.append(mid)
        elif snap.up_best_ask > 0:
            self._price_history.append(snap.up_best_ask)
        else:
            return None

        # Wait for some price history
        if snap.elapsed_pct < self.min_elapsed_pct:
            return None
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        current_price = self._price_history[-1]

        # Only trade in target zone
        if not (self.target_zone_low <= current_price <= self.target_zone_high):
            return None

        # Calculate average price (expected = 0.50 for efficient market)
        avg_price = sum(self._price_history) / len(self._price_history)

        # Deviation from 0.50 (fair value)
        deviation = current_price - 0.50

        # If UP token is cheap (below fair value) → buy UP
        if deviation < -self.deviation_threshold:
            confidence = min(0.80, 0.55 + abs(deviation) * 2)
            return self.make_signal(
                "up", confidence, current_price,
                reason=f"calibration_arb: UP token undervalued "
                       f"price={current_price:.3f} (fair=0.50)",
                deviation=deviation,
                avg_price=avg_price,
            )

        # If UP token is expensive → bet DOWN (buy DOWN token which is cheap)
        if deviation > self.deviation_threshold:
            down_price = 1.0 - current_price
            confidence = min(0.80, 0.55 + abs(deviation) * 2)
            return self.make_signal(
                "down", confidence, down_price,
                reason=f"calibration_arb: DOWN token undervalued "
                       f"up_price={current_price:.3f}",
                deviation=deviation,
                avg_price=avg_price,
            )

        return None
