"""
scripts/bench_discovery_plan.py

Phase 82e Sprint 4.2+ — print the actual EXPLAIN QUERY PLAN for the REAL
_discover_market_windows SQL (not the simplified proxy in
ensure_hot_indexes.py) and list ob_snapshots indexes.

Why a separate script:
  * ensure_hot_indexes.py tests 3 proxy queries that include ts_ms but omit
    the full SELECT list. With a covering index the SELECT columns matter:
    if SELECT references columns outside the index, the planner still
    fetches the row.
  * This script mirrors the EXACT SELECT/WHERE/GROUP BY/ORDER BY shape
    from backtest/replay_engine.py::_discover_market_windows so the plan
    we print is the plan hyperopt actually runs.

Read-only. Bot calisirken calistirilabilir.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# UTF-8 on Windows stdout so ASCII stays safe under redirection
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# The REAL discovery query shape — matches replay_engine.py::_discover_market_windows
# (the default hyperopt path: only ts_lower_bound filter set).
REAL_DISCOVERY_SQL = """
SELECT slug, asset, timeframe,
       up_token_id, down_token_id,
       market_start_time, market_end_time,
       MIN(ts_ms) as first_snap_ms,
       MAX(ts_ms) as last_snap_ms,
       COUNT(*) as snap_count
FROM ob_snapshots
WHERE 1=1
  AND ts_ms >= ?
GROUP BY slug, market_start_time
HAVING snap_count >= 2
ORDER BY first_snap_ms ASC
"""

REAL_DISCOVERY_SQL_WITH_ASSET = """
SELECT slug, asset, timeframe,
       up_token_id, down_token_id,
       market_start_time, market_end_time,
       MIN(ts_ms) as first_snap_ms,
       MAX(ts_ms) as last_snap_ms,
       COUNT(*) as snap_count
FROM ob_snapshots
WHERE 1=1
  AND ts_ms >= ?
  AND asset = ?
  AND timeframe = ?
GROUP BY slug, market_start_time
HAVING snap_count >= 2
ORDER BY first_snap_ms ASC
"""


def _print_section(title: str) -> None:
    print()
    print(f"-- {title} " + "-" * max(0, 66 - len(title)))


def _list_indexes(con: sqlite3.Connection) -> None:
    rows = con.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='ob_snapshots' "
        "AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    ).fetchall()
    print(f"ob_snapshots indexes ({len(rows)}):")
    for (name,) in rows:
        info = con.execute(f"PRAGMA index_info({name})").fetchall()
        cols = ", ".join(r[2] for r in info)
        print(f"  {name:38s}  ({cols})")


def _print_plan(con: sqlite3.Connection, label: str, sql: str, params: list) -> None:
    print(f"[{label}]")
    cur = con.execute("EXPLAIN QUERY PLAN " + sql, params)
    for row in cur.fetchall():
        # row = (id, parent, notused, detail)
        print(f"  {row[3]}")


def _has_index(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db-path",
        default=os.getenv("DB_PATH", str(ROOT / "data_store" / "polypaper.db")),
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"[FAIL] DB not found: {db_path}")
        return 2

    print(f"bench_discovery_plan -- {db_path}")
    print(f"  DB size: {db_path.stat().st_size / (1024**3):.2f} GB")

    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.execute("PRAGMA busy_timeout = 60000")
    try:
        _print_section("Indexes")
        _list_indexes(con)

        _print_section("EXPLAIN: real discovery (ts_ms filter only)")
        _print_plan(con, "default_hyperopt", REAL_DISCOVERY_SQL, [0])

        _print_section("EXPLAIN: real discovery (asset+tf+ts_ms)")
        _print_plan(con, "split_backtest", REAL_DISCOVERY_SQL_WITH_ASSET,
                    [0, "BTC", "5m"])

        _print_section("Covering index probe")
        for new_idx in ("idx_ob_snap_slug_mst_ts", "idx_ob_snap_atf_slug_mst_ts"):
            if _has_index(con, new_idx):
                print(f"  [OK] {new_idx} present")
            else:
                print(f"  [--] {new_idx} NOT present")

        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
