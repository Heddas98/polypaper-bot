"""
Phase 59 — DB Archive Job (DB-01b)
Phase 82e Sprint B.1 — Async refactor + Zstd L9 + streaming ParquetWriter
================================================================
Nightly scheduled job that runs ob_archive.py logic at 03:00 UTC.
Moves old ob_snapshots to parquet files for long-term storage.

Phase 82e Sprint B.1 CHANGES:
  1. Heavy I/O moved to asyncio.to_thread() — event loop no longer
     blocks for ~62s. Telegram heartbeat + trading continues during
     archive.
  2. Streaming pa.ParquetWriter: read chunk → write chunk → free mem.
     No more pd.concat of 421K rows. RAM footprint: ~500 MB → ~80 MB.
  3. Compression: Snappy → Zstd L9 (lossless, 2.25x smaller).
     Benchmarked: 126 MB → 56 MB on real ob_snapshots parquet.
     Bit-perfect reversible — no data quality loss.
  4. Chunked DELETE (50K rows per commit) — avoids 62s DB lock.
  5. ENV-tunable compression + row group + chunk sizes.

Also provides /db_archive Telegram command for manual admin triggering.

Env:
  DB_ARCHIVE_ENABLED            (default "1")
  DB_ARCHIVE_DAYS               (default 7)      # Archive data older than N days
  DB_ARCHIVE_VACUUM             (default "0")    # Run VACUUM after archive (unsafe on large DB)
  DB_ARCHIVE_TIME_UTC           (default "03:00") # Nightly run time in HH:MM UTC
  DB_ARCHIVE_FIRST_SEC          (default 600)    # Delay from startup (10 min)
  DB_ARCHIVE_NOTIFY             (default "1")    # Notify admin on completion
  DB_ARCHIVE_COMPRESSION        (default "zstd") # snappy|zstd|gzip|brotli
  DB_ARCHIVE_COMPRESSION_LEVEL  (default 9)      # 1-22 for zstd, 1-9 for gzip
  DB_ARCHIVE_CHUNK_SIZE         (default 100000) # Read chunk size
  DB_ARCHIVE_DELETE_BATCH       (default 50000)  # Rows per DELETE commit
  DB_ARCHIVE_ROW_GROUP_SIZE     (default 50000)  # Parquet row group size
"""
from __future__ import annotations

import asyncio
import os
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, Application

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.db_archive")

# Project root & DB path
ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data_store" / "polypaper.db"
ARCHIVE_DIR = ROOT / "data" / "archive"


def _env_int(key: str, default: int) -> int:
    """Parse integer env var."""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = True) -> bool:
    """Parse boolean env var (1/true/yes/on = True)."""
    return os.getenv(key, "1" if default else "0").strip() in ("1", "true", "True", "yes", "on")


def _env_str(key: str, default: str) -> str:
    """Get string env var."""
    return os.getenv(key, default).strip()


