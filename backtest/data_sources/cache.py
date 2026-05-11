"""
PolyPaper Bot - Backtest v2 Data Cache Layer
SQLite-based TTL cache for API responses.
All data sources (PolyBackTest, Binance, Gamma) use this to avoid
redundant API calls and respect rate limits.

Tables:
  api_cache     — generic key-value with TTL
  market_cache  — resolved market metadata
  snapshot_cache — orderbook snapshots (bulk)
  kline_cache   — Binance OHLCV candles
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger("polypaper.backtest.cache")

# Default TTL values (seconds)
TTL_MARKETS = 3600  # 1 hour — market list
TTL_SNAPSHOTS = 86400 * 7  # 7 days — historical snapshots don't change
TTL_KLINES = 86400 * 7  # 7 days — historical klines don't change
TTL_METADATA = 86400  # 1 day  — resolved market metadata
TTL_DEFAULT = 3600  # 1 hour — generic fallback

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data_store" / "backtest_cache.db"


class BacktestCache:
    """Async SQLite cache with TTL support for backtest data."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> "BacktestCache":
        """Initialize DB connection and create tables."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(str(self.db_path))
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        await self._create_tables()
        logger.info("Backtest cache initialized: %s", self.db_path)
        return self

    async def _create_tables(self):
        """Create cache tables if they don't exist."""
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS api_cache (
                cache_key   TEXT PRIMARY KEY,
                data        TEXT NOT NULL,
                source      TEXT DEFAULT '',
                created_at  REAL NOT NULL,
                ttl         REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS market_cache (
                market_id       TEXT PRIMARY KEY,
                coin            TEXT NOT NULL,
                market_type     TEXT NOT NULL,
                question        TEXT DEFAULT '',
                start_time      TEXT DEFAULT '',
                end_time        TEXT DEFAULT '',
                winner          TEXT DEFAULT '',
                volume          REAL DEFAULT 0,
                liquidity       REAL DEFAULT 0,
                up_token_id     TEXT DEFAULT '',
                down_token_id   TEXT DEFAULT '',
                raw_json        TEXT DEFAULT '{}',
                cached_at       REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshot_cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id       TEXT NOT NULL,
                timestamp_ms    INTEGER NOT NULL,
                up_best_bid     REAL DEFAULT 0,
                up_best_ask     REAL DEFAULT 0,
                down_best_bid   REAL DEFAULT 0,
                down_best_ask   REAL DEFAULT 0,
                spread          REAL DEFAULT 0,
                binance_price   REAL DEFAULT 0,
                raw_json        TEXT DEFAULT '{}',
                UNIQUE(market_id, timestamp_ms)
            );

            CREATE TABLE IF NOT EXISTS kline_cache (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT NOT NULL,
                interval_       TEXT NOT NULL,
                open_time       INTEGER NOT NULL,
                open            REAL NOT NULL,
                high            REAL NOT NULL,
                low             REAL NOT NULL,
                close           REAL NOT NULL,
                volume          REAL NOT NULL,
                close_time      INTEGER NOT NULL,
                taker_buy_vol   REAL DEFAULT 0,
                UNIQUE(symbol, interval_, open_time)
            );

            CREATE INDEX IF NOT EXISTS idx_snap_market
                ON snapshot_cache(market_id);
            CREATE INDEX IF NOT EXISTS idx_snap_ts
                ON snapshot_cache(market_id, timestamp_ms);
            CREATE INDEX IF NOT EXISTS idx_kline_sym
                ON kline_cache(symbol, interval_, open_time);
        """)
        await self.conn.commit()

    # ── Generic key-value cache ──────────────────────────────

    async def get(self, key: str) -> Optional[Any]:
        """Get cached value by key. Returns None if expired or missing."""
        if not self.conn:
            return None
        cursor = await self.conn.execute(
            "SELECT data, created_at, ttl FROM api_cache WHERE cache_key = ?", (key,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        data, created_at, ttl = row
        if time.time() - created_at > ttl:
            await self.conn.execute("DELETE FROM api_cache WHERE cache_key = ?", (key,))
            await self.conn.commit()
            return None
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return data

    async def set(self, key: str, value: Any, ttl: float = TTL_DEFAULT, source: str = "") -> None:
        """Store value with TTL."""
        if not self.conn:
            return
        data = json.dumps(value) if not isinstance(value, str) else value
        await self.conn.execute(
            """INSERT OR REPLACE INTO api_cache
               (cache_key, data, source, created_at, ttl)
               VALUES (?, ?, ?, ?, ?)""",
            (key, data, source, time.time(), ttl),
        )
        await self.conn.commit()

    # ── Market cache ─────────────────────────────────────────

    async def get_market(self, market_id: str) -> Optional[dict]:
        """Get cached market metadata."""
        if not self.conn:
            return None
        cursor = await self.conn.execute(
            "SELECT * FROM market_cache WHERE market_id = ?", (market_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row, strict=False))

    async def set_market(self, market: dict) -> None:
        """Cache a resolved market."""
        if not self.conn:
            return
        mid = market.get("market_id") or market.get("id", "")
        await self.conn.execute(
            """INSERT OR REPLACE INTO market_cache
               (market_id, coin, market_type, question, start_time, end_time,
                winner, volume, liquidity, up_token_id, down_token_id,
                raw_json, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                mid,
                market.get("coin", ""),
                market.get("market_type", ""),
                market.get("question", ""),
                market.get("start_time", ""),
                market.get("end_time", ""),
                market.get("winner", ""),
                market.get("volume", 0),
                market.get("liquidity", 0),
                market.get("up_token_id", ""),
                market.get("down_token_id", ""),
                json.dumps(market),
                time.time(),
            ),
        )
        await self.conn.commit()

    async def get_markets(self, coin: str = "", market_type: str = "") -> list:
        """Get all cached markets, optionally filtered."""
        if not self.conn:
            return []
        query = "SELECT raw_json FROM market_cache WHERE 1=1"
        params = []
        if coin:
            query += " AND coin = ?"
            params.append(coin.upper())
        if market_type:
            query += " AND market_type = ?"
            params.append(market_type)
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            try:
                results.append(json.loads(row[0]))
            except (json.JSONDecodeError, TypeError):
                pass
        return results

    # ── Snapshot cache ───────────────────────────────────────

    async def get_snapshots(self, market_id: str) -> list:
        """Get all cached snapshots for a market, sorted by time."""
        if not self.conn:
            return []
        cursor = await self.conn.execute(
            """SELECT raw_json FROM snapshot_cache
               WHERE market_id = ?
               ORDER BY timestamp_ms ASC""",
            (market_id,),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            try:
                results.append(json.loads(row[0]))
            except (json.JSONDecodeError, TypeError):
                pass
        return results

    async def has_snapshots(self, market_id: str) -> bool:
        """Check if snapshots exist for a market."""
        if not self.conn:
            return False
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM snapshot_cache WHERE market_id = ?", (market_id,)
        )
        row = await cursor.fetchone()
        return (row[0] or 0) > 0

    async def store_snapshots(self, market_id: str, snapshots: list) -> int:
        """Bulk insert snapshots. Returns count of new rows."""
        if not self.conn or not snapshots:
            return 0
        inserted = 0
        for snap in snapshots:
            ts = snap.get("timestamp_ms") or snap.get("timestamp", 0)
            try:
                await self.conn.execute(
                    """INSERT OR IGNORE INTO snapshot_cache
                       (market_id, timestamp_ms, up_best_bid, up_best_ask,
                        down_best_bid, down_best_ask, spread, binance_price,
                        raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        market_id,
                        int(ts),
                        snap.get("up_best_bid", 0),
                        snap.get("up_best_ask", 0),
                        snap.get("down_best_bid", 0),
                        snap.get("down_best_ask", 0),
                        snap.get("spread", 0),
                        snap.get("binance_price", 0),
                        json.dumps(snap),
                    ),
                )
                inserted += 1
            except Exception:
                pass  # UNIQUE constraint → skip duplicate
        await self.conn.commit()
        return inserted

    # ── Kline cache ──────────────────────────────────────────

    async def get_klines(
        self, symbol: str, interval: str, start_ms: int = 0, end_ms: int = 0
    ) -> list:
        """Get cached klines for a symbol+interval range."""
        if not self.conn:
            return []
        query = """SELECT open_time, open, high, low, close, volume,
                          close_time, taker_buy_vol
                   FROM kline_cache
                   WHERE symbol = ? AND interval_ = ?"""
        params: list = [symbol.upper(), interval]
        if start_ms:
            query += " AND open_time >= ?"
            params.append(start_ms)
        if end_ms:
            query += " AND open_time <= ?"
            params.append(end_ms)
        query += " ORDER BY open_time ASC"
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        return [
            {
                "open_time": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5],
                "close_time": r[6],
                "taker_buy_vol": r[7],
            }
            for r in rows
        ]

    async def store_klines(self, symbol: str, interval: str, klines: list) -> int:
        """Bulk insert klines. Returns count of new rows."""
        if not self.conn or not klines:
            return 0
        inserted = 0
        for k in klines:
            try:
                await self.conn.execute(
                    """INSERT OR IGNORE INTO kline_cache
                       (symbol, interval_, open_time, open, high, low,
                        close, volume, close_time, taker_buy_vol)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        symbol.upper(),
                        interval,
                        int(k.get("open_time", k[0]) if isinstance(k, dict) else k[0]),
                        float(k.get("open", k[1]) if isinstance(k, dict) else k[1]),
                        float(k.get("high", k[2]) if isinstance(k, dict) else k[2]),
                        float(k.get("low", k[3]) if isinstance(k, dict) else k[3]),
                        float(k.get("close", k[4]) if isinstance(k, dict) else k[4]),
                        float(k.get("volume", k[5]) if isinstance(k, dict) else k[5]),
                        int(k.get("close_time", k[6]) if isinstance(k, dict) else k[6]),
                        float(
                            k.get(
                                "taker_buy_vol", k[9] if isinstance(k, list) and len(k) > 9 else 0
                            )
                            if isinstance(k, dict)
                            else (k[9] if len(k) > 9 else 0)
                        ),
                    ),
                )
                inserted += 1
            except Exception:
                pass  # UNIQUE constraint → skip duplicate
        await self.conn.commit()
        return inserted

    # ── Maintenance ──────────────────────────────────────────

    async def cleanup_expired(self) -> int:
        """Remove expired entries from api_cache."""
        if not self.conn:
            return 0
        now = time.time()
        cursor = await self.conn.execute(
            "DELETE FROM api_cache WHERE (created_at + ttl) < ?", (now,)
        )
        await self.conn.commit()
        return cursor.rowcount

    async def stats(self) -> dict:
        """Return cache statistics."""
        if not self.conn:
            return {}
        result = {}
        for table in ["api_cache", "market_cache", "snapshot_cache", "kline_cache"]:
            cursor = await self.conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = await cursor.fetchone()
            result[table] = row[0] if row else 0
        return result

    async def close(self):
        """Close DB connection."""
        if self.conn:
            await self.conn.close()
            self.conn = None
            logger.info("Backtest cache closed")
