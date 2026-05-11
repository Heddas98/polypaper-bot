"""Backtest v2 Simulation — fill model, fee model, virtual portfolio.

2026-04-21 Epic 4 T4.1 consolidation: fee model unified to fees_v2-backed
FeeCalculatorV3 (was dual v1/v3). Legacy fee_model.py moved to _archive/.
FeeCalculator name kept as alias so downstream (portfolio, replay_engine,
engine_v2) needs no further edits. Default mode (FeeModeV3.V3) matches the
old FeeMode.STANDARD behavior bit-for-bit (taker-only, crypto category).
"""

from backtest.simulation.fee_model_v3 import (
    FeeCalculatorV3 as FeeCalculator,
    FeeModeV3 as FeeMode,
)
from backtest.simulation.fill_model import FillMode, FillResult, FillSimulator
from backtest.simulation.portfolio import PortfolioStats, Trade, VirtualPortfolio

__all__ = [
    "FillSimulator",
    "FillMode",
    "FillResult",
    "FeeCalculator",
    "FeeMode",
    "VirtualPortfolio",
    "PortfolioStats",
    "Trade",
]
