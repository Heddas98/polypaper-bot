"""Unit tests for core/engine_fills.py pure-logic surface (Epic 9 T9.6 P1 Tier 1).

Coverage gap baseline (2026-04-22): `engine_fills.py` 0% / 280 stmts.
Phase 82e Sprint 5 introduced fill-starvation hotfix (v6 TAKER stuck
auto-cancel at 120s). Zero tests before this file.

Scope (pure logic only, no DB/engine-graph):
  1. ``_snap_to_tick`` — Polymarket $0.01 tick snap + [0.01, 0.99] clamp
  2. ``_compute_ob_imbalance`` — top-3 weighted imbalance ∈ [-1, 1]
  3. ``_compute_queue_ahead_usd`` — FIFO maker-queue notional calc
  4. ``_taker_fee`` — routes to fees_v2 (Phase 65 Mart 2026 linear)
  5. ``_compute_slippage`` — signed signal→fill percent
  6. ``on_real_trade`` — maker queue counter advance (via harness)

Out-of-scope (→ T9.8 integration smoke):
  * ``_check_pending`` / ``_fill`` / ``cancel_pending`` — DB/engine state
  * ``_rest_latency_sleep`` — async sleep, not regression-critical
  * ``_becker_delta`` — calibration curve lookup (covered elsewhere)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.engine_fills import EngineFillsMixin


# ═══ Minimal harness class ═════════════════════════════════════════════
# The mixin references ``self.PRICE_TICK``, ``self.PRICE_TICK_TOL`` and
# ``self._pending`` that live on ``TradingEngine``. For pure-logic tests
# we need just a stub class with those attributes.

class FillsHarness(EngineFillsMixin):
    """Minimal stub exposing only the state needed by pure-logic methods."""

    PRICE_TICK = 0.01
    PRICE_TICK_TOL = 0.001
    PARTIAL_FILL_MIN_USD = 1.0

    def __init__(self):
        self._pending = []  # list of pending orders (stubs)


# ═══ _snap_to_tick — classmethod ═══════════════════════════════════════

class TestSnapToTick:
    """``_snap_to_tick`` rounds to $0.01 tick and clamps to [0.01, 0.99]."""

    def test_exact_tick_passthrough(self):
        assert FillsHarness._snap_to_tick(0.55) == 0.55

    def test_rounds_to_nearest(self):
        # 0.554 → 0.55 (rounds DOWN, not up). 0.556 → 0.56 (rounds UP).
        # The name captures the actual behaviour: round-to-nearest tick.
        assert FillsHarness._snap_to_tick(0.554) == 0.55
        assert FillsHarness._snap_to_tick(0.556) == 0.56

    def test_rounds_half_up(self):
        # 0.555 sits at mid-tick; Python banker's rounding makes this
        # ambiguous, so just verify output is one of the two adjacent ticks.
        v = FillsHarness._snap_to_tick(0.555)
        assert v in (0.55, 0.56)

    def test_clamps_below_min(self):
        """CLOB rejects prices < $0.01 — snap must clamp, not raise."""
        assert FillsHarness._snap_to_tick(0.003) == 0.01
        assert FillsHarness._snap_to_tick(0.0) == 0.01

    def test_clamps_above_max(self):
        """CLOB rejects prices > $0.99 — snap must clamp."""
        assert FillsHarness._snap_to_tick(0.995) == 0.99
        assert FillsHarness._snap_to_tick(1.50) == 0.99

    def test_none_returns_zero(self):
        """Defensive: None input → 0.0 (not TypeError)."""
        assert FillsHarness._snap_to_tick(None) == 0.0


# ═══ _compute_ob_imbalance — staticmethod ══════════════════════════════

class TestOrderBookImbalance:
    """Top-of-book imbalance ∈ [-1, 1]. +1 = bid-heavy, -1 = ask-heavy."""

    def test_empty_orderbook_zero(self):
        assert EngineFillsMixin._compute_ob_imbalance({}) == 0.0
        assert EngineFillsMixin._compute_ob_imbalance(None) == 0.0

    def test_balanced_book_near_zero(self):
        ob = {
            "bids": [{"price": 0.50, "size": 100}],
            "asks": [{"price": 0.52, "size": 100}],
        }
        imb = EngineFillsMixin._compute_ob_imbalance(ob)
        # 0.50*100 = 50 vs 0.52*100 = 52 → very slightly negative
        assert -0.05 < imb < 0.0

    def test_bid_heavy_positive(self):
        ob = {
            "bids": [{"price": 0.50, "size": 500}],
            "asks": [{"price": 0.52, "size": 50}],
        }
        imb = EngineFillsMixin._compute_ob_imbalance(ob)
        assert imb > 0.5  # strongly bid-heavy

    def test_ask_heavy_negative(self):
        ob = {
            "bids": [{"price": 0.50, "size": 50}],
            "asks": [{"price": 0.52, "size": 500}],
        }
        imb = EngineFillsMixin._compute_ob_imbalance(ob)
        assert imb < -0.5  # strongly ask-heavy

    def test_malformed_level_skipped_not_raised(self):
        """T1.4 Faz 1 narrow: None/non-numeric size must skip, not raise."""
        ob = {
            "bids": [
                {"price": 0.50, "size": None},  # malformed
                {"price": 0.49, "size": 100},   # valid
            ],
            "asks": [{"price": 0.52, "size": 100}],
        }
        # Must not raise
        imb = EngineFillsMixin._compute_ob_imbalance(ob)
        assert -1.0 <= imb <= 1.0


# ═══ _compute_queue_ahead_usd — staticmethod ═══════════════════════════

class TestQueueAheadUsd:
    """FIFO maker-queue notional calc — how much USD sits AT or BETTER than our limit."""

    def test_buy_side_accumulates_at_or_above_limit(self):
        ob = {
            "bids": [
                (0.60, 100),  # px >= 0.58 → count
                (0.59, 200),  # px >= 0.58 → count
                (0.58, 150),  # px >= 0.58 → count (exact)
                (0.57, 300),  # px < 0.58 → stop
            ]
        }
        ahead = EngineFillsMixin._compute_queue_ahead_usd(ob, 0.58, side="BUY")
        expected = 0.60*100 + 0.59*200 + 0.58*150
        assert ahead == pytest.approx(expected, abs=0.01)

    def test_sell_side_accumulates_at_or_below_limit(self):
        ob = {
            "asks": [
                (0.60, 100),
                (0.61, 200),
                (0.62, 150),
                (0.63, 300),  # > 0.62 → stop
            ]
        }
        ahead = EngineFillsMixin._compute_queue_ahead_usd(ob, 0.62, side="SELL")
        expected = 0.60*100 + 0.61*200 + 0.62*150
        assert ahead == pytest.approx(expected, abs=0.01)

    def test_no_matching_levels_zero(self):
        """Limit below top bid means no queue ahead."""
        ob = {"bids": [(0.50, 100), (0.49, 200)]}
        assert EngineFillsMixin._compute_queue_ahead_usd(ob, 0.60, side="BUY") == 0.0

    def test_empty_orderbook_zero(self):
        assert EngineFillsMixin._compute_queue_ahead_usd({}, 0.50) == 0.0
        assert EngineFillsMixin._compute_queue_ahead_usd(None, 0.50) == 0.0


# ═══ _taker_fee — instance method, wraps fees_v2 ════════════════════════

class TestTakerFee:
    """Phase 65: v1 removed, only v2 (Mart 2026 linear) active."""

    def test_taker_fee_positive_for_reasonable_price(self):
        h = FillsHarness()
        fee = h._taker_fee(price=0.60, amount_usd=10.0)
        # v2 linear model: fee scales with amount
        assert fee > 0
        assert fee < 10.0  # but less than notional

    def test_taker_fee_scales_with_amount(self):
        h = FillsHarness()
        small = h._taker_fee(0.50, 1.0)
        large = h._taker_fee(0.50, 100.0)
        assert large > small
        # Linear ratio should be approximately proportional
        assert large == pytest.approx(small * 100, rel=0.1)


# ═══ _compute_slippage — instance method ═══════════════════════════════

class TestComputeSlippage:
    """Signed signal→fill slippage percent (positive = adverse)."""

    def test_no_signal_price_zero(self):
        h = FillsHarness()
        o = SimpleNamespace(signal_price=0.0)
        assert h._compute_slippage(o, fill_price=0.60) == 0.0

    def test_none_signal_price_zero(self):
        h = FillsHarness()
        o = SimpleNamespace(signal_price=None)
        assert h._compute_slippage(o, fill_price=0.60) == 0.0

    def test_adverse_fill_positive_slippage(self):
        """Fill ABOVE signal = paid more than expected = adverse for BUYer."""
        h = FillsHarness()
        o = SimpleNamespace(signal_price=0.50)
        slip = h._compute_slippage(o, fill_price=0.55)
        # (0.55 - 0.50) / 0.50 * 100 = 10.0
        assert slip == pytest.approx(10.0, abs=0.01)

    def test_favorable_fill_negative_slippage(self):
        """Fill BELOW signal = filled cheaper than expected (rare, but possible)."""
        h = FillsHarness()
        o = SimpleNamespace(signal_price=0.50)
        slip = h._compute_slippage(o, fill_price=0.48)
        assert slip == pytest.approx(-4.0, abs=0.01)


# ═══ on_real_trade — maker-queue counter advance ═══════════════════════

class TestOnRealTrade:
    """Phase 39 (P1.2): MarketRecorder → engine callback.

    taker SELL at >= our limit → advance our maker BUY queue counter.
    taker BUY  at <= our limit → advance our maker SELL queue counter.
    """

    def _make_pending_maker(self, token_id: str, limit_price: float):
        """Stub of a maker Execution row (only attrs on_real_trade reads)."""
        return SimpleNamespace(
            is_maker=True,
            token_id=token_id,
            limit_price=limit_price,
            cum_traded_at_price_usd=0.0,
        )

    def test_taker_sell_at_or_above_limit_advances_maker_buy(self):
        h = FillsHarness()
        order = self._make_pending_maker("TOK1", limit_price=0.60)
        h._pending.append(order)
        # Taker SELL at 0.61 (> 0.60) → should help our maker BUY
        h.on_real_trade("TOK1", price=0.61, size=100.0, side="SELL", ts_ms=123)
        assert order.cum_traded_at_price_usd == pytest.approx(0.61 * 100.0)

    def test_taker_sell_below_limit_ignored(self):
        h = FillsHarness()
        order = self._make_pending_maker("TOK1", limit_price=0.60)
        h._pending.append(order)
        # Taker SELL at 0.50 (< 0.60) → does NOT help us
        h.on_real_trade("TOK1", price=0.50, size=100.0, side="SELL", ts_ms=123)
        assert order.cum_traded_at_price_usd == 0.0

    def test_mismatched_token_ignored(self):
        h = FillsHarness()
        order = self._make_pending_maker("TOK1", limit_price=0.60)
        h._pending.append(order)
        # Trade on a different token must not touch our counter
        h.on_real_trade("OTHER_TOKEN", price=0.61, size=100.0, side="SELL", ts_ms=123)
        assert order.cum_traded_at_price_usd == 0.0

    def test_non_maker_pending_ignored(self):
        """Taker orders don't get queue-counter advances."""
        h = FillsHarness()
        taker = SimpleNamespace(
            is_maker=False, token_id="TOK1", limit_price=0.60,
            cum_traded_at_price_usd=0.0,
        )
        h._pending.append(taker)
        h.on_real_trade("TOK1", price=0.61, size=100.0, side="SELL", ts_ms=123)
        assert taker.cum_traded_at_price_usd == 0.0

    def test_malformed_price_does_not_raise(self):
        """T1.4 Faz 1 narrow: non-numeric price/size → logged debug, no crash."""
        h = FillsHarness()
        order = self._make_pending_maker("TOK1", limit_price=0.60)
        h._pending.append(order)
        # Must not raise
        h.on_real_trade("TOK1", price="bad", size=100.0, side="SELL", ts_ms=123)
        assert order.cum_traded_at_price_usd == 0.0  # untouched
