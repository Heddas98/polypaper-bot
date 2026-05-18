"""Wave 3-C (2026-05-18) — ai_brain.run_brain_cycle e2e.

Audit C-03: run_brain_cycle was only exercised via synthetic mocks. Its
single most important invariant — P0-01: the LLM can NEVER auto-execute
a strategy mutation, every action goes to the human approval queue — had
no real regression test.

This file drives the actual `run_brain_cycle` → `_run_brain_cycle_inner`
flow against a real :memory: SQLite DB. Only the genuine I/O boundaries
are mocked (LLM call, engine-backed data gather, outcome measurement,
the approval-queue + telegram sinks). `_parse` runs for real.

Covered:
  - budget gate (_spent >= MAX_BUDGET) short-circuits before any LLM call
  - minimum-trades gate (too few settled trades → no cycle)
  - P0-01: an LLM STOP action at confidence 0.99 STILL only reaches
    `_queue_for_approval` — never an execute path
  - empty action list records a decision, does NOT queue
  - unparseable LLM output → retry → "Parse failed" + operator notified
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

try:
    import pytest_asyncio

    _ASYNC_FIXTURE = pytest_asyncio.fixture
except ImportError:  # pragma: no cover - pytest-asyncio is a dev dep
    _ASYNC_FIXTURE = pytest.fixture

from core.ai_brain import MAX_BUDGET, AIBrain


async def _seed_settled_trades(db, n: int) -> None:
    """Insert n settled executions dated 'now' so the minimum-trades gate
    (recent settled trades in the last 24h) is satisfied.

    executions.user_id / wallet_id are NOT NULL foreign keys (PRAGMA
    foreign_keys=ON), so a parent user + wallet row is seeded first.
    """
    now = datetime.now(UTC).isoformat()
    await db.conn.execute(
        "INSERT INTO users (id,telegram_id,created_at) VALUES (?,?,?)",
        ("u1", 1, now),
    )
    await db.conn.execute(
        "INSERT INTO wallets (id,user_id,created_at) VALUES (?,?,?)",
        ("w1", "u1", now),
    )
    for i in range(n):
        await db.conn.execute(
            "INSERT INTO executions (id,user_id,wallet_id,event_slug,direction,"
            "trade_amount,status,result,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"ex{i}", "u1", "w1", f"slug{i}", "up", 1.0, "settled", "won", now, now),
        )
    await db.conn.commit()


@_ASYNC_FIXTURE
async def ai_db():
    """Real in-memory SQLite DB with the full schema (executions etc.)."""
    from db.database import Database

    db = Database(":memory:")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


@_ASYNC_FIXTURE
async def brain(ai_db):
    """AIBrain on a real :memory: DB with I/O boundaries stubbed.

    `_parse` is deliberately left REAL — the JSON parse + P0-01 routing
    are exactly what these tests verify. Everything that does network or
    engine I/O is mocked.
    """
    b = AIBrain(db=ai_db, engine=None, bot_app=None, settings=None)
    b._measure_outcomes = AsyncMock()
    b._analyze_losses = AsyncMock()
    b._gather_data = AsyncMock(return_value="# MARKET\nstrats=3 open=0")
    b._queue_for_approval = AsyncMock()
    b._save_decision = AsyncMock()
    b._send = AsyncMock()
    return b


# ── budget + minimum-trades gates ────────────────────────────────────────


@pytest.mark.asyncio
async def test_budget_exhausted_skips_llm(brain):
    """_spent >= MAX_BUDGET short-circuits before any LLM call."""
    brain._spent = MAX_BUDGET
    brain._two_agent_cycle = AsyncMock()

    res = await brain.run_brain_cycle()

    assert res is not None and "Budget" in res
    brain._two_agent_cycle.assert_not_called()
    brain._queue_for_approval.assert_not_called()


@pytest.mark.asyncio
async def test_minimum_trades_gate_blocks_cycle(brain):
    """Too few settled trades → cycle exits without calling the LLM."""
    # executions table empty → recent_trades = 0 < MIN_TRADES_FOR_ACTION.
    brain._two_agent_cycle = AsyncMock()

    res = await brain.run_brain_cycle()

    assert res is not None and "Minimum trades" in res
    brain._two_agent_cycle.assert_not_called()


# ── P0-01: no auto-execute, ever ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_p0_01_high_confidence_stop_only_queued(brain, ai_db):
    """P0-01 INVARIANT: an LLM STOP action at confidence 0.99 reaches ONLY
    the approval queue — there is no auto-execute path.

    This is the regression guard for the 2026-05-08 P0-01 fix. If anyone
    re-introduces a confidence-gated auto-execute branch, this test fails.
    """
    await _seed_settled_trades(ai_db, 20)
    brain._two_agent_cycle = AsyncMock(
        return_value=(
            '{"actions": [{"type": "STOP", "strategy_id": "S1", '
            '"reason": "killing it"}], "confidence": 0.99, '
            '"lessons_learned": ""}'
        )
    )

    res = await brain.run_brain_cycle()

    # The action reached the human approval queue.
    brain._queue_for_approval.assert_awaited_once()
    queued_actions = brain._queue_for_approval.call_args[0][0]
    assert any(a.get("type") == "STOP" for a in queued_actions)
    # Cycle reported the action count — but nothing executed it.
    assert res is not None and "1 action" in res


@pytest.mark.asyncio
async def test_empty_actions_records_decision_no_queue(brain, ai_db):
    """LLM returning no actions → decision recorded, approval queue untouched."""
    await _seed_settled_trades(ai_db, 20)
    brain._two_agent_cycle = AsyncMock(
        return_value='{"actions": [], "confidence": 0.5, "lessons_learned": ""}'
    )

    await brain.run_brain_cycle()

    brain._save_decision.assert_awaited()
    brain._queue_for_approval.assert_not_called()


# ── parse-failure path ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unparseable_llm_output_notifies_operator(brain, ai_db):
    """Garbage LLM output → parse retry → 'Parse failed' + operator notified."""
    await _seed_settled_trades(ai_db, 20)
    brain._two_agent_cycle = AsyncMock(return_value="this is not json at all {{{")
    # retry path calls _call_groq — also return garbage.
    brain._call_groq = AsyncMock(return_value="still not json )))")

    res = await brain.run_brain_cycle()

    assert res is not None and "Parse failed" in res
    brain._send.assert_awaited()
    brain._queue_for_approval.assert_not_called()
