"""
Phase 82e Sprint B.2 — Archive Reader
=====================================
Transparent reader that UNIONs SQLite hot tier + Parquet archive tier.

Allows backtest / hyperopt / edge_discovery to query ob_snapshots across
BOTH live DB (`data_store/polypaper.db::ob_snapshots`) AND archived
parquet files (`data/archive/ob_snapshots_*.parquet`) as if they were
one table — without touching the hot write path.

ARCHITECTURE:
  • Hot tier  : sqlite3 std-lib, read-only (WAL-safe; coexists with bot writer)
  • Cold tier : duckdb read_parquet on a glob; parquets are Zstd L9 but
                DuckDB decompresses transparently at read time.
  • Merge     : Python-side set-merge for aggregates (GROUP BY) and
                concat for SELECTs. This avoids the duckdb sqlite_scanner
                extension dependency (which needs internet on first load).

DESIGN GOALS (no quality compromise):
  1. All 38 ob_snapshots columns preserved across hot + cold.
  2. 2-second snapshot cadence preserved (Parquet is bit-perfect).
  3. L2 orderbook JSON columns intact (up_bids_json, up_asks_json,
     down_bids_json, down_asks_json).
  4. No changes to live write path (market_recorder.py untouched).
  5. Opt-in via ReplayConfig.use_archive — existing code behaves
     identically if the flag is not set.

USAGE FROM BACKTEST:
    from backtest.archive_reader import ArchiveReader

    reader = ArchiveReader()  # defaults: data_store/polypaper.db + data/archive
    max_ts = reader.get_max_ts_ms()
    windows = reader.discover_market_windows(
        ts_lower_bound=max_ts - 30*86400*1000,  # last 30 days
        asset_filter="BTC",
        timeframe_filter="5m",
    )
    for w in windows:
        snaps = reader.load_window_snapshots(
            slug=w["slug"], ts_from=w["first_snap_ms"], ts_to=w["last_snap_ms"]
        )
        ...

  Because DuckDB + sqlite3 are both SYNCHRONOUS libraries, async callers
  should wrap calls via `asyncio.to_thread(reader.get_max_ts_ms)`.

ENV (tuning, all optional):
  ARCHIVE_READER_ENABLED    (default "1")
  ARCHIVE_DIR               (default "data/archive")
  POLYPAPER_DB              (default "data_store/polypaper.db")
  ARCHIVE_READER_SQLITE_TIMEOUT  (default 30)  # seconds
"""
from __future__ import annotations

import glob
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger("polypaper.archive_reader")

ROOT = Path(__file__).resolve().parent.parent

# Default paths (can be overridden at instance level or via ENV)
_DEFAULT_DB_PATH = ROOT / "data_store" / "polypaper.db"
_DEFAULT_ARCHIVE_DIR = ROOT / "data" / "archive"


# ───────────────────────────────────────────────────────────────────────
#  Helpers
# ───────────────────────────────────────────────────────────────────────
def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, "1" if default else "0").strip() in (
        "1", "true", "True", "yes", "on"
    )


def _format_ts(ts_ms: int) -> str:
    if not ts_ms:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return str(ts_ms)


