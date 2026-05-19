"""Backtest v2 Strategies — auto-import all strategy modules."""

from backtest.strategies.base import (
    BacktestStrategy,
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Resolution,
    Signal,
    StrategyRegistryV2,
)
from backtest.strategies.calibration_arb import CalibrationArbStrategy
from backtest.strategies.composite import CompositeStrategy
from backtest.strategies.cross_coin import CrossCoinStrategy
from backtest.strategies.fade_rip import FadeRipStrategy
from backtest.strategies.funding_rate import FundingRateStrategy

# Import strategies to trigger @register decorators
from backtest.strategies.hour_edge import HourEdgeStrategy
from backtest.strategies.late_convergence import LateConvergenceStrategy
from backtest.strategies.opening_breakout import OpeningBreakoutStrategy
from backtest.strategies.orderbook_imbalance import OrderbookImbalanceStrategy
from backtest.strategies.rule_based import RuleBasedStrategy  # Faz 3 (2026-05-20)
from backtest.strategies.streak_reversal import StreakReversalStrategy
from backtest.strategies.taker_flow import TakerFlowStrategy

__all__ = [
    "BacktestStrategy",
    "BaseBacktestStrategy",
    "StrategyRegistryV2",
    "MarketData",
    "OrderbookSnapshot",
    "Signal",
    "Resolution",
    "Direction",
    "HourEdgeStrategy",
    "StreakReversalStrategy",
    "LateConvergenceStrategy",
    "TakerFlowStrategy",
    "OrderbookImbalanceStrategy",
    "FadeRipStrategy",
    "CrossCoinStrategy",
    "OpeningBreakoutStrategy",
    "FundingRateStrategy",
    "CalibrationArbStrategy",
    "CompositeStrategy",
    "RuleBasedStrategy",
]
