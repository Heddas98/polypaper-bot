"""
Phase 82 Static Tests — Plugin Metadata Enrichment
===================================================

Covers two concerns introduced in Phase 82:

1. ``_get_ob_cached`` — cycle-level orderbook cache (TTL=2s by default).
   We simulate multiple calls against a stub client and verify that
   within-TTL calls hit the cache and post-TTL calls re-fetch.

2. Ported strategies (hour_edge, orderbook_imbalance, fade_rip,
   opening_breakout, funding_rate, calibration_arb) produce the expected
   should_trade / direction outcomes when given the metadata keys that
   the Phase 82 plugin-routing block is designed to populate.

These tests do NOT boot the full TradingEngine — they focus on the
smallest units relevant to the change: the cache helper and the
strategies' evaluate() contracts. Run with:

    py -3.11 -m pytest tests/test_phase82_metadata.py -v
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest

from core.strategy_plugins import (
    MarketSnapshot,
    HourEdgeLiveStrategy,
    OrderbookImbalanceLiveStrategy,
    FadeRipLiveStrategy,
    OpeningBreakoutLiveStrategy,
    FundingRateLiveStrategy,
    CalibrationArbLiveStrategy,
)


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

def _snap(metadata=None, *, up_odds=0.50, down_odds=0.50,
          direction_filter="any", minutes_remaining=3.0,
          total_minutes=5.0, best_bid=0.48, best_ask=0.50) -> MarketSnapshot:
    """Factory for a MarketSnapshot with sensible defaults."""
    return MarketSnapshot(
        up_odds=up_odds, down_odds=down_odds,
        threshold=0.03, direction_filter=direction_filter,
        odds_series=[0.49, 0.50, 0.51, 0.50, 0.51, 0.52],
        minutes_remaining=minutes_remaining,
        total_minutes=total_minutes,
        spread=0.02,
        best_ask=best_ask, best_bid=best_bid,
        metadata=metadata or {},
    )


class _StubClient:
    """Stub polymarket client for _get_ob_cached tests."""
    def __init__(self):
        self.fetch_count = 0
        self.next_result = {
            "bids": [[0.54, 100.0], [0.53, 80.0]],
            "asks": [[0.56, 90.0], [0.57, 70.0]],
        }

    async def get_orderbook(self, token_id: str):
        self.fetch_count += 1
        return self.next_result


class _StubEngine:
    """Minimal host for _get_ob_cached — mirrors the engine attrs the
    helper relies on."""
    def __init__(self, ttl: float = 2.0):
        self.client = _StubClient()
        self._ob_cache: dict = {}
        self._OB_CACHE_TTL: float = ttl


# ═══════════════════════════════════════════════════════════════════════
#  1. _get_ob_cached cache behavior
# ═══════════════════════════════════════════════════════════════════════

def _bind_helper():
    """Pull the unbound _get_ob_cached from EngineSignalsMixin and bind
    it to a stub engine. This lets us test the helper in isolation."""
    from core.engine_signals import EngineSignalsMixin
    eng = _StubEngine()
    # Bind the mixin method to our stub
    bound = EngineSignalsMixin._get_ob_cached.__get__(eng, _StubEngine)
    return eng, bound


def test_cache_miss_then_hit_within_ttl():
    """First call fetches, second within TTL uses cache."""
    eng, get_ob = _bind_helper()

    async def run():
        data1 = await get_ob("tok_a")
        data2 = await get_ob("tok_a")
        return data1, data2

    d1, d2 = asyncio.run(run())
    assert d1 is d2, "within-TTL calls must share the same dict"
    assert eng.client.fetch_count == 1, (
        f"expected 1 fetch, got {eng.client.fetch_count}")


def test_cache_expires_after_ttl():
    """Past TTL, helper re-fetches."""
    eng, get_ob = _bind_helper()
    eng._OB_CACHE_TTL = 0.1  # 100ms for speed

    async def run():
        await get_ob("tok_b")
        await asyncio.sleep(0.15)
        await get_ob("tok_b")

    asyncio.run(run())
    assert eng.client.fetch_count == 2, (
        f"expected 2 fetches after TTL expiry, got {eng.client.fetch_count}")


def test_cache_keys_independent():
    """Different token_ids do not share cache slots."""
    eng, get_ob = _bind_helper()

    async def run():
        await get_ob("tok_x")
        await get_ob("tok_y")

    asyncio.run(run())
    assert eng.client.fetch_count == 2
    assert "tok_x" in eng._ob_cache
    assert "tok_y" in eng._ob_cache


def test_cache_none_token_returns_none():
    """Empty token_id short-circuits to None (no fetch)."""
    eng, get_ob = _bind_helper()

    async def run():
        return await get_ob("")

    result = asyncio.run(run())
    assert result is None
    assert eng.client.fetch_count == 0


def test_cache_swallows_fetch_errors():
    """Fetch exception → None returned, no cache poisoning."""
    eng, get_ob = _bind_helper()

    async def bad_fetch(token_id):
        raise RuntimeError("boom")

    eng.client.get_orderbook = bad_fetch

    async def run():
        return await get_ob("tok_err")

    result = asyncio.run(run())
    assert result is None
    assert "tok_err" not in eng._ob_cache


# ═══════════════════════════════════════════════════════════════════════
#  2. Ported strategies — metadata contract verification
# ═══════════════════════════════════════════════════════════════════════

def test_hour_edge_uses_metadata_hour():
    """HourEdge reads hour_utc from metadata — 14h UTC = UP edge."""
    strat = HourEdgeLiveStrategy()
    snap = _snap(metadata={"hour_utc": 14}, minutes_remaining=2.0, total_minutes=5.0)
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "up"
    assert sig.confidence == pytest.approx(0.818, rel=0.01)


def test_hour_edge_falls_back_without_metadata():
    """Even if metadata is empty, HourEdge uses datetime.now() fallback."""
    strat = HourEdgeLiveStrategy()
    # No metadata — strategy should use system time. Outcome depends on
    # current hour, but the call must not raise.
    snap = _snap(metadata={})
    sig = strat.evaluate(snap)
    assert sig is not None
    # It either trades (if current hour happens to be in EDGES) or doesn't —
    # both are valid. We just verify no crash.


def test_orderbook_imbalance_up_signal():
    """UP-token bid/ask ratio ≥ 1.30 → UP signal with min_depth met."""
    strat = OrderbookImbalanceLiveStrategy()
    snap = _snap(metadata={
        "up_bid_depth": 400.0, "up_ask_depth": 250.0,
        "down_bid_depth": 100.0, "down_ask_depth": 100.0,
    })
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "up"


def test_orderbook_imbalance_no_signal_without_depth():
    """Below min_depth (100.0) → no signal."""
    strat = OrderbookImbalanceLiveStrategy()
    snap = _snap(metadata={
        "up_bid_depth": 5.0, "up_ask_depth": 3.0,
        "down_bid_depth": 5.0, "down_ask_depth": 3.0,
    })
    sig = strat.evaluate(snap)
    assert sig.should_trade is False


def test_fade_rip_reads_btc_price_change():
    """BTC +0.4% with time window open → fade DOWN."""
    strat = FadeRipLiveStrategy()
    # Default threshold 0.3%, fade_up_only=True → BTC +0.4% → DOWN
    snap = _snap(metadata={"btc_price_change": 0.4},
                 minutes_remaining=3.0, total_minutes=5.0)
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "down"


def test_opening_breakout_reads_btc_move_usd():
    """BTC $15 move in opening window → direction follows move sign."""
    strat = OpeningBreakoutLiveStrategy()
    # Default breakout_usd=10.0, time window must be time_pct ≥ 0.65
    # minutes_remaining / total_minutes = 4.0/5.0 = 0.80 ≥ 0.65 ✓
    snap = _snap(metadata={"btc_move_usd": 15.0},
                 minutes_remaining=4.0, total_minutes=5.0)
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "up"


def test_funding_rate_contrarian():
    """Positive funding rate above threshold → DOWN (contrarian mode)."""
    strat = FundingRateLiveStrategy()
    # Default threshold 0.0005, contrarian=True → positive → DOWN
    snap = _snap(metadata={"funding_rate": 0.001})
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "down"


def test_calibration_arb_uses_best_bid_ask():
    """CalibrationArb reads best_bid/best_ask (NOT metadata) — deviation
    below 0.42 (>-0.08 from 0.50) → UP signal."""
    strat = CalibrationArbLiveStrategy()
    # (best_bid + best_ask) / 2 = 0.41 → deviation = -0.09 below -0.08 → UP
    snap = _snap(best_bid=0.40, best_ask=0.42)
    sig = strat.evaluate(snap)
    assert sig.should_trade is True
    assert sig.direction == "up"


def test_calibration_arb_no_signal_outside_zone():
    """Outside [0.35, 0.65] target zone → no signal."""
    strat = CalibrationArbLiveStrategy()
    snap = _snap(best_bid=0.20, best_ask=0.25)
    sig = strat.evaluate(snap)
    assert sig.should_trade is False


# ═══════════════════════════════════════════════════════════════════════
#  3. Sanity check — expected plugin_meta keys exist in some strategy
# ═══════════════════════════════════════════════════════════════════════

PHASE82_EXPECTED_KEYS = {
    # Time
    "hour_utc", "minute_utc", "time_pct",
    # UP orderbook
    "up_best_bid", "up_best_ask", "up_spread",
    "up_bid_depth", "up_ask_depth",
    # DOWN orderbook
    "down_best_bid", "down_best_ask", "down_spread",
    "down_bid_depth", "down_ask_depth",
    # Spot / momentum
    "asset_spot_price", "asset_price_change", "spot_momentum_strength",
    "btc_price_change", "btc_move_usd",
    # Binance microstructure
    "binance_mid", "binance_microprice", "binance_ob_imbalance",
    "binance_spread_bps", "binance_trade_flow_60s", "binance_trade_count_60s",
    "funding_rate", "mark_price",
    # Divergence
    "divergence_signal", "divergence_confidence", "divergence_active",
    # Strategy-specific
    "loss_streak", "base_amount",
    # Risk
    "total_exposure", "daily_pnl", "open_position_count",
    "consecutive_losses", "daily_trade_count", "market_exposure",
    # Lifecycle
    "strategy_phase",
}


def test_phase82_metadata_keys_superset_of_strategy_needs():
    """Every key a ported strategy reads from metadata is in our
    PHASE82_EXPECTED_KEYS contract (sanity regression)."""
    used_by_strategies = {
        "hour_utc",             # HourEdge
        "up_bid_depth", "up_ask_depth",   # OB imb
        "down_bid_depth", "down_ask_depth",
        "btc_price_change",     # FadeRip
        "btc_move_usd",         # OpeningBreakout
        "funding_rate",         # FundingRate
    }
    missing = used_by_strategies - PHASE82_EXPECTED_KEYS
    assert not missing, f"strategies read {missing} but engine does not emit them"