# ───────────────────────────────────────────────────────────────────────
#  ArchiveReader
# ───────────────────────────────────────────────────────────────────────
class ArchiveReader:
    """Unified reader over live SQLite + archived Parquet.

    Thread-safe: each method opens its own sqlite3 + duckdb connections.
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        archive_dir: Optional[Path] = None,
    ):
        self.db_path = Path(
            db_path or os.getenv("POLYPAPER_DB", str(_DEFAULT_DB_PATH))
        )
        self.archive_dir = Path(
            archive_dir or os.getenv("ARCHIVE_DIR", str(_DEFAULT_ARCHIVE_DIR))
        )
        self.sqlite_timeout = _env_int("ARCHIVE_READER_SQLITE_TIMEOUT", 30)

        # Lazy check of duckdb availability
        self._duckdb_available: Optional[bool] = None

    # ── Infra ─────────────────────────────────────────────────────────
    def _hot_connect(self) -> sqlite3.Connection:
        """Open a read-only connection to the live DB using URI mode.

        Phase 82e Sprint 2.2: delegated to db.ro_connect.open_ro_connection
        which adds retry (exponential backoff on OperationalError), an
        immutable=1 fallback (WAL-bypass frozen snapshot), and as last
        resort a tmp-copy fallback. Behavior is transparent — callers
        still get a plain sqlite3.Connection.
        """
        from db.ro_connect import open_ro_connection
        return open_ro_connection(
            self.db_path,
            busy_timeout_ms=self.sqlite_timeout * 1000,
        )

    def _hot_available(self) -> bool:
        return self.db_path.exists()

    def _parquet_glob(self) -> str:
        return str(self.archive_dir / "ob_snapshots_*.parquet")

    def _parquet_files(self) -> list[str]:
        try:
            return sorted(glob.glob(self._parquet_glob()))
        except Exception:
            return []

    def _duckdb_conn(self):
        """Open a fresh in-memory duckdb connection (sync)."""
        if self._duckdb_available is False:
            return None
        try:
            import duckdb  # type: ignore
            con = duckdb.connect(":memory:")
            # Speed: single-threaded is typically faster for parquet scans
            # on these file sizes. Users can override via DUCKDB_THREADS env.
            try:
                con.execute(
                    f"SET threads TO {_env_int('DUCKDB_THREADS', 2)}"
                )
            except Exception:
                pass
            self._duckdb_available = True
            return con
        except ImportError:
            self._duckdb_available = False
            logger.warning(
                "duckdb not installed — archive reader will read hot tier only. "
                "Run: pip install duckdb --break-system-packages"
            )
            return None

    def info(self) -> dict:
        """Return a summary of what we can see."""
        info: dict = {
            "db_path": str(self.db_path),
            "archive_dir": str(self.archive_dir),
            "hot_available": self._hot_available(),
            "parquet_files": len(self._parquet_files()),
        }
        info["parquet_size_mb"] = round(
            sum(
                os.path.getsize(f) for f in self._parquet_files()
            ) / (1024 * 1024),
            2,
        )
        # Ranges
        try:
            hot_min, hot_max = self._hot_range()
            info["hot_range"] = {
                "min_ms": hot_min, "max_ms": hot_max,
                "min_iso": _format_ts(hot_min),
                "max_iso": _format_ts(hot_max),
            }
        except Exception as e:
            info["hot_range_error"] = str(e)
        try:
            cold_min, cold_max = self._cold_range()
            info["cold_range"] = {
                "min_ms": cold_min, "max_ms": cold_max,
                "min_iso": _format_ts(cold_min),
                "max_iso": _format_ts(cold_max),
            }
        except Exception as e:
            info["cold_range_error"] = str(e)
        return info

    # ── Ranges ───────────────────────────────────────────────────────
    def _hot_range(self) -> tuple[int, int]:
        if not self._hot_available():
            return 0, 0
        with self._hot_connect() as conn:
            row = conn.execute(
                "SELECT MIN(ts_ms), MAX(ts_ms) FROM ob_snapshots"
            ).fetchone()
        return (row[0] or 0, row[1] or 0) if row else (0, 0)

    def _cold_range(self) -> tuple[int, int]:
        files = self._parquet_files()
        if not files:
            return 0, 0
        con = self._duckdb_conn()
        if con is None:
            return 0, 0
        try:
            row = con.execute(
                f"SELECT MIN(ts_ms), MAX(ts_ms) "
                f"FROM read_parquet('{self._parquet_glob()}')"
            ).fetchone()
            return (row[0] or 0, row[1] or 0) if row else (0, 0)
        finally:
            con.close()

    # ── Public: max ts ───────────────────────────────────────────────
    def get_max_ts_ms(self) -> int:
        """MAX(ts_ms) across hot + cold. Returns 0 if both empty."""
        hot_max = 0
        if self._hot_available():
            try:
                with self._hot_connect() as conn:
                    row = conn.execute(
                        "SELECT MAX(ts_ms) FROM ob_snapshots"
                    ).fetchone()
                    hot_max = (row[0] or 0) if row else 0
            except Exception as e:
                logger.warning(f"hot MAX(ts_ms) failed: {e}")

        cold_max = 0
        if self._parquet_files():
            con = self._duckdb_conn()
            if con is not None:
                try:
                    row = con.execute(
                        f"SELECT MAX(ts_ms) "
                        f"FROM read_parquet('{self._parquet_glob()}')"
                    ).fetchone()
                    cold_max = (row[0] or 0) if row else 0
                except Exception as e:
                    logger.warning(f"cold MAX(ts_ms) failed: {e}")
                finally:
                    con.close()
        return max(hot_max, cold_max)

    # ── Public: columns ──────────────────────────────────────────────
    def get_columns(self) -> list[str]:
        """Return ob_snapshots column list, prefer hot tier."""
        if self._hot_available():
            try:
                with self._hot_connect() as conn:
                    rows = conn.execute(
                        "PRAGMA table_info(ob_snapshots)"
                    ).fetchall()
                    return [r[1] for r in rows]
            except Exception as e:
                logger.warning(f"PRAGMA table_info failed: {e}")
        # Fallback: read parquet schema
        files = self._parquet_files()
        if files:
            con = self._duckdb_conn()
            if con is not None:
                try:
                    row = con.execute(
                        f"DESCRIBE SELECT * FROM read_parquet('{files[0]}')"
                    ).fetchall()
                    return [r[0] for r in row]
                finally:
                    con.close()
        return []

    # ── Public: discover_market_windows ──────────────────────────────
    def discover_market_windows(
        self,
        ts_lower_bound: int = 0,
        ts_upper_bound: int = 0,
        asset_filter: Optional[str] = None,
        timeframe_filter: Optional[str] = None,
        min_snap_count: int = 2,
    ) -> list[dict]:
        """Discover distinct market windows across hot + cold.

        Each window: (slug, market_start_time) pair.

        Returns sorted list of dicts identical to replay_engine's shape:
          {slug, asset, timeframe, up_token_id, down_token_id,
           market_start_time, market_end_time,
           first_snap_ms, last_snap_ms, snap_count,
           avg_volume, avg_liquidity}

        Performance:
          • Hot path is identical to the Phase 82b.5 query (uses ts_ms
            index when ts_lower_bound > 0).
          • Cold path uses DuckDB columnar scan; 56 MB of Zstd L9 parquet
            aggregates in ~0.3-0.8s depending on row count.
          • Results are merged in Python by (slug, market_start_time).
            If a window straddles hot+cold (i.e. started before archive
            cutoff and ended after), counts are summed, first/last are
            min/max-ed.
        """
        select_cols = (
            "slug, asset, timeframe, "
            "up_token_id, down_token_id, "
            "market_start_time, market_end_time, "
            "MIN(ts_ms) as first_snap_ms, "
            "MAX(ts_ms) as last_snap_ms, "
            "COUNT(*) as snap_count"
        )
        group_by = " GROUP BY slug, asset, timeframe, up_token_id, down_token_id, market_start_time, market_end_time"
        order_by = " ORDER BY first_snap_ms ASC"

        def _build_where(dialect: str):
            """Return (where_sql, params_list)."""
            parts = []
            params: list = []
            if ts_lower_bound > 0:
                parts.append("ts_ms >= ?")
                params.append(ts_lower_bound)
            if ts_upper_bound > 0:
                parts.append("ts_ms <= ?")
                params.append(ts_upper_bound)
            if asset_filter:
                parts.append("asset = ?")
                params.append(asset_filter.upper())
            if timeframe_filter:
                parts.append("timeframe = ?")
                params.append(timeframe_filter)
            where_sql = " WHERE " + " AND ".join(parts) if parts else ""
            return where_sql, params

        rows_hot: list[tuple] = []
        rows_cold: list[tuple] = []

        # Hot
        if self._hot_available():
            where_sql, params = _build_where("sqlite")
            sql = f"SELECT {select_cols} FROM ob_snapshots{where_sql}{group_by} HAVING snap_count >= {int(min_snap_count)}{order_by}"
            try:
                with self._hot_connect() as conn:
                    rows_hot = conn.execute(sql, params).fetchall()
            except Exception as e:
                logger.warning(f"hot discover failed: {e}")

        # Cold
        if self._parquet_files():
            con = self._duckdb_conn()
            if con is not None:
                try:
                    where_sql, params = _build_where("duckdb")
                    sql = (
                        f"SELECT {select_cols} "
                        f"FROM read_parquet('{self._parquet_glob()}'){where_sql}"
                        f"{group_by} HAVING snap_count >= {int(min_snap_count)}{order_by}"
                    )
                    rows_cold = con.execute(sql, params).fetchall()
                except Exception as e:
                    logger.warning(f"cold discover failed: {e}")
                finally:
                    con.close()

        # Merge — key is (slug, market_start_time)
        merged: dict[tuple, dict] = {}
        for row in list(rows_hot) + list(rows_cold):
            (slug, asset, timeframe, up_token_id, down_token_id,
             mkt_start, mkt_end, first_ms, last_ms, cnt) = row
            key = (slug, mkt_start)
            if key in merged:
                existing = merged[key]
                existing["first_snap_ms"] = min(
                    existing["first_snap_ms"], first_ms or 0
                ) or existing["first_snap_ms"]
                existing["last_snap_ms"] = max(
                    existing["last_snap_ms"], last_ms or 0
                )
                existing["snap_count"] += cnt
            else:
                merged[key] = {
                    "slug": slug,
                    "asset": asset,
                    "timeframe": timeframe,
                    "up_token_id": up_token_id,
                    "down_token_id": down_token_id,
                    "market_start_time": mkt_start,
                    "market_end_time": mkt_end,
                    "first_snap_ms": first_ms or 0,
                    "last_snap_ms": last_ms or 0,
                    "snap_count": int(cnt),
                    "avg_volume": 0.0,
                    "avg_liquidity": 0.0,
                }

        # Sort by first_snap_ms ASC (same as replay_engine contract)
        windows = sorted(merged.values(), key=lambda w: w["first_snap_ms"])
        logger.info(
            "ArchiveReader.discover: %d windows (hot=%d, cold=%d, merged=%d)",
            len(windows), len(rows_hot), len(rows_cold), len(windows),
        )
        return windows

    # ── Public: load_window_snapshots ────────────────────────────────
    def load_window_snapshots(
        self,
        slug: str,
        ts_from: int,
        ts_to: int,
    ) -> list[dict]:
        """Load all snapshots for a single market window, hot + cold.

        Returns list of dicts with ALL 38 columns preserved.
        Sorted by ts_ms ASC.
        """
        cols = self.get_columns()
        if not cols:
            logger.warning("ArchiveReader.load: no columns resolved")
            return []

        rows_hot: list[tuple] = []
        rows_cold: list[tuple] = []

        # Hot
        if self._hot_available():
            try:
                with self._hot_connect() as conn:
                    cur = conn.execute(
                        "SELECT * FROM ob_snapshots "
                        "WHERE slug = ? AND ts_ms >= ? AND ts_ms <= ? "
                        "ORDER BY ts_ms ASC",
                        (slug, int(ts_from), int(ts_to)),
                    )
                    rows_hot = cur.fetchall()
            except Exception as e:
                logger.warning(f"hot load_window failed: {e}")

        # Cold
        if self._parquet_files():
            con = self._duckdb_conn()
            if con is not None:
                try:
                    # read_parquet with pushdown for slug + ts range.
                    # DuckDB pushes these into parquet statistics reading.
                    # NOTE: SELECT * respects parquet's declared schema; we
                    # re-order to hot schema afterwards if order differs.
                    q = (
                        f"SELECT * FROM read_parquet('{self._parquet_glob()}') "
                        f"WHERE slug = ? AND ts_ms >= ? AND ts_ms <= ? "
                        f"ORDER BY ts_ms ASC"
                    )
                    rows_cold_raw = con.execute(
                        q, [slug, int(ts_from), int(ts_to)]
                    ).fetchall()
                    cold_cols = [
                        d[0] for d in con.description
                    ] if con.description else cols
                    # Re-map cold rows to hot column order
                    col_index = {c: i for i, c in enumerate(cold_cols)}
                    rows_cold = [
                        tuple(
                            r[col_index[c]] if c in col_index else None
                            for c in cols
                        )
                        for r in rows_cold_raw
                    ]
                except Exception as e:
                    logger.warning(f"cold load_window failed: {e}")
                finally:
                    con.close()

        # Merge + sort by ts_ms (column 0 is typically id, find ts_ms index)
        try:
            ts_idx = cols.index("ts_ms")
        except ValueError:
            ts_idx = 0

        combined = list(rows_hot) + list(rows_cold)
        combined.sort(key=lambda r: r[ts_idx] if r[ts_idx] is not None else 0)
        return [dict(zip(cols, row)) for row in combined]

    # ── Public: raw row counts (for diagnostics) ─────────────────────
    def count_rows(self) -> dict:
        """Return {'hot': n, 'cold': n, 'total': n}."""
        result = {"hot": 0, "cold": 0, "total": 0}
        if self._hot_available():
            try:
                with self._hot_connect() as conn:
                    result["hot"] = conn.execute(
                        "SELECT COUNT(*) FROM ob_snapshots"
                    ).fetchone()[0]
            except Exception:
                pass
        if self._parquet_files():
            con = self._duckdb_conn()
            if con is not None:
                try:
                    result["cold"] = con.execute(
                        f"SELECT COUNT(*) FROM read_parquet('{self._parquet_glob()}')"
                    ).fetchone()[0]
                finally:
                    con.close()
        result["total"] = result["hot"] + result["cold"]
        return result


# ───────────────────────────────────────────────────────────────────────
#  Singleton convenience (opt-in)
# ───────────────────────────────────────────────────────────────────────
_default_reader: Optional[ArchiveReader] = None


def get_default_reader() -> ArchiveReader:
    """Module-level shared reader (lazy-init). Thread-safe per-call usage."""
    global _default_reader
    if _default_reader is None:
        _default_reader = ArchiveReader()
    return _default_reader


# ───────────────────────────────────────────────────────────────────────
#  CLI for quick sanity check: python -m backtest.archive_reader
# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    reader = ArchiveReader()
    info = reader.info()
    print("── ArchiveReader Info ──")
    print(json.dumps(info, indent=2, default=str))
    print()
    counts = reader.count_rows()
    print(f"Rows: hot={counts['hot']:,} cold={counts['cold']:,} total={counts['total']:,}")
    print()
    max_ts = reader.get_max_ts_ms()
    print(f"Max ts_ms: {max_ts} ({_format_ts(max_ts)})")
