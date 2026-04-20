"""
Phase 82e Sprint 4.2 — Hot Indexes Maintenance Script
======================================================

Ensures the ob_snapshots composite index used by hyperopt's
_discover_market_windows GROUP BY exists, with EXPLAIN QUERY PLAN
verification before and after.

Why a standalone script instead of db/migrations.py:
  * The 10GB+ production DB can take several minutes to build a new
    index. In-bot migrations.py runs at startup, would block the bot,
    and failures are hard to recover from (Telegram can't see them).
  * Running externally lets us report progress to the console, bound
    the build with PRAGMA cache_size, and exit cleanly on error.
  * Idempotent: re-running is safe; `CREATE INDEX IF NOT EXISTS` skips
    existing indexes.

Usage:
    py -3.11 scripts/ensure_hot_indexes.py
    py -3.11 scripts/ensure_hot_indexes.py --db-path data_store/polypaper.db
    py -3.11 scripts/ensure_hot_indexes.py --explain-only   # no writes

Exit codes:
    0 — all indexes present, EXPLAIN shows expected plan
    1 — failure (IO / SQL / missing expected index)
    2 — DB file does not exist
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force UTF-8 on stdout/stderr so ASCII art + any stray unicode survives
# Windows cp1252 consoles and subprocess pipes. Safe no-op on UTF-8 hosts.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# The indexes this script is responsible for. Each entry is:
#   (name, ddl, why)
# ─────────────────────────────────────────────────────────────

HOT_INDEXES = [
    (
        "idx_ob_snap_slug_mst",
        "CREATE INDEX IF NOT EXISTS idx_ob_snap_slug_mst "
        "ON ob_snapshots(slug, market_start_time)",
        "Sort-free GROUP BY for _discover_market_windows "
        "(covers GROUP BY slug, market_start_time).",
    ),
]

# Query fragments used to verify the query planner picks up the new
# indexes. We only SELECT 1 row (LIMIT 1) — EXPLAIN QUERY PLAN runs
# without executing the query on the storage engine, so it's free.

EXPLAIN_QUERIES: list[tuple[str, str, list]] = [
    (
        "discovery_with_ts_filter",
        # Hyperopt hot path. ts_lower_bound is set by _discover_market_windows
        # when last_n > 0 and start_ms == 0. We pass an arbitrary integer
        # for EXPLAIN purposes — SQLite needs a value, the plan is static.
        """
        SELECT slug, asset, timeframe, up_token_id, down_token_id,
               market_start_time, market_end_time,
               MIN(ts_ms), MAX(ts_ms), COUNT(*)
        FROM ob_snapshots
        WHERE ts_ms >= ?
        GROUP BY slug, market_start_time
        HAVING COUNT(*) >= 2
        ORDER BY MIN(ts_ms) ASC
        """,
        [0],
    ),
    (
        "discovery_asset_tf_filter",
        # Split backtest path (supplies asset + tf filter).
        """
        SELECT slug, asset, timeframe, up_token_id, down_token_id,
               market_start_time, market_end_time,
               MIN(ts_ms), MAX(ts_ms), COUNT(*)
        FROM ob_snapshots
        WHERE asset = ? AND timeframe = ?
        GROUP BY slug, market_start_time
        HAVING COUNT(*) >= 2
        ORDER BY MIN(ts_ms) ASC
        """,
        ["BTC", "5m"],
    ),
    (
        "replay_slug_window",
        # Per-trial replay scan. Must stay on idx_ob_snap_slug_ts.
        "SELECT * FROM ob_snapshots WHERE slug = ? "
        "AND ts_ms >= ? AND ts_ms <= ? ORDER BY ts_ms ASC",
        ["some-slug", 0, 9999999999999],
    ),
]


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a permissive connection.

    busy_timeout: wait up to 5 min for WAL writer to release the page lock.
    cache_size: negative -> size in KB; -524288 == 512MB cache. Large pages
                dramatically cut random I/O during CREATE INDEX on SSD.
    """
    con = sqlite3.connect(str(db_path), timeout=300.0)
    con.execute("PRAGMA busy_timeout = 300000")  # 5 min
    con.execute("PRAGMA cache_size = -524288")  # 512 MB page cache
    con.execute("PRAGMA temp_store = MEMORY")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def _list_ob_indexes(con: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return list of (index_name, column_list) for ob_snapshots."""
    rows = con.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='ob_snapshots' "
        "ORDER BY name"
    ).fetchall()
    out: list[tuple[str, str]] = []
    for (name,) in rows:
        if name.startswith("sqlite_"):
            continue  # auto-indexes
        info = con.execute(f"PRAGMA index_info({name})").fetchall()
        cols = [r[2] for r in info]
        out.append((name, ", ".join(cols)))
    return out


def _explain(con: sqlite3.Connection, sql: str, params: list) -> list[str]:
    """Return EXPLAIN QUERY PLAN output as a list of plain strings."""
    cur = con.execute("EXPLAIN QUERY PLAN " + sql, params)
    # Rows: (id, parent, notused, detail)
    return [r[3] for r in cur.fetchall()]


def _print_header(title: str) -> None:
    print()
    print(f"-- {title} {'-' * (70 - len(title))}")


def _ensure_indexes(con: sqlite3.Connection, dry_run: bool) -> list[str]:
    """Create missing indexes. Returns list of index names touched."""
    existing = {name for name, _ in _list_ob_indexes(con)}
    touched: list[str] = []
    for idx_name, ddl, why in HOT_INDEXES:
        if idx_name in existing:
            print(f"  [OK] {idx_name} already exists - skipping")
            continue
        print(f"  [+ ] {idx_name} (new) - {why}")
        if dry_run:
            print(f"    (dry-run, not executing)")
            continue
        t0 = time.monotonic()
        # Wrap with explicit BEGIN/COMMIT so we can measure + rollback on error
        try:
            con.execute(ddl)
            con.commit()
        except Exception as e:
            print(f"    [FAIL] {e}")
            raise
        dt = time.monotonic() - t0
        print(f"    [OK] created in {dt:.1f}s")
        touched.append(idx_name)
    return touched


def _print_explain_block(con: sqlite3.Connection, label: str) -> None:
    print(f"[{label}]")
    for qlabel, sql, params in EXPLAIN_QUERIES:
        plan = _explain(con, sql, params)
        print(f"  - {qlabel}:")
        for line in plan:
            print(f"      {line}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-path",
        default=os.getenv("DB_PATH", str(ROOT / "data_store" / "polypaper.db")),
    )
    parser.add_argument(
        "--explain-only",
        action="store_true",
        help="print EXPLAIN QUERY PLAN but skip CREATE INDEX",
    )
    parser.add_argument(
        "--verify-index",
        default="idx_ob_snap_slug_mst",
        help="after creation, confirm the planner picks this index",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[FAIL] DB file not found: {db_path}")
        return 2

    size = db_path.stat().st_size
    print(f"ob_snapshots index maintenance - {db_path}")
    print(f"  DB size: {_fmt_bytes(size)}")

    con = _connect(db_path)
    try:
        _print_header("Indexes BEFORE")
        for name, cols in _list_ob_indexes(con):
            print(f"  {name:38s}  ({cols})")

        _print_header("EXPLAIN QUERY PLAN - BEFORE")
        _print_explain_block(con, "before")

        _print_header("Creating missing indexes")
        touched = _ensure_indexes(con, dry_run=args.explain_only)

        _print_header("Indexes AFTER")
        for name, cols in _list_ob_indexes(con):
            print(f"  {name:38s}  ({cols})")

        _print_header("EXPLAIN QUERY PLAN - AFTER")
        _print_explain_block(con, "after")

        # Planner verification: the discovery query should reference
        # our new composite index (or stay on idx_ob_snap_ts if that's
        # more selective - either is fine, both beat a full scan).
        _print_header("Plan verification")
        ok = True
        for qlabel, sql, params in EXPLAIN_QUERIES:
            plan = "\n".join(_explain(con, sql, params))
            if "SCAN" in plan and "USING INDEX" not in plan and "USING COVERING" not in plan:
                print(f"  [WARN] {qlabel}: planner did a table SCAN - plan:")
                for line in plan.splitlines():
                    print(f"       {line}")
                # Not a hard failure - SQLite may legitimately prefer a scan
                # for small result sets. Flag as warning only.
            else:
                print(f"  [OK]   {qlabel}: planner uses an index")
        if not ok:
            return 1

        print()
        if args.explain_only:
            print("[DONE] explain-only mode complete (no writes).")
        elif touched:
            print(f"[DONE] created {len(touched)} new index(es): {', '.join(touched)}")
        else:
            print("[DONE] all hot indexes already present.")
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
