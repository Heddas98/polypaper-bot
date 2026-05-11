"""Unit tests for db/database.py::atomic_deduct_balance (Epic 5 T5.2).

Verifies F-02 atomic deduction pattern holds under concurrent access:
  1. N concurrent deducts on same wallet never over-draw balance
  2. WHERE balance >= amount prevents negative balance
  3. rowcount semantics: True iff row was actually updated
  4. Non-matching wallet_id returns False (not an error)

SQLite single-connection + asyncio single-thread means these tests run
sequentially at the event-loop level — the "race" simulated is the logical
interleaving of multiple coroutines, which is exactly what happens in the
engine when settlement + fill commit in the same tick.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import UTC, datetime, timezone

import pytest
import pytest_asyncio

from db.database import Database
from db.models import User, Wallet


@pytest_asyncio.fixture
async def db():
    """Fresh Database instance on a temp SQLite file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)
    await database.initialize()
    yield database
    await database.close()
    try:
        os.remove(path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def funded_wallet(db):
    """Create a user + wallet with $100 starting balance."""
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    wallet_id = f"w_{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC).isoformat()

    await db.conn.execute(
        "INSERT INTO users (id, telegram_id, username, accepted_terms, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (user_id, 123456, "testuser", now),
    )
    await db.conn.execute(
        "INSERT INTO wallets (id, user_id, label, balance, is_primary, created_at) "
        "VALUES (?, ?, 'primary', 100.0, 1, ?)",
        (wallet_id, user_id, now),
    )
    await db.conn.commit()
    return wallet_id


@pytest.mark.asyncio
async def test_single_deduct_success(db, funded_wallet):
    """Baseline: simple deduct succeeds and decrements balance."""
    ok = await db.atomic_deduct_balance(funded_wallet, 30.0)
    assert ok is True

    cur = await db.conn.execute("SELECT balance FROM wallets WHERE id=?", (funded_wallet,))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_insufficient_balance_returns_false(db, funded_wallet):
    """Deduct larger than balance: returns False, balance unchanged."""
    ok = await db.atomic_deduct_balance(funded_wallet, 200.0)
    assert ok is False

    cur = await db.conn.execute("SELECT balance FROM wallets WHERE id=?", (funded_wallet,))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_exact_balance_deduct(db, funded_wallet):
    """Deduct == balance: allowed, leaves zero."""
    ok = await db.atomic_deduct_balance(funded_wallet, 100.0)
    assert ok is True

    cur = await db.conn.execute("SELECT balance FROM wallets WHERE id=?", (funded_wallet,))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_nonexistent_wallet_returns_false(db):
    """Nonexistent wallet_id: returns False, no exception."""
    ok = await db.atomic_deduct_balance("w_nonexistent", 10.0)
    assert ok is False


@pytest.mark.asyncio
async def test_concurrent_deducts_never_overdraw(db, funded_wallet):
    """10 concurrent $15 deducts on $100 wallet: exactly 6 succeed, 4 fail.

    This is the core F-02 race test. Before the atomic WHERE clause was added,
    a read-modify-write pattern could have allowed all 10 to see balance=100
    and deduct in parallel → negative balance. With UPDATE...WHERE balance >=
    amount, SQLite serializes the writes and rejects those that can't cover.
    """

    async def deduct():
        return await db.atomic_deduct_balance(funded_wallet, 15.0)

    results = await asyncio.gather(*(deduct() for _ in range(10)))
    successes = sum(1 for r in results if r is True)
    failures = sum(1 for r in results if r is False)

    # 100 / 15 = 6.66 → 6 full deducts succeed, 4 fail
    assert successes == 6, f"expected 6 successes, got {successes}"
    assert failures == 4, f"expected 4 failures, got {failures}"

    # Balance must be non-negative (10 remaining after 6×15=90 deducted)
    cur = await db.conn.execute("SELECT balance FROM wallets WHERE id=?", (funded_wallet,))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(10.0)
    assert row[0] >= 0, "CRITICAL: balance went negative — race condition bug"


@pytest.mark.asyncio
async def test_concurrent_mixed_amounts(db, funded_wallet):
    """Mixed amounts: $40+$30+$25+$10 = $105 requested from $100 wallet.

    Exactly 3 deducts should fit ($40+$30+$25=$95 or any other combination ≤100).
    The 4th should fail. Which 3 succeed depends on asyncio scheduling but the
    total deducted must be ≤ $100 and balance must stay ≥ 0.
    """
    amounts = [40.0, 30.0, 25.0, 10.0]
    results = await asyncio.gather(*(db.atomic_deduct_balance(funded_wallet, a) for a in amounts))

    total_deducted = sum(a for a, r in zip(amounts, results, strict=False) if r)
    assert total_deducted <= 100.0, f"overdrew: deducted ${total_deducted}"

    cur = await db.conn.execute("SELECT balance FROM wallets WHERE id=?", (funded_wallet,))
    row = await cur.fetchone()
    remaining = row[0]
    assert remaining >= 0, "balance went negative"
    assert remaining == pytest.approx(100.0 - total_deducted)
