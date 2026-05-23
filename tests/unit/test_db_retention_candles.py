"""Regression tests for db_retention_job candle pruning (2026-05-23 fix).

The retention job swept candles_ext / candles_poly with a non-existent
`close_ts` column and an ISO-text cutoff. Two bugs:

  1. close_ts does not exist → the query failed silently (live structured.jsonl
     showed `[retention] candles_*>30d: failed — no such column: close_ts`) so
     the candle tables were never pruned. This is the candles-side loose end of
     the 2026-05-22 DB-retention / fan root-cause work (e9618a9).
  2. open_ts is a numeric epoch column, not ISO text. In SQLite an INTEGER
     always sorts before TEXT, so `open_ts < '<iso>'` would have matched EVERY
     row → wiping the very backtest data we keep. candles_poly also mixes epoch
     SECONDS (live time.time() path) with legacy epoch-MS rows; candles_ext is
     uniformly MS (Binance kline open time).

The fix normalizes open_ts to ms (>10e9 ⇒ already ms, else ×1000 — the same
boundary backtest/candle_runner.py uses) and compares against a numeric ms
cutoff. These tests run the real job against an in-memory aiosqlite DB; the
pre-existing smoke test (TestDbRetentionJobWave21) mocks the DB and swallows
exceptions, so it could not catch a SQL-correctness bug.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import aiosqlite
import pytest
import pytest_asyncio

from telegram_bot.jobs.db_retention_job import db_retention_job

DAY = 86_400


class _DB:
    """Minimal stand-in for the bot's DB wrapper — the job only uses .conn."""

    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn


@pytest_asyncio.fixture
async def mem_db():
    """In-memory aiosqlite DB with candle tables (real schema, db/migrations.py
    v18) plus empty stubs for the other tables the job sweeps, so it runs
    cleanly instead of erroring on missing tables."""
    conn = await aiosqlite.connect(":memory:")
    await conn.execute(
        "CREATE TABLE candles_ext (symbol TEXT, interval TEXT, open_ts INTEGER,"
        " open REAL, high REAL, low REAL, close REAL, volume REAL,"
        " PRIMARY KEY (symbol, interval, open_ts))"
    )
    await conn.execute(
        "CREATE TABLE candles_poly (asset_id TEXT, slug TEXT, asset TEXT,"
        " timeframe TEXT, open_ts INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL, PRIMARY KEY (asset_id, timeframe, open_ts))"
    )
    for stub in ("ob_snapshots", "ob_deltas", "ob_trades"):
        await conn.execute(f"CREATE TABLE {stub} (ts_ms INTEGER)")
    await conn.execute("CREATE TABLE odds_history (timestamp TEXT)")
    await conn.commit()
    yield conn
    await conn.close()


@pytest.fixture
def epochs():
    """Known ages relative to now, in both epoch units."""
    now = int(datetime.now(UTC).timestamp())
    return {
        "old_s": now - 40 * DAY,  # 40d old, seconds
        "fresh_s": now - 1 * DAY,  # 1d old, seconds
        "old_ms": (now - 40 * DAY) * 1000,  # 40d old, milliseconds (legacy)
        "fresh_ms": (now - 1 * DAY) * 1000,  # 1d old, milliseconds
    }


def _ctx(db: _DB) -> MagicMock:
    ctx = MagicMock()
    ctx.application.bot_data = {"db": db}
    return ctx


def _env(monkeypatch, mode: str):
    monkeypatch.setenv("DB_RETENTION_MODE", mode)
    monkeypatch.setenv("DB_RETENTION_CANDLES_POLY_DAYS", "30")
    monkeypatch.setenv("DB_RETENTION_CANDLES_EXT_DAYS", "30")
    monkeypatch.setenv("DB_RETENTION_VACUUM_ENABLED", "0")
    monkeypatch.setenv("DB_RETENTION_NOTIFY", "0")


async def _poly(conn, open_ts: int):
    await conn.execute(
        "INSERT INTO candles_poly (asset_id, slug, asset, timeframe, open_ts,"
        " open, high, low, close, volume) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"tok{open_ts}", "btc-up-down", "BTC", "5m", open_ts, 0.5, 0.5, 0.5, 0.5, 1.0),
    )


async def _ext(conn, open_ts: int):
    await conn.execute(
        "INSERT INTO candles_ext (symbol, interval, open_ts, open, high, low,"
        " close, volume) VALUES (?,?,?,?,?,?,?,?)",
        ("BTCUSDT", "5m", open_ts, 0.5, 0.5, 0.5, 0.5, 1.0),
    )


async def _count(conn, table: str) -> int:
    cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


@pytest.mark.asyncio
async def test_delete_prunes_old_keeps_fresh_both_units(mem_db, monkeypatch, epochs):
    """delete mode removes >30d rows and keeps fresh rows, in BOTH the seconds
    and milliseconds conventions found in candles_poly."""
    db = _DB(mem_db)
    # candles_poly: mixed sec + ms, each with an old and a fresh row.
    await _poly(mem_db, epochs["old_s"])
    await _poly(mem_db, epochs["old_ms"])
    await _poly(mem_db, epochs["fresh_s"])
    await _poly(mem_db, epochs["fresh_ms"])
    # candles_ext: ms only (its real unit), old + fresh.
    await _ext(mem_db, epochs["old_ms"])
    await _ext(mem_db, epochs["fresh_ms"])
    await mem_db.commit()

    _env(monkeypatch, "delete")
    summary = await db_retention_job(_ctx(db), force_notify=False)

    assert summary["candles_poly"] == 2  # both old rows (sec + ms) deleted
    assert summary["candles_ext"] == 1  # old ms row deleted

    cur = await mem_db.execute("SELECT open_ts FROM candles_poly ORDER BY open_ts")
    remaining = sorted(r[0] for r in await cur.fetchall())
    assert remaining == sorted([epochs["fresh_s"], epochs["fresh_ms"]])
    assert await _count(mem_db, "candles_ext") == 1


@pytest.mark.asyncio
async def test_report_counts_but_deletes_nothing(mem_db, monkeypatch, epochs):
    """report mode counts the same >30d rows but is non-destructive."""
    db = _DB(mem_db)
    await _poly(mem_db, epochs["old_s"])
    await _poly(mem_db, epochs["old_ms"])
    await _poly(mem_db, epochs["fresh_s"])
    await _ext(mem_db, epochs["old_ms"])
    await mem_db.commit()

    _env(monkeypatch, "report")
    summary = await db_retention_job(_ctx(db), force_notify=False)

    assert summary["candles_poly"] == 2  # 2 old rows counted
    assert summary["candles_ext"] == 1
    # Nothing deleted in report mode.
    assert await _count(mem_db, "candles_poly") == 3
    assert await _count(mem_db, "candles_ext") == 1


@pytest.mark.asyncio
async def test_fresh_rows_never_pruned(mem_db, monkeypatch, epochs):
    """Guard against the old bug directly: an ISO-text cutoff on a numeric
    column made `open_ts < '<iso>'` true for every row. With only fresh data a
    correct job must delete zero rows in both unit conventions."""
    db = _DB(mem_db)
    await _poly(mem_db, epochs["fresh_s"])
    await _poly(mem_db, epochs["fresh_ms"])
    await _ext(mem_db, epochs["fresh_ms"])
    await mem_db.commit()

    _env(monkeypatch, "delete")
    summary = await db_retention_job(_ctx(db), force_notify=False)

    assert summary["candles_poly"] == 0
    assert summary["candles_ext"] == 0
    assert await _count(mem_db, "candles_poly") == 2
    assert await _count(mem_db, "candles_ext") == 1
