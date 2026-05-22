"""
Phase 47f.7+ DB Retention Job  (Phase 57: Archive-first, no delete)
=====================================================================
Runs daily. NOW defaults to REPORT-ONLY mode (no deletion).

Phase 57 change: DB_RETENTION_MODE controls behaviour:
  "archive"  (DEFAULT) — move old rows to archive DB, then delete from main
  "report"   — count old rows and report sizes, but delete NOTHING
  "delete"   — legacy mode: prune old rows and VACUUM (Phase 47f.7 original)

When mode="archive", rows older than the threshold are INSERT'd into
a monthly archive database (data_store/archive_YYYY_MM.db) before
being removed from the main DB. This preserves ALL data for historical
backtests while keeping the live DB lean.

All thresholds are env-driven:
  DB_RETENTION_MODE                (default "report")  # Phase 57: safe default
  DB_RETENTION_OB_SNAPSHOTS_DAYS  (default 7)
  DB_RETENTION_OB_DELTAS_DAYS     (default 14)  # 2026-05-22 fan/disk
  DB_RETENTION_OB_TRADES_DAYS     (default 14)
  DB_RETENTION_ODDS_HISTORY_DAYS  (default 14)
  DB_RETENTION_CANDLES_POLY_DAYS  (default 30)
  DB_RETENTION_CANDLES_EXT_DAYS   (default 30)
  DB_RETENTION_VACUUM_ENABLED     (default "1")
  DB_RETENTION_NOTIFY             (default "1")
  DB_RETENTION_INTERVAL_SEC       (default 86400)
  DB_RETENTION_FIRST_SEC          (default 900)  # 15 min after startup
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Optional

import aiosqlite
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.db_retention")

# ── Phase 57: Archive DB helpers ──

ARCHIVE_DIR = os.path.join("data_store", "archives")


def _archive_db_path(table: str, cutoff_days: int) -> str:
    """Monthly archive DB: data_store/archives/archive_YYYY_MM.db"""
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    now = datetime.now(UTC)
    return os.path.join(ARCHIVE_DIR, f"archive_{now.strftime('%Y_%m')}.db")


async def _ensure_archive_table(archive_conn, table: str, main_db):
    """Copy table schema from main DB to archive DB if it doesn't exist."""
    try:
        cur = await main_db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        row = await cur.fetchone()
        if row and row[0]:
            create_sql = row[0].replace(
                f"CREATE TABLE {table}",
                f"CREATE TABLE IF NOT EXISTS {table}",
            )
            await archive_conn.execute(create_sql)
            await archive_conn.commit()
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT sql FROM
        # sqlite_master + CREATE TABLE IF NOT EXISTS surface aiosqlite.Error
        # (OperationalError on locked archive). Archive is best-effort;
        # failure falls through to caller.
        logger.warning(f"[archive] schema copy for {table} failed: " f"{type(e).__name__}: {e}")


async def _archive_rows(db, table: str, where: str, label: str) -> int:
    """Move rows matching WHERE to monthly archive DB, then delete from main.
    Returns count of archived+deleted rows."""
    try:
        # Count first
        cur = await db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        row = await cur.fetchone()
        estimated = int(row[0]) if row else 0
        if estimated == 0:
            logger.info(f"[archive] {label}: nothing to archive")
            return 0

        archive_path = _archive_db_path(table, 0)
        async with aiosqlite.connect(archive_path) as archive_conn:
            await archive_conn.execute("PRAGMA journal_mode=WAL")
            await _ensure_archive_table(archive_conn, table, db)

            # Chunked archive: read+insert+delete in 10k batches
            total = 0
            while True:
                cur = await db.conn.execute(f"SELECT * FROM {table} WHERE {where} LIMIT 10000")
                rows = await cur.fetchall()
                if not rows:
                    break

                # Get column count for placeholder
                placeholders = ",".join(["?"] * len(rows[0]))
                await archive_conn.executemany(
                    f"INSERT OR IGNORE INTO {table} VALUES ({placeholders})",
                    rows,
                )
                await archive_conn.commit()

                # Delete the archived rows from main DB
                ids = [r[0] for r in rows]  # id column (first)
                id_list = ",".join(str(i) for i in ids)
                await db.conn.execute(f"DELETE FROM {table} WHERE id IN ({id_list})")
                await db.conn.commit()
                total += len(rows)

                if len(rows) < 10000:
                    break

        logger.info(f"[archive] {label}: archived {total:,} rows → {archive_path}")
        return total
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): archive function outer wrapper intentionally
        # wide. Multi-step: open archive DB + copy schema + chunked SELECT +
        # executemany INSERT + DELETE on main. Heterogeneous failure surface.
        # 0 return signals caller "did nothing"; logger.exception preserves
        # full trace. T7.6 job-safety pattern.
        logger.exception(f"[archive] {label}: failed — {e}")
        return 0


