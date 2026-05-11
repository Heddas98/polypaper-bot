"""Unit tests for Epic 5 T5.4 — WS reconnect invalidation + REST backfill.

Covers the two-part fix:
  1. `PolymarketWebSocket._connected_since` invalidates pre-reconnect cache
     entries in `get_live_price()`, preventing stale prices from being served
     after a drop/reconnect.
  2. `_backfill_prices_on_reconnect` (engine) fills fresh midpoints via
     REST `/midpoint` in parallel, reducing the stale-gap from "next WS tick"
     (2-15s on sparse crypto markets) to ~500ms (REST latency).

The tests exercise the WS client directly (no asyncio required for get_live_price
checks) and a minimal async shim for the backfill helper so we avoid standing
up the full TradingEngine.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from data.websocket_client import PolymarketWebSocket

# ═══════════════════════════════════════════════════════════════════════════
# Fix A — _connected_since invalidation in get_live_price
# ═══════════════════════════════════════════════════════════════════════════


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_connected_since_zero_legacy_behavior():
    """_connected_since=0 → invalidation disabled, only WS_STALE_SEC applies.

    Ensures the invalidation logic is opt-in: if the WS never connected, the
    cache still works as before (age-only check).
    """
    ws = PolymarketWebSocket()
    assert ws._connected_since == 0.0
    # Seed a fresh entry
    now = datetime.now(UTC)
    ws.live_prices["tok_a"] = {"price": 0.55, "ts": _iso(now)}
    # _connected_since=0 means "don't apply reconnect gate"
    assert ws.get_live_price("tok_a") == 0.55


def test_precconnect_cached_price_returns_none():
    """Entry cached BEFORE _connected_since → invalidated → None.

    This is the core regression: previously a WS drop followed by reconnect
    would serve the pre-drop price for up to WS_STALE_SEC more seconds.
    """
    ws = PolymarketWebSocket()
    # Pre-reconnect entry (5 seconds ago)
    before = datetime.now(UTC) - timedelta(seconds=5)
    ws.live_prices["tok_a"] = {"price": 0.55, "ts": _iso(before)}
    # Simulate reconnect happening NOW — _connected_since is set to "now"
    ws._connected_since = time.time()
    # Cached entry is pre-reconnect → must return None
    assert ws.get_live_price("tok_a") is None


def test_postreconnect_fresh_price_accepted():
    """Entry cached AFTER _connected_since → valid → returns price.

    Complements the previous test: once a fresh tick arrives post-reconnect,
    the cache starts serving again.
    """
    ws = PolymarketWebSocket()
    # Reconnect happened 2 seconds ago
    ws._connected_since = time.time() - 2.0
    # Fresh entry cached NOW
    now = datetime.now(UTC)
    ws.live_prices["tok_a"] = {"price": 0.55, "ts": _iso(now)}
    assert ws.get_live_price("tok_a") == 0.55


def test_age_staleness_independent_of_reconnect():
    """Old entry still blocked by WS_STALE_SEC even if _connected_since=0.

    Confirms the two gates (reconnect + age) compose correctly: either one
    is sufficient to invalidate.
    """
    ws = PolymarketWebSocket()
    # Very old entry (10 minutes ago), _connected_since=0 (not reconnected)
    old = datetime.now(UTC) - timedelta(seconds=600)
    ws.live_prices["tok_a"] = {"price": 0.55, "ts": _iso(old)}
    # Age gate alone rejects (>60s default)
    assert ws.get_live_price("tok_a") is None


# ═══════════════════════════════════════════════════════════════════════════
# Fix B — _backfill_prices_on_reconnect helper behavior
#
# We don't spin up the full TradingEngine. Instead we replicate the helper's
# loop body against a mock WS + mock client to pin the contract.
# ═══════════════════════════════════════════════════════════════════════════


class _MockWS:
    """Minimal WS surface used by the backfill helper."""

    def __init__(self, subscribed):
        self._subscribed = set(subscribed)
        self.live_prices: dict = {}
        self.is_connected = True


async def _run_backfill(ws, client):
    """Mirror of engine._backfill_prices_on_reconnect body (sans TradingEngine).

    Keeps the unit test isolated from engine construction while pinning the
    observable behavior: parallel fetch, skip exceptions/None/out-of-range,
    overwrite live_prices with fresh timestamps.
    """
    if not ws or not ws.is_connected:
        return 0
    subscribed = list(ws._subscribed)
    if not subscribed:
        return 0
    if not client:
        return 0
    try:
        results = await asyncio.gather(
            *(client.get_live_midpoint(tid) for tid in subscribed), return_exceptions=True
        )
    except Exception:
        return 0
    now_iso = datetime.now(UTC).isoformat()
    backfilled = 0
    for tid, p in zip(subscribed, results, strict=False):
        if isinstance(p, Exception) or p is None:
            continue
        if not (0.005 < p < 0.995):
            continue
        ws.live_prices[tid] = {"price": p, "ts": now_iso}
        backfilled += 1
    return backfilled


@pytest.mark.asyncio
async def test_backfill_populates_live_prices():
    """All subscribed tokens → REST returns valid prices → live_prices filled."""
    ws = _MockWS(subscribed=["tok_a", "tok_b", "tok_c"])
    client = AsyncMock()
    # Dict-keyed side_effect (set iteration order is nondeterministic, so a
    # positional list would race with test ordering hash-seed).
    price_map = {"tok_a": 0.55, "tok_b": 0.42, "tok_c": 0.61}
    client.get_live_midpoint.side_effect = lambda tid: price_map[tid]

    n = await _run_backfill(ws, client)
    assert n == 3
    # All three tokens got fresh entries
    assert set(ws.live_prices.keys()) == {"tok_a", "tok_b", "tok_c"}
    # Prices preserved
    assert ws.live_prices["tok_a"]["price"] == 0.55
    assert ws.live_prices["tok_b"]["price"] == 0.42
    assert ws.live_prices["tok_c"]["price"] == 0.61


@pytest.mark.asyncio
async def test_backfill_skips_none_results():
    """REST returns None for some tokens (e.g. no midpoint) → skip, not error.

    Critical behavior: best-effort backfill. One unresponsive token must not
    prevent the others from being filled.
    """
    ws = _MockWS(subscribed=["tok_a", "tok_b", "tok_c"])
    client = AsyncMock()
    price_map = {"tok_a": 0.55, "tok_b": None, "tok_c": 0.61}
    client.get_live_midpoint.side_effect = lambda tid: price_map[tid]

    n = await _run_backfill(ws, client)
    assert n == 2
    # tok_b is NOT in live_prices (was None)
    assert "tok_b" not in ws.live_prices
    assert ws.live_prices["tok_a"]["price"] == 0.55
    assert ws.live_prices["tok_c"]["price"] == 0.61


@pytest.mark.asyncio
async def test_backfill_empty_subscribed_no_rest_calls():
    """No subscribed tokens → no REST calls, no errors."""
    ws = _MockWS(subscribed=[])
    client = AsyncMock()
    n = await _run_backfill(ws, client)
    assert n == 0
    client.get_live_midpoint.assert_not_called()


@pytest.mark.asyncio
async def test_backfill_handles_exceptions_gracefully():
    """One get_live_midpoint raises → return_exceptions catches, others proceed."""
    ws = _MockWS(subscribed=["tok_a", "tok_b"])
    client = AsyncMock()

    async def _midpoint(tid):
        if tid == "tok_a":
            raise RuntimeError("network blip")
        return 0.42

    client.get_live_midpoint.side_effect = _midpoint

    n = await _run_backfill(ws, client)
    assert n == 1
    assert "tok_a" not in ws.live_prices
    assert ws.live_prices["tok_b"]["price"] == 0.42


@pytest.mark.asyncio
async def test_backfill_rejects_out_of_range_prices():
    """REST returns e.g. 0.0 or 1.0 → sanity-filter (0.005 < p < 0.995) rejects.

    Polymarket spits bogus values occasionally; the same guard used in WS
    _extract_price applies here.
    """
    ws = _MockWS(subscribed=["tok_a", "tok_b", "tok_c"])
    client = AsyncMock()
    # Dict-keyed (set iteration order is nondeterministic)
    price_map = {"tok_a": 0.0, "tok_b": 0.55, "tok_c": 1.0}
    client.get_live_midpoint.side_effect = lambda tid: price_map[tid]

    n = await _run_backfill(ws, client)
    assert n == 1
    assert "tok_a" not in ws.live_prices
    assert ws.live_prices["tok_b"]["price"] == 0.55
    assert "tok_c" not in ws.live_prices


@pytest.mark.asyncio
async def test_backfill_timestamp_survives_connected_since_gate():
    """End-to-end: after backfill, get_live_price returns the backfilled price.

    Guards the integration contract: _connected_since (set at reconnect) must
    be BEFORE the backfill timestamp, so get_live_price accepts the fresh entry.
    """
    ws = PolymarketWebSocket()
    ws._subscribed = {"tok_a"}
    ws._connected = True
    # Simulate reconnect just happened
    ws._connected_since = time.time()

    client = AsyncMock()
    client.get_live_midpoint.return_value = 0.73

    # Tiny delay to ensure timestamp > _connected_since
    await asyncio.sleep(0.01)
    n = await _run_backfill(ws, client)
    assert n == 1
    # Most importantly: get_live_price now returns the backfilled value
    assert ws.get_live_price("tok_a") == 0.73
