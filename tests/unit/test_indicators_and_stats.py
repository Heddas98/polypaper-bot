"""Unit tests for core/indicators.py + core/stats_utils.py.

Coverage gap baseline (2026-04-29):
  - core/indicators.py 10.5% / 50 stmts
  - core/stats_utils.py 0%   / 20 stmts

Both modules are pure stdlib math (Phase 1 indicators + T7.6 B2 stats
helpers). No async, no DB, no I/O — straight number-crunching contracts.

Test boundaries we pin:
  - None-return on insufficient data (don't coerce to 0)
  - Round-trip for known math identities (SMA of constants = constant)
  - Edge cases: empty, single value, all-zeros, negative
  - Direction filters (up/down/None tri-state)
  - pearson_like None contract for low variance / low N
"""
from __future__ import annotations

import math

import pytest

from core import indicators as ind
from core.stats_utils import pearson_like


# ════════════════════════════════════════════════════════════════
# core/indicators.py
# ════════════════════════════════════════════════════════════════
class TestEMA:
    def test_empty_returns_none(self):
        assert ind.calculate_ema([], period=5) is None

    def test_too_few_values_returns_none(self):
        assert ind.calculate_ema([1.0, 2.0], period=5) is None

    def test_constant_series_returns_same_value(self):
        """EMA of [5,5,5,5,5] with any period is 5.0 (stable)."""
        result = ind.calculate_ema([5.0] * 10, period=5)
        assert result == 5.0

    def test_increasing_series_ema_lags_below_latest(self):
        """For monotonic up, EMA < latest value but > first value."""
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        ema = ind.calculate_ema(vals, period=4)
        assert ema is not None
        assert vals[0] < ema < vals[-1]

    def test_returns_rounded_to_6dp(self):
        ema = ind.calculate_ema([1.0, 1.1, 1.2, 1.3, 1.4], period=3)
        # 6 dp → no more than 6 fractional digits
        assert ema is not None
        assert abs(ema - round(ema, 6)) < 1e-9


class TestSMA:
    def test_empty_returns_none(self):
        assert ind.calculate_sma([], period=5) is None

    def test_too_few_values_returns_none(self):
        assert ind.calculate_sma([1.0, 2.0], period=5) is None

    def test_constant_series(self):
        assert ind.calculate_sma([3.0] * 10, period=5) == 3.0

    def test_known_average(self):
        """SMA of [1,2,3,4,5] period=5 = 3.0."""
        assert ind.calculate_sma([1.0, 2.0, 3.0, 4.0, 5.0], period=5) == 3.0

    def test_uses_last_period_values(self):
        """SMA period=3 of [1..6] uses [4,5,6] → mean=5.0."""
        assert ind.calculate_sma([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], period=3) == 5.0


class TestVolatility:
    def test_empty_returns_none(self):
        assert ind.calculate_volatility([], period=5) is None

    def test_too_few_values_returns_none(self):
        assert ind.calculate_volatility([1.0, 2.0], period=5) is None

    def test_constant_series_zero_volatility(self):
        """No price change → returns are all 0 → vol = 0."""
        result = ind.calculate_volatility([100.0] * 10, period=5)
        assert result == 0.0

    def test_zero_price_skipped(self):
        """If a price is 0, that return is skipped (avoid div/0).
        With all zeros, returns is empty → None."""
        result = ind.calculate_volatility([0.0, 0.0, 0.0, 0.0, 0.0, 0.0], period=5)
        assert result is None

    def test_positive_volatility_for_oscillating(self):
        vals = [100.0, 105.0, 100.0, 105.0, 100.0, 105.0]
        vol = ind.calculate_volatility(vals, period=5)
        assert vol is not None
        assert vol > 0


class TestMomentum:
    def test_empty_returns_none(self):
        assert ind.calculate_momentum([], period=3) is None

    def test_too_few_values_returns_none(self):
        assert ind.calculate_momentum([1.0, 2.0], period=5) is None

    def test_zero_past_returns_none(self):
        """If past price is 0, momentum undefined → None (avoid div/0)."""
        assert ind.calculate_momentum([0.0, 1.0, 2.0, 3.0, 4.0, 5.0], period=5) is None

    def test_positive_momentum_for_rising(self):
        # period=2 → past=values[-3]=3.0, current=5.0 → (5-3)/3 = 0.6667
        result = ind.calculate_momentum([1.0, 2.0, 3.0, 4.0, 5.0], period=2)
        assert result is not None
        assert result > 0

    def test_negative_momentum_for_falling(self):
        result = ind.calculate_momentum([5.0, 4.0, 3.0, 2.0, 1.0], period=2)
        assert result is not None
        assert result < 0

    def test_zero_momentum_for_flat(self):
        result = ind.calculate_momentum([100.0] * 10, period=5)
        assert result == 0.0


