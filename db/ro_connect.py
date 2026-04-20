"""
Phase 82e Sprint 2.2 — Read-only SQLite Connection Hardening
============================================================
A unified helper that opens a **read-only** sqlite3 connection to the
live PolyPaper DB with:

  1. Retry on `sqlite3.OperationalError` (lock contention, disk I/O).
  2. Exponential backoff capped by max elapsed time.
  3. Two-stage fallback:
     a) `file:X?mode=ro`         — normal WAL-safe read
     b) `file:X?mode=ro&immutable=1` — frozen snapshot, bypasses WAL
        locking entirely (the DB file must not change during the conn
        lifetime; only safe for short-lived analytical reads)
  4. Optional third-stage fallback: copy the DB to a temp file and read
     from there. Useful when all else fails during VACUUM or massive
     WAL checkpoints.

WHY THIS EXISTS:
  - backtest/archive_reader.py reads the live DB concurrently with the
    bot writer. Busy-timeout alone (30s) isn't enough during long VACUUM
    or ARCHIVE operations.
  - maintenance_jobs.backup_job opens its own ro connection; a transient
    lock caused silent backup failures before.
  - scripts/shadow_monitor_47f7.py already did this manually — now
    consolidated into a shared helper.

USAGE:
    from db.ro_connect import open_ro
    with open_ro("polypaper.db") as conn:
        cur = conn.execute("SELECT ...")

ENV:
    RO_CONNECT_ATTEMPTS         default 4
    RO_CONNECT_BUSY_TIMEOUT_MS  default 30000
    RO_CONNECT_BACKOFF_BASE_S   default 0.5
    RO_CONNECT_BACKOFF_MAX_S    default 8.0
    RO_CONNECT_COPY_FALLBACK    default "1" (enabled)
"""
from __future__ import annotations

import contextlib
import logging
import os
import random
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional, Iterator

logger = logging.getLogger("polypaper.db.ro_connect")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key, "1" if default else "0").strip().lower()
    return val in ("1", "true", "yes", "on")


def _try_connect(uri: str, busy_timeout_ms: int,
                 connect_timeout_s: float) -> sqlite3.Connection:
    """Single connect attempt. Raises OperationalError on failure."""
    conn = sqlite3.connect(uri, uri=True, timeout=connect_timeout_s)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Quick sanity check — a trivial read forces the DB to be
        # actually openable, not just the file descriptor.
        conn.execute("SELECT 1").fetchone()
    except Exception:
        conn.close()
        raise
    return conn


