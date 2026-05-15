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
        # 2026-05-11 docs cross-check: crypto rate = 0.07, exp = 1.
        # fee = (100/0.5) × 0.07 × (0.5 × 0.5) = 200 × 0.07 × 0.25 = 3.5
        # Polymarket docs peak fee table: 100 sh × $0.50 → $1.75
        # → 100 sh × 2 (for $100 = 200 sh) × $0.50 × proportion = $3.50.
        assert fee == pytest.approx(3.5, abs=0.01)

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
    # deleted. fees_v2 is now canonical — validated against Polymarket docs
    # (rate=0.07, exp=1, rebateRate=0.2 for crypto) verified 2026-05-11 via
    # docs.polymarket.com/trading/fees (was 0.072 pre-fix, off by +2.86%).

    def test_p0_10_precision_5_decimal_places(self):
        """P0-10 (2026-05-13 audit): docs say smallest fee 0.00001 USDC.

        Pre-fix the result was rounded to 4 decimals, which would truncate
        any 5th-decimal value. After the fix any returned fee with a
        non-zero 5th digit must be preserved.
        """
        # Geopolitics is fee-free → 0.0 (sanity).
        assert polymarket_taker_fee_v2(0.50, 0.0001, category="geopolitics") == 0.0
        # Very small trade in tail zone — 5th-decimal precision matters.
        # shares = 0.01 / 0.05 = 0.2; fee = 0.2 × 0.07 × 0.05 × 0.95 = 0.000665
        # 4-decimal round would give 0.0007 (drift!), 5-decimal gives 0.00067.
        small = polymarket_taker_fee_v2(0.05, 0.01, category="crypto")
        assert small == pytest.approx(0.00067, abs=1e-6)
        # Confirm precision really is 5: the value must have at most 5
        # digits after the decimal point.
        decimals = len(f"{small:.10f}".rstrip("0").split(".")[1])
        assert decimals <= 5, f"precision drift: {small} has >5 decimals"


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


class TestPolymarketDocsParity:
    """FAZ 0.1 — Audit dosyasi: docs/audits/fee_reality_check_2026_04.md.

    Polymarket dokumantasyonundaki resmi fee tablolarina karsi parity check.
    Veri kaynagi: https://docs.polymarket.com/trading/fees (100 shares table).

    Bu testler kazara regression yakalamak icin: birisi exp veya rebate
    degerini degistirirse, bu testler hemen FAIL eder ve docs/audits/
    altinda yeni reality-check raporlanmasi gerekir.
    """

    # (category, price, trade_value_usd, expected_fee_usdc) - 100 shares
    # 2026-05-13 audit: crypto values refreshed to 0.07 rate era.
    # Docs https://docs.polymarket.com/trading/fees Fee Tables (100 shares):
    #   p=0.10 → $0.63 · p=0.50 → $1.75 (peak) · p=0.90 → $0.63
    # Pre-2026-05-11 the rate was 0.072 → peak $1.80, p=0.1 = $0.65.
    TAKER_FEE_TABLE = [
        ("crypto", 0.01, 1.00, 0.07),
        ("crypto", 0.10, 10.00, 0.63),
        ("crypto", 0.50, 50.00, 1.75),
        ("crypto", 0.90, 90.00, 0.63),
        ("crypto", 0.99, 99.00, 0.07),
        ("sports", 0.50, 50.00, 0.75),
        ("finance", 0.50, 50.00, 1.00),
        ("politics", 0.50, 50.00, 1.00),
        ("mentions", 0.50, 50.00, 1.00),
        ("tech", 0.50, 50.00, 1.00),
        ("economics", 0.50, 50.00, 1.25),
        ("weather", 0.50, 50.00, 1.25),
        ("culture", 0.50, 50.00, 1.25),
        ("other", 0.50, 50.00, 1.25),
        ("geopolitics", 0.50, 50.00, 0.00),
    ]

    DOCS_REBATE_PCT = {
        "crypto": 0.20,
        "sports": 0.25,
        "politics": 0.25,
        "finance": 0.25,
        "economics": 0.25,
        "culture": 0.25,
        "weather": 0.25,
        "other": 0.25,
        "mentions": 0.25,
        "tech": 0.25,
        "geopolitics": 0.00,
    }

    @pytest.mark.parametrize("category,price,amount_usd,expected", TAKER_FEE_TABLE)
    def test_taker_fee_matches_docs_table(
        self, category: str, price: float, amount_usd: float, expected: float
    ) -> None:
        fee = polymarket_taker_fee_v2(price, amount_usd, category=category)
        # Docs round to 2 decimal places in the table; tolerate 0.01 USDC drift.
        assert fee == pytest.approx(expected, abs=0.01), (
            f"{category} @ p={price} notional=${amount_usd}: " f"bot={fee} docs={expected}"
        )

    @pytest.mark.parametrize("category,expected_pct", list(DOCS_REBATE_PCT.items()))
    def test_maker_rebate_pct_matches_docs(self, category: str, expected_pct: float) -> None:
        actual = CATEGORY_FEES[category]["maker_rebate_pct"]
        assert actual == pytest.approx(expected_pct, abs=1e-6), (
            f"{category}: maker_rebate_pct bot={actual} docs={expected_pct}. "
            f"docs URL: https://docs.polymarket.com/market-makers/maker-rebates"
        )

    def test_all_documented_categories_use_exp_one(self) -> None:
        """Polymarket docs (Apr 2026) tum kategoriler icin exp=1 kullanir."""
        for cat, params in CATEGORY_FEES.items():
            assert params["taker_exp"] == 1, (
                f"{cat}: taker_exp={params['taker_exp']} != 1. "
                "Docs uniform exp=1; per-market override mekanizmasi "
                "polymarket_taker_fee_v2(override_exp=...) ile yapilmali."
            )
