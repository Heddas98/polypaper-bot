"""
PolyPaper Bot - Candle Collector (Phase 35)
=============================================
Surekli 5m mum verisi toplar ve ana DB'ye kaydeder.
Polymarket odds + Binance fiyat verileri birlikte depolanir.

Tablolar:
  candles_poly  — Polymarket odds OHLCV (5m/15m pencere bazli)
  candles_ext   — Binance BTC/ETH/SOL/XRP spot OHLCV (5m)

Her 50 mum = ~4 saat veri. Sistem surekli calisir, bosluk birakmaz.
Backtest v3 icin temel veri kaynagidir.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.candle_collector")

# 5-minute interval in seconds
CANDLE_INTERVAL = 300  # 5m
BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}


class CandleBuilder:
    """Aggregates tick-level price data into OHLCV candles."""

    def __init__(self):
        self._current: dict[str, dict] = {}  # slug -> {open, high, low, close, volume, tick_count, open_ts}

    def tick(self, slug: str, price: float, volume: float = 0.0, ts: float = None):
        """Record a price tick. Call on every WS update or scanner cycle."""
        if price <= 0 or price >= 1.0:
            return
        ts = ts or time.time()

        if slug not in self._current:
            self._current[slug] = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "tick_count": 1,
                "open_ts": ts,
            }
        else:
            c = self._current[slug]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] += volume
            c["tick_count"] += 1

    def flush(self, slug: str) -> Optional[dict]:
        """Close current candle and return it. Returns None if no data."""
        if slug not in self._current:
            return None
        candle = self._current.pop(slug)
        candle["close_ts"] = time.time()
        return candle

    def flush_all(self) -> dict[str, dict]:
        """Close all current candles and return them."""
        result = {}
        for slug in list(self._current.keys()):
            candle = self.flush(slug)
            if candle:
                result[slug] = candle
        return result

    def active_slugs(self) -> list[str]:
        return list(self._current.keys())


class CandleCollector:
    """
    Continuous 5m candle collection system.

    Runs as background task, collects:
    1. Polymarket odds candles (from WS/scanner data)
    2. Binance spot price candles (from REST API)

    Stores everything in main DB for permanent history.
    """

    def __init__(self, db, odds_feed=None, ws_client=None, external_feed=None, httpx_client=None):
        self.db = db
        self.odds_feed = odds_feed
        self.ws_client = ws_client
        self.external_feed = external_feed
        self._httpx_client = httpx_client
        self._running = False
        self._task = None
        self._poly_builder = CandleBuilder()
        self._ext_builder = CandleBuilder()
        self._candle_count = 0
        self._last_binance_fetch = 0
        self._enabled = True  # Can be toggled via brain_flags

    async def initialize_tables(self):
        """Create candle tables in main DB if they don't exist."""
        await self.db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS candles_poly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL DEFAULT '5m',
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL DEFAULT 0,
                tick_count INTEGER DEFAULT 0,
                open_ts TEXT NOT NULL,
                close_ts TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_candles_poly_slug ON candles_poly(slug);
            CREATE INDEX IF NOT EXISTS idx_candles_poly_asset_ts ON candles_poly(asset, close_ts);
            CREATE INDEX IF NOT EXISTS idx_candles_poly_tf ON candles_poly(timeframe, close_ts);

            CREATE TABLE IF NOT EXISTS candles_ext (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL DEFAULT '5m',
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL DEFAULT 0,
                quote_volume REAL DEFAULT 0,
                trades INTEGER DEFAULT 0,
                taker_buy_vol REAL DEFAULT 0,
                open_ts TEXT NOT NULL,
                close_ts TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_candles_ext_unique
                ON candles_ext(symbol, interval, open_ts);
            CREATE INDEX IF NOT EXISTS idx_candles_ext_symbol ON candles_ext(symbol, close_ts);

            CREATE TABLE IF NOT EXISTS candle_collector_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        await self.db.conn.commit()
        logger.info("📊 CandleCollector: tables initialized")

    async def start(self):
        """Start the background collection loop."""
        if self._running:
            return
        await self.initialize_tables()
        self._running = True
        # Phase 82e Sprint 2.1: candle loop death = no 5m candles = signal gap
        self._task = safe_create_task(
            self._collection_loop(), name="candle_collector_loop")
        logger.info("📊 CandleCollector: STARTED (5m interval)")

    async def stop(self):
        """Stop collection and flush remaining candles."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Flush any remaining data
        await self._flush_poly_candles()
        await self._flush_ext_candles()
        logger.info(f"📊 CandleCollector: STOPPED (total candles: {self._candle_count})")

    # ── MAIN LOOP ──

    async def _collection_loop(self):
        """Main loop: every 5 minutes, flush candles and fetch Binance data."""
        logger.info("📊 CandleCollector loop started")

        # Initial Binance backfill (last 24h)
        await self._backfill_binance(hours=24)

        while self._running:
            try:
                # Check if collection is enabled via brain_flags
                if not self._enabled:
                    await asyncio.sleep(30)
                    continue

                # Wait until next 5-minute boundary
                now = time.time()
                next_boundary = (int(now) // CANDLE_INTERVAL + 1) * CANDLE_INTERVAL
                wait_time = next_boundary - now + 2  # +2s buffer for data to arrive

                await asyncio.sleep(min(wait_time, 30))  # Check every 30s max

                # Check if we crossed a 5-minute boundary
                current_boundary = int(time.time()) // CANDLE_INTERVAL * CANDLE_INTERVAL
                last_boundary_key = f"last_flush_{current_boundary}"

                # Collect ticks from WS/scanner
                await self._collect_poly_ticks()

                # At each boundary, flush and store
                if time.time() >= next_boundary:
                    await self._flush_poly_candles()
                    await self._fetch_and_store_binance()

                    # Update meta
                    await self._update_meta("last_collection",
                        datetime.now(timezone.utc).isoformat())
                    await self._update_meta("total_candles", str(self._candle_count))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"📊 CandleCollector error: {e}", exc_info=True)
                await asyncio.sleep(30)

    # ── POLYMARKET ODDS CANDLES ──

    async def _collect_poly_ticks(self):
        """Collect current odds data from WS/scanner into candle builder."""
        try:
            # Source 1: WebSocket live prices
            if self.ws_client and self.ws_client.is_connected:
                for token_id, data in list(self.ws_client.live_prices.items()):
                    price = data.get("price")
                    if price and 0.01 < price < 0.99:
                        # Use token_id as slug for now (engine maps these)
                        self._poly_builder.tick(token_id, price)

            # Source 2: OddsFeed tracked slugs
            if self.odds_feed:
                for slug in list(self.odds_feed._series.keys()):
                    series = self.odds_feed.get_odds_series(slug)
                    if series:
                        latest = series[-1]
                        self._poly_builder.tick(slug, latest)
        except Exception as e:
            logger.debug(f"CandleCollector poly tick error: {e}")

    async def _flush_poly_candles(self):
        """Flush all Polymarket odds candles to DB."""
        candles = self._poly_builder.flush_all()
        if not candles:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        rows = []
        for slug, c in candles.items():
            # Extract asset from slug pattern: btc-updown-5m-XXXXX
            asset = "BTC"
            slug_lower = slug.lower() if isinstance(slug, str) else ""
            if "eth" in slug_lower:
                asset = "ETH"
            elif "sol" in slug_lower:
                asset = "SOL"
            elif "xrp" in slug_lower:
                asset = "XRP"

            rows.append((
                slug, asset, "5m",
                c["open"], c["high"], c["low"], c["close"],
                c["volume"], c["tick_count"],
                datetime.fromtimestamp(c["open_ts"], tz=timezone.utc).isoformat(),
                datetime.fromtimestamp(c.get("close_ts", time.time()), tz=timezone.utc).isoformat(),
                now_iso,
            ))

        if rows:
            try:
                await self.db.conn.executemany(
                    """INSERT INTO candles_poly
                       (slug, asset, timeframe, open_price, high_price, low_price, close_price,
                        volume, tick_count, open_ts, close_ts, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows
                )
                await self.db.conn.commit()
                self._candle_count += len(rows)
                logger.info(f"📊 Flushed {len(rows)} poly candles (total: {self._candle_count})")
            except Exception as e:
                logger.error(f"📊 Poly candle write error: {e}")

    # ── BINANCE EXTERNAL CANDLES ──

    async def _fetch_and_store_binance(self):
        """Fetch latest 5m candles from Binance and store in DB."""
        if not self._httpx_client:
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        for coin, symbol in BINANCE_SYMBOLS.items():
            try:
                url = f"{BINANCE_BASE}/klines"
                params = {"symbol": symbol, "interval": "5m", "limit": 2}

                resp = await self._httpx_client.get(url, params=params, timeout=5.0)
                if resp.status_code != 200:
                    continue

                klines = resp.json()
                for k in klines:
                    open_ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat()
                    close_ts = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc).isoformat()

                    try:
                        await self.db.conn.execute(
                            """INSERT OR IGNORE INTO candles_ext
                               (symbol, interval, open_price, high_price, low_price, close_price,
                                volume, quote_volume, trades, taker_buy_vol, open_ts, close_ts, created_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (symbol, "5m",
                             float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                             float(k[5]), float(k[7]), int(k[8]), float(k[9]),
                             open_ts, close_ts, now_iso)
                        )
                    except Exception:
                        pass  # UNIQUE constraint = already exists

                await self.db.conn.commit()

            except Exception as e:
                logger.debug(f"📊 Binance {symbol} fetch error: {e}")

        self._last_binance_fetch = time.time()

    async def _backfill_binance(self, hours: int = 24):
        """Backfill Binance candles for the last N hours."""
        if not self._httpx_client:
            logger.info("📊 No httpx client — skipping Binance backfill")
            return

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (hours * 3600 * 1000)
        total_stored = 0
        now_iso = datetime.now(timezone.utc).isoformat()

        for coin, symbol in BINANCE_SYMBOLS.items():
            try:
                url = f"{BINANCE_BASE}/klines"
                params = {
                    "symbol": symbol,
                    "interval": "5m",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                }

                resp = await self._httpx_client.get(url, params=params, timeout=10.0)
                if resp.status_code != 200:
                    logger.warning(f"📊 Binance backfill {symbol}: HTTP {resp.status_code}")
                    continue

                klines = resp.json()
                rows = []
                for k in klines:
                    open_ts = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat()
                    close_ts = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc).isoformat()
                    rows.append((
                        symbol, "5m",
                        float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                        float(k[5]), float(k[7]), int(k[8]), float(k[9]),
                        open_ts, close_ts, now_iso,
                    ))

                if rows:
                    await self.db.conn.executemany(
                        """INSERT OR IGNORE INTO candles_ext
                           (symbol, interval, open_price, high_price, low_price, close_price,
                            volume, quote_volume, trades, taker_buy_vol, open_ts, close_ts, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        rows
                    )
                    await self.db.conn.commit()
                    total_stored += len(rows)
                    logger.info(f"📊 Backfill {symbol}: {len(rows)} candles ({hours}h)")

                await asyncio.sleep(0.2)  # Rate limit

            except Exception as e:
                logger.error(f"📊 Binance backfill {symbol} error: {e}")

        logger.info(f"📊 Binance backfill complete: {total_stored} candles stored")

    async def _flush_ext_candles(self):
        """Flush external candle builder (used for non-Binance sources)."""
        candles = self._ext_builder.flush_all()
        # Currently ext candles come from Binance API directly, not builder

    # ── META ──

    async def _update_meta(self, key: str, value: str):
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                """INSERT OR REPLACE INTO candle_collector_meta (key, value, updated_at)
                   VALUES (?, ?, ?)""", (key, value, now))
            await self.db.conn.commit()
        except Exception:
            pass

    # ── QUERY HELPERS ──

    async def get_poly_candles(self, asset: str = "BTC", timeframe: str = "5m",
                                limit: int = 100) -> list[dict]:
        """Get recent Polymarket odds candles."""
        rows = []
        try:
            async with self.db.conn.execute(
                """SELECT * FROM candles_poly
                   WHERE asset = ? AND timeframe = ?
                   ORDER BY close_ts DESC LIMIT ?""",
                (asset, timeframe, limit)
            ) as c:
                async for row in c:
                    rows.append(dict(row))
        except Exception as e:
            logger.error(f"get_poly_candles error: {e}")
        rows.reverse()
        return rows

    async def get_ext_candles(self, symbol: str = "BTCUSDT", interval: str = "5m",
                               limit: int = 100) -> list[dict]:
        """Get recent Binance candles."""
        rows = []
        try:
            async with self.db.conn.execute(
                """SELECT * FROM candles_ext
                   WHERE symbol = ? AND interval = ?
                   ORDER BY close_ts DESC LIMIT ?""",
                (symbol, interval, limit)
            ) as c:
                async for row in c:
                    rows.append(dict(row))
        except Exception as e:
            logger.error(f"get_ext_candles error: {e}")
        rows.reverse()
        return rows

    async def get_candle_stats(self) -> dict:
        """Get statistics about collected candles."""
        stats = {"poly_total": 0, "ext_total": 0, "assets": {}, "oldest": None, "newest": None}
        try:
            async with self.db.conn.execute("SELECT COUNT(*) FROM candles_poly") as c:
                row = await c.fetchone()
                stats["poly_total"] = row[0] if row else 0

            async with self.db.conn.execute("SELECT COUNT(*) FROM candles_ext") as c:
                row = await c.fetchone()
                stats["ext_total"] = row[0] if row else 0

            async with self.db.conn.execute(
                "SELECT asset, COUNT(*) as cnt FROM candles_poly GROUP BY asset"
            ) as c:
                async for row in c:
                    stats["assets"][row["asset"]] = row["cnt"]

            async with self.db.conn.execute(
                "SELECT MIN(open_ts) as oldest, MAX(close_ts) as newest FROM candles_ext"
            ) as c:
                row = await c.fetchone()
                if row:
                    stats["oldest"] = row["oldest"]
                    stats["newest"] = row["newest"]
        except Exception as e:
            logger.error(f"get_candle_stats error: {e}")

        return stats

    def get_status(self) -> dict:
        """Quick status for /ws or monitoring."""
        return {
            "running": self._running,
            "candle_count": self._candle_count,
            "active_builders": len(self._poly_builder.active_slugs()),
            "last_binance_fetch": self._last_binance_fetch,
        }