def open_ro_connection(
    db_path: str | Path,
    attempts: Optional[int] = None,
    busy_timeout_ms: Optional[int] = None,
    connect_timeout_s: float = 10.0,
    backoff_base_s: Optional[float] = None,
    backoff_max_s: Optional[float] = None,
    allow_copy_fallback: Optional[bool] = None,
) -> sqlite3.Connection:
    """Open a read-only sqlite3 connection with retry + fallback.

    Strategy (in order):
      1. `file:X?mode=ro`                   — tries `attempts` times with
         exponential backoff on OperationalError
      2. `file:X?mode=ro&immutable=1`       — frozen snapshot, 1 attempt
      3. Copy DB to temp file, open normal  — 1 attempt (opt-out via env)

    Raises:
      sqlite3.OperationalError if all strategies fail.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    attempts = attempts or _env_int("RO_CONNECT_ATTEMPTS", 4)
    busy_timeout_ms = busy_timeout_ms or _env_int(
        "RO_CONNECT_BUSY_TIMEOUT_MS", 30000)
    backoff_base_s = backoff_base_s or _env_float(
        "RO_CONNECT_BACKOFF_BASE_S", 0.5)
    backoff_max_s = backoff_max_s or _env_float(
        "RO_CONNECT_BACKOFF_MAX_S", 8.0)
    if allow_copy_fallback is None:
        allow_copy_fallback = _env_bool("RO_CONNECT_COPY_FALLBACK", True)

    last_err: Optional[Exception] = None

    # ── Stage 1: normal read-only URI with retries ────────────────────
    uri = f"file:{db_path.as_posix()}?mode=ro"
    for i in range(attempts):
        try:
            conn = _try_connect(uri, busy_timeout_ms, connect_timeout_s)
            if i > 0:
                logger.info(
                    f"ro_connect: succeeded on attempt {i + 1}/{attempts} "
                    f"({db_path.name})")
            return conn
        except sqlite3.OperationalError as e:
            last_err = e
            # Exponential backoff with jitter
            wait = min(backoff_base_s * (2 ** i), backoff_max_s)
            wait += random.uniform(0, wait * 0.1)
            logger.warning(
                f"ro_connect: OperationalError attempt {i + 1}/{attempts}"
                f" ({e}); retry in {wait:.2f}s")
            time.sleep(wait)
        except sqlite3.DatabaseError as e:
            # Corruption — don't retry, escalate
            last_err = e
            logger.error(f"ro_connect: DatabaseError on {db_path.name}: {e}")
            break

    # ── Stage 2: immutable=1 (WAL bypass, frozen snapshot) ────────────
    # This ignores WAL entirely. Safe for short reads where we accept a
    # slightly stale view in exchange for zero lock contention.
    try:
        uri_imm = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
        conn = _try_connect(uri_imm, busy_timeout_ms, connect_timeout_s)
        logger.warning(
            f"ro_connect: fell back to immutable=1 snapshot for {db_path.name}")
        return conn
    except Exception as e:
        last_err = e
        logger.warning(f"ro_connect: immutable=1 also failed: {e}")

    # ── Stage 3: copy-and-read (last resort) ──────────────────────────
    if allow_copy_fallback:
        try:
            tmp = Path(tempfile.gettempdir()) / (
                f"polypaper_ro_{os.getpid()}_{int(time.time())}.db")
            # Copy main DB (use copy2 — preserves mtime). WAL file is
            # skipped: immutable snapshot in tmp WITH no WAL is effectively
            # pre-WAL state. We also copy -wal and -shm if present so the
            # copy is readable with WAL journal mode.
            shutil.copy2(str(db_path), str(tmp))
            for suf in ("-wal", "-shm"):
                side = db_path.with_name(db_path.name + suf)
                if side.exists():
                    try:
                        shutil.copy2(str(side), str(tmp) + suf)
                    except Exception as _ce:
                        logger.debug(f"copy {suf} failed: {_ce}")
            conn = _try_connect(
                f"file:{tmp.as_posix()}?mode=ro",
                busy_timeout_ms, connect_timeout_s)
            # Attach a finalizer so the temp is cleaned when the caller
            # closes the connection. We stash the path on the object.
            conn._ro_tmp_copy = str(tmp)  # type: ignore[attr-defined]
            logger.warning(
                f"ro_connect: fell back to tmp-copy for {db_path.name} "
                f"→ {tmp.name}")
            return conn
        except Exception as e:
            last_err = e
            logger.error(f"ro_connect: copy-fallback failed: {e}")

    # All strategies exhausted
    raise sqlite3.OperationalError(
        f"open_ro_connection: all strategies failed for {db_path.name}. "
        f"Last error: {last_err}"
    )


@contextlib.contextmanager
def open_ro(
    db_path: str | Path,
    **kwargs,
) -> Iterator[sqlite3.Connection]:
    """Context manager variant that cleans up temp-copy if one was used."""
    conn = open_ro_connection(db_path, **kwargs)
    try:
        yield conn
    finally:
        tmp = getattr(conn, "_ro_tmp_copy", None)
        try:
            conn.close()
        except Exception:
            pass
        # Cleanup tmp copy (main + sidecars) if stage-3 was used
        if tmp:
            for p in (Path(tmp), Path(tmp + "-wal"), Path(tmp + "-shm")):
                try:
                    if p.exists():
                        p.unlink()
                except Exception as _e:
                    logger.debug(f"tmp cleanup {p.name}: {_e}")


# ══════════════════════════════════════════════════════════════════════
# Async variant for aiosqlite callers (maintenance_jobs backup, etc.)
# ══════════════════════════════════════════════════════════════════════
async def open_ro_aiosqlite(
    db_path: str | Path,
    attempts: Optional[int] = None,
    busy_timeout_ms: Optional[int] = None,
    connect_timeout_s: float = 60.0,
    backoff_base_s: Optional[float] = None,
    backoff_max_s: Optional[float] = None,
):
    """Open an aiosqlite read-only connection with retry + fallback.

    Returns an already-connected `aiosqlite.Connection`. The caller is
    responsible for closing it (use `async with` via the connection's
    own context manager).

    Fallback order mirrors the sync helper:
      1. `file:X?mode=ro`          — retries `attempts` times
      2. `file:X?mode=ro&immutable=1` — frozen snapshot, 1 attempt

    Stage-3 (copy-fallback) is NOT included here: aiosqlite backup()
    already handles long-running streaming backups natively, so copy
    fallback would duplicate work and risk disk pressure.
    """
    import asyncio
    import aiosqlite

    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    attempts = attempts or _env_int("RO_CONNECT_ATTEMPTS", 4)
    busy_timeout_ms = busy_timeout_ms or _env_int(
        "RO_CONNECT_BUSY_TIMEOUT_MS", 30000)
    backoff_base_s = backoff_base_s or _env_float(
        "RO_CONNECT_BACKOFF_BASE_S", 0.5)
    backoff_max_s = backoff_max_s or _env_float(
        "RO_CONNECT_BACKOFF_MAX_S", 8.0)

    last_err: Optional[Exception] = None

    async def _try(uri: str) -> "aiosqlite.Connection":
        conn = await aiosqlite.connect(uri, uri=True, timeout=connect_timeout_s)
        try:
            await conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
            cur = await conn.execute("SELECT 1")
            await cur.fetchone()
            await cur.close()
        except Exception:
            await conn.close()
            raise
        return conn

    # Stage 1: normal mode=ro with retries
    uri = f"file:{db_path.as_posix()}?mode=ro"
    for i in range(attempts):
        try:
            return await _try(uri)
        except Exception as e:
            last_err = e
            wait = min(backoff_base_s * (2 ** i), backoff_max_s)
            wait += random.uniform(0, wait * 0.1)
            logger.warning(
                f"open_ro_aiosqlite: attempt {i + 1}/{attempts} failed "
                f"({type(e).__name__}: {e}); retry in {wait:.2f}s")
            await asyncio.sleep(wait)

    # Stage 2: immutable=1 fallback
    try:
        uri_imm = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
        conn = await _try(uri_imm)
        logger.warning(
            f"open_ro_aiosqlite: fell back to immutable=1 for {db_path.name}")
        return conn
    except Exception as e:
        last_err = e

    raise sqlite3.OperationalError(
        f"open_ro_aiosqlite: all retries exhausted for {db_path.name}. "
        f"Last error: {last_err}")
