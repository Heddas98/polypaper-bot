"""
Phase 73 Tests: Code Quality & Skill Modules
=============================================
Covers:
  - FAZ 8.1: Skill modules (EMA, Volatility, Orderbook)
  - FAZ 8.2: Performance metrics (Sharpe, Sortino, MaxDD, etc.)
  - FAZ 8.3: Kelly Decay (regime-based fraction adjustment)
"""

import math
import os
import sys
import unittest

# Ensure project root on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ═══════════════════════════════════════════════════
# FAZ 8.1: Skill Module Tests
# ═══════════════════════════════════════════════════


class TestEMASkill(unittest.TestCase):
    """Tests for skills/ema_skill.py."""

    def test_ema_basic(self):
        from skills.ema_skill import ema

        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = ema(series, period=3)
        self.assertEqual(len(result), 5)
        # EMA starts at first value
        self.assertEqual(result[0], 1.0)
        # Should trend towards recent values
        self.assertGreater(result[-1], result[0])

    def test_ema_empty(self):
        from skills.ema_skill import ema

        self.assertEqual(ema([], 5), [])

    def test_ema_single(self):
        from skills.ema_skill import ema

        result = ema([42.0], 10)
        self.assertEqual(result, [42.0])

    def test_ema_crossover_up(self):
        from skills.ema_skill import ema_crossover

        # Gradual uptrend: fast EMA should cross above slow
        series = list(range(1, 30))  # 1 to 29
        series = [float(x) for x in series]
        result = ema_crossover(series, fast_period=3, slow_period=10)
        self.assertEqual(result.direction, "up")
        self.assertGreater(result.fast, result.slow)
        self.assertGreater(result.spread, 0)

    def test_ema_crossover_down(self):
        from skills.ema_skill import ema_crossover

        # Gradual downtrend
        series = list(range(30, 1, -1))  # 30 down to 2
        series = [float(x) for x in series]
        result = ema_crossover(series, fast_period=3, slow_period=10)
        self.assertEqual(result.direction, "down")
        self.assertLess(result.fast, result.slow)

    def test_ema_crossover_insufficient_data(self):
        from skills.ema_skill import ema_crossover

        result = ema_crossover([1.0, 2.0], fast_period=3, slow_period=10)
        self.assertEqual(result.direction, "flat")
        self.assertFalse(result.crossed_up)
        self.assertFalse(result.crossed_down)

    def test_ema_direction(self):
        from skills.ema_skill import ema_direction

        series = [float(x) for x in range(1, 20)]
        self.assertEqual(ema_direction(series, period=5), "up")

    def test_ema_direction_flat(self):
        from skills.ema_skill import ema_direction

        series = [5.0] * 20
        self.assertEqual(ema_direction(series, period=5), "flat")


class TestVolatilitySkill(unittest.TestCase):
    """Tests for skills/volatility_skill.py."""

    def test_rolling_volatility_basic(self):
        from skills.volatility_skill import rolling_volatility

        # Alternating series: should have non-zero vol
        series = [1.0, 1.1, 1.0, 1.1, 1.0, 1.1] * 5  # 30 points
        vol = rolling_volatility(series, window=10)
        self.assertGreater(vol, 0)

    def test_rolling_volatility_insufficient(self):
        from skills.volatility_skill import rolling_volatility

        vol = rolling_volatility([1.0, 2.0], window=20)
        self.assertEqual(vol, 0.0)

    def test_rolling_volatility_constant(self):
        from skills.volatility_skill import rolling_volatility

        series = [5.0] * 30
        vol = rolling_volatility(series, window=10)
        self.assertEqual(vol, 0.0)

    def test_volatility_regime_low(self):
        from skills.volatility_skill import volatility_regime

        # Very small moves = low vol
        series = [1.000, 1.001, 1.000, 1.001] * 10
        result = volatility_regime(series, window=10, low_threshold=0.01, high_threshold=0.05)
        self.assertEqual(result.regime, "low")

    def test_volatility_regime_high(self):
        from skills.volatility_skill import volatility_regime

        # Large swings = high vol
        series = [
            1.0,
            1.5,
            0.8,
            1.6,
            0.7,
            1.8,
            0.5,
            1.9,
            0.4,
            2.0,
            1.0,
            1.5,
            0.8,
            1.6,
            0.7,
            1.8,
            0.5,
            1.9,
            0.4,
            2.0,
            1.0,
            1.5,
        ]
        result = volatility_regime(series, window=10, low_threshold=0.005, high_threshold=0.020)
        self.assertEqual(result.regime, "high")

    def test_price_range(self):
        from skills.volatility_skill import price_range

        series = [10.0, 12.0, 8.0, 11.0, 9.0] * 5  # 25 points
        high, low, pct = price_range(series, window=10)
        self.assertEqual(high, 12.0)
        self.assertEqual(low, 8.0)
        self.assertGreater(pct, 0)

    def test_price_range_insufficient(self):
        from skills.volatility_skill import price_range

        self.assertEqual(price_range([1.0], window=20), (0.0, 0.0, 0.0))


