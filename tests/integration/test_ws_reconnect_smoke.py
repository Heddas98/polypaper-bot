"""Integration smoke test for WS drop→reconnect→backfill sequence
(Epic 9 T9.8 part 3).

Goal: pin the end-to-end reconnect replay as a single scenario rather than
isolated unit checks. `test_ws_reconnect.py` covers individual steps of
Epic 5 T5.4 (connected_since gate, age-stale gate, backfill helper); this
file wires them together to guarantee the full sequence holds the
price-freshness doctrine:

  "Fresh > stale; cap + prune; no silent drops; reconnect backfill."

Scenario:
  1. Fresh boot — connected_since=now, 3 markets seeded with fresh prices.
  2. get_live_price() returns all three (baseline healthy state).
  3. WS drop — connected_since reset to 0 (legacy behavior).
  4. Reconnect — connected_since reset to "now"; all pre-drop cache
     entries are invalidated (return None).
  5. Post-reconnect fresh tick seeded → served correctly.
  6. Second drop→reconnect — cache invalidation is idempotent (no bleed).

Out-of-scope (→ T9.8-REG Windows backlog):
  * Real websockets.connect() against Polymarket endpoint
  * Actual _backfill_prices_on_reconnect() with REST /midpoint call
  * Race condition testing (multiple concurrent reconnects)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta

import pytest

from data.websocket_client import PolymarketWebSocket


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ═══ Fixture: 3-market seed ═════════════════════════════════════════════

@pytest.fixture
def ws_with_three_markets(monkeypatch):
    """Returns a WS client with 3 markets cached fresh + connected_since in
    the recent past.

    Simulates the state right after a successful WS connect + 3 book-update
    events.

    Hardening (Epic 9 T9.10):
      * Pin ``WS_STALE_SEC=60`` so a prior test that leaked a low
        override cannot cause ``get_live_price`` to return None.
      * Offset ``_connected_since`` 1s into the past so a microsecond
        clock race (where ``time.time()`` and ``datetime.now().timestamp()``
        land on the same tick after ISO roundtrip) cannot make
        ``entry_dt.timestamp() < _connected_since`` falsely True.
    """
    monkeypatch.setenv("WS_STALE_SEC", "60")
    ws = PolymarketWebSocket()
    # 1s cushion: guarantees entry_ts > _connected_since after roundtrip.
    ws._connected_since = time.time() - 1.0
    now = datetime.now(timezone.utc)
    ws.live_prices["btc-up"] = {"price": 0.52, "ts": _iso(now)}
    ws.live_prices["btc-dn"] = {"price": 0.48, "ts": _iso(now)}
    ws.live_prices["eth-up"] = {"price": 0.60, "ts": _iso(now)}
    return ws


# ═══ 1. Baseline: fresh connection serves all 3 ═════════════════════════

class TestBaselineHealthy:
    """Pre-drop state: all 3 tokens are within staleness window AND
    post-connected_since → all returned."""

    def test_all_three_served(self, ws_with_three_markets):
        ws = ws_with_three_markets
        assert ws.get_live_price("btc-up") == 0.52
        assert ws.get_live_price("btc-dn") == 0.48
        assert ws.get_live_price("eth-up") == 0.60

    def test_unknown_token_returns_none(self, ws_with_three_markets):
        ws = ws_with_three_markets
        assert ws.get_live_price("sol-up") is None


# ═══ 2. Drop sequence: connected_since=0 (legacy path) ═════════════════

class TestDropLegacyPath:
    """When `_connected_since=0` (never connected OR reset by drop), the
    reconnect-gate is disabled — only age staleness check applies.
    """

    def test_drop_resets_connected_since(self, ws_with_three_markets):
        ws = ws_with_three_markets
        # Simulate clean internal drop handler: connected_since=0
        ws._connected_since = 0.0
        # Entries still inside staleness window — served (legacy behavior)
        assert ws.get_live_price("btc-up") == 0.52


# ═══ 3. Reconnect sequence: stale cache invalidated ═════════════════════

class TestReconnectInvalidation:
    """After reconnect, pre-drop entries must be served as None until a
    fresh post-reconnect tick replaces them. This is the core T5.4 fix.
    """

    def test_preconnect_entries_invalidated(self, ws_with_three_markets):
        ws = ws_with_three_markets
        # Simulate: pre-drop cache is actually a few seconds old (after a
        # brief outage). Re-age the timestamps to BEFORE the reconnect marker.
        before = datetime.now(timezone.utc) - timedelta(seconds=3)
        for tid in ("btc-up", "btc-dn", "eth-up"):
            ws.live_prices[tid]["ts"] = _iso(before)
        # Reconnect: connected_since = now
        ws._connected_since = time.time()
        # Stale pre-drop entries must all return None
        assert ws.get_live_price("btc-up") is None
        assert ws.get_live_price("btc-dn") is None
        assert ws.get_live_price("eth-up") is None

    def test_post_reconnect_fresh_tick_served(self, ws_with_three_markets):
        """Once a fresh tick arrives POST-reconnect, the cache serves again."""
        ws = ws_with_three_markets
        # Reconnect 2s ago
        ws._connected_since = time.time() - 2.0
        # Pre-reconnect entries aged
        before = datetime.now(timezone.utc) - timedelta(seconds=5)
        for tid in ("btc-up", "btc-dn", "eth-up"):
            ws.live_prices[tid]["ts"] = _iso(before)
        assert ws.get_live_price("btc-up") is None
        # Post-reconnect fresh tick
        now = datetime.now(timezone.utc)
        ws.live_prices["btc-up"] = {"price": 0.53, "ts": _iso(now)}
        assert ws.get_live_price("btc-up") == 0.53


# ═══ 4. Double drop→reconnect: idempotent ═══════════════════════════════

class TestDoubleReconnectIdempotent:
    """Two consecutive drop→reconnect cycles must not leak pre-first-drop
    entries into the second. Doctrine: each reconnect invalidates everything
    cached before its marker.
    """

    def test_two_cycles_no_stale_bleed(self, ws_with_three_markets):
        ws = ws_with_three_markets

        # Cycle 1: drop + reconnect
        ws._connected_since = 0.0  # drop
        ws._connected_since = time.time() - 10.0  # reconnect A (10s ago)

        # Post-reconnect-A tick seeded 8s ago
        post_a = datetime.now(timezone.utc) - timedelta(seconds=8)
        ws.live_prices["btc-up"] = {"price": 0.55, "ts": _iso(post_a)}
        # At this point post_a (8s ago) is AFTER reconnect A (10s ago) — served
        assert ws.get_live_price("btc-up") == 0.55

        # Cycle 2: drop + reconnect
        ws._connected_since = 0.0  # drop
        ws._connected_since = time.time()  # reconnect B (now)
        # The post-A entry (8s ago) is now BEFORE reconnect B → invalidated
        assert ws.get_live_price("btc-up") is None


# ═══ 5. Price freshness doctrine pin ═══════════════════════════════════

class TestFreshnessDoctrine:
    """Direct pin of the stated doctrine: fresh > stale; no silent drops;
    reconnect backfill invalidates stale cache.
    """

    def test_stale_age_enforced(self):
        """Even without a reconnect, entries older than WS_STALE_SEC default
        (30s per the module default) should NOT be served."""
        import data.websocket_client as wsmod
        # WS_STALE_SEC default — exact number may drift; test the shape
        ws = PolymarketWebSocket()
        ws._connected_since = 0.0  # disable reconnect gate
        very_old = datetime.now(timezone.utc) - timedelta(seconds=600)
        ws.live_prices["tok_x"] = {"price": 0.50, "ts": _iso(very_old)}
        # 600s >> any reasonable WS_STALE_SEC → must be None
        assert ws.get_live_price("tok_x") is None

    def test_missing_ts_returns_none_fresh_over_stale(self):
        """Malformed cache entry (no 'ts' key) must return None.

        Epic 10 T10.5 (2026-04-22): tightened from prior defensive
        fallback (which served the price despite unknown freshness).

        All 3 WS cache-write sites in _handle_message always set
        'ts' alongside 'price', so a missing 'ts' can only occur via
        corruption, a future refactor bug, or a hand-constructed
        fixture. In those cases None (no-trade) is the safe fail mode,
        matching the project's "fresh > stale" doctrine.
        """
        ws = PolymarketWebSocket()
        ws._connected_since = 0.0
        ws.live_prices["tok_x"] = {"price": 0.50}  # no 'ts'
        result = ws.get_live_price("tok_x")
        # Epic 10 T10.5: malformed entry → None (was 0.50 pre-T10.5).
        assert result is None

    def test_malformed_ts_string_returns_none(self):
        """Epic 10 T10.5: un-parseable ISO string → None.

        Prior behavior swallowed ValueError from fromisoformat and
        served the raw cached price. Post-T10.5: any freshness-check
        failure → None, to honour fresh > stale.
        """
        ws = PolymarketWebSocket()
        ws._connected_since = 0.0
        ws.live_prices["tok_y"] = {"price": 0.75, "ts": "not-an-iso-date"}
        assert ws.get_live_price("tok_y") is None

    def test_non_string_ts_returns_none(self):
        """Epic 10 T10.5: non-string 'ts' (e.g. int epoch) → None.

        int.replace() does not exist — AttributeError — post-T10.5
        this returns None instead of serving the stale price.
        """
        ws = PolymarketWebSocket()
        ws._connected_since = 0.0
        ws.live_prices["tok_z"] = {"price": 0.25, "ts": 1234567890}
        assert ws.get_live_price("tok_z") is None
