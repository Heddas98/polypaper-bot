"""Unit tests for Epic 5 T5.6 — WS subscription cap overflow fix.

Covers the three parts of the fix:

  Fix A — `data/market_scanner.py:_do_scan` now calls
  `ws.prune_stale_tokens(live_token_ids)` at end of each scan, freeing
  slots held by tokens from dead markets. Tested indirectly by exercising
  `PolymarketWebSocket.prune_stale_tokens` (the scanner-side wiring is
  verified by inspection; a full scanner integration test would need a
  mocked PolymarketClient).

  Fix B — `PolymarketWebSocket.subscribe()` now:
    - accepts `priority_first` list that wins cap admission first
    - preserves caller ordering (no more set-slicing nondeterminism)
    - drops from the TAIL when cap is hit

  Fix C — `_cap_hit_count`, `_cap_skipped_total`, `_last_cap_hit_ts`
  counters expose cap pressure via get_status().
"""

from __future__ import annotations

import asyncio
import os
import time
from unittest.mock import AsyncMock

import pytest

from data.websocket_client import PolymarketWebSocket

# ═══════════════════════════════════════════════════════════════════════════
# Fix A — prune_stale_tokens contract (WS client side)
# ═══════════════════════════════════════════════════════════════════════════


def test_prune_removes_missing_tokens():
    """Tokens not in active_token_ids are removed from _subscribed + cache."""
    ws = PolymarketWebSocket()
    ws._subscribed = {"tok_a", "tok_b", "tok_c"}
    ws.live_prices = {
        "tok_a": {"price": 0.5, "ts": "2026-04-21T10:00:00+00:00"},
        "tok_b": {"price": 0.4, "ts": "2026-04-21T10:00:00+00:00"},
        "tok_c": {"price": 0.6, "ts": "2026-04-21T10:00:00+00:00"},
    }
    # tok_a is still live; tok_b and tok_c are gone (resolved markets)
    pruned = ws.prune_stale_tokens({"tok_a"})
    assert pruned == 2
    assert ws._subscribed == {"tok_a"}
    assert "tok_b" not in ws.live_prices
    assert "tok_c" not in ws.live_prices
    assert "tok_a" in ws.live_prices


def test_prune_noop_when_nothing_stale():
    """Prune returns 0 and doesn't touch anything when all subs are active."""
    ws = PolymarketWebSocket()
    ws._subscribed = {"tok_a", "tok_b"}
    ws.live_prices = {
        "tok_a": {"price": 0.5, "ts": "2026-04-21T10:00:00+00:00"},
        "tok_b": {"price": 0.4, "ts": "2026-04-21T10:00:00+00:00"},
    }
    # Scanner reports a superset — no token should be dropped
    pruned = ws.prune_stale_tokens({"tok_a", "tok_b", "tok_c"})
    assert pruned == 0
    assert ws._subscribed == {"tok_a", "tok_b"}
    assert len(ws.live_prices) == 2


def test_prune_empty_subscribed_is_safe():
    """Pruning a never-subscribed WS doesn't raise or mutate state."""
    ws = PolymarketWebSocket()
    assert ws.prune_stale_tokens({"tok_a"}) == 0
    assert ws._subscribed == set()


# ═══════════════════════════════════════════════════════════════════════════
# Fix B — deterministic cap + priority_first
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_subscribe_below_cap_admits_all(monkeypatch):
    """Below MAX_WS_TOKENS → every token is subscribed, no counters fire."""
    monkeypatch.setenv("MAX_WS_TOKENS", "10")
    ws = PolymarketWebSocket()
    # Stub out _send so we don't try to hit real WS
    ws._send = AsyncMock()

    await ws.subscribe(["a", "b", "c"])

    assert ws._subscribed == {"a", "b", "c"}
    assert ws._cap_hit_count == 0
    assert ws._cap_skipped_total == 0
    assert ws._send.await_count == 3