class TestOrderbookSkill(unittest.TestCase):
    """Tests for skills/orderbook_skill.py."""

    def _make_ob(self, bid_p=0.55, ask_p=0.57, bid_s=100, ask_s=50):
        return {
            "bids": [{"price": str(bid_p), "size": str(bid_s)}],
            "asks": [{"price": str(ask_p), "size": str(ask_s)}],
        }

    def test_microprice_basic(self):
        from skills.orderbook_skill import compute_microprice

        ob = self._make_ob(0.55, 0.57, 100, 50)
        result = compute_microprice(ob, levels=1)
        self.assertAlmostEqual(result.mid_price, 0.56, places=2)
        # Microprice should lean towards ask (more bid depth)
        self.assertGreater(result.microprice, result.mid_price)

    def test_microprice_empty(self):
        from skills.orderbook_skill import compute_microprice

        result = compute_microprice({})
        self.assertEqual(result.microprice, 0.0)

    def test_imbalance_positive(self):
        from skills.orderbook_skill import compute_imbalance

        # More bids than asks → positive imbalance
        ob = self._make_ob(0.50, 0.52, 200, 50)
        imb = compute_imbalance(ob, levels=1)
        self.assertGreater(imb, 0)

    def test_imbalance_negative(self):
        from skills.orderbook_skill import compute_imbalance

        # More asks than bids → negative
        ob = self._make_ob(0.50, 0.52, 50, 200)
        imb = compute_imbalance(ob, levels=1)
        self.assertLess(imb, 0)

    def test_depth_at_level(self):
        from skills.orderbook_skill import depth_at_level

        ob = self._make_ob(0.55, 0.57, 100, 50)
        price, size = depth_at_level(ob, level=0, side="bid")
        self.assertAlmostEqual(price, 0.55, places=2)
        self.assertAlmostEqual(size, 100.0, places=0)

    def test_depth_at_level_oob(self):
        from skills.orderbook_skill import depth_at_level

        ob = self._make_ob()
        price, size = depth_at_level(ob, level=5, side="ask")
        self.assertEqual(price, 0.0)
        self.assertEqual(size, 0.0)

    def test_spread(self):
        from skills.orderbook_skill import compute_microprice

        ob = self._make_ob(0.55, 0.60)
        result = compute_microprice(ob, levels=1)
        self.assertAlmostEqual(result.spread, 0.05, places=2)


# ═══════════════════════════════════════════════════
# FAZ 8.2: Performance Metrics Tests
# ═══════════════════════════════════════════════════


