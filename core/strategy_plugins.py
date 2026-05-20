"""
PolyPaper Bot - Strategy Plugin System (2026-05-21 minimal)

═══════════════════════════════════════════════════════════════════════
HEDDAS DİREKTİFİ 2026-05-21: 20 hazır live strategy plugin silindi
(MomentumStrategy, ContrarianStrategy, ScalperStrategy, SniperStrategy,
MartingaleStrategy, FlashCrashStrategy, StreakReversalStrategy,
HighThresholdStrategy, LateConvergenceStrategy, PennyContractStrategy,
BondingYieldLiveStrategy, HourEdgeLiveStrategy, OrderbookImbalanceLiveStrategy,
FadeRipLiveStrategy, OpeningBreakoutLiveStrategy, FundingRateLiveStrategy,
CalibrationArbLiveStrategy, FusionStrategy, ClassicStrategy +
StrategyRegistry'nin __init__'inde otomatik kayıt).
Hiçbiri para kazandırmadı; Heddas LAB no-code rule_based ile kendi
kurallarını yazıyor.

KORUNANLAR (engine.py + engine_signals.py + tests bağlı):
  • StrategySignal       — dataclass (sinyal çıktısı)
  • MarketSnapshot       — dataclass (giriş verisi)
  • BaseStrategy         — Protocol (gelecekte ihtiyaç olursa)
  • StrategyRegistry     — boş registry (API uyumlu, no-op döner)

Bot etkisi:
  • engine.py:132        self.plugins = StrategyRegistry() → boş
  • engine_signals.py:494 self.plugins.get(stype) → None (loop atla)
  • engine_signals.py:667 self.plugins.evaluate(stype, snap) → no-signal
  • Otomatik trade YOK — Manuel /buy /sell + LAB backtest çalışmaya devam.

İleride live'da kullanmak istersen LAB RuleBasedStrategy live'a port
edilebilir (Aşama 4 sonrası iş).
═══════════════════════════════════════════════════════════════════════
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger("polypaper.core.strategy_plugins")


# ── Data classes (korundu — başka yerlerde bağlı) ────────────


@dataclass
class StrategySignal:
    """Output of a strategy evaluation."""

    direction: str | None = None  # "up", "down", None
    confidence: float = 0.0  # 0.0 to 1.0
    should_trade: bool = False
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class MarketSnapshot:
    """Input data for strategy evaluation.

    P0-08-F (2026-05-08): `timeframe` field eklendi. Plugin'ler
    TF-adaptive logic için kullanabilir (örn. 5m'de "son 1 dk" mantıklı,
    24h'de değil; ratio-based `minutes_remaining/total_minutes` tercih).
    """

    up_odds: float = 0.5
    down_odds: float = 0.5
    threshold: float = 0.50
    direction_filter: str = "any"  # "up", "down", "any"
    odds_series: list = field(default_factory=list)  # Historical up_odds
    minutes_remaining: float = 2.5
    total_minutes: float = 5.0
    timeframe: str = "5m"  # P0-08-F: TF context (5m/15m/1h/24h)
    spread: float = 0.02
    best_ask: float = 0.5
    best_bid: float = 0.48
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Protocol for all strategy plugins (placeholder, current registry empty)."""

    origin: str = "core"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @abstractmethod
    def evaluate(self, snapshot: MarketSnapshot) -> StrategySignal:
        """Evaluate market conditions and return signal."""
        ...


# ── Registry (boş — API uyumlu no-op) ────────────────────────


class StrategyRegistry:
    """Empty registry — bot otomatik trade etmez.

    2026-05-21 Heddas direktifi: tüm hazır plugin'ler silindi.
    Bu sınıf engine.py + engine_signals.py + tests'ler tarafından hâlâ
    instantiate ediliyor; API'sini koruyup boş dict üzerinde no-op
    işlem yapacak şekilde geriye uyumlu.
    """

    CONFIGURABLE: dict[str, dict[str, type]] = {}

    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}
        logger.info(
            "StrategyRegistry: 0 plugin yüklendi — "
            "Heddas direktifi (2026-05-21) ile hazır stratejiler silindi. "
            "Manuel /buy /sell + LAB backtest çalışıyor."
        )

    def register(self, strategy: BaseStrategy):
        """Yeni strateji kaydet — şu an kimse çağırmıyor ama API koruna."""
        self._strategies[strategy.name] = strategy
        logger.info(f"Registered strategy plugin: {strategy.name}")

    def get(self, name: str) -> BaseStrategy | None:
        return self._strategies.get(name)

    def evaluate(self, name: str, snapshot: MarketSnapshot) -> StrategySignal:
        """Boş registry'de daima no-signal döner — engine atomik kabul eder."""
        strategy = self._strategies.get(name)
        if not strategy:
            return StrategySignal(
                reason=f"strategy '{name}' kayıtlı değil (2026-05-21 cleanup)"
            )
        return strategy.evaluate(snapshot)

    def list_all(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description, "origin": getattr(s, "origin", "core")}
            for s in self._strategies.values()
        ]

    @property
    def names(self) -> list[str]:
        return list(self._strategies.keys())

    def get_config(self, plugin_name: str) -> dict:
        """Boş registry'de daima {} döner."""
        return {}

    def set_config(self, plugin_name: str, param: str, value) -> bool:
        """Boş registry'de daima False döner — engine reload-config no-op."""
        return False