@pytest.mark.asyncio
async def test_subscribe_preserves_caller_order_on_partial_cap(monkeypatch):
    """When cap is exceeded, FIRST tokens (by caller order) are kept.

    This is Fix B's key property: no more set-slicing that could drop
    high-priority (earlier-in-list) tokens at random.
    """
    monkeypatch.setenv("MAX_WS_TOKENS", "3")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # 6 tokens vs cap=3 → first 3 in caller order must win
    await ws.subscribe(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"])

    assert ws._subscribed == {"alpha", "bravo", "charlie"}
    # Cap partial-hit counters must fire
    assert ws._cap_hit_count == 1
    assert ws._cap_skipped_total == 3
    assert ws._last_cap_hit_ts > 0


@pytest.mark.asyncio
async def test_subscribe_full_cap_admits_none(monkeypatch):
    """If WS is already at cap, subscribe() bails without sending."""
    monkeypatch.setenv("MAX_WS_TOKENS", "2")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # Prime: fill to cap
    await ws.subscribe(["a", "b"])
    assert ws._subscribed == {"a", "b"}
    assert ws._send.await_count == 2

    # Now try to add more — should not send any, counters increment
    ws._send.reset_mock()
    await ws.subscribe(["c", "d", "e"])

    assert ws._subscribed == {"a", "b"}  # unchanged
    assert ws._send.await_count == 0  # nothing sent
    assert ws._cap_hit_count == 1
    assert ws._cap_skipped_total == 3


@pytest.mark.asyncio
async def test_priority_first_admitted_before_regular_on_cap(monkeypatch):
    """priority_first tokens get the scarce slots, regular tokens dropped.

    This is the shadow-live / open-position protection path.
    """
    monkeypatch.setenv("MAX_WS_TOKENS", "2")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # 2 slots, 4 candidates. Priority = [prot_a, prot_b].
    # Regular = [reg_x, reg_y]. Expected: prot_a + prot_b admitted,
    # reg_x + reg_y dropped.
    await ws.subscribe(["reg_x", "reg_y"], priority_first=["prot_a", "prot_b"])

    assert ws._subscribed == {"prot_a", "prot_b"}
    assert ws._cap_hit_count == 1
    assert ws._cap_skipped_total == 2


@pytest.mark.asyncio
async def test_priority_first_dedupe_cross_list(monkeypatch):
    """A tid in both priority_first and token_ids is counted once only."""
    monkeypatch.setenv("MAX_WS_TOKENS", "5")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    await ws.subscribe(["a", "b", "c"], priority_first=["b", "a"])  # overlap

    assert ws._subscribed == {"a", "b", "c"}
    assert ws._send.await_count == 3  # not 5 — dedupe worked
    assert ws._cap_hit_count == 0


@pytest.mark.asyncio
async def test_already_subscribed_filtered_out(monkeypatch):
    """Tokens already in _subscribed don't consume fresh slots."""
    monkeypatch.setenv("MAX_WS_TOKENS", "3")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # First batch fills 2 slots
    await ws.subscribe(["a", "b"])
    assert ws._subscribed == {"a", "b"}

    # Second batch resends "a" (should be filtered) + adds "c" + overflows "d"
    ws._send.reset_mock()
    await ws.subscribe(["a", "c", "d"])

    # "a" was already there → not re-sent. "c" fits (slot 3), "d" overflows.
    assert ws._subscribed == {"a", "b", "c"}
    assert ws._send.await_count == 1  # only "c"
    assert ws._cap_hit_count == 1
    assert ws._cap_skipped_total == 1  # "d"


@pytest.mark.asyncio
async def test_priority_overflows_only_priorities_dropped_from_tail(monkeypatch):
    """Edge case: priority list alone exceeds cap → tail of PRIORITY drops.

    This guards the invariant that the earlier entries in priority_first
    have precedence over later ones, and nothing from token_ids sneaks in.
    """
    monkeypatch.setenv("MAX_WS_TOKENS", "2")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    await ws.subscribe(["reg_x"], priority_first=["p1", "p2", "p3"])

    assert ws._subscribed == {"p1", "p2"}  # p3 dropped, reg_x never touched
    assert ws._cap_hit_count == 1
    assert ws._cap_skipped_total == 2  # p3 + reg_x


@pytest.mark.asyncio
async def test_empty_inputs_no_ops():
    """Both lists empty → function returns without state change."""
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()
    await ws.subscribe([])
    await ws.subscribe([], priority_first=[])
    assert ws._send.await_count == 0
    assert ws._cap_hit_count == 0


@pytest.mark.asyncio
async def test_none_priority_first_behaves_as_empty(monkeypatch):
    """Backward-compat: subscribe(tokens) with no priority_first works."""
    monkeypatch.setenv("MAX_WS_TOKENS", "5")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()
    await ws.subscribe(["a", "b"])
    assert ws._subscribed == {"a", "b"}


# ═══════════════════════════════════════════════════════════════════════════
# Fix C — cap telemetry surfaces via get_status()
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_status_exposes_cap_counters(monkeypatch):
    """get_status() includes cap_hits, cap_skipped, last_cap_hit_age."""
    monkeypatch.setenv("MAX_WS_TOKENS", "1")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # Before any cap hit: counters are 0, last_cap_hit_age is None
    s0 = ws.get_status()
    assert s0["cap_hits"] == 0
    assert s0["cap_skipped"] == 0
    assert s0["last_cap_hit_age"] is None

    # Trigger 2 cap hits
    await ws.subscribe(["a", "b"])  # b drops
    await ws.subscribe(["c", "d"])  # both drop (full)

    s1 = ws.get_status()
    assert s1["cap_hits"] == 2
    assert s1["cap_skipped"] == 3  # b, c, d
    assert s1["last_cap_hit_age"] is not None
    assert s1["last_cap_hit_age"] >= 0


# ═══════════════════════════════════════════════════════════════════════════
# Integration — Fix A scanner path: prune after subscribe cycle
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_scan_cycle_pattern_prunes_dead_tokens(monkeypatch):
    """End-to-end shape: after subscribe round, prune clears dead tokens.

    Simulates the loop that market_scanner._do_scan implements: add new
    tokens each cycle, then call prune_stale_tokens with the currently
    live set. Dead tokens from resolved markets should drop out and free
    cap slots so future cycles can subscribe fresh tokens.
    """
    monkeypatch.setenv("MAX_WS_TOKENS", "4")
    ws = PolymarketWebSocket()
    ws._send = AsyncMock()

    # Cycle 1: market A with 2 tokens, market B with 2 tokens
    await ws.subscribe(["a_up", "a_down", "b_up", "b_down"])
    assert ws._subscribed == {"a_up", "a_down", "b_up", "b_down"}

    # Cycle 2: market A resolved & gone. New market C appears.
    # Without prune, cap would refuse new tokens (full at 4/4).
    live_after_resolve = {"b_up", "b_down", "c_up", "c_down"}
    # Scanner prunes first:
    pruned = ws.prune_stale_tokens(live_after_resolve)
    assert pruned == 2  # a_up + a_down gone
    assert ws._subscribed == {"b_up", "b_down"}

    # Now scanner subscribes new set — c_up + c_down fit
    await ws.subscribe(["c_up", "c_down"])
    assert ws._subscribed == live_after_resolve
    # No cap hits: prune freed slots in time
    assert ws._cap_hit_count == 0