class TestPerformanceMetrics(unittest.TestCase):
    """Tests for backtest/metrics.py."""

    def test_empty_series(self):
        from backtest.metrics import compute_metrics

        m = compute_metrics([])
        self.assertEqual(m.total_trades, 0)
        self.assertEqual(m.total_pnl, 0.0)

    def test_all_wins(self):
        from backtest.metrics import compute_metrics

        m = compute_metrics([1.0, 2.0, 1.5, 0.5])
        self.assertEqual(m.wins, 4)
        self.assertEqual(m.losses, 0)
        self.assertEqual(m.win_rate, 1.0)
        self.assertEqual(m.total_pnl, 5.0)
        self.assertEqual(m.max_drawdown, 0.0)

    def test_all_losses(self):
        from backtest.metrics import compute_metrics

        m = compute_metrics([-1.0, -2.0, -0.5])
        self.assertEqual(m.wins, 0)
        self.assertEqual(m.losses, 3)
        self.assertEqual(m.win_rate, 0.0)
        self.assertGreater(m.max_drawdown, 0)

    def test_mixed_pnl(self):
        from backtest.metrics import compute_metrics

        pnl = [1.0, -0.5, 2.0, -1.0, 0.5, -0.3, 1.5]
        m = compute_metrics(pnl)
        self.assertEqual(m.total_trades, 7)
        self.assertEqual(m.wins, 4)
        self.assertEqual(m.losses, 3)
        self.assertAlmostEqual(m.win_rate, 4 / 7, places=3)
        self.assertAlmostEqual(m.total_pnl, 3.2, places=1)
        self.assertGreater(m.profit_factor, 1.0)
        self.assertGreater(m.expectancy, 0)

    def test_sharpe_ratio(self):
        from backtest.metrics import compute_metrics

        # Consistent positive returns → high Sharpe
        pnl = [0.1] * 50
        m = compute_metrics(pnl)
        # With near-zero std, Sharpe should be very high or limited by zero std
        # All identical returns → std=0 → Sharpe=0
        self.assertEqual(m.sharpe_ratio, 0.0)

    def test_sharpe_ratio_varied(self):
        from backtest.metrics import compute_metrics

        pnl = [0.5, -0.1, 0.3, -0.05, 0.4, 0.1, -0.2, 0.6]
        m = compute_metrics(pnl)
        # Positive net PnL with some variance → positive Sharpe
        self.assertGreater(m.sharpe_ratio, 0)

    def test_sortino_ratio(self):
        from backtest.metrics import compute_metrics

        pnl = [0.5, -0.1, 0.3, -0.05, 0.4, 0.1, -0.2, 0.6]
        m = compute_metrics(pnl)
        # Sortino should be >= Sharpe (penalizes only downside)
        self.assertGreaterEqual(m.sortino_ratio, m.sharpe_ratio)

    def test_max_drawdown(self):
        from backtest.metrics import compute_metrics

        # Peak at +3 after first 3 trades, then drops to +0 = DD of 3
        pnl = [1.0, 1.0, 1.0, -1.0, -1.0, -1.0]
        m = compute_metrics(pnl)
        self.assertAlmostEqual(m.max_drawdown, 3.0, places=2)

    def test_streaks(self):
        from backtest.metrics import compute_metrics

        pnl = [1.0, 1.0, 1.0, -0.5, -0.5, 1.0]
        m = compute_metrics(pnl)
        self.assertEqual(m.max_win_streak, 3)
        self.assertEqual(m.max_loss_streak, 2)

    def test_profit_factor(self):
        from backtest.metrics import compute_metrics

        pnl = [2.0, -1.0, 2.0, -1.0]
        m = compute_metrics(pnl)
        # Gross profit = 4, gross loss = 2 → PF = 2.0
        self.assertAlmostEqual(m.profit_factor, 2.0, places=1)

    def test_expectancy(self):
        from backtest.metrics import compute_metrics

        pnl = [1.0, -0.5, 1.0, -0.5]
        m = compute_metrics(pnl)
        self.assertAlmostEqual(m.expectancy, 0.25, places=2)

    def test_format_telegram(self):
        from backtest.metrics import compute_metrics, format_metrics_telegram

        m = compute_metrics([1.0, -0.5, 2.0])
        text = format_metrics_telegram(m)
        self.assertIn("Performance Metrics", text)
        self.assertIn("Sharpe", text)
        self.assertIn("Drawdown", text)

    def test_skewness_kurtosis(self):
        from backtest.metrics import compute_metrics

        pnl = [0.5, -0.1, 0.3, -0.2, 0.4, -0.05, 0.6, -0.15]
        m = compute_metrics(pnl)
        # Just check they're computed (non-default)
        self.assertIsInstance(m.skewness, float)
        self.assertIsInstance(m.kurtosis, float)

    def test_calmar_ratio(self):
        from backtest.metrics import compute_metrics

        pnl = [1.0, 1.0, 1.0, -0.5, 1.0, 1.0]
        m = compute_metrics(pnl)
        # Total PnL positive + max DD > 0 → Calmar should be positive
        self.assertGreater(m.calmar_ratio, 0)


