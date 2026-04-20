"""
PolyPaper Bot - Backtest v2 Chart Generator
Generates chart images using Pillow (no matplotlib dependency).

Charts:
  - Equity curve with drawdown overlay
  - Hourly win rate heatmap
  - PnL distribution histogram
  - Zone breakdown bar chart

Uses pure Pillow for Replit/low-dependency environments.
If matplotlib is available, uses it for better charts.
"""
import io
import logging
from typing import Optional
from collections import defaultdict

from backtest.simulation.portfolio import VirtualPortfolio, Trade

logger = logging.getLogger("polypaper.backtest.charts")

# Try matplotlib first, fallback to Pillow
try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

try:
    from PIL import Image, ImageDraw
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class ChartGenerator:
    """Generate chart images from backtest results."""

    def __init__(self, portfolio: VirtualPortfolio,
                 strategy_name: str = "",
                 width: int = 800, height: int = 400):
        self.portfolio = portfolio
        self.strategy_name = strategy_name
        self.width = width
        self.height = height

    def equity_curve(self) -> Optional[bytes]:
        """Generate equity curve chart as PNG bytes."""
        eq = self.portfolio.equity_curve
        if len(eq) < 2:
            return None

        if HAS_MPL:
            return self._mpl_equity_curve(eq)
        elif HAS_PIL:
            return self._pil_equity_curve(eq)
        return None

    def hourly_heatmap(self) -> Optional[bytes]:
        """Generate hourly win rate heatmap as PNG bytes."""
        trades = self.portfolio.trades
        if not trades:
            return None

        if HAS_MPL:
            return self._mpl_hourly_heatmap(trades)
        return None

    def pnl_distribution(self) -> Optional[bytes]:
        """Generate PnL distribution histogram as PNG bytes."""
        trades = self.portfolio.trades
        if len(trades) < 5:
            return None

        if HAS_MPL:
            return self._mpl_pnl_distribution(trades)
        return None

    # ── Matplotlib implementations ──────────────────

    def _mpl_equity_curve(self, eq: list) -> bytes:
        fig, ax = plt.subplots(figsize=(10, 5))

        ax.plot(eq, color="#2196F3", linewidth=1.5, label="Equity")
        ax.axhline(y=eq[0], color="gray", linestyle="--", alpha=0.5,
                    label=f"Start ${eq[0]:,.0f}")

        # Drawdown shading
        peak = eq[0]
        dd = []
        for val in eq:
            peak = max(peak, val)
            dd.append(val - peak)

        ax2 = ax.twinx()
        ax2.fill_between(range(len(dd)), dd, 0,
                         alpha=0.15, color="red", label="Drawdown")
        ax2.set_ylabel("Drawdown ($)")

        ax.set_title(f"Equity Curve: {self.strategy_name}", fontsize=14)
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Balance ($)")
        ax.legend(loc="upper left")
        ax.grid(True, alpha=0.3)

        # Format y-axis as currency
        ax.yaxis.set_major_formatter(
            mticker.FormatStrFormatter('$%,.0f'))

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _mpl_hourly_heatmap(self, trades: list) -> bytes:
        hourly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
        for t in trades:
            h = hourly[t.hour_utc]
            h["trades"] += 1
            h["wins"] += int(t.won)
            h["pnl"] += t.pnl

        hours = list(range(24))
        wr = [hourly[h]["wins"] / hourly[h]["trades"] * 100
              if hourly[h]["trades"] > 0 else 50 for h in hours]
        counts = [hourly[h]["trades"] for h in hours]

        fig, ax = plt.subplots(figsize=(12, 4))

        colors = []
        for w in wr:
            if w >= 60:
                colors.append("#4CAF50")
            elif w >= 55:
                colors.append("#8BC34A")
            elif w >= 50:
                colors.append("#FFC107")
            elif w >= 45:
                colors.append("#FF9800")
            else:
                colors.append("#F44336")

        bars = ax.bar(hours, wr, color=colors, alpha=0.8, edgecolor="white")

        # Add trade count labels
        for i, (bar, cnt) in enumerate(zip(bars, counts)):
            if cnt > 0:
                ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                        str(cnt), ha="center", va="bottom", fontsize=8)

        ax.axhline(y=50, color="gray", linestyle="--", alpha=0.5)
        ax.set_title(f"Win Rate by Hour (UTC): {self.strategy_name}",
                     fontsize=14)
        ax.set_xlabel("Hour (UTC)")
        ax.set_ylabel("Win Rate (%)")
        ax.set_xticks(hours)
        ax.set_ylim(0, 100)
        ax.grid(True, axis="y", alpha=0.3)

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _mpl_pnl_distribution(self, trades: list) -> bytes:
        pnls = [t.pnl for t in trades]

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(pnls, bins=30, color="#2196F3", alpha=0.7, edgecolor="white")
        ax.axvline(x=0, color="red", linestyle="--", alpha=0.5)

        avg = sum(pnls) / len(pnls) if pnls else 0
        ax.axvline(x=avg, color="green", linestyle="-", alpha=0.7,
                   label=f"Mean: ${avg:+.4f}")

        ax.set_title(f"PnL Distribution: {self.strategy_name}", fontsize=14)
        ax.set_xlabel("PnL ($)")
        ax.set_ylabel("Frequency")
        ax.legend()
        ax.grid(True, alpha=0.3)

        buf = io.BytesIO()
        fig.tight_layout()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    # ── Pillow fallback ─────────────────────────────

    def _pil_equity_curve(self, eq: list) -> bytes:
        """Simple equity curve using Pillow (no matplotlib)."""
        w, h = self.width, self.height
        img = Image.new("RGB", (w, h), "#1a1a2e")
        draw = ImageDraw.Draw(img)

        # Margins
        mx, my = 60, 30
        pw, ph = w - 2 * mx, h - 2 * my

        # Scale
        min_v = min(eq)
        max_v = max(eq)
        v_range = max_v - min_v if max_v != min_v else 1

        # Draw gridlines
        for i in range(5):
            y = my + int(ph * i / 4)
            val = max_v - (v_range * i / 4)
            draw.line([(mx, y), (mx + pw, y)], fill="#333355", width=1)
            draw.text((5, y - 6), f"${val:,.0f}", fill="#888888")

        # Draw equity line
        points = []
        for i, val in enumerate(eq):
            x = mx + int(pw * i / (len(eq) - 1))
            y = my + int(ph * (1 - (val - min_v) / v_range))
            points.append((x, y))

        if len(points) > 1:
            draw.line(points, fill="#2196F3", width=2)

        # Title
        draw.text((mx, 5), f"Equity: {self.strategy_name}",
                  fill="#FFFFFF")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.read()
