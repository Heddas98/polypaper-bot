"""
PolyPaper Bot - Composite Strategy (Backtest v2)
Birden fazla sinyali birleştir. Signal fusion for backtesting.

Çalışma mantığı:
  1. Her sub-strategy'ye snapshot'ı gönder
  2. Signal'leri topla
  3. Majority vote veya weighted average ile karar ver

Parameters:
  - strategies: list of strategy instances to combine
  - min_agreement: minimum strategies agreeing on direction (default 2)
  - voting: "majority" or "weighted" (default "majority")
"""
from typing import Optional
from backtest.strategies.base import (
    BaseBacktestStrategy, StrategyRegistryV2,
    MarketData, OrderbookSnapshot, Signal, Direction,
)


@StrategyRegistryV2.register
class CompositeStrategy(BaseBacktestStrategy):
    name = "composite"
    version = "1.0"
    description = "Multi-signal fusion: combine multiple strategies"

    def __init__(self, strategies: list = None,
                 min_agreement: int = 2,
                 voting: str = "majority"):
        self.min_agreement = min_agreement
        self.voting = voting

        if strategies:
            self.sub_strategies = strategies
        else:
            # Phase 75-fix: Auto-populate with core strategies when none provided
            # This makes composite work in HyperOpt/Tournament (RegistryV2.create()
            # passes no args). Uses 3 diverse strategies for majority vote.
            self.sub_strategies = []
            try:
                from backtest.strategies.late_convergence import LateConvergenceStrategy
                from backtest.strategies.streak_reversal import StreakReversalStrategy
                from backtest.strategies.orderbook_imbalance import OrderbookImbalanceStrategy
                self.sub_strategies = [
                    LateConvergenceStrategy(),
                    StreakReversalStrategy(),
                    OrderbookImbalanceStrategy(),
                ]
                self.min_agreement = min(min_agreement, len(self.sub_strategies))
            except ImportError:
                pass  # Degrade gracefully — will produce no signals

    def add_strategy(self, strategy: BaseBacktestStrategy):
        """Add a sub-strategy to the composite."""
        self.sub_strategies.append(strategy)

    def on_market_open(self, market: MarketData) -> None:
        super().on_market_open(market)
        for strat in self.sub_strategies:
            strat.on_market_open(market)

    def on_snapshot(self, snap: OrderbookSnapshot) -> Optional[Signal]:
        # Don't call super() — we manage _signal_emitted ourselves
        self._snapshots_seen += 1
        if self._signal_emitted:
            return None

        # Collect signals from sub-strategies
        signals = []
        for strat in self.sub_strategies:
            sig = strat.on_snapshot(snap)
            if sig:
                signals.append(sig)

        if not signals:
            return None

        # Count votes by direction
        up_votes = [s for s in signals if s.is_up]
        down_votes = [s for s in signals if s.is_down]

        if self.voting == "majority":
            # Simple majority with min_agreement threshold
            if len(up_votes) >= self.min_agreement:
                avg_conf = sum(s.confidence for s in up_votes) / len(up_votes)
                avg_entry = sum(s.entry_price for s in up_votes) / len(up_votes)
                reasons = [s.reason for s in up_votes]
                self._signal_emitted = True
                return Signal(
                    direction=Direction.UP,
                    confidence=min(0.95, avg_conf + 0.05 * len(up_votes)),
                    entry_price=avg_entry,
                    reason=f"composite({len(up_votes)}/{len(signals)} UP): "
                           + " | ".join(reasons),
                )
            if len(down_votes) >= self.min_agreement:
                avg_conf = sum(s.confidence for s in down_votes) / len(down_votes)
                avg_entry = sum(s.entry_price for s in down_votes) / len(down_votes)
                reasons = [s.reason for s in down_votes]
                self._signal_emitted = True
                return Signal(
                    direction=Direction.DOWN,
                    confidence=min(0.95, avg_conf + 0.05 * len(down_votes)),
                    entry_price=avg_entry,
                    reason=f"composite({len(down_votes)}/{len(signals)} DOWN): "
                           + " | ".join(reasons),
                )

        elif self.voting == "weighted":
            # Weighted by confidence
            up_weight = sum(s.confidence for s in up_votes)
            down_weight = sum(s.confidence for s in down_votes)
            total_weight = up_weight + down_weight

            if total_weight == 0:
                return None

            if up_weight > down_weight and len(up_votes) >= self.min_agreement:
                avg_entry = sum(s.entry_price * s.confidence for s in up_votes) / up_weight
                self._signal_emitted = True
                return Signal(
                    direction=Direction.UP,
                    confidence=min(0.95, up_weight / total_weight),
                    entry_price=avg_entry,
                    reason=f"composite(weighted UP={up_weight:.2f} "
                           f"DOWN={down_weight:.2f})",
                )
            if down_weight > up_weight and len(down_votes) >= self.min_agreement:
                avg_entry = sum(s.entry_price * s.confidence for s in down_votes) / down_weight
                self._signal_emitted = True
                return Signal(
                    direction=Direction.DOWN,
                    confidence=min(0.95, down_weight / total_weight),
                    entry_price=avg_entry,
                    reason=f"composite(weighted DOWN={down_weight:.2f} "
                           f"UP={up_weight:.2f})",
                )

        return None

    def on_market_close(self, market: MarketData, result) -> None:
        super().on_market_close(market, result)
        for strat in self.sub_strategies:
            strat.on_market_close(market, result)
