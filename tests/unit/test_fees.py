"""Unit tests for core/fees.py (quadratic / legacy model).

These functions are pure and on the hot path of EV decisions — any regression
here silently corrupts every /backtest and every live entry gate.
"""
from __future__ import annotations

import pytest

from core.fees import (
    ev_after_fee,
    kelly_fraction,
    polymarket_fee_percent,
    polymarket_taker_fee,
)


class TestTakerFeeQuadratic:
    def test_zero_at_extremes(self):
        assert polymarket_taker_fee(0.0, 100) == 0.0
        assert polymarket_taker_fee(1.0, 100) == 0.0
        assert polymarket_taker_fee(0.0005, 100) == 0.0
        assert polymarket_taker_fee(0.9995, 100) == 0.0

    def test_fee_higher_than_extremes(self):
        # fee_pct(p) = 0.25 × p × (1-p)^2 — concave with peak at p=1/3
        # Just verify middle prices cost more than deep-tail prices
        fee_mid = polymarket_fee_percent(0.50)
        fee_low = polymarket_fee_percent(0.05)
        fee_high = polymarket_fee_percent(0.95)
        assert fee_mid > fee_low
        assert fee_mid > fee_high

    def test_peak_near_one_third(self):
        # Analytical peak of p(1-p)^2 is at p=1/3
        fee_third = polymarket_fee_percent(1.0 / 3.0)
        fee_20 = polymarket_fee_percent(0.20)
        fee_50 = polymarket_fee_percent(0.50)
        assert fee_third >= fee_20
        assert fee_third >= fee_50

    def test_scales_linearly_with_amount(self):
        f1 = polymarket_taker_fee(0.50, 100)
        f2 = polymarket_taker_fee(0.50, 200)
        assert f2 == pytest.approx(2 * f1, rel=1e-6)

    def test_known_value_at_0_50(self):
        # shares = 100/0.50 = 200; fee = 200 × 0.25 × (0.25)^2 = 3.125
        assert polymarket_taker_fee(0.50, 100) == pytest.approx(3.125, abs=0.001)


class TestEvAfterFee:
    def test_positive_ev_when_probability_exceeds_price(self):
        # Buy at 0.40, real prob 0.60 → clearly +EV even after fee
        ev = ev_after_fee(0.40, 0.60, amount=10)
        assert ev > 0

    def test_negative_ev_when_price_exceeds_probability(self):
        ev = ev_after_fee(0.60, 0.40, amount=10)
        assert ev < 0

    def test_zero_ev_at_fair_price_minus_fee(self):
        # At fair price (prob == price), expected return is just −fee, so EV is negative
        ev = ev_after_fee(0.50, 0.50, amount=10)
        assert ev < 0  # fee eats into EV


class TestKellyFraction:
    def test_zero_kelly_when_no_edge(self):
        assert kelly_fraction(0.50, 0.50) == 0.0
        assert kelly_fraction(0.60, 0.40) == 0.0  # price > prob → negative edge

    def test_positive_kelly_with_edge(self):
        f = kelly_fraction(0.40, 0.60)
        assert f > 0
        assert f <= 1.0

    def test_clipped_to_unit_interval(self):
        # Extreme edge case — should never exceed 1.0
        f = kelly_fraction(0.05, 0.95)
        assert 0.0 <= f <= 1.0

    def test_zero_at_invalid_inputs(self):
        assert kelly_fraction(0.0, 0.5) == 0.0
        assert kelly_fraction(1.0, 0.5) == 0.0
        assert kelly_fraction(0.5, 0.0) == 0.0
        assert kelly_fraction(0.5, 1.0) == 0.0