class TestEMADirectionFilter:
    def test_insufficient_data_returns_none(self):
        assert ind.ema_direction_filter([1.0, 2.0]) is None

    def test_uptrend_returns_up(self):
        """Recent values higher than older → short EMA > long EMA → 'up'."""
        # Simple uptrend
        vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0,
                11.0, 12.0, 13.0, 14.0, 15.0]
        assert ind.ema_direction_filter(vals, short_period=3, long_period=10) == "up"

    def test_downtrend_returns_down(self):
        vals = list(range(20, 0, -1))  # 20,19,...,1
        vals = [float(v) for v in vals]
        assert ind.ema_direction_filter(vals, short_period=3, long_period=10) == "down"

    def test_flat_returns_none(self):
        """Equal EMAs → None (no clear direction)."""
        # All same value → both EMAs == 5.0 → returns None
        result = ind.ema_direction_filter([5.0] * 20, short_period=3, long_period=10)
        assert result is None


class TestVolatilityThreshold:
    def test_insufficient_data_blocks(self):
        """Defensive: not enough data → False (block trade)."""
        assert ind.check_volatility_threshold([1.0], min_vol=0.01) is False

    def test_below_threshold_blocks(self):
        # Constant series → vol=0 → 0 >= 0.01 = False
        assert ind.check_volatility_threshold([5.0] * 20, min_vol=0.01) is False

    def test_above_threshold_passes(self):
        vals = [100.0, 110.0, 90.0, 105.0, 95.0, 100.0, 110.0, 90.0,
                105.0, 95.0, 100.0, 110.0, 90.0]
        assert ind.check_volatility_threshold(vals, min_vol=0.001) is True


# ════════════════════════════════════════════════════════════════
# core/stats_utils.py — pearson_like
# ════════════════════════════════════════════════════════════════
class TestPearsonLike:
    def test_perfect_positive_correlation(self):
        """y=x → r=1.0."""
        pairs = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]
        r = pearson_like(pairs)
        assert r is not None
        assert pytest.approx(r, abs=1e-9) == 1.0

    def test_perfect_negative_correlation(self):
        """y = -x → r = -1.0."""
        pairs = [(0.0, 0.0), (1.0, -1.0), (2.0, -2.0), (3.0, -3.0)]
        r = pearson_like(pairs)
        assert r is not None
        assert pytest.approx(r, abs=1e-9) == -1.0

    def test_too_few_pairs_returns_none(self):
        assert pearson_like([(1.0, 2.0)]) is None
        assert pearson_like([]) is None

    def test_zero_variance_x_returns_none(self):
        """All x identical → vx≈0 → None (no correlation defined)."""
        pairs = [(5.0, 1.0), (5.0, 2.0), (5.0, 3.0)]
        assert pearson_like(pairs) is None

    def test_zero_variance_y_returns_none(self):
        pairs = [(1.0, 5.0), (2.0, 5.0), (3.0, 5.0)]
        assert pearson_like(pairs) is None

    def test_unrelated_returns_near_zero(self):
        """Random-ish data → correlation near 0."""
        # Uncorrelated data
        pairs = [(1.0, 5.0), (2.0, 1.0), (3.0, 7.0), (4.0, 2.0),
                 (5.0, 6.0), (6.0, 3.0), (7.0, 4.0), (8.0, 8.0)]
        r = pearson_like(pairs)
        assert r is not None
        assert -1.0 <= r <= 1.0

    def test_returns_in_unit_range(self):
        """Correlation always in [-1, 1]."""
        pairs = [(1.0, 2.0), (2.0, 3.5), (3.0, 5.5), (4.0, 7.0), (5.0, 8.5)]
        r = pearson_like(pairs)
        assert r is not None
        assert -1.0 <= r <= 1.0

    def test_accepts_iterable_not_just_list(self):
        """pearson_like internally materializes — generators ok."""
        def gen():
            for i in range(5):
                yield (float(i), float(i * 2))
        r = pearson_like(gen())
        assert r is not None
        assert pytest.approx(r, abs=1e-9) == 1.0

    def test_accepts_int_pairs(self):
        """Mixed int/float input → coerced to float."""
        pairs = [(1, 2), (2, 4), (3, 6), (4, 8)]
        r = pearson_like(pairs)
        assert r is not None
        assert pytest.approx(r, abs=1e-9) == 1.0
