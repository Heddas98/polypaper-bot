"""
Phase 81: Live Strategy ↔ Backtest Adapter
==========================================

Bu adaptör, core/strategy_plugins.py'deki LIVE stratejileri
backtest/replay_engine.py'de çalıştırılabilir hale getirir.

SORUN:
  - Live stratejiler: evaluate(MarketSnapshot) → StrategySignal
  - Backtest stratejiler: on_snapshot(OrderbookSnapshot) → Signal
  - İki farklı interface, iki farklı data model

ÇÖZÜM:
  LiveStrategyBacktestAdapter bir köprü görevi görür:
  1. OrderbookSnapshot → MarketSnapshot dönüşümü yapar
  2. Live strategy'nin evaluate()'ini çağırır
  3. StrategySignal → Signal dönüşümü yapar

KULLANIM:
    from core.strategy_plugins import MomentumStrategy
    from backtest.strategies.live_adapter import LiveStrategyBacktestAdapter

    adapter = LiveStrategyBacktestAdapter(MomentumStrategy())
    # Artık ReplayEngine'de kullanılabilir:
    # engine = ReplayEngine(db, config)
    # engine._strategy = adapter

HyperOpt'ta:
    # PARAM_SPACES["momentum"] ile optimize et
    # Sonuçlar doğrudan live engine'e uygulanabilir

TEK STRATEJİ PRENSİBİ:
    Bir strateji oluşturulduğunda:
    - Backtest'te → bu adaptör ile test edilir
    - Paper trade'de → aynı evaluate() çalışır
    - Shadow'da → aynı evaluate() çalışır
    - Live'da → aynı evaluate() çalışır
    Hiçbir yerde farklı logic yoktur.
"""

from __future__ import annotations

import logging
from typing import Optional

from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Resolution,
    Signal,
)
from core.strategy_plugins import (
    BaseStrategy,
    MarketSnapshot,
    StrategyRegistry,
    StrategySignal,
)

logger = logging.getLogger("polypaper.backtest.live_adapter")


class LiveStrategyBacktestAdapter(BaseBacktestStrategy):
    """
    Live strategy'yi backtest motorunda çalıştıran adaptör.

    Live evaluate(MarketSnapshot) → Backtest on_snapshot(OrderbookSnapshot) köprüsü.
    Tek strateji, her yerde aynı logic.
    """

    def __init__(self, live_strategy: BaseStrategy, extra_params: Optional[dict] = None):
        """
        Args:
            live_strategy: core/strategy_plugins.py'den bir strateji instance'ı
            extra_params: DB'den gelen strateji-spesifik parametreler (opsiyonel).
                         Örn: {"trend_threshold": 0.03, "min_confidence": 0.4}
                         Bu parametreler live_strategy'nin attribute'larına set edilir.
        """
        self.live = live_strategy
        self.name = live_strategy.name
        self.version = "adapter-1.0"
        self.description = f"[LIVE→BT] {live_strategy.description}"

        # Extra parametreleri live strategy'ye uygula
        if extra_params:
            for k, v in extra_params.items():
                if hasattr(self.live, k):
                    setattr(self.live, k, v)
                elif hasattr(self.live, k.upper()):
                    setattr(self.live, k.upper(), v)

        # Internal state
        self._market: Optional[MarketData] = None
        self._odds_history: list[float] = []
        self._signal_emitted: bool = False
        self._snapshots_seen: int = 0
        self._total_minutes: float = 5.0
        self._direction_filter: str = "any"
        self._threshold: float = 0.50

    def configure(
        self, direction_filter: str = "any", threshold: float = 0.50, total_minutes: float = 5.0
    ):
        """DB'deki strateji parametrelerinden configure et."""
        self._direction_filter = direction_filter
        self._threshold = threshold
        self._total_minutes = total_minutes
        return self

    def on_market_open(self, market: MarketData) -> None:
        """Market başladığında state reset."""
        self._market = market
        self._odds_history = []
        self._signal_emitted = False
        self._snapshots_seen = 0

        # MarketData'dan timing bilgisi
        if market.duration_seconds > 0:
            self._total_minutes = market.duration_seconds / 60.0

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        """
        Her tick'te çağrılır.
        OrderbookSnapshot → MarketSnapshot dönüşümü yapar,
        live strategy'nin evaluate()'ini çağırır.
        """
        self._snapshots_seen += 1

        # Odds geçmişini biriktir (live evaluate için)
        self._odds_history.append(snap.up_best_bid if snap.up_best_bid > 0 else 0.5)

        # Tek sinyal prensibi: ilk sinyal verildiyse tekrar verme
        if self._signal_emitted:
            return None

        # ── OrderbookSnapshot → MarketSnapshot dönüşümü ──
        minutes_remaining = (
            snap.remaining_seconds / 60.0
            if snap.remaining_seconds > 0
            else (self._total_minutes * (1.0 - snap.elapsed_pct))
        )

        market_snapshot = MarketSnapshot(
            up_odds=snap.up_best_bid if snap.up_best_bid > 0 else 0.5,
            down_odds=snap.down_best_bid if snap.down_best_bid > 0 else 0.5,
            threshold=self._threshold,
            direction_filter=self._direction_filter,
            odds_series=list(self._odds_history),
            minutes_remaining=minutes_remaining,
            total_minutes=self._total_minutes,
            spread=snap.spread,
            best_ask=snap.up_best_ask if snap.up_best_ask > 0 else 0.5,
            best_bid=snap.up_best_bid if snap.up_best_bid > 0 else 0.5,
            metadata={
                "binance_price": snap.binance_price,
                "binance_change": snap.binance_price_change,
                "elapsed_pct": snap.elapsed_pct,
                "up_bid_depth": snap.up_bid_depth,
                "up_ask_depth": snap.up_ask_depth,
                "down_bid_depth": snap.down_bid_depth,
                "down_ask_depth": snap.down_ask_depth,
                "taker_buy_vol": snap.taker_buy_volume,
                "taker_sell_vol": snap.taker_sell_volume,
            },
        )

        # ── Live strategy'nin evaluate()'ini çağır ──
        sig: StrategySignal = self.live.evaluate(market_snapshot)

        # ── should_trade kontrolü ──
        if not sig.should_trade or not sig.direction:
            return None

        # ── StrategySignal → Signal dönüşümü ──
        direction = Direction.UP if sig.direction == "up" else Direction.DOWN

        # Entry price: yön'e göre uygun ask fiyatı
        if sig.direction == "up":
            entry_price = snap.up_best_ask if snap.up_best_ask > 0 else 0.5
        else:
            entry_price = snap.down_best_ask if snap.down_best_ask > 0 else 0.5

        self._signal_emitted = True

        return Signal(
            direction=direction,
            confidence=sig.confidence,
            entry_price=entry_price,
            reason=f"[LIVE:{self.name}] {sig.reason}",
            metadata=sig.metadata,
        )

    def on_market_close(self, market: MarketData, result: Resolution) -> None:
        """Market kapandığında temizlik."""
        self._odds_history = []
        self._signal_emitted = False


