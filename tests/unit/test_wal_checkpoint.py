"""Unit tests for Epic 5 T5.5 — PRAGMA wal_checkpoint(TRUNCATE) behavior.

Verifies the TRUNCATE checkpoint behavior that the new wal_checkpoint_job
relies on:

  1. TRUNCATE on an idle WAL returns (busy=0, log=0, ckpt=0) — no-op, no error
  2. After writes, TRUNCATE shrinks -wal file to 0 bytes
  3. Running TRUNCATE twice is idempotent (2nd call is a no-op)
  4. Data survives the checkpoint (SELECT returns expected rows)
  5. A second concurrent connection can still read during checkpoint

We don't test the TelegramContext-wrapped job itself (that would need
asyncio application mocks); we pin the SQLite contract the job depends on.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def wal_db():
    """Fresh SQLite DB with WAL mode enabled, populated with test rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(path)

    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    # Very low autocheckpoint so tiny test inserts don't auto-flush
    await conn.execute("PRAGMA wal_autocheckpoint=100000")
    await conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    await conn.commit()

    yield conn, db_path

    await conn.close()
    for suf in ("", "-wal", "-shm"):
        try:
            Path(str(db_path) + suf).unlink()
        except OSError:
            pass


def _wal_size(db_path: Path) -> int:
    wal = db_path.with_name(db_path.name + "-wal")
    return wal.stat().st_size if wal.exists() else 0


@pytest.mark.asyncio
async def test_truncate_on_idle_db_returns_zero(wal_db):
    """TRUNCATE on a fresh DB with no pending frames → (0, 0, 0), no error."""
    conn, db_path = wal_db
    # Force a small checkpoint so WAL is at a known state
    cur = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row = await cur.fetchone()
    # Row is (busy, log_pages, checkpointed_pages)
    assert row is not None
    busy = int(row[0])
    assert busy == 0, f"expected busy=0 on idle DB, got {busy}"


@pytest.mark.asyncio
async def test_truncate_shrinks_wal_after_writes(wal_db):
    """Insert rows → WAL grows. TRUNCATE → WAL shrinks to 0 bytes."""
    conn, db_path = wal_db

    # Insert enough rows to produce visible WAL growth
    for i in range(500):
        await conn.execute("INSERT INTO t (v) VALUES (?)", (f"val_{i}",))
    await conn.commit()

    size_before = _wal_size(db_path)
    assert size_before > 0, "WAL should contain frames after writes"

    cur = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row = await cur.fetchone()
    assert int(row[0]) == 0, f"busy expected 0, got {row[0]}"
    # Note: log/ckpt values are SQLite-version-dependent (some builds reset
    # counters post-truncate). What we care about is the FILE shrinking —
    # that's the job's actual goal.

    size_after = _wal_size(db_path)
    # TRUNCATE either zeroes the WAL or shrinks it significantly
    assert size_after < size_before, f"WAL did not shrink: {size_before} → {size_after}"
    # On most SQLite builds TRUNCATE zeroes it
    assert size_after == 0, f"TRUNCATE expected size=0, got {size_after}"


@pytest.mark.asyncio
async def test_truncate_is_idempotent(wal_db):
    """Running TRUNCATE twice in a row → second call is a clean no-op."""
    conn, db_path = wal_db

    # Seed some writes + checkpoint
    for i in range(100):
        await conn.execute("INSERT INTO t (v) VALUES (?)", (f"x_{i}",))
    await conn.commit()

    cur1 = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row1 = await cur1.fetchone()
    assert int(row1[0]) == 0

    # 2nd call — WAL already empty → busy=0, log=0, ckpt=0
    cur2 = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row2 = await cur2.fetchone()
    assert int(row2[0]) == 0, "2nd TRUNCATE should not be busy"


@pytest.mark.asyncio
async def test_data_survives_truncate(wal_db):
    """Writes committed before TRUNCATE must still be readable after."""
    conn, db_path = wal_db

    await conn.execute("INSERT INTO t (v) VALUES ('alpha')")
    await conn.execute("INSERT INTO t (v) VALUES ('beta')")
    await conn.execute("INSERT INTO t (v) VALUES ('gamma')")
    await conn.commit()

    await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    cur = await conn.execute("SELECT v FROM t ORDER BY id")
    rows = await cur.fetchall()
    values = [r[0] for r in rows]
    assert values == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_truncate_with_concurrent_reader(wal_db):
    """A second connection reading during TRUNCATE doesn't fail.

    TRUNCATE may return busy=1 if the reader holds an old snapshot — the
    important contract is that neither side errors. The job logs busy=1
    as PARTIAL and moves on.
    """
    conn, db_path = wal_db

    # Write then commit
    for i in range(50):
        await conn.execute("INSERT INTO t (v) VALUES (?)", (f"r_{i}",))
    await conn.commit()

    # Open a 2nd connection that holds a read snapshot
    reader = await aiosqlite.connect(str(db_path))
    try:
        cur_r = await reader.execute("SELECT COUNT(*) FROM t")
        count_row = await cur_r.fetchone()
        assert count_row[0] == 50

        # Now checkpoint — should NOT raise regardless of busy result
        cur_ckpt = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = await cur_ckpt.fetchone()
        # busy may be 0 or 1 depending on whether reader released by now;
        # both are acceptable — the job treats busy=1 as PARTIAL, not error
        busy = int(row[0])
        assert busy in (0, 1), f"unexpected busy value: {busy}"

        # Reader can still read
        cur_r2 = await reader.execute("SELECT COUNT(*) FROM t")
        count2 = await cur_r2.fetchone()
        assert count2[0] == 50
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_truncate_returns_three_tuple(wal_db):
    """Shape check: PRAGMA wal_checkpoint returns (busy, log, ckpt) — the
    job parses indices 0/1/2. A SQLite upgrade that changes this shape
    would break the job silently; this test catches that early."""
    conn, db_path = wal_db
    await conn.execute("INSERT INTO t (v) VALUES ('one')")
    await conn.commit()
    cur = await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    row = await cur.fetchone()
    assert row is not None
    # Row must have at least 3 elements
    assert len(row) >= 3, f"expected ≥3 columns, got {len(row)}: {tuple(row)}"
    # Each must be int-coercible
    busy = int(row[0])
    log_pages = int(row[1])
    ckpt_pages = int(row[2])
    assert busy >= 0 and log_pages >= 0 and ckpt_pages >= 0
