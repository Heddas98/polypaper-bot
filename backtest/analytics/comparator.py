"""
PolyPaper Bot - Backtest v2 Strategy Comparator
Compare multiple strategy results side by side.

Generates:
  - Side-by-side metrics table
  - Ranking by different criteria (PnL, WR, Sharpe, etc.)
  - Overlap analysis (do strategies agree?)
"""

import logging
from typing import Optional

from backtest.simulation.portfolio import PortfolioStats, VirtualPortfolio

logger = logging.getLogger("polypaper.backtest.comparator")


class StrategyResult:
    """Container for a single strategy's backtest result."""

    def __init__(self, name: str, portfolio: VirtualPortfolio):
        self.name = name
        self.portfolio = portfolio
        self._stats: Optional[PortfolioStats] = None

    @property
    def stats(self) -> PortfolioStats:
        if self._stats is None:
            self._stats = self.portfolio.get_stats()
        return self._stats


class StrategyComparator:
    """Compare multiple backtest results."""

    def __init__(self):
        self.results: list[StrategyResult] = []

    def add_result(self, name: str, portfolio: VirtualPortfolio):
        """Add a strategy result for comparison."""
        self.results.append(StrategyResult(name, portfolio))

    def compare(self) -> str:
        """Generate comparison table as formatted text."""
        if not self.results:
            return "No results to compare."

        lines = [
            f"{'=' * 70}",
            f"  STRATEGY COMPARISON ({len(self.results)} strategies)",
            f"{'=' * 70}",
            "",
        ]

        # Header
        header = (
            f"{'Strategy':<20} {'Trades':>6} {'WR%':>6} {'PnL':>10} "
            f"{'Sharpe':>7} {'MaxDD%':>7} {'PF':>6}"
        )
        lines.append(header)
        lines.append("─" * 70)

        # Rows
        for r in sorted(self.results, key=lambda x: x.stats.total_pnl, reverse=True):
            s = r.stats
            lines.append(
                f"{r.name:<20} {s.total_trades:>6} {s.win_rate:>5.1f}% "
                f"${s.total_pnl:>+8.2f} {s.sharpe_ratio:>7.3f} "
                f"{s.max_drawdown_pct:>6.1f}% {s.profit_factor:>5.2f}"
            )

        lines.append("─" * 70)

        # Rankings
        lines.append("")
        lines.append("🏆 RANKINGS:")
        lines.append("")

        rankings = {
            "Best PnL": sorted(self.results, key=lambda x: x.stats.total_pnl, reverse=True),
            "Best WR": sorted(self.results, key=lambda x: x.stats.win_rate, reverse=True),
            "Best Sharpe": sorted(self.results, key=lambda x: x.stats.sharpe_ratio, reverse=True),
            "Lowest DD": sorted(self.results, key=lambda x: x.stats.max_drawdown_pct),
            "Most Trades": sorted(self.results, key=lambda x: x.stats.total_trades, reverse=True),
        }

        for metric, ranked in rankings.items():
            if ranked:
                top = ranked[0]
                val = ""
                s = top.stats
                if "PnL" in metric:
                    val = f"${s.total_pnl:+.2f}"
                elif "WR" in metric:
                    val = f"{s.win_rate:.1f}%"
                elif "Sharpe" in metric:
                    val = f"{s.sharpe_ratio:.3f}"
                elif "DD" in metric:
                    val = f"{s.max_drawdown_pct:.1f}%"
                elif "Trades" in metric:
                    val = str(s.total_trades)
                lines.append(f"  {metric:<15}: {top.name} ({val})")

        lines.append(f"\n{'=' * 70}")
        return "\n".join(lines)

    def compare_telegram(self) -> str:
        """Generate compact Telegram comparison."""
        if not self.results:
            return "No results."

        lines = [
            f"📊 <b>Strategy Comparison</b> ({len(self.results)})",
            "",
        ]

        for r in sorted(self.results, key=lambda x: x.stats.total_pnl, reverse=True):
            s = r.stats
            emoji = "🟢" if s.total_pnl > 0 else "🔴"
            lines.append(
                f"{emoji} <b>{r.name}</b>: {s.total_trades}t "
                f"{s.win_rate:.0f}%WR ${s.total_pnl:+.2f} "
                f"Sharpe={s.sharpe_ratio:.2f}"
            )

        # Best overall
        if self.results:
            best = max(self.results, key=lambda x: x.stats.total_pnl)
            lines.append(f"\n🏆 Winner: <b>{best.name}</b> " f"(${best.stats.total_pnl:+.2f})")

        return "\n".join(lines)

    def to_dict(self) -> list[dict]:
        """Export comparison as list of dicts."""
        return [
            {
                "strategy": r.name,
                "trades": r.stats.total_trades,
                "win_rate": r.stats.win_rate,
                "pnl": r.stats.total_pnl,
                "sharpe": r.stats.sharpe_ratio,
                "sortino": r.stats.sortino_ratio,
                "max_dd_pct": r.stats.max_drawdown_pct,
                "profit_factor": r.stats.profit_factor,
                "fees": r.stats.total_fees,
            }
            for r in self.results
        ]
