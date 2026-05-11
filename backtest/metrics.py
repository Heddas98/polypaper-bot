"""
Phase 73: Performance Metrics
==============================
Source: R4 (freqtrade detailed performance), TradingView MCP

Provides comprehensive performance metrics for strategy evaluation:
    - Sharpe Ratio
    - Sortino Ratio
    - Max Drawdown
    - Calmar Ratio
    - Expectancy
    - Win/Loss Streaks
    - Profit Factor

Usage:
    from backtest.metrics import compute_metrics, PerformanceMetrics
    metrics = compute_metrics(pnl_series, risk_free_rate=0.05)
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PerformanceMetrics:
    """Comprehensive strategy performance metrics."""

    # Core
    total_pnl: float = 0.0
    total_trades: int = 0
    win_rate: float = 0.0
    wins: int = 0
    losses: int = 0

    # Risk-adjusted returns
    sharpe_ratio: float = 0.0  # (mean_return - rf) / std_return
    sortino_ratio: float = 0.0  # (mean_return - rf) / downside_std
    calmar_ratio: float = 0.0  # annualized_return / max_drawdown

    # Drawdown
    max_drawdown: float = 0.0  # Maximum peak-to-trough decline
    max_drawdown_pct: float = 0.0  # As percentage of peak
    max_drawdown_duration: int = 0  # Trades in longest drawdown

    # Streaks
    max_win_streak: int = 0
    max_loss_streak: int = 0
    current_streak: int = 0  # Positive = winning, negative = losing

    # Expectancy & profit factor
    expectancy: float = 0.0  # E[PnL] per trade
    profit_factor: float = 0.0  # gross_profit / gross_loss
    avg_win: float = 0.0
    avg_loss: float = 0.0
    win_loss_ratio: float = 0.0  # avg_win / avg_loss

    # Distribution
    mean_return: float = 0.0
    std_return: float = 0.0
    skewness: float = 0.0  # Positive = right-tailed (good)
    kurtosis: float = 0.0  # >3 = fat tails


def compute_metrics(
    pnl_series: list[float],
    risk_free_rate: float = 0.05,
    annualize_factor: float = 252.0,  # Trading days per year
) -> PerformanceMetrics:
    """
    Compute comprehensive performance metrics from a PnL series.

    Args:
        pnl_series: List of individual trade PnL values
        risk_free_rate: Annual risk-free rate (default 5%)
        annualize_factor: Factor to annualize (252 = daily, 365 = calendar)

    Returns:
        PerformanceMetrics with all calculated values.
    """
    m = PerformanceMetrics()

    if not pnl_series:
        return m

    n = len(pnl_series)
    m.total_trades = n
    m.total_pnl = round(sum(pnl_series), 4)

    # Win/loss
    wins = [p for p in pnl_series if p > 0]
    losses = [p for p in pnl_series if p <= 0]
    m.wins = len(wins)
    m.losses = len(losses)
    m.win_rate = round(m.wins / max(1, n), 4)

    # Average win/loss
    m.avg_win = round(sum(wins) / max(1, len(wins)), 4) if wins else 0.0
    m.avg_loss = round(sum(losses) / max(1, len(losses)), 4) if losses else 0.0
    m.win_loss_ratio = (
        round(abs(m.avg_win / m.avg_loss), 4)
        if m.avg_loss != 0
        else float("inf")
        if m.avg_win > 0
        else 0.0
    )

    # Expectancy
    m.expectancy = round(m.total_pnl / max(1, n), 4)

    # Profit factor
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    m.profit_factor = round(gross_profit / max(0.001, gross_loss), 4)

    # ── Returns statistics ──
    m.mean_return = round(sum(pnl_series) / n, 6)
    if n > 1:
        variance = sum((p - m.mean_return) ** 2 for p in pnl_series) / (n - 1)
        m.std_return = round(math.sqrt(variance), 6)
    else:
        m.std_return = 0.0

    # ── Sharpe Ratio ──
    rf_per_trade = risk_free_rate / annualize_factor
    if m.std_return > 0:
        m.sharpe_ratio = round(
            (m.mean_return - rf_per_trade) / m.std_return * math.sqrt(annualize_factor), 4
        )
    else:
        m.sharpe_ratio = 0.0

    # ── Sortino Ratio ──
    downside_returns = [p for p in pnl_series if p < 0]
    if downside_returns:
        downside_variance = sum(p**2 for p in downside_returns) / len(downside_returns)
        downside_std = math.sqrt(downside_variance)
        if downside_std > 0:
            m.sortino_ratio = round(
                (m.mean_return - rf_per_trade) / downside_std * math.sqrt(annualize_factor), 4
            )

    # ── Max Drawdown ──
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    current_dd_duration = 0
    max_dd_duration = 0

    for _i, pnl in enumerate(pnl_series):
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
            current_dd_duration = 0
        else:
            current_dd_duration += 1
            max_dd_duration = max(max_dd_duration, current_dd_duration)

        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    m.max_drawdown = round(max_dd, 4)
    m.max_drawdown_pct = round(max_dd / max(0.001, abs(peak)) * 100, 2) if peak > 0 else 0.0
    m.max_drawdown_duration = max_dd_duration

    # ── Calmar Ratio ──
    if max_dd > 0 and m.total_pnl > 0:
        annualized_return = m.total_pnl * (annualize_factor / max(1, n))
        m.calmar_ratio = round(annualized_return / max_dd, 4)

    # ── Streaks ──
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0

    for pnl in pnl_series:
        if pnl > 0:
            if current_streak > 0:
                current_streak += 1
            else:
                current_streak = 1
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if current_streak < 0:
                current_streak -= 1
            else:
                current_streak = -1
            max_loss_streak = max(max_loss_streak, abs(current_streak))

    m.max_win_streak = max_win_streak
    m.max_loss_streak = max_loss_streak
    m.current_streak = current_streak

    # ── Skewness & Kurtosis ──
    if n > 2 and m.std_return > 0:
        skew_sum = sum(((p - m.mean_return) / m.std_return) ** 3 for p in pnl_series)
        m.skewness = round(skew_sum * n / ((n - 1) * (n - 2)), 4) if n > 2 else 0.0

        kurt_sum = sum(((p - m.mean_return) / m.std_return) ** 4 for p in pnl_series)
        m.kurtosis = round(kurt_sum / max(1, n) - 3.0, 4)  # Excess kurtosis

    return m


def format_metrics_telegram(m: PerformanceMetrics) -> str:
    """Format metrics for Telegram display."""
    lines = [
        "📈 <b>Performance Metrics</b>",
        f"Total PnL: <b>${m.total_pnl:+.2f}</b> | " f"Trades: {m.total_trades}",
        f"WR: <b>{m.win_rate*100:.1f}%</b> | " f"W:{m.wins} L:{m.losses}",
        "",
        "<b>Risk-Adjusted:</b>",
        f"Sharpe: <b>{m.sharpe_ratio:.2f}</b> | " f"Sortino: <b>{m.sortino_ratio:.2f}</b>",
        f"Calmar: {m.calmar_ratio:.2f} | " f"Profit Factor: {m.profit_factor:.2f}",
        "",
        "<b>Drawdown:</b>",
        f"Max DD: <b>${m.max_drawdown:.2f}</b> ({m.max_drawdown_pct:.1f}%)",
        f"DD Duration: {m.max_drawdown_duration} trades",
        "",
        f"<b>Streaks:</b> Win {m.max_win_streak} | Loss {m.max_loss_streak}",
        f"<b>Expectancy:</b> ${m.expectancy:+.4f}/trade",
        f"<b>Avg W/L:</b> ${m.avg_win:+.4f} / ${m.avg_loss:+.4f}",
    ]
    return "\n".join(lines)
