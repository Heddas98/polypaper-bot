"""
PolyPaper Bot — ob_snapshots Archive Script (Phase 58)
=====================================================
Eski orderbook snapshot verilerini SQLite'tan parquet'e export eder.
VERİ SİLMEZ — taşır ve sıkıştırır.

Kullanım:
  py -3.11 scripts/ob_archive.py              # 7 günden eski → parquet
  py -3.11 scripts/ob_archive.py --days 3     # 3 günden eski
  py -3.11 scripts/ob_archive.py --vacuum     # Export + VACUUM

Çıktı:
  data/archive/ob_snapshots_YYYY-MM-DD.parquet

Gereksinimler:
  pip install pandas pyarrow (veya fastparquet)
"""

import argparse
import logging
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data_store" / "polypaper.db"
ARCHIVE_DIR = ROOT / "data" / "archive"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ob_archive")


def get_db_size_mb() -> float:
    """Return DB file size in MB."""
    try:
        return DB_PATH.stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def count_rows(conn: sqlite3.Connection, table: str, where: str = "") -> int:
    """Count rows in a table with optional WHERE clause."""
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql).fetchone()[0]


def archive_to_parquet(conn: sqlite3.Connection, cutoff_date: str, dry_run: bool = False) -> int:
    """Export old ob_snapshots to parquet, then DELETE from SQLite."""
    try:
        import pandas as pd
    except ImportError:
        log.error("pandas not installed. Run: pip install pandas pyarrow --break-system-packages")
        return 0

    # Count rows to archive
    where = f"created_at < '{cutoff_date}'"
    total = count_rows(conn, "ob_snapshots", where)

    if total == 0:
        log.info("No rows to archive (all data is newer than cutoff).")
        return 0

    log.info(f"Found {total:,} rows older than {cutoff_date}")

    if dry_run:
        log.info("[DRY RUN] Would archive %d rows. No changes made.", total)
        return total

    # Ensure archive directory exists
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Export in chunks to avoid memory issues
    CHUNK_SIZE = 100_000
    archived = 0
    batch = 0

    # Generate output filename
    date_tag = cutoff_date[:10]
    ts = datetime.now().strftime("%H%M%S")
    out_path = ARCHIVE_DIR / f"ob_snapshots_{date_tag}_{ts}.parquet"

    log.info(f"Exporting to {out_path} ...")

    # Read all matching rows into DataFrame
    # For very large datasets, we chunk the read
    frames = []
    offset = 0
    while offset < total:
        sql = (
            f"SELECT * FROM ob_snapshots "
            f"WHERE created_at < '{cutoff_date}' "
            f"ORDER BY created_at "
            f"LIMIT {CHUNK_SIZE} OFFSET {offset}"
        )
        df_chunk = pd.read_sql_query(sql, conn)
        if df_chunk.empty:
            break
        frames.append(df_chunk)
        offset += len(df_chunk)
        batch += 1
        log.info(f"  Read chunk {batch}: {len(df_chunk):,} rows (total {offset:,}/{total:,})")

    if not frames:
        log.warning("No data read — aborting.")
        return 0

    # Concatenate and write to parquet
    df_all = pd.concat(frames, ignore_index=True)
    df_all.to_parquet(out_path, engine="pyarrow", compression="snappy", index=False)
    parquet_size_mb = out_path.stat().st_size / (1024 * 1024)
    log.info(f"Parquet written: {out_path.name} ({parquet_size_mb:.1f} MB, {len(df_all):,} rows)")
    archived = len(df_all)

    # Delete from SQLite
    log.info(f"Deleting {archived:,} archived rows from ob_snapshots ...")
    conn.execute(f"DELETE FROM ob_snapshots WHERE created_at < '{cutoff_date}'")
    conn.commit()
    remaining = count_rows(conn, "ob_snapshots")
    log.info(f"Done. Remaining in ob_snapshots: {remaining:,} rows")

    return archived


def create_hourly_summary(conn: sqlite3.Connection):
    """Create ob_hourly_summary aggregation table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ob_hourly_summary (
            hour TEXT NOT NULL,
            token_id TEXT NOT NULL,
            avg_best_bid REAL,
            avg_best_ask REAL,
            avg_spread REAL,
            avg_bid_depth REAL,
            avg_ask_depth REAL,
            avg_imbalance REAL,
            snapshot_count INTEGER,
            PRIMARY KEY (hour, token_id)
        )
    """)
    conn.commit()
    log.info("ob_hourly_summary table ensured.")


def main():
    parser = argparse.ArgumentParser(description="Archive ob_snapshots to parquet")
    parser.add_argument(
        "--days", type=int, default=7, help="Archive data older than N days (default: 7)"
    )
    parser.add_argument(
        "--vacuum", action="store_true", help="Run VACUUM after archiving to reclaim disk space"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be archived without making changes"
    )
    parser.add_argument(
        "--summary", action="store_true", help="Also create ob_hourly_summary aggregation table"
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        log.error(f"Database not found: {DB_PATH}")
        sys.exit(1)

    db_before = get_db_size_mb()
    log.info(f"Database: {DB_PATH}")
    log.info(f"DB size before: {db_before:.1f} MB")

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")

    # Check if ob_snapshots table exists
    tables = [
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    if "ob_snapshots" not in tables:
        log.warning("ob_snapshots table not found. Nothing to archive.")
        conn.close()
        return

    total_rows = count_rows(conn, "ob_snapshots")
    log.info(f"ob_snapshots total rows: {total_rows:,}")

    if total_rows == 0:
        log.info("Table is empty. Nothing to do.")
        conn.close()
        return

    # Calculate cutoff date
    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    log.info(f"Archive cutoff: {cutoff_str} (older than {args.days} days)")

    # Archive
    archived = archive_to_parquet(conn, cutoff_str, dry_run=args.dry_run)

    # Optional: create summary table
    if args.summary and not args.dry_run:
        create_hourly_summary(conn)

    # Optional: VACUUM
    if args.vacuum and archived > 0 and not args.dry_run:
        log.info("Running VACUUM (this may take a while for large DBs)...")
        conn.execute("VACUUM")
        db_after = get_db_size_mb()
        log.info(
            f"VACUUM done. DB size: {db_before:.1f} MB → {db_after:.1f} MB "
            f"(saved {db_before - db_after:.1f} MB)"
        )
    else:
        db_after = get_db_size_mb()

    conn.close()

    # Summary
    log.info("=" * 50)
    log.info("SUMMARY:")
    log.info(f"  Rows archived: {archived:,}")
    log.info(f"  DB size: {db_before:.1f} MB → {db_after:.1f} MB")
    if archived > 0 and not args.dry_run:
        log.info(f"  Parquet files: {ARCHIVE_DIR}")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
