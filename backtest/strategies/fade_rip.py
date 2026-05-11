"""
PolyPaper Bot - Fade the Rip Strategy (Backtest v2)
Büyük BTC hareketinden sonra ters yön bet.

Tweet verisi:
  - BTC %0.3+ yükseldiğinde DOWN bet → profitable
  - BTC düştüğünde UP bet → coin flip (asimetrik!)

Parameters:
  - rip_threshold_pct: min BTC move to trigger fade (default 0.3%)
  - min_elapsed_sec: wait for price to establish (default 30)
  - max_elapsed_pct: max market progress for entry (default 0.50)
  - fade_up_only: only fade up moves, not down (default True)
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
class FadeRipStrategy(BaseBacktestStrategy):
    name = "fade_rip"
    version = "1.0"
    description = "Fade large BTC price moves (mean reversion)"

    def __init__(
        self,
        rip_threshold_pct: float = 0.3,
        min_elapsed_sec: float = 30.0,
        max_elapsed_pct: float = 0.50,
        fade_up_only: bool = True,
    ):
        self.rip_threshold_pct = rip_threshold_pct
        self.min_elapsed_sec = min_elapsed_sec
        self.max_elapsed_pct = max_elapsed_pct
        self.fade_up_only = fade_up_only
        self._open_btc_price = 0.0

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        self._open_btc_price = 0.0

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        super().on_snapshot(snap)

        # Record opening BTC price
        if self._open_btc_price == 0.0 and snap.binance_price > 0:
            self._open_btc_price = snap.binance_price
            return None

        # Need BTC price reference
        if self._open_btc_price <= 0 or snap.binance_price <= 0:
            return None

        # Wait for price to develop
        if snap.elapsed_seconds < self.min_elapsed_sec:
            return None

        # Don't enter too late
        if snap.elapsed_pct > self.max_elapsed_pct:
            return None

        # Calculate BTC price change since market open
        pct_change = ((snap.binance_price - self._open_btc_price) / self._open_btc_price) * 100

        # Use binance_price_change if available and non-zero
        if snap.binance_price_change != 0:
            pct_change = snap.binance_price_change

        # Fade UP move (BTC ripped up → bet DOWN)
        if pct_change >= self.rip_threshold_pct:
            confidence = min(0.85, 0.55 + (pct_change - self.rip_threshold_pct) * 0.2)
            entry = snap.down_best_ask if snap.down_best_ask > 0 else 0.5
            return self.make_signal(
                "down",
                confidence,
                entry,
                reason=f"fade_rip: BTC +{pct_change:.3f}% → DOWN",
                btc_pct_change=pct_change,
            )

        # Fade DOWN move (BTC dropped → bet UP) — only if enabled
        if not self.fade_up_only and pct_change <= -self.rip_threshold_pct:
            confidence = min(0.75, 0.50 + (abs(pct_change) - self.rip_threshold_pct) * 0.15)
            entry = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
            return self.make_signal(
                "up",
                confidence,
                entry,
                reason=f"fade_rip: BTC {pct_change:.3f}% → UP",
                btc_pct_change=pct_change,
            )

        return None
