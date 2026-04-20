"""Unit tests for core/fees_v2.py (March 2026 linear model)."""
from __future__ import annotations

import pytest

from core.fees_v2 import (
    CATEGORY_FEES,
    TAIL_HIGH,
    TAIL_LOW,
    ev_after_fee_v2,
    in_tail_zone,
    polymarket_fee_percent_v2,
    polymarket_maker_rebate,
    polymarket_taker_fee_v2,
)


class TestTakerFeeV2:
    def test_crypto_rate_used_by_default(self):
        fee = polymarket_taker_fee_v2(0.50, 100)
        # crypto rate 0.072, exp 1 → fee = 200 × 0.072 × 0.25 = 3.6
        assert fee == pytest.approx(3.6, abs=0.01)

    def test_override_rate_applies(self):
        fee_default = polymarket_taker_fee_v2(0.50, 100)
        fee_override = polymarket_taker_fee_v2(0.50, 100, override_rate=0.20)
        assert fee_override > fee_default

    def test_unknown_category_falls_back_to_default(self):
        # Should not KeyError — defaults to crypto
        fee = polymarket_taker_fee_v2(0.50, 100, category="bogus-category")
        assert fee > 0

    def test_zero_at_extremes(self):
        assert polymarket_taker_fee_v2(0.0, 100) == 0.0
        assert polymarket_taker_fee_v2(1.0, 100) == 0.0
        assert polymarket_taker_fee_v2(0.50, 0) == 0.0

    # Removed 2026-04-21 Epic 4 T4.1: legacy category + core/fees.py v1 module
    # deleted. fees_v2 is now canonical — validated against live Polymarket
    # Gamma feeSchedule (rate=0.072, exp=1, rebateRate=0.2 for crypto) rather
    # than the pre-Mart 2026 quadratic oracle.


class TestMakerRebate:
    def test_rebate_is_positive_fraction_of_taker(self):
        taker = polymarket_taker_fee_v2(0.50, 100, category="crypto")
        rebate = polymarket_maker_rebate(taker, category="crypto")
        crypto_pct = CATEGORY_FEES["crypto"]["maker_rebate_pct"]
        assert rebate == pytest.approx(taker * crypto_pct, abs=0.01)

    def test_zero_rebate_on_zero_fee(self):
        assert polymarket_maker_rebate(0.0) == 0.0


class TestTailZone:
    def test_low_tail(self):
        assert in_tail_zone(0.05) is True
        assert in_tail_zone(TAIL_LOW - 0.01) is True

    def test_high_tail(self):
        assert in_tail_zone(0.95) is True
        assert in_tail_zone(TAIL_HIGH + 0.01) is True

    def test_middle_not_in_tail(self):
        assert in_tail_zone(0.50) is False
        assert in_tail_zone(0.40) is False
        assert in_tail_zone(0.70) is False

    def test_zero_price_treated_as_tail(self):
        assert in_tail_zone(0.0) is True


class TestEvAfterFeeV2:
    def test_maker_beats_taker_on_same_bet(self):
        ev_taker = ev_after_fee_v2(0.40, 0.55, amount=10, is_maker=False)
        ev_maker = ev_after_fee_v2(0.40, 0.55, amount=10, is_maker=True)
        assert ev_maker > ev_taker  # rebate credit improves maker EV

    def test_positive_edge_still_positive(self):
        assert ev_after_fee_v2(0.40, 0.60, amount=10) > 0
