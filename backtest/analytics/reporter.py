"""
PolyPaper Bot - Backtest v2 Reporter
Comprehensive result analysis and text-based reporting.

Generates:
  - Summary statistics (WR, PnL, Sharpe, etc.)
  - Hour-based win rate breakdown (UTC 0-23)
  - Coin/timeframe breakdown
  - Price zone analysis
  - Streak analysis
  - Trade distribution
"""

import logging
from collections import defaultdict
from dataclasses import dataclass

from backtest.simulation.portfolio import PortfolioStats, VirtualPortfolio

logger = logging.getLogger("polypaper.backtest.reporter")


@dataclass
class HourlyStats:
    """Win rate stats for a specific hour."""

    hour: int = 0
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0


@dataclass
class ZoneStats:
    """Stats for a price zone."""

    zone: str = ""
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0


class BacktestReporter:
    """Analyze and format backtest results."""

    def __init__(self, portfolio: VirtualPortfolio, strategy_name: str = "", config: dict = None):
        self.portfolio = portfolio
        self.strategy_name = strategy_name
        self.config = config or {}

    def generate_summary(self) -> str:
        """Generate a complete text summary."""
        stats = self.portfolio.get_stats()
        sections = [
            self._header(),
            self._overview(stats),
            self._direction_breakdown(),
            self._hourly_breakdown(),
            self._zone_breakdown(),
            self._coin_breakdown(),
            self._streak_analysis(),
            self._confidence_breakdown(),
            self._footer(stats),
        ]
        return "\n".join(s for s in sections if s)

    def generate_telegram_summary(self) -> str:
        """Generate a compact Telegram-friendly summary."""
        stats = self.portfolio.get_stats()
        trades = self.portfolio.trades

        if stats.total_trades == 0:
            return "⚠️ No trades generated."

        lines = [
            f"📊 <b>Backtest: {self.strategy_name}</b>",
            "",
            f"💰 PnL: ${stats.total_pnl:+.2f}",
            f"📈 Trades: {stats.total_trades} | WR: {stats.win_rate:.1f}%",
            f"🏆 Best: ${stats.best_trade:+.2f} | Worst: ${stats.worst_trade:+.2f}",
            f"📉 Max DD: {stats.max_drawdown_pct:.1f}%",
            f"📐 Sharpe: {stats.sharpe_ratio:.2f} | Sortino: {stats.sortino_ratio:.2f}",
            f"💵 Fees: ${stats.total_fees:.2f}",
        ]

        # Add direction breakdown
        up_trades = [t for t in trades if t.direction == "up"]
        down_trades = [t for t in trades if t.direction == "down"]
        if up_trades:
            up_wr = sum(1 for t in up_trades if t.won) / len(up_trades) * 100
            lines.append(f"⬆️ UP: {len(up_trades)}t {up_wr:.0f}%WR")
        if down_trades:
            dn_wr = sum(1 for t in down_trades if t.won) / len(down_trades) * 100
            lines.append(f"⬇️ DOWN: {len(down_trades)}t {dn_wr:.0f}%WR")

        # Top 3 hours
        hourly = self._get_hourly_stats()
        top_hours = sorted(hourly.values(), key=lambda h: h.pnl, reverse=True)[:3]
        if top_hours and top_hours[0].trades > 0:
            hour_str = " | ".join(f"{h.hour}h:{h.win_rate:.0f}%({h.trades}t)" for h in top_hours)
            lines.append(f"🕐 Top hours: {hour_str}")

        return "\n".join(lines)

    # ── Section generators ──────────────────────────

    def _header(self) -> str:
        return f"{'=' * 50}\n" f"  BACKTEST REPORT: {self.strategy_name}\n" f"{'=' * 50}"

    def _overview(self, stats: PortfolioStats) -> str:
        if stats.total_trades == 0:
            return "\n⚠️  No trades generated.\n"

        return (
            f"\n📊 OVERVIEW\n"
            f"{'─' * 40}\n"
            f"  Trades:       {stats.total_trades}\n"
            f"  Win Rate:     {stats.win_rate:.1f}% "
            f"({stats.wins}W / {stats.losses}L)\n"
            f"  Total PnL:    ${stats.total_pnl:+.2f}\n"
            f"  Avg PnL:      ${stats.avg_pnl:+.4f}\n"
            f"  Best Trade:   ${stats.best_trade:+.2f}\n"
            f"  Worst Trade:  ${stats.worst_trade:+.2f}\n"
            f"  Total Fees:   ${stats.total_fees:.2f}\n"
            f"  Total Slip:   ${stats.total_slippage:.2f}\n"
            f"\n📐 RISK METRICS\n"
            f"{'─' * 40}\n"
            f"  Sharpe Ratio:   {stats.sharpe_ratio:.3f}\n"
            f"  Sortino Ratio:  {stats.sortino_ratio:.3f}\n"
            f"  Profit Factor:  {stats.profit_factor:.3f}\n"
            f"  Max Drawdown:   ${stats.max_drawdown:.2f} "
            f"({stats.max_drawdown_pct:.1f}%)\n"
            f"  Final Balance:  ${self.portfolio.balance:.2f}\n"
        )

    def _direction_breakdown(self) -> str:
        trades = self.portfolio.trades
        if not trades:
            return ""

        up_trades = [t for t in trades if t.direction == "up"]
        down_trades = [t for t in trades if t.direction == "down"]

        lines = ["\n⬆️⬇️ DIRECTION BREAKDOWN", f"{'─' * 40}"]

        for label, subset in [("UP", up_trades), ("DOWN", down_trades)]:
            if subset:
                wins = sum(1 for t in subset if t.won)
                wr = wins / len(subset) * 100
                pnl = sum(t.pnl for t in subset)
                lines.append(f"  {label:5s}: {len(subset):4d}t  " f"{wr:5.1f}%WR  ${pnl:+8.2f}")
        return "\n".join(lines)

    def _hourly_breakdown(self) -> str:
        hourly = self._get_hourly_stats()
        if not hourly:
            return ""

        lines = ["\n🕐 HOURLY BREAKDOWN (UTC)", f"{'─' * 40}"]
        for h in range(24):
            stats = hourly.get(h)
            if stats and stats.trades > 0:
                bar = "█" * min(int(stats.win_rate / 5), 20)
                lines.append(
                    f"  {h:02d}h: {stats.trades:4d}t  "
                    f"{stats.win_rate:5.1f}%  ${stats.pnl:+7.2f}  {bar}"
                )
        return "\n".join(lines)

    def _zone_breakdown(self) -> str:
        trades = self.portfolio.trades
        if not trades:
            return ""

        zones = defaultdict(lambda: ZoneStats())
        for t in trades:
            p = t.entry_price
            if p < 0.20:
                z = "0-20c"
            elif p < 0.35:
                z = "20-35c"
            elif p < 0.50:
                z = "35-50c"
            elif p < 0.65:
                z = "50-65c"
            elif p < 0.80:
                z = "65-80c"
            else:
                z = "80-100c"

            s = zones[z]
            s.zone = z
            s.trades += 1
            s.wins += int(t.won)
            s.pnl += t.pnl

        lines = ["\n💰 PRICE ZONE BREAKDOWN", f"{'─' * 40}"]
        for z in ["0-20c", "20-35c", "35-50c", "50-65c", "65-80c", "80-100c"]:
            s = zones.get(z)
            if s and s.trades > 0:
                lines.append(f"  {z:8s}: {s.trades:4d}t  " f"{s.win_rate:5.1f}%  ${s.pnl:+7.2f}")
        return "\n".join(lines)

    def _coin_breakdown(self) -> str:
        trades = self.portfolio.trades
        if not trades:
            return ""

        coins = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in trades:
            c = coins[t.coin]
            c["trades"] += 1
            c["wins"] += int(t.won)
            c["pnl"] += t.pnl

        if len(coins) <= 1:
            return ""

        lines = ["\n🪙 COIN BREAKDOWN", f"{'─' * 40}"]
        for coin, c in sorted(coins.items()):
            wr = c["wins"] / c["trades"] * 100 if c["trades"] > 0 else 0
            lines.append(f"  {coin:4s}: {c['trades']:4d}t  " f"{wr:5.1f}%  ${c['pnl']:+7.2f}")
        return "\n".join(lines)

    def _streak_analysis(self) -> str:
        trades = self.portfolio.trades
        if len(trades) < 5:
            return ""

        # Calculate win/loss streaks
        max_win_streak = 0
        max_loss_streak = 0
        current_streak = 0
        current_dir = None

        for t in trades:
            if t.won == current_dir:
                current_streak += 1
            else:
                current_dir = t.won
                current_streak = 1

            if current_dir:
                max_win_streak = max(max_win_streak, current_streak)
            else:
                max_loss_streak = max(max_loss_streak, current_streak)

        return (
            f"\n📊 STREAK ANALYSIS\n"
            f"{'─' * 40}\n"
            f"  Max Win Streak:  {max_win_streak}\n"
            f"  Max Loss Streak: {max_loss_streak}\n"
        )

    def _confidence_breakdown(self) -> str:
        trades = self.portfolio.trades
        if not trades:
            return ""

        buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in trades:
            c = t.confidence
            if c < 0.55:
                b = "< 55%"
            elif c < 0.65:
                b = "55-65%"
            elif c < 0.75:
                b = "65-75%"
            elif c < 0.85:
                b = "75-85%"
            else:
                b = "85%+"
            d = buckets[b]
            d["trades"] += 1
            d["wins"] += int(t.won)
            d["pnl"] += t.pnl

        lines = ["\n🎯 CONFIDENCE BREAKDOWN", f"{'─' * 40}"]
        for b in ["< 55%", "55-65%", "65-75%", "75-85%", "85%+"]:
            d = buckets.get(b)
            if d and d["trades"] > 0:
                wr = d["wins"] / d["trades"] * 100
                lines.append(f"  {b:7s}: {d['trades']:4d}t  " f"{wr:5.1f}%  ${d['pnl']:+7.2f}")
        return "\n".join(lines)

    def _footer(self, stats: PortfolioStats) -> str:
        ev = stats.avg_pnl if stats.total_trades > 0 else 0
        ev_label = "✅ POSITIVE" if ev > 0 else "❌ NEGATIVE"
        return (
            f"\n{'=' * 50}\n" f"  Expected Value: ${ev:+.4f}/trade ({ev_label})\n" f"{'=' * 50}\n"
        )

    # ── Helpers ──────────────────────────────────────

    def _get_hourly_stats(self) -> dict[int, HourlyStats]:
        hourly = {}
        for t in self.portfolio.trades:
            h = t.hour_utc
            if h not in hourly:
                hourly[h] = HourlyStats(hour=h)
            s = hourly[h]
            s.trades += 1
            s.wins += int(t.won)
            s.pnl += t.pnl
        return hourly

    def to_dict(self) -> dict:
        """Export results as a serializable dict."""
        stats = self.portfolio.get_stats()
        return {
            "strategy": self.strategy_name,
            "total_trades": stats.total_trades,
            "win_rate": stats.win_rate,
            "total_pnl": stats.total_pnl,
            "sharpe": stats.sharpe_ratio,
            "sortino": stats.sortino_ratio,
            "max_drawdown_pct": stats.max_drawdown_pct,
            "profit_factor": stats.profit_factor,
            "avg_pnl": stats.avg_pnl,
            "total_fees": stats.total_fees,
            "balance": self.portfolio.balance,
            "equity_curve": self.portfolio.equity_curve,
            "trades_count": {
                "wins": stats.wins,
                "losses": stats.losses,
            },
        }