def _get_db_size_mb() -> float:
    """Return DB file size in MB."""
    try:
        return DB_PATH.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def _count_rows(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    """Count rows in a table with optional WHERE clause."""
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        return conn.execute(sql).fetchone()[0]
    except Exception as e:
        logger.warning(f"count_rows failed for {table}: {e}")
        return 0


def _archive_to_parquet_sync(cutoff_ms: int, dry_run: bool = False) -> dict:
    """Phase 82e Sprint B.1 — Synchronous archive (runs inside asyncio.to_thread).

    Streams ob_snapshots → Parquet (Zstd L9) → chunked DELETE.

    Returns:
        dict with keys: archived, parquet_size_mb, parquet_path, remaining, error
    """
    result = {
        "archived": 0,
        "parquet_size_mb": 0.0,
        "parquet_path": None,
        "remaining": 0,
        "error": None,
    }

    try:
        import pandas as pd
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:
        result["error"] = f"pandas/pyarrow missing: {e}. Run: pip install pandas pyarrow --break-system-packages"
        logger.error(result["error"])
        return result

    # Config from ENV
    compression = _env_str("DB_ARCHIVE_COMPRESSION", "zstd").lower()
    compression_level = _env_int("DB_ARCHIVE_COMPRESSION_LEVEL", 9)
    chunk_size = _env_int("DB_ARCHIVE_CHUNK_SIZE", 100_000)
    delete_batch = _env_int("DB_ARCHIVE_DELETE_BATCH", 50_000)
    row_group_size = _env_int("DB_ARCHIVE_ROW_GROUP_SIZE", 50_000)

    # Snappy does not accept compression_level kw — guard
    parquet_codec_kw = {"compression": compression}
    if compression not in ("snappy", "uncompressed", "none"):
        parquet_codec_kw["compression_level"] = compression_level

    # New connection for this thread (sqlite3 connections are not thread-safe)
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        result["error"] = f"DB connect failed: {e}"
        logger.error(result["error"])
        return result

    try:
        # Verify table exists
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "ob_snapshots" not in tables:
            result["error"] = "ob_snapshots table not found"
            logger.warning(result["error"])
            return result

        # Count rows to archive
        where = f"ts_ms < {int(cutoff_ms)}"
        total = _count_rows(conn, "ob_snapshots", where)
        cutoff_iso = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc).isoformat()

        if total == 0:
            logger.info(f"No rows to archive (ts_ms >= {cutoff_ms} / {cutoff_iso}).")
            result["remaining"] = _count_rows(conn, "ob_snapshots")
            return result

        logger.info(f"Found {total:,} rows older than ts_ms={cutoff_ms} ({cutoff_iso})")

        if dry_run:
            logger.info(f"[DRY RUN] Would archive {total:,} rows. No changes.")
            result["archived"] = total
            return result

        # Ensure archive directory exists
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # Output path
        date_tag = cutoff_iso[:10]
        ts = datetime.now().strftime("%H%M%S")
        out_path = ARCHIVE_DIR / f"ob_snapshots_{date_tag}_{ts}.parquet"
        result["parquet_path"] = str(out_path)

        logger.info(
            f"Streaming to {out_path.name} "
            f"(codec={compression} L{compression_level}, "
            f"chunk={chunk_size:,}, row_group={row_group_size:,})"
        )

        # Streaming write: open ParquetWriter with schema from first chunk
        t0 = time.monotonic()
        writer: Optional[pq.ParquetWriter] = None
        archived = 0
        batch = 0
        offset = 0

        try:
            while offset < total:
                sql = (
                    f"SELECT * FROM ob_snapshots "
                    f"WHERE ts_ms < {int(cutoff_ms)} "
                    f"ORDER BY ts_ms "
                    f"LIMIT {chunk_size} OFFSET {offset}"
                )
                df_chunk = pd.read_sql_query(sql, conn)
                if df_chunk.empty:
                    break

                # Convert to Arrow Table, infer schema from first chunk
                table = pa.Table.from_pandas(df_chunk, preserve_index=False)

                if writer is None:
                    writer = pq.ParquetWriter(
                        str(out_path),
                        table.schema,
                        **parquet_codec_kw,
                        use_dictionary=True,
                        write_statistics=True,
                    )

                writer.write_table(table, row_group_size=row_group_size)

                archived += len(df_chunk)
                offset += len(df_chunk)
                batch += 1
                if batch % 5 == 0 or batch == 1:
                    elapsed = time.monotonic() - t0
                    rate = archived / max(elapsed, 0.01)
                    logger.info(
                        f"  Chunk {batch}: {len(df_chunk):,} rows "
                        f"(total {archived:,}/{total:,}, {rate:.0f} rows/s)"
                    )
        finally:
            if writer is not None:
                writer.close()

        if archived == 0:
            logger.warning("No data streamed — aborting.")
            if out_path.exists():
                out_path.unlink()
            return result

        parquet_size_mb = out_path.stat().st_size / (1024 * 1024)
        result["archived"] = archived
        result["parquet_size_mb"] = parquet_size_mb
        elapsed_write = time.monotonic() - t0
        logger.info(
            f"Parquet written: {out_path.name} "
            f"({parquet_size_mb:.1f} MB, {archived:,} rows, {elapsed_write:.1f}s)"
        )

        # Chunked DELETE — avoids long lock on big tables
        logger.info(
            f"Chunked DELETE starting ({archived:,} rows, batch={delete_batch:,})..."
        )
        t0 = time.monotonic()
        deleted_total = 0
        del_batch_n = 0
        while True:
            # Delete batch. Uses rowid subquery to avoid scanning entire table
            # each iteration.
            cur = conn.execute(
                "DELETE FROM ob_snapshots "
                "WHERE rowid IN ("
                "  SELECT rowid FROM ob_snapshots "
                "  WHERE ts_ms < ? "
                "  LIMIT ?"
                ")",
                (int(cutoff_ms), int(delete_batch)),
            )
            conn.commit()
            n = cur.rowcount
            if n <= 0:
                break
            deleted_total += n
            del_batch_n += 1
            if del_batch_n % 5 == 0 or del_batch_n == 1:
                logger.info(
                    f"  DELETE batch {del_batch_n}: {n:,} rows "
                    f"(total {deleted_total:,}/{archived:,})"
                )
            # Yield to other connections (writers) between batches.
            # Tiny sleep gives market_recorder INSERT a chance.
            time.sleep(0.02)

        elapsed_del = time.monotonic() - t0
        logger.info(
            f"DELETE done: {deleted_total:,} rows in {elapsed_del:.1f}s "
            f"({deleted_total / max(elapsed_del, 0.01):.0f} rows/s)"
        )

        result["remaining"] = _count_rows(conn, "ob_snapshots")
        logger.info(f"Remaining in ob_snapshots: {result['remaining']:,} rows")

    except Exception as e:
        result["error"] = f"archive failed: {e}"
        logger.error(result["error"], exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


async def db_archive_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 82e Sprint B.1 — Nightly archive job (async wrapper).

    Heavy sync I/O is offloaded to asyncio.to_thread() so the event loop
    keeps processing Telegram + trading events during the 30-60s archive.
    """
    enabled = _env_bool("DB_ARCHIVE_ENABLED", True)
    if not enabled:
        logger.debug("db_archive_job disabled (DB_ARCHIVE_ENABLED=0)")
        return

    logger.info("=" * 60)
    logger.info("🗂️ DB_ARCHIVE_JOB starting (Phase 82e Sprint B.1 — async)...")

    days = _env_int("DB_ARCHIVE_DAYS", 7)
    do_vacuum = _env_bool("DB_ARCHIVE_VACUUM", False)
    do_notify = _env_bool("DB_ARCHIVE_NOTIFY", True)

    if not DB_PATH.exists():
        logger.error(f"Database not found: {DB_PATH}")
        return

    db_before = _get_db_size_mb()
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"DB size before: {db_before:.1f} MB")

    # Calculate cutoff
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    logger.info(
        f"Archive cutoff: ts_ms={cutoff_ms} ({cutoff_str}, older than {days} days)"
    )

    # Offload the heavy sync work to a thread so event loop stays responsive.
    t_start = time.monotonic()
    try:
        result = await asyncio.to_thread(
            _archive_to_parquet_sync, cutoff_ms, False
        )
    except Exception as e:
        logger.error(f"to_thread archive failed: {e}", exc_info=True)
        return
    elapsed = time.monotonic() - t_start
    logger.info(f"Archive thread finished in {elapsed:.1f}s")

    archived = int(result.get("archived") or 0)
    parquet_size_mb = float(result.get("parquet_size_mb") or 0.0)
    parquet_path = result.get("parquet_path")
    error = result.get("error")

    # Optional VACUUM — also moved to thread.
    db_after = _get_db_size_mb()
    if do_vacuum and archived > 0 and not error:
        logger.warning(
            "VACUUM requested — this locks DB for 10+ minutes on large DBs. "
            "Running in thread (DB_ARCHIVE_VACUUM=1)."
        )
        try:
            await asyncio.to_thread(_run_vacuum_sync)
            db_after = _get_db_size_mb()
            logger.info(
                f"VACUUM done. DB size: {db_before:.1f} MB → "
                f"{db_after:.1f} MB (saved {db_before - db_after:.1f} MB)"
            )
        except Exception as e:
            logger.error(f"VACUUM failed: {e}")
    elif archived > 0 and not do_vacuum:
        logger.info(
            "Skipping VACUUM (DB_ARCHIVE_VACUUM=0). Freed pages will be "
            "reused by future INSERTs; DB file size will NOT shrink. To "
            "shrink, stop bot and run vacuum_db.bat manually."
        )

    # Summary
    logger.info("=" * 60)
    logger.info("📦 ARCHIVE SUMMARY:")
    logger.info(f"  Rows archived: {archived:,}")
    logger.info(f"  DB size: {db_before:.1f} MB → {db_after:.1f} MB")
    if parquet_size_mb > 0:
        logger.info(f"  Parquet size: {parquet_size_mb:.1f} MB")
        ratio = (parquet_size_mb / (db_before - db_after)) if (db_before - db_after) > 0 else 0
        if ratio > 0:
            logger.info(f"  Compression ratio vs freed DB: {ratio*100:.1f}%")
    if parquet_path:
        logger.info(f"  Parquet path: {parquet_path}")
    if error:
        logger.error(f"  ERROR: {error}")
    logger.info(f"  Total elapsed: {elapsed:.1f}s")
    logger.info("=" * 60)

    # Notify admin if enabled
    if do_notify and (archived > 0 or error):
        admin_id = resolve_admin_chat_id()
        if admin_id and context.bot:
            try:
                if error:
                    msg = (
                        f"⚠️ <b>Nightly Archive Failed</b>\n\n"
                        f"<b>Error:</b> <code>{error[:200]}</code>"
                    )
                else:
                    compression = _env_str("DB_ARCHIVE_COMPRESSION", "zstd").upper()
                    level = _env_int("DB_ARCHIVE_COMPRESSION_LEVEL", 9)
                    msg = (
                        f"📦 <b>Nightly Archive Complete</b>\n\n"
                        f"<b>Rows archived:</b> {archived:,}\n"
                        f"<b>DB size:</b> {db_before:.1f} → {db_after:.1f} MB\n"
                        f"<b>Freed:</b> {db_before - db_after:.1f} MB\n"
                        f"<b>Parquet:</b> {parquet_size_mb:.1f} MB ({compression} L{level})\n"
                        f"<b>Elapsed:</b> {elapsed:.1f}s (non-blocking)\n"
                    )
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=msg,
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.warning(f"Could not notify admin: {e}")


def _run_vacuum_sync() -> None:
    """Run VACUUM in a dedicated connection (sync, called via to_thread)."""
    conn = sqlite3.connect(str(DB_PATH), timeout=600)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=600000")
        conn.execute("VACUUM")
    finally:
        conn.close()


async def db_archive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: /db_archive — trigger archive job manually."""
    admin_id = os.getenv("ADMIN_TELEGRAM_ID")
    caller_id = update.effective_user.id if update.effective_user else None

    if not admin_id or str(caller_id) != str(admin_id):
        await update.message.reply_text(
            "🔒 Yalnız admin tarafından kullanılabilir.",
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "🔄 OB archive tetikleniyor (async, streaming Zstd)...\n"
        "<i>Bot + trading çalışmaya devam eder.</i>",
        parse_mode="HTML"
    )

    try:
        await db_archive_job(context)
        await update.message.reply_text(
            "✅ Archive job tamamlandı. Detaylar için /changelog veya logs'a bakın.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"db_archive_command failed: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Archive başarısız: <code>{str(e)[:100]}</code>",
            parse_mode="HTML"
        )


def setup_db_archive_job(app: Application) -> None:
    """Register nightly archive job with bot's JobQueue.

    Runs at 03:00 UTC daily (or configurable time via DB_ARCHIVE_TIME_UTC).
    """
    enabled = _env_bool("DB_ARCHIVE_ENABLED", True)
    if not enabled:
        logger.info("db_archive_job disabled (DB_ARCHIVE_ENABLED=0)")
        return

    time_str = _env_str("DB_ARCHIVE_TIME_UTC", "03:00")
    first_sec = _env_int("DB_ARCHIVE_FIRST_SEC", 600)

    try:
        hour, minute = map(int, time_str.split(":"))
        logger.info(
            f"📅 Scheduling db_archive_job: {hour:02d}:{minute:02d} UTC daily "
            f"(first run in {first_sec}s)"
        )

        # Register job
        app.job_queue.run_daily(
            db_archive_job,
            time=__import__("datetime").time(
                hour=hour, minute=minute, tzinfo=__import__("datetime").timezone.utc
            ),
            name="db_archive"
        )

        # First run after startup delay
        app.job_queue.run_once(
            db_archive_job,
            when=first_sec,
            name="db_archive_first"
        )
    except Exception as e:
        logger.error(f"Failed to setup db_archive_job: {e}")
