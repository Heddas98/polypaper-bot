"""Wave 3-B (2026-05-18) — live_trader maybe_mirror SUCCESS-path e2e.

Audit C-03: tests/unit/test_live_trader.py covers maybe_mirror REJECTION
paths thoroughly but explicitly declares `_place` + `_execute_clob`
"out-of-scope" (its module docstring). That left the real mirror
execution flow — the part that moves real pUSD on mainnet — with zero
regression coverage.

This file fills the gap. It exercises the full mirror path against a
real :memory: SQLite DB, with only the CLOB network layer (`_execute_clob`)
mocked:

  - maybe_mirror SUCCESS → _place → _open set + _total_spent + live_trades INSERT
  - _place failure branches (CLOB status='failed' / returns None)
  - 'mock' / 'filled' status acceptance
  - single-slot guard after a successful open
  - check_settlement closing an open position (PnL applied, _open cleared)
  - _total_spent accumulation across sequential trades

Scope boundary: `_execute_clob` (and the sync py-clob-client call beneath
it) stays mocked — that is genuine network I/O, not unit-testable. Every
other layer (gate logic, _place body, DB writes, settlement) runs for real.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

try:
    import pytest_asyncio

    _ASYNC_FIXTURE = pytest_asyncio.fixture
except ImportError:  # pragma: no cover - pytest-asyncio is a dev dep
    _ASYNC_FIXTURE = pytest.fixture

from core.live_trader import LiveTrader

# Strategy label that IS in core.live_trader.LIVE_STRATEGIES whitelist.
_STRAT = "M_BTC_5m_any_0.92"


@_ASYNC_FIXTURE
async def live_db():
    """Real in-memory SQLite DB with the full schema (live_trades etc.)."""
    from db.database import Database

    db = Database(":memory:")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@_ASYNC_FIXTURE
async def trader(live_db):
    """LiveTrader wired to a real :memory: DB.

    Gates are forced open (_enabled / _auth_verified / not _paused) and the
    telegram notify path is stubbed (bot_app=None). _execute_clob is left
    for each test to patch with its desired CLOB outcome.
    """
    lt = LiveTrader(db=live_db, bot_app=None, settings=None)
    lt._enabled = True
    lt._auth_verified = True
    lt._paused = False
    # _notify touches bot_app/telegram — stub it so _place stays offline.
    lt._notify = AsyncMock()
    return lt


# ── maybe_mirror SUCCESS path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_maybe_mirror_success_sets_open_and_persists(trader, live_db):
    """All gates pass → _place runs → _open set + _total_spent + DB row."""
    trader._execute_clob = AsyncMock(return_value={"id": "ord-1", "status": "placed"})

    res = await trader.maybe_mirror(
        strategy_label=_STRAT,
        signal_score=0.85,
        direction="up",
        token_id="tok-abc",
        odds=0.80,
        slug="btc-updown-e2e-1",
    )

    assert res is not None, "success path must return the open dict"
    assert trader._open is not None
    assert trader._open["order_id"] == "ord-1"
    assert trader._open["strategy"] == _STRAT
    assert trader._open["slug"] == "btc-updown-e2e-1"
    assert trader._total_spent > 0.0
    assert trader._daily_trades == 1

    # Real DB row persisted by _place.
    rows = await live_db.conn.execute_fetchall(
        "SELECT strategy_label, slug, order_id FROM live_trades"
    )
    assert len(rows) == 1
    assert rows[0][0] == _STRAT
    assert rows[0][1] == "btc-updown-e2e-1"
    assert rows[0][2] == "ord-1"


@pytest.mark.asyncio
async def test_place_mock_status_accepted(trader):
    """CLOB status='mock' is an accepted fill outcome (paper-on-live shadow)."""
    trader._execute_clob = AsyncMock(return_value={"id": "m1", "status": "mock"})

    res = await trader.maybe_mirror(
        strategy_label=_STRAT,
        signal_score=0.85,
        direction="up",
        token_id="tok-mock",
        odds=0.80,
        slug="btc-mock-e2e",
    )

    assert res is not None
    assert trader._open is not None
    assert trader._open["order_id"] == "m1"


@pytest.mark.asyncio
async def test_place_filled_status_accepted(trader):
    """CLOB status='filled' is also accepted."""
    trader._execute_clob = AsyncMock(return_value={"id": "f1", "status": "filled"})

    res = await trader.maybe_mirror(
        strategy_label=_STRAT,
        signal_score=0.90,
        direction="down",
        token_id="tok-fill",
        odds=0.78,
        slug="btc-fill-e2e",
    )

    assert res is not None
    assert trader._open is not None


# ── _place failure branches ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_clob_failed_leaves_no_open(trader, live_db):
    """CLOB status='failed' → no _open, no spend, no DB row."""
    trader._execute_clob = AsyncMock(return_value={"id": "", "status": "failed"})

    res = await trader.maybe_mirror(
        strategy_label=_STRAT,
        signal_score=0.85,
        direction="up",
        token_id="tok-fail",
        odds=0.80,
        slug="btc-fail-e2e",
    )

    assert res is None
    assert trader._open is None
    assert trader._total_spent == 0.0

    rows = await live_db.conn.execute_fetchall("SELECT * FROM live_trades")
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_place_clob_none_graceful(trader):
    """_execute_clob returning None must not crash and must leave no _open."""
    trader._execute_clob = AsyncMock(return_value=None)

    res = await trader.maybe_mirror(
        strategy_label=_STRAT,
        signal_score=0.85,
        direction="up",
        token_id="tok-none",
        odds=0.80,
        slug="btc-none-e2e",
    )

    assert res is None
    assert trader._open is None
    assert trader._total_spent == 0.0


# ── single-slot guard ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_slot_guard_after_successful_open(trader):
    """A second maybe_mirror while _open is set is rejected (one live slot)."""
    trader._execute_clob = AsyncMock(return_value={"id": "ord-1", "status": "placed"})

    first = await trader.maybe_mirror(
        _STRAT, 0.85, "up", "tok-1", 0.80, "btc-slot-1"
    )
    assert first is not None
    assert trader._open is not None

    # _open still set → second call short-circuits before _place.
    second = await trader.maybe_mirror(
        _STRAT, 0.85, "up", "tok-2", 0.80, "btc-slot-2"
    )
    assert second is None
    # still the first trade's open, untouched.
    assert trader._open["slug"] == "btc-slot-1"


# ── check_settlement ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_settlement_closes_open_and_applies_pnl(trader):
    """Settling the matching slug clears _open and applies PnL."""
    trader._execute_clob = AsyncMock(return_value={"id": "o1", "status": "placed"})
    await trader.maybe_mirror(_STRAT, 0.85, "up", "tok-s", 0.80, "btc-settle-e2e")
    assert trader._open is not None

    await trader.check_settlement(
        slug="btc-settle-e2e", won=True, pnl_paper=2.0, paper_amount=1.0
    )

    assert trader._open is None, "settlement must clear the open slot"
    assert trader._total_pnl != 0.0, "winning settlement must move total PnL"


@pytest.mark.asyncio
async def test_check_settlement_ignores_nonmatching_slug(trader):
    """check_settlement for a different slug must NOT close the open trade."""
    trader._execute_clob = AsyncMock(return_value={"id": "o2", "status": "placed"})
    await trader.maybe_mirror(_STRAT, 0.85, "up", "tok-z", 0.80, "btc-real-slug")
    assert trader._open is not None

    await trader.check_settlement(
        slug="some-other-slug", won=True, pnl_paper=5.0, paper_amount=1.0
    )

    assert trader._open is not None, "non-matching slug must leave _open intact"
    assert trader._open["slug"] == "btc-real-slug"


# ── budget accumulation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_total_spent_accumulates_across_sequential_trades(trader):
    """_total_spent grows trade-over-trade; slot freed via settlement."""
    trader._execute_clob = AsyncMock(return_value={"id": "o", "status": "placed"})

    await trader.maybe_mirror(_STRAT, 0.85, "up", "t1", 0.80, "btc-acc-1")
    spent_after_first = trader._total_spent
    assert spent_after_first > 0.0

    # Free the single slot with a WINNING settlement — a losing one would
    # push _daily_pnl to -1.0 and trip the daily-loss halt on the next
    # maybe_mirror, masking the spend-accumulation behaviour under test.
    await trader.check_settlement(
        slug="btc-acc-1", won=True, pnl_paper=0.5, paper_amount=1.0
    )
    assert trader._open is None

    await trader.maybe_mirror(_STRAT, 0.85, "up", "t2", 0.80, "btc-acc-2")
    assert trader._total_spent > spent_after_first, "spend must accumulate"