# ═══════════════════════════════════════════════════
# FAZ 8.3: Kelly Decay (Regime-Based) Tests
# ═══════════════════════════════════════════════════


class TestKellyDecay(unittest.TestCase):
    """Tests for Kelly Decay regime-based fraction adjustment."""

    def test_get_regime_fraction_trending(self):
        from core.kelly import get_regime_kelly_fraction

        f = get_regime_kelly_fraction("trending")
        self.assertAlmostEqual(f, 0.25, places=2)

    def test_get_regime_fraction_ranging(self):
        from core.kelly import get_regime_kelly_fraction

        f = get_regime_kelly_fraction("ranging")
        self.assertAlmostEqual(f, 0.167, places=2)

    def test_get_regime_fraction_volatile(self):
        from core.kelly import get_regime_kelly_fraction

        f = get_regime_kelly_fraction("volatile")
        self.assertAlmostEqual(f, 0.125, places=2)

    def test_get_regime_fraction_unknown(self):
        from core.kelly import get_regime_kelly_fraction

        # Unknown regime falls back to KELLY_FRACTION (0.25)
        f = get_regime_kelly_fraction("unknown_regime")
        self.assertAlmostEqual(f, 0.25, places=2)

    def test_decay_disabled(self):
        """When KELLY_DECAY_ENABLED=false, always returns base KELLY_FRACTION."""
        import core.kelly as km

        old = km.KELLY_DECAY_ENABLED
        try:
            km.KELLY_DECAY_ENABLED = False
            # All regimes should return base fraction
            self.assertAlmostEqual(km.get_regime_kelly_fraction("volatile"), 0.25, places=2)
            self.assertAlmostEqual(km.get_regime_kelly_fraction("trending"), 0.25, places=2)
        finally:
            km.KELLY_DECAY_ENABLED = old

    def test_calculate_kelly_size_basic(self):
        from core.kelly import calculate_kelly_size

        result = calculate_kelly_size(
            win_rate=0.60, avg_entry_price=0.55, bankroll=1000, trade_count=20
        )
        self.assertFalse(result["skip"])
        self.assertGreater(result["size"], 0)

    def test_kelly_volatile_smaller(self):
        """Volatile regime should produce smaller bet than trending."""
        from core.kelly import calculate_kelly_size, get_regime_kelly_fraction

        # Same WR and price, different fractions
        trending_f = get_regime_kelly_fraction("trending")
        volatile_f = get_regime_kelly_fraction("volatile")

        result_t = calculate_kelly_size(
            win_rate=0.60, avg_entry_price=0.55, bankroll=1000, trade_count=20, fraction=trending_f
        )
        result_v = calculate_kelly_size(
            win_rate=0.60, avg_entry_price=0.55, bankroll=1000, trade_count=20, fraction=volatile_f
        )
        # Volatile should be smaller
        self.assertGreater(result_t["size"], result_v["size"])

    def test_kelly_no_edge(self):
        from core.kelly import calculate_kelly_size

        result = calculate_kelly_size(
            win_rate=0.45, avg_entry_price=0.55, bankroll=1000, trade_count=20
        )
        self.assertTrue(result["skip"])

    def test_regime_fractions_ordering(self):
        """Trending > Ranging > Volatile fractions."""
        from core.kelly import get_regime_kelly_fraction

        t = get_regime_kelly_fraction("trending")
        r = get_regime_kelly_fraction("ranging")
        v = get_regime_kelly_fraction("volatile")
        self.assertGreater(t, r)
        self.assertGreater(r, v)


if __name__ == "__main__":
    unittest.main()
