"""Unit tests for engine_settlement.py pure-logic surface (Epic 9 T9.6 P2).

Coverage gap baseline (2026-04-22): `engine_settlement.py` 8.1% / 353 stmts.
The module is mostly DB-heavy async exit/settle/close/notify paths — those
belong to T9.8 integration smoke. This file pins the tiny pure-logic
surface that does exist: per-market settle lock dict lazy-init.

Scope:
  * `_get_settle_lock(slug)` — Phase 54 P0-05 per-market lock factory
    - Lazy-init `_settle_locks` dict on first call
    - Same slug → same lock instance (idempotency)
    - Different slug → different lock (isolation)

Out-of-scope (→ T9.8):
  * `_exit` / `_settle` / `_settle_inner` — DB writes + fees_v2
  * `_classic_exit_notify` / `_classic_resolution_notify` — Telegram
  * `_ai_trade_analysis` / `_close` / `_notify` — DB + AI brain + TG
  * `_count` / `_count_losses` / `_get_avg_slippage` — DB reads
"""
from __future__ import annotations

import asyncio

import pytest

from core.engine_settlement import EngineSettlementMixin


class _SettlementHarness(EngineSettlementMixin):
    """Minimal stub — no state, relies on hasattr check in the method."""
    pass


class TestGetSettleLock:
    """Phase 54 P0-05: `_get_settle_lock` must:
      1. Lazy-init the `_settle_locks` dict on first access
      2. Return the SAME lock for the same slug (prevents race regression)
      3. Return DIFFERENT locks for different slugs (market isolation)
    """

    def test_first_call_lazy_inits_dict(self):
        h = _SettlementHarness()
        assert not hasattr(h, "_settle_locks")
        lock = h._get_settle_lock("btc-up-down")
        assert hasattr(h, "_settle_locks")
        assert isinstance(lock, asyncio.Lock)
        assert "btc-up-down" in h._settle_locks

    def test_same_slug_returns_same_lock(self):
        """CRITICAL: Phase 54 P0-05 lock-per-market contract.
        If this fails, concurrent settle/exit races can corrupt state."""
        h = _SettlementHarness()
        lock1 = h._get_settle_lock("btc-up-down")
        lock2 = h._get_settle_lock("btc-up-down")
        assert lock1 is lock2

    def test_different_slugs_different_locks(self):
        h = _SettlementHarness()
        btc_lock = h._get_settle_lock("btc-up-down")
        eth_lock = h._get_settle_lock("eth-up-down")
        assert btc_lock is not eth_lock
        assert len(h._settle_locks) == 2

    def test_empty_slug_allowed(self):
        """Defensive: empty slug still returns a lock (callers pre-default)."""
        h = _SettlementHarness()
        lock = h._get_settle_lock("")
        assert isinstance(lock, asyncio.Lock)

    def test_lock_is_functional_async(self):
        """The returned lock must actually gate concurrent coroutines."""
        async def scenario():
            h = _SettlementHarness()
            lock = h._get_settle_lock("test-slug")
            # Fresh lock must be acquirable
            async with lock:
                # Inside critical section — the same lock must report locked
                assert lock.locked() is True
            # Released after exit
            assert lock.locked() is False

        asyncio.run(scenario())
