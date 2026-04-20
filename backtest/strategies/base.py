"""
PolyPaper Bot - Backtest v2 Strategy Framework
Protocol-based strategy interface for event-driven backtesting.

Every strategy implements BacktestStrategy Protocol:
  on_market_open()   → called when market window starts
  on_snapshot()      → called for each orderbook snapshot → returns Signal or None
  on_market_close()  → called when market resolves

Strategies are auto-discovered from this package.
Each strategy is a single file with a class inheriting BaseBacktestStrategy.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger("polypaper.backtest.strategies.base")
from enum import Enum


# ── Data Models ──────────────────────────────────────────────

class Direction(Enum):
    UP = "up"
    DOWN = "down"


@dataclass
class MarketData:
    """Market metadata passed to strategies."""
    market_id: str = ""
    coin: str = "BTC"
    market_type: str = "5m"          # 5m, 15m, 1h, 4h, 24h
    question: str = ""
    start_time: str = ""
    end_time: str = ""
    winner: str = ""                 # "UP" or "DOWN" (set after resolution)
    volume: float = 0.0
    liquidity: float = 0.0
    up_token_id: str = ""
    down_token_id: str = ""
    duration_seconds: int = 300      # market window duration
    hour_utc: int = 0               # hour of day (0-23) for hour_edge
    metadata: dict = field(default_factory=dict)


@dataclass
class OrderbookSnapshot:
    """Single orderbook state at a point in time."""
    timestamp_ms: int = 0
    # Token prices
    up_best_bid: float = 0.0
    up_best_ask: float = 0.0
    down_best_bid: float = 0.0
    down_best_ask: float = 0.0
    spread: float = 0.0
    # External data
    binance_price: float = 0.0
    binance_price_change: float = 0.0  # % change since market open
    # Depth info (if available)
    up_bid_depth: float = 0.0       # total $ on UP bid side
    up_ask_depth: float = 0.0
    down_bid_depth: float = 0.0
    down_ask_depth: float = 0.0
    # Timing
    elapsed_seconds: float = 0.0     # seconds since market open
    remaining_seconds: float = 0.0   # seconds until market close
    elapsed_pct: float = 0.0         # 0.0-1.0 progress through market
    # Taker flow (if available from Binance)
    taker_buy_volume: float = 0.0
    taker_sell_volume: float = 0.0
    # Raw data
    raw: dict = field(default_factory=dict)


@dataclass
class Signal:
    """Trade signal emitted by a strategy."""
    direction: Direction             # UP or DOWN
    confidence: float = 0.5          # 0.0-1.0
    entry_price: float = 0.5        # desired entry price
    reason: str = ""                 # human-readable reason
    metadata: dict = field(default_factory=dict)

    @property
    def is_up(self) -> bool:
        return self.direction == Direction.UP

    @property
    def is_down(self) -> bool:
        return self.direction == Direction.DOWN


@dataclass
class Resolution:
    """Market resolution result."""
    winner: Direction                # UP or DOWN
    final_up_price: float = 0.0
    final_down_price: float = 0.0
    final_binance_price: float = 0.0
    total_volume: float = 0.0


# ── Strategy Protocol ────────────────────────────────────────

@runtime_checkable
class BacktestStrategy(Protocol):
    """
    Protocol that all backtest strategies must implement.

    Lifecycle:
      1. on_market_open(market)      — reset state, check if interested
      2. on_snapshot(snap) × N       — process each tick, optionally emit Signal
      3. on_market_close(market, res) — cleanup, record outcome
    """
    name: str
    version: str

    def on_market_open(self, market: MarketData) -> None:
        """Called when a new market window starts."""
        ...

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        """
        Called for each orderbook snapshot during market window.
        Return Signal to enter a trade, None to skip.
        Only the FIRST signal per market is used (single entry).
        """
        ...

    def on_market_close(self, market: MarketData,
                        result: Resolution) -> None:
        """Called when market resolves. For stats/learning."""
        ...


# ── Base Class (optional convenience) ────────────────────────

class BaseBacktestStrategy:
    """
    Base class providing common functionality.
    Strategies can inherit this instead of implementing Protocol from scratch.
    """
    name: str = "base"
    version: str = "1.0"
    description: str = ""

    # Parameters (override in subclass)
    params: dict = {}

    # Internal state (reset per market)
    _market: Optional[MarketData] = None
    _snapshots_seen: int = 0
    _signal_emitted: bool = False

    def on_market_open(self, market: MarketData) -> None:
        """Reset state for new market."""
        self._market = market
        self._snapshots_seen = 0
        self._signal_emitted = False

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        """Override in subclass."""
        self._snapshots_seen += 1
        return None

    def on_market_close(self, market: MarketData,
                        result: Resolution) -> None:
        """Override for post-market analysis."""
        pass

    def make_signal(self, direction: str, confidence: float,
                    entry_price: float, reason: str = "",
                    **kwargs) -> Optional[Signal]:
        """Helper to create Signal if not already emitted."""
        if self._signal_emitted:
            return None
        self._signal_emitted = True
        d = Direction.UP if direction.lower() == "up" else Direction.DOWN
        return Signal(
            direction=d,
            confidence=confidence,
            entry_price=entry_price,
            reason=reason,
            metadata=kwargs,
        )

    def __repr__(self):
        return f"<Strategy:{self.name} v{self.version}>"


# ── Strategy Registry ────────────────────────────────────────

class StrategyRegistryV2:
    """Registry for backtest v2 strategies. Auto-discovers from files."""

    _strategies: dict[str, type] = {}

    @classmethod
    def register(cls, strategy_class: type) -> type:
        """Decorator to register a strategy."""
        name = getattr(strategy_class, "name", strategy_class.__name__)
        cls._strategies[name] = strategy_class
        return strategy_class

    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Get strategy class by name."""
        return cls._strategies.get(name)

    @classmethod
    def create(cls, name: str, **params) -> Optional[BaseBacktestStrategy]:
        """Create strategy instance with parameters.

        Phase 81: Eğer backtest registry'de bulamazsa, live strategy'yi
        adaptör ile sarmalayarak döndürür. Böylece "momentum", "fusion"
        gibi live stratejiler de backtest'te çalışır.
        """
        klass = cls._strategies.get(name)
        if klass:
            instance = klass()
            if params:
                instance.params = {**getattr(instance, "params", {}), **params}
            return instance

        # Phase 81: Backtest'te yoksa live adaptörü dene
        try:
            from backtest.strategies.live_adapter import get_live_adapter
            adapter = get_live_adapter(name, extra_params=params if params else None)
            if adapter:
                logger.info(f"StrategyRegistryV2: '{name}' → live adapter kullanılıyor")
                return adapter
        except ImportError:
            pass

        return None

    @classmethod
    def list_all(cls) -> list[str]:
        """Return all registered strategy names (backtest + live)."""
        names = list(cls._strategies.keys())
        # Phase 81: Live stratejileri de listele
        try:
            from core.strategy_plugins import StrategyRegistry
            live_reg = StrategyRegistry()
            for ln in live_reg.names:
                if ln not in names:
                    names.append(ln)
        except ImportError:
            pass
        return names

    @classmethod
    def count(cls) -> int:
        return len(cls._strategies)