# ══════════════════════════════════════════════════════════════
#  CONVENIENCE: Tüm live stratejileri adaptör ile sarmalayan
#  registry. ReplayEngine ve HyperOpt bu registry'yi kullanır.
# ══════════════════════════════════════════════════════════════


def get_live_adapter(
    strategy_name: str,
    extra_params: Optional[dict] = None,
    direction_filter: str = "any",
    threshold: float = 0.50,
    total_minutes: float = 5.0,
) -> Optional[LiveStrategyBacktestAdapter]:
    """
    İsme göre live strategy'yi adaptör ile sarmalayıp döndürür.

    Args:
        strategy_name: "momentum", "contrarian", "fusion", vb.
        extra_params: HyperOpt'tan gelen optimize edilmiş parametreler
        direction_filter: "up", "down", "any"
        threshold: odds_threshold
        total_minutes: market süresi (dakika)

    Returns:
        LiveStrategyBacktestAdapter veya None (strateji bulunamazsa)

    Usage:
        adapter = get_live_adapter("momentum", {"trend_threshold": 0.03})
        # Bu artık ReplayEngine'de doğrudan kullanılabilir
    """
    registry = StrategyRegistry()
    live_strat = registry.get(strategy_name)
    if not live_strat:
        logger.warning(f"Live strategy not found: {strategy_name}")
        return None

    adapter = LiveStrategyBacktestAdapter(live_strat, extra_params)
    adapter.configure(
        direction_filter=direction_filter,
        threshold=threshold,
        total_minutes=total_minutes,
    )
    return adapter


def get_all_live_adapters() -> dict[str, LiveStrategyBacktestAdapter]:
    """
    Tüm live stratejileri adaptör ile döndürür.
    HyperOpt batch optimization için kullanılır.

    Returns:
        {"momentum": adapter, "contrarian": adapter, ...}
    """
    registry = StrategyRegistry()
    adapters = {}
    for name in registry.names:
        strat = registry.get(name)
        if strat:
            adapters[name] = LiveStrategyBacktestAdapter(strat)
    return adapters