async def _count_old(db, table: str, where: str, label: str) -> int:
    """Report-only mode: count rows that WOULD be deleted."""
    try:
        cur = await db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        row = await cur.fetchone()
        count = int(row[0]) if row else 0
        logger.info(f"[retention-report] {label}: {count:,} rows older than threshold")
        return count
    except (aiosqlite.Error, IndexError, TypeError, ValueError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT COUNT(*)
        # + fetchone + int() coercion. aiosqlite.Error (missing table),
        # IndexError (row None[0]), ValueError (non-int COUNT result).
        logger.warning(f"[retention-report] {label}: count failed: " f"{type(e).__name__}: {e}")
        return 0


def _days(env_key: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(env_key, str(default))))
    except (ValueError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. int() coercion
        # of ENV surfaces ValueError + TypeError (None). Fallback to default
        # on malformed retention-day ENV.
        return default


def _iso_cutoff(days: int) -> str:
    """ISO8601 UTC timestamp N days ago."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ms_cutoff(days: int) -> int:
    """Unix epoch milliseconds N days ago."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    return int(cutoff.timestamp() * 1000)


async def _delete_old(db, table: str, where: str, label: str) -> int:
    """Run DELETE in small chunks to avoid WAL bloat. Returns rows deleted."""
    total = 0
    try:
        # Count first so we can report even if DELETE goes in chunks.
        cur = await db.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")
        row = await cur.fetchone()
        estimated = int(row[0]) if row else 0
        if estimated == 0:
            logger.info(f"[retention] {label}: nothing to delete")
            return 0

        # Chunked delete — SQLite LIMIT in DELETE requires sqlite ≥3.7.11
        # and build-time ENABLE_UPDATE_DELETE_LIMIT, which Windows builds
        # may not have. Fall back to single DELETE on failure.
        try:
            while True:
                cur = await db.conn.execute(
                    f"DELETE FROM {table} WHERE rowid IN "
                    f"(SELECT rowid FROM {table} WHERE {where} LIMIT 20000)"
                )
                await db.conn.commit()
                deleted = cur.rowcount or 0
                total += deleted
                if deleted < 20000:
                    break
        except aiosqlite.Error as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Chunked DELETE
            # fallback — older SQLite builds raise OperationalError on LIMIT
            # in DELETE (ENABLE_UPDATE_DELETE_LIMIT off). Fall back to single
            # DELETE which is always supported.
            logger.warning(
                f"[retention] {label}: chunked delete failed "
                f"({type(e).__name__}: {e}), falling back to single DELETE"
            )
            cur = await db.conn.execute(f"DELETE FROM {table} WHERE {where}")
            await db.conn.commit()
            total = cur.rowcount or estimated

        logger.info(f"[retention] {label}: deleted {total:,} rows")
        return total
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outer delete wrapper intentionally wide.
        # Mixed SQL + row coercion + fallback chain above. 0 return signals
        # caller "did nothing"; logger.exception preserves full trace.
        logger.exception(f"[retention] {label}: failed — {e}")
        return 0


async def db_retention_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force_notify: Optional[bool] = None,
) -> dict:
    """
    Execute retention pass. Returns summary dict {table: count}.
    Safe to call manually (from /db_cleanup command).

    Phase 57: Three modes via DB_RETENTION_MODE env:
      "report"  (default) — count old rows, report sizes, delete NOTHING
      "archive" — move old rows to archive DB, then delete from main
      "delete"  — legacy: prune old rows and VACUUM
    """
    db = context.application.bot_data.get("db")
    if db is None:
        logger.warning("[retention] db missing — skip")
        return {}

    mode = os.getenv("DB_RETENTION_MODE", "report").strip().lower()
    if mode not in ("report", "archive", "delete"):
        logger.warning(f"[retention] unknown mode '{mode}', defaulting to 'report'")
        mode = "report"

    logger.info(f"[retention] mode={mode}")

    summary: dict[str, int] = {}

    # Thresholds
    ob_snap_days = _days("DB_RETENTION_OB_SNAPSHOTS_DAYS", 7)
    ob_deltas_days = _days("DB_RETENTION_OB_DELTAS_DAYS", 14)
    ob_trade_days = _days("DB_RETENTION_OB_TRADES_DAYS", 14)
    odds_days = _days("DB_RETENTION_ODDS_HISTORY_DAYS", 14)
    candles_poly_days = _days("DB_RETENTION_CANDLES_POLY_DAYS", 30)
    candles_ext_days = _days("DB_RETENTION_CANDLES_EXT_DAYS", 30)

    # Pre-retention DB size
    try:
        db_path = "data_store/polypaper.db"
        from pathlib import Path

        pre_size_mb = Path(db_path).stat().st_size / (1024 * 1024)
    except OSError:
        # T11.8-B (2026-04-24): narrow from bare Exception. Path.stat()
        # raises OSError (FileNotFoundError/PermissionError subclass) when
        # DB missing or permission denied. 0.0 is a safe sentinel for the
        # size-delta report.
        pre_size_mb = 0.0

    t0 = datetime.utcnow()

    # Pick the action function based on mode
    if mode == "report":
        action = _count_old
    elif mode == "archive":
        action = _archive_rows
    else:  # "delete"
        action = _delete_old

    # 1) ob_snapshots — ts_ms INTEGER
    cutoff_ms = _ms_cutoff(ob_snap_days)
    summary["ob_snapshots"] = await action(
        db, "ob_snapshots", f"ts_ms < {cutoff_ms}", f"ob_snapshots>{ob_snap_days}d"
    )

    # 1b) ob_deltas — ts_ms INTEGER (2026-05-22: 14g retention, fan/disk).
    # ob_deltas write-only (hiçbir sorgu okumuyor) ama gelecekteki ultra-
    # gerçekçi fill-sim için tutuluyor — 14 günlük pencere disk'i kapar.
    cutoff_ms = _ms_cutoff(ob_deltas_days)
    summary["ob_deltas"] = await action(
        db, "ob_deltas", f"ts_ms < {cutoff_ms}", f"ob_deltas>{ob_deltas_days}d"
    )

    # 2) ob_trades — ts_ms INTEGER
    cutoff_ms = _ms_cutoff(ob_trade_days)
    summary["ob_trades"] = await action(
        db, "ob_trades", f"ts_ms < {cutoff_ms}", f"ob_trades>{ob_trade_days}d"
    )

    # 3) odds_history — timestamp TEXT (ISO)
    cutoff_iso = _iso_cutoff(odds_days)
    summary["odds_history"] = await action(
        db,
        "odds_history",
        f"timestamp < '{cutoff_iso}'",
        f"odds_history>{odds_days}d",
    )

    # 4) candles_poly — close_ts TEXT (ISO)
    cutoff_iso = _iso_cutoff(candles_poly_days)
    summary["candles_poly"] = await action(
        db,
        "candles_poly",
        f"close_ts < '{cutoff_iso}'",
        f"candles_poly>{candles_poly_days}d",
    )

    # 5) candles_ext — close_ts TEXT (ISO)
    cutoff_iso = _iso_cutoff(candles_ext_days)
    summary["candles_ext"] = await action(
        db,
        "candles_ext",
        f"close_ts < '{cutoff_iso}'",
        f"candles_ext>{candles_ext_days}d",
    )

    # VACUUM (only in delete mode when rows were actually removed).
    vacuum = os.getenv("DB_RETENTION_VACUUM_ENABLED", "1") == "1"
    vacuumed = False
    if mode == "delete" and vacuum and sum(summary.values()) > 0:
        try:
            logger.info("[retention] VACUUM start")
            await db.conn.execute("VACUUM")
            await db.conn.commit()
            vacuumed = True
            logger.info("[retention] VACUUM done")
        except aiosqlite.Error as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. VACUUM
            # surfaces aiosqlite.OperationalError (locked DB, no space).
            # VACUUM is shrinkage-only; deletes already committed.
            logger.warning(f"[retention] VACUUM failed: " f"{type(e).__name__}: {e}")

    # Post-retention DB size
    try:
        post_size_mb = Path(db_path).stat().st_size / (1024 * 1024)
    except OSError:
        # T11.8-B (2026-04-24): narrow from bare Exception. Same OSError
        # surface as pre-size read above.
        post_size_mb = 0.0

    elapsed = (datetime.utcnow() - t0).total_seconds()
    total_count = sum(summary.values())

    mode_labels = {"report": "counted", "archive": "archived", "delete": "deleted"}
    verb = mode_labels.get(mode, "processed")
    logger.info(
        f"[retention] done in {elapsed:.1f}s — "
        f"{verb} {total_count:,} rows, "
        f"size {pre_size_mb:.1f} → {post_size_mb:.1f} MB"
    )

    # Notify admin
    notify = os.getenv("DB_RETENTION_NOTIFY", "1") == "1"
    if force_notify is not None:
        notify = force_notify
    if notify:
        admin_id = resolve_admin_chat_id()
        if admin_id:
            mode_emoji = {"report": "📊", "archive": "📦", "delete": "🧹"}
            lines = [
                f"<b>{mode_emoji.get(mode, '🔧')} DB Retention ({mode.upper()})</b>",
                f"elapsed: <code>{elapsed:.1f}s</code>",
                f"size: <code>{pre_size_mb:.1f}</code> → " f"<code>{post_size_mb:.1f} MB</code>",
            ]
            if mode == "delete":
                lines.append(f"VACUUM: <code>{'yes' if vacuumed else 'no'}</code>")
            if mode == "archive":
                lines.append(f"archive: <code>{ARCHIVE_DIR}/</code>")
            lines.append("")
            lines.append(f"<b>{verb}</b>")
            for table, count in summary.items():
                lines.append(f"{table}: <code>{count:,}</code>")
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="\n".join(lines),
                    parse_mode="HTML",
                )
            except (TimeoutError, TelegramError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception. Retention
                # pass already done; notify is best-effort.
                logger.warning(f"[retention] notify failed: " f"{type(e).__name__}: {e}")

    return summary
