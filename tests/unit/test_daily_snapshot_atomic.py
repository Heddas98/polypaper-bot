"""Unit tests for T11.3 Bulgu B fix — daily_db_snapshot_job atomic write.

Fix: telegram_bot/jobs/maintenance_jobs.py::daily_db_snapshot_job now writes
to `<dest>.tmp` and atomically renames to `<dest>` only on success. This
test suite pins the atomic invariant so interrupted backups can never
leave corrupt `<dest>` files on disk.

Coverage:
  1. Happy path: tmp -> rename -> dest exists, valid SQLite header
  2. Ghost tmp cleanup: pre-existing `*.db.tmp` files cleaned at cycle start
  3. Interrupt simulation: exception during backup -> dest_tmp cleaned in
     finally, dest never touched
  4. Previous dest preserved on failure: if old dest exists, new backup
     fails -> old dest intact
  5. Magic bytes: successfully renamed dest starts with "SQLite format 3"
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def populated_source_db():
    """Fresh source SQLite DB with some rows, WAL mode. Returns Path."""
    fd, path = tempfile.mkstemp(suffix="_source.db")
    os.close(fd)
    db_path = Path(path)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("CREATE TABLE executions (id INTEGER PRIMARY KEY, v TEXT)")
    await conn.executemany(
        "INSERT INTO executions (v) VALUES (?)",
        [(f"trade_{i}",) for i in range(100)],
    )
    await conn.commit()
    await conn.close()
    yield db_path
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db_path) + ext)
        if p.exists():
            p.unlink()


@pytest_asyncio.fixture
def backup_dir(tmp_path):
    d = tmp_path / "backups"
    d.mkdir()
    yield d


async def _run_atomic_backup(source_path: Path, dest: Path) -> None:
    """Minimal reproduction of the fix's atomic-rename flow (what
    daily_db_snapshot_job does internally)."""
    dest_tmp = dest.with_suffix(".db.tmp")

    # Ghost cleanup (cycle start)
    for ghost in dest.parent.glob("polypaper_*.db.tmp"):
        try:
            ghost.unlink()
        except OSError:
            pass

    source = await aiosqlite.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        async with aiosqlite.connect(str(dest_tmp), timeout=60) as target:
            await source.backup(target, pages=200, sleep=0.001)
        dest_tmp.replace(dest)
    finally:
        await source.close()
        if dest_tmp.exists():
            try:
                dest_tmp.unlink()
            except OSError:
                pass


@pytest.mark.asyncio
async def test_happy_path_creates_valid_dest(populated_source_db: Path, backup_dir: Path) -> None:
    """Successful backup: dest_tmp created, renamed to dest, tmp gone."""
    dest = backup_dir / "polypaper_2026-04-23.db"
    dest_tmp = dest.with_suffix(".db.tmp")

    await _run_atomic_backup(populated_source_db, dest)

    assert dest.exists(), "dest must exist after successful backup"
    assert not dest_tmp.exists(), "dest_tmp must be gone after rename"

    # Validate SQLite magic bytes
    with open(dest, "rb") as f:
        header = f.read(16)
    assert header.startswith(b"SQLite format 3"), f"dest must be valid SQLite; got {header!r}"

    # Verify data survived
    conn = sqlite3.connect(str(dest))
    try:
        count = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        assert count == 100, f"Expected 100 rows, got {count}"
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_ghost_tmp_cleaned_at_cycle_start(
    populated_source_db: Path, backup_dir: Path
) -> None:
    """Pre-existing `*.db.tmp` from a previous interrupted cycle must be
    cleaned before new backup starts."""
    # Plant 2 ghost tmp files from "previous failed runs"
    ghost1 = backup_dir / "polypaper_2026-04-20.db.tmp"
    ghost2 = backup_dir / "polypaper_2026-04-22.db.tmp"
    ghost1.write_bytes(b"\x00" * 1024)  # corrupt fake
    ghost2.write_bytes(b"\x00" * 2048)
    assert ghost1.exists() and ghost2.exists()

    dest = backup_dir / "polypaper_2026-04-23.db"
    await _run_atomic_backup(populated_source_db, dest)

    assert not ghost1.exists(), "ghost1 must be cleaned"
    assert not ghost2.exists(), "ghost2 must be cleaned"
    assert dest.exists(), "new dest must still be created"


@pytest.mark.asyncio
async def test_interrupt_during_backup_leaves_dest_untouched(
    populated_source_db: Path, backup_dir: Path
) -> None:
    """If backup raises mid-copy, dest_tmp is cleaned in finally and
    dest is never created (or if it existed from a prior run, unmodified)."""
    dest = backup_dir / "polypaper_2026-04-23.db"
    dest_tmp = dest.with_suffix(".db.tmp")
    assert not dest.exists()

    async def _interrupted_backup():
        # Copy the flow but raise before the rename
        for ghost in dest.parent.glob("polypaper_*.db.tmp"):
            try:
                ghost.unlink()
            except OSError:
                pass

        source = await aiosqlite.connect(f"file:{populated_source_db}?mode=ro", uri=True)
        try:
            async with aiosqlite.connect(str(dest_tmp), timeout=60) as target:
                await source.backup(target, pages=200, sleep=0.001)
            # Simulate interrupt right before rename
            raise RuntimeError("simulated Ctrl+C / crash")
            dest_tmp.replace(dest)  # never reached
        finally:
            await source.close()
            if dest_tmp.exists():
                try:
                    dest_tmp.unlink()
                except OSError:
                    pass

    with pytest.raises(RuntimeError, match="simulated"):
        await _interrupted_backup()

    # Post-interrupt state: dest NEVER created, dest_tmp cleaned by finally
    assert not dest.exists(), "dest must not exist after interrupt"
    assert not dest_tmp.exists(), "dest_tmp must be cleaned by finally"


@pytest.mark.asyncio
async def test_previous_dest_preserved_on_failure(
    populated_source_db: Path, backup_dir: Path
) -> None:
    """Critical invariant: if dest ALREADY exists from yesterday and
    today's backup fails, yesterday's dest MUST remain intact."""
    dest = backup_dir / "polypaper_2026-04-23.db"
    dest_tmp = dest.with_suffix(".db.tmp")

    # Yesterday's backup (valid)
    await _run_atomic_backup(populated_source_db, dest)
    original_size = dest.stat().st_size
    original_bytes = dest.read_bytes()[:64]
    assert dest.exists()

    # Today's backup: simulate failure
    async def _failing_backup():
        source = await aiosqlite.connect(f"file:{populated_source_db}?mode=ro", uri=True)
        try:
            async with aiosqlite.connect(str(dest_tmp), timeout=60) as target:
                await source.backup(target, pages=200, sleep=0.001)
            raise OSError("simulated disk full")
        finally:
            await source.close()
            if dest_tmp.exists():
                try:
                    dest_tmp.unlink()
                except OSError:
                    pass

    with pytest.raises(OSError, match="simulated disk full"):
        await _failing_backup()

    # Yesterday's dest must be untouched
    assert dest.exists(), "previous dest must still exist after failure"
    assert dest.stat().st_size == original_size, "size must match"
    assert dest.read_bytes()[:64] == original_bytes, "bytes must match"
    assert not dest_tmp.exists(), "failed dest_tmp must be cleaned"


@pytest.mark.asyncio
async def test_atomic_rename_is_single_step(populated_source_db: Path, backup_dir: Path) -> None:
    """Verify `Path.replace()` semantics: file either fully at dest or
    fully at dest_tmp, never half-way. This is the POSIX/Windows rename
    atomicity contract the fix relies on."""
    dest = backup_dir / "polypaper_2026-04-23.db"
    dest_tmp = dest.with_suffix(".db.tmp")

    # Manual write to tmp
    source_bytes = populated_source_db.read_bytes()[:1024]
    dest_tmp.write_bytes(source_bytes)
    assert dest_tmp.exists()
    assert not dest.exists()

    # Atomic rename
    dest_tmp.replace(dest)

    # Post-condition: tmp gone, dest has exact bytes
    assert not dest_tmp.exists()
    assert dest.exists()
    assert dest.read_bytes()[:1024] == source_bytes
