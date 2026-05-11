"""
PolyPaper Bot - Opening Breakout Strategy (Backtest v2)
İlk dakikada BTC hareketine göre yön belirle.

Tweet verisi:
  - İlk dakikada BTC $10+ hareket → %57 hit rate
  - $50+ hareket → daha yüksek doğruluk ama az trade

Parameters:
  - breakout_usd: min BTC move in USD to trigger (default 10)
  - entry_window_sec: how long to monitor for breakout (default 60)
  - max_elapsed_pct: latest entry point (default 0.35)
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
class OpeningBreakoutStrategy(BaseBacktestStrategy):
    name = "opening_breakout"
    version = "1.0"
    description = "First-minute BTC price breakout → directional bet"

    def __init__(
        self,
        breakout_usd: float = 10.0,
        entry_window_sec: float = 60.0,
        max_elapsed_pct: float = 0.35,
    ):
        self.breakout_usd = breakout_usd
        self.entry_window_sec = entry_window_sec
        self.max_elapsed_pct = max_elapsed_pct
        self._open_price = 0.0

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        self._open_price = 0.0

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        if snap.binance_price <= 0:
            return None

        # Record opening price
        if self._open_price == 0.0:
            self._open_price = snap.binance_price
            return None

        # Only check within entry window
        if snap.elapsed_seconds > self.entry_window_sec:
            return None
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Calculate USD move
        move = snap.binance_price - self._open_price

        if abs(move) >= self.breakout_usd:
            direction = "up" if move > 0 else "down"
            # Confidence scales with move size
            confidence = min(0.85, 0.55 + (abs(move) / self.breakout_usd - 1) * 0.05)
            if direction == "up":
                entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
            else:
                entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
            return self.make_signal(
                direction,
                confidence,
                entry,
                reason=f"opening_breakout: BTC ${move:+.1f} in " f"{snap.elapsed_seconds:.0f}s",
                btc_move_usd=move,
            )

        return None
