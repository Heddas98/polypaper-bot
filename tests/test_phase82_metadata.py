"""
Phase 82 Static Tests — Orderbook Cache Helper
==============================================

Covers the cycle-level orderbook cache introduced in Phase 82:

``_get_ob_cached`` (core.engine_signals.EngineSignalsMixin, TTL=2s by
default). We simulate multiple calls against a stub client and verify
that within-TTL calls hit the cache and post-TTL calls re-fetch.

2026-05-21 (RADİKAL strateji temizliği): bu dosyanın ikinci yarısı —
silinen "ported strategies" (hour_edge, orderbook_imbalance, fade_rip,
opening_breakout, funding_rate, calibration_arb) evaluate() testleri ve
metadata-key sanity bloğu — kaldırıldı. Stratejiler core/strategy_plugins.py
bone-thin yapılırken silindi (Heddas direktifi); cache helper canlı kod
olduğu için testleri korundu.

These tests do NOT boot the full TradingEngine — they focus on the
smallest unit relevant to the change: the cache helper. Run with:

    py -3.11 -m pytest tests/test_phase82_metadata.py -v
"""

from __future__ import annotations

import asyncio


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
#  _get_ob_cached cache behavior
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
    assert eng.client.fetch_count == 1, f"expected 1 fetch, got {eng.client.fetch_count}"


def test_cache_expires_after_ttl():
    """Past TTL, helper re-fetches."""
    eng, get_ob = _bind_helper()
    eng._OB_CACHE_TTL = 0.1  # 100ms for speed

    async def run():
        await get_ob("tok_b")
        await asyncio.sleep(0.15)
        await get_ob("tok_b")

    asyncio.run(run())
    assert (
        eng.client.fetch_count == 2
    ), f"expected 2 fetches after TTL expiry, got {eng.client.fetch_count}"


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
