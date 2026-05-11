"""
PolyPaper Bot - Backtest v2 Virtual Portfolio
Tracks balance, positions, trades, and performance metrics.

Supports:
  - Single position per market (binary outcome)
  - Trade logging with full PnL breakdown
  - Equity curve tracking
  - Sharpe, Sortino, max drawdown calculation
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from backtest.simulation.fee_model_v3 import FeeCalculatorV3 as FeeCalculator  # T4.1 unified
from backtest.simulation.fill_model import FillResult
from backtest.strategies.base import Signal

logger = logging.getLogger("polypaper.backtest.portfolio")


@dataclass
class Trade:
    """A completed trade record."""

    market_id: str = ""
    coin: str = "BTC"
    market_type: str = "5m"
    strategy: str = ""
    direction: str = ""  # "up" or "down"
    entry_price: float = 0.0
    exit_price: float = 0.0  # 1.0 if won, 0.0 if lost
    amount: float = 0.0  # USDC risked
    shares: float = 0.0
    fee: float = 0.0
    slippage: float = 0.0
    pnl: float = 0.0  # net PnL after fees
    won: bool = False
    confidence: float = 0.0
    reason: str = ""
    entry_time_pct: float = 0.0  # when in market window (0-1)
    hour_utc: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class PortfolioStats:
    """Aggregated portfolio statistics."""

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)


class VirtualPortfolio:
    """Tracks positions and performance during a backtest run."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        trade_amount: float = 1.0,
        fee_calculator: Optional[FeeCalculator] = None,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.trade_amount = trade_amount
        self.fee_calc = fee_calculator or FeeCalculator()

        self.trades: list[Trade] = []
        self.equity_curve: list[float] = [initial_balance]
        self._peak_balance = initial_balance
        self._max_drawdown = 0.0

    def open_trade(
        self,
        signal: Signal,
        fill: FillResult,
        market_id: str = "",
        coin: str = "BTC",
        market_type: str = "5m",
        strategy: str = "",
        hour_utc: int = 0,
        entry_time_pct: float = 0.0,
    ) -> Optional[Trade]:
        """
        Open a new trade position.

        Args:
            signal: strategy signal
            fill: fill simulation result
            market_id: market identifier
            coin: asset
            market_type: timeframe
            strategy: strategy name
        Returns:
            Trade object (incomplete — needs close_trade to finalize)
        """
        if not fill.filled:
            return None

        fee = self.fee_calc.calculate_fee(fill.fill_price, fill.fill_amount)

        # Check if we have enough balance
        total_cost = fill.fill_amount + fee
        if total_cost > self.balance:
            return None

        trade = Trade(
            market_id=market_id,
            coin=coin,
            market_type=market_type,
            strategy=strategy,
            direction=signal.direction.value,
            entry_price=fill.fill_price,
            amount=fill.fill_amount,
            shares=fill.shares,
            fee=fee,
            slippage=fill.slippage,
            confidence=signal.confidence,
            reason=signal.reason,
            entry_time_pct=entry_time_pct,
            hour_utc=hour_utc,
            metadata=signal.metadata,
        )

        # Deduct cost from balance
        self.balance -= total_cost
        return trade

    def close_trade(self, trade: Trade, winner: str) -> Trade:
        """
        Close a trade based on market resolution.

        Args:
            trade: open trade
            winner: "UP" or "DOWN"
        Returns:
            Finalized trade with PnL
        """
        won = trade.direction.upper() == winner.upper()
        trade.won = won

        if won:
            # Binary outcome: shares × $1.00
            payout = trade.shares * 1.0
            trade.exit_price = 1.0
        else:
            payout = 0.0
            trade.exit_price = 0.0

        # PnL = payout - amount - fee
        trade.pnl = round(payout - trade.amount - trade.fee, 6)

        # Update balance (add back payout)
        self.balance += payout

        # Track equity
        self.equity_curve.append(round(self.balance, 2))

        # Track drawdown
        if self.balance > self._peak_balance:
            self._peak_balance = self.balance
        current_dd = self._peak_balance - self.balance
        if current_dd > self._max_drawdown:
            self._max_drawdown = current_dd

        self.trades.append(trade)
        return trade

    def get_stats(self) -> PortfolioStats:
        """Calculate comprehensive portfolio statistics."""
        stats = PortfolioStats()
        stats.trades = self.trades
        stats.equity_curve = self.equity_curve
        stats.total_trades = len(self.trades)

        if not self.trades:
            return stats

        pnls = [t.pnl for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        stats.wins = len(wins)
        stats.losses = len(losses)
        stats.win_rate = (
            round(stats.wins / stats.total_trades * 100, 1) if stats.total_trades > 0 else 0
        )
        stats.total_pnl = round(sum(pnls), 2)
        stats.total_fees = round(sum(t.fee for t in self.trades), 4)
        stats.total_slippage = round(sum(t.slippage for t in self.trades), 4)
        stats.avg_pnl = round(stats.total_pnl / stats.total_trades, 4)
        stats.avg_win = round(sum(wins) / len(wins), 4) if wins else 0
        stats.avg_loss = round(sum(losses) / len(losses), 4) if losses else 0
        stats.best_trade = round(max(pnls), 4)
        stats.worst_trade = round(min(pnls), 4)
        stats.max_drawdown = round(self._max_drawdown, 2)
        stats.max_drawdown_pct = (
            round(self._max_drawdown / self.initial_balance * 100, 2)
            if self.initial_balance > 0
            else 0
        )

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        stats.profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss > 0
            else float("inf")
            if gross_profit > 0
            else 0
        )

        # Sharpe ratio (annualized, assuming ~100 trades/day)
        if len(pnls) > 1:
            mean_pnl = sum(pnls) / len(pnls)
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1))
            if std_pnl > 0:
                daily_sharpe = mean_pnl / std_pnl
                stats.sharpe_ratio = round(daily_sharpe * math.sqrt(252), 2)

            # Sortino (downside deviation only)
            neg_pnls = [p for p in pnls if p < 0]
            if neg_pnls:
                downside_std = math.sqrt(sum(p**2 for p in neg_pnls) / len(neg_pnls))
                if downside_std > 0:
                    stats.sortino_ratio = round(mean_pnl / downside_std * math.sqrt(252), 2)

        return stats
