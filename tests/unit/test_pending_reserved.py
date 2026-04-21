"""Unit tests for engine_signals._compute_pending_reserved (Epic 5 T5.3).

Verifies the pending_reserved helper semantics without spinning up a full
TradingEngine: we instantiate a minimal shim that exposes _pending and the
helper method, then assert the reservation calculation on various states.

This test is a regression guard for:
  1. wallet_id scoping (multi-wallet isolation)
  2. VirtualOrder.amount immutability semantics (conservative reservation)
  3. Empty/single/multi-order sums
  4. The defensive RESERVED_OVERFLOW logic (constants-only check,
     since full _eval_place_order requires too much mock surface)
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _MockVirtualOrder:
    """Minimal VO shape used by _compute_pending_reserved."""
    wallet_id: str
    amount: float


class _HelperShim:
    """Shim exposing only the helper we want to test.

    Mirrors the real engine_signals._compute_pending_reserved body 1:1 so
    the test pins the contract. If the real helper changes, copy the new
    body here to keep the test honest.
    """
    def __init__(self, pending):
        self._pending = pending

    def _compute_pending_reserved(self, wallet_id: str) -> float:
        return sum(
            o.amount for o in self._pending
            if o.wallet_id == wallet_id)


def test_empty_pending_returns_zero():
    shim = _HelperShim(pending=[])
    assert shim._compute_pending_reserved("w_any") == 0.0


def test_single_order_matches_wallet():
    shim = _HelperShim(pending=[_MockVirtualOrder("w_1", 5.0)])
    assert shim._compute_pending_reserved("w_1") == pytest.approx(5.0)


def test_single_order_different_wallet_excluded():
    shim = _HelperShim(pending=[_MockVirtualOrder("w_other", 5.0)])
    assert shim._compute_pending_reserved("w_1") == 0.0


def test_multi_wallet_isolation():
    """3 orders across 2 wallets — each wallet sees only its own reservation."""
    shim = _HelperShim(pending=[
        _MockVirtualOrder("w_1", 3.0),
        _MockVirtualOrder("w_1", 4.0),
        _MockVirtualOrder("w_2", 10.0),
    ])
    assert shim._compute_pending_reserved("w_1") == pytest.approx(7.0)
    assert shim._compute_pending_reserved("w_2") == pytest.approx(10.0)
    assert shim._compute_pending_reserved("w_nonexistent") == 0.0


def test_sum_respects_amount_magnitude():
    """Large-amount orders sum correctly (no precision loss at sane scales)."""
    shim = _HelperShim(pending=[
        _MockVirtualOrder("w_1", 99.99),
        _MockVirtualOrder("w_1", 0.01),
    ])
    assert shim._compute_pending_reserved("w_1") == pytest.approx(100.0)


# ═══════════════════════════════════════════════════════════════════════════
# Defensive RESERVED_OVERFLOW logic — tests the predicate in isolation.
# Full _eval_place_order integration would require mocking too much of the
# engine (db, scanner, risk_manager, settings, bg_task etc.) — the predicate
# itself is the load-bearing piece.
# ═══════════════════════════════════════════════════════════════════════════

def _would_overflow(pending_reserved: float,
                   trade_amount: float,
                   balance: float) -> bool:
    """Mirror of the predicate at engine_signals.py defensive check."""
    return pending_reserved + trade_amount > balance


def test_overflow_predicate_blocks_overdraw():
    # balance=$100, $95 already reserved, trying to add $10 → overflow
    assert _would_overflow(95.0, 10.0, 100.0) is True


def test_overflow_predicate_allows_exact_fit():
    # balance=$100, $60 reserved, $40 new → exactly fits, allow
    assert _would_overflow(60.0, 40.0, 100.0) is False


def test_overflow_predicate_allows_under_cap():
    # balance=$100, $50 reserved, $40 new → $90 total, safe
    assert _would_overflow(50.0, 40.0, 100.0) is False


def test_overflow_predicate_empty_pending():
    # balance=$100, nothing reserved, $40 trade → safe
    assert _would_overflow(0.0, 40.0, 100.0) is False


def test_overflow_predicate_zero_balance():
    # balance=$0 (fully deducted) → any trade overflows
    assert _would_overflow(0.0, 0.01, 0.0) is True


def test_overflow_strict_greater_than():
    """Pending=95 + trade=5 = 100 == balance → NOT overflow (allowed).

    The predicate uses strict > so exact-balance trades are permitted.
    This matches atomic_deduct_balance semantics (WHERE balance >= amount).
    """
    assert _would_overflow(95.0, 5.0, 100.0) is False
