"""
PolyPaper Bot — Candle Collector (P0-08-E3 multi-TF, 2026-05-08)
=================================================================
Sürekli 5m mum verisi toplar ve ana DB'ye kaydeder.
Polymarket odds + Binance fiyat verileri.

Tablolar (db.migrations v18):
  candles_poly  — Polymarket odds OHLCV (asset_id + slug + timeframe + open_ts INT)
                   Her aktif market için 5m candle. 15m/1h/24h marketler kendi
                   TF'lerinde yazılır (timeframe field'ı doğru TF'i taşır).
  candles_ext   — Binance BTC/ETH/SOL/XRP spot OHLCV (symbol, interval='5m', open_ts INT)
                   YALNIZCA 5m base. 15m/1h/24h runtime aggregation ile üretilir
                   (`aggregate_candles_ext()` helper). Disk verimli + tutarlılık garantili.

P0-08-E3 (Heddas direktifi 2026-05-08):
  - "1 günde 5m candle data ile 1h data aynı değil mi" → Binance için EVET, aggregation OK.
  - Polymarket odds: her TF ayrı market (farklı condition_id). Her market kendi candle stream'i.

T11.8-B (2026-04-24): every catch in this module is annotated `# noqa: BLE001`.
Data-feed orchestrator: WebSockets + httpx + aiosqlite reconnect chain.
Single network blip should NOT crash the feed thread.

Reference: memory/reference_polymarket_updown_discovery.md
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from core.bg_task import safe_create_task

logger = logging.getLogger("polypaper.data.candle_collector")

# 5m base interval (seconds). Diğer TF'ler aggregate ile.
CANDLE_INTERVAL = 300

# Aggregation factor: 5m → target_tf
AGGREGATION_FACTORS = {
    "5m":  1,
    "15m": 3,
    "1h":  12,
    "4h":  48,
    "24h": 288,
}

BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}


class CandleBuilder:
    """Aggregates tick-level price data into OHLCV candles.

    Key: (asset_id, timeframe) — each market+TF gets its own builder slot.
    """

    def __init__(self):
        self._current: dict[tuple, dict] = {}

    def tick(self, asset_id: str, timeframe: str, price: float,
             slug: str = "", asset: str = "?",
             volume: float = 0.0, ts: float | None = None):
        if price <= 0 or price >= 1.0:
            return
        ts = ts or time.time()
        key = (asset_id, timeframe)

        if key not in self._current:
            self._current[key] = {
                "asset_id": asset_id,
                "slug": slug,
                "asset": asset,
                "timeframe": timeframe,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "tick_count": 1,
                "open_ts": ts,
            }
        else:
            c = self._current[key]
            c["high"] = max(c["high"], price)
            c["low"] = min(c["low"], price)
            c["close"] = price
            c["volume"] += volume
            c["tick_count"] += 1

    def flush_all(self) -> list[dict]:
        result = list(self._current.values())
        self._current.clear()
        return result

    def active_count(self) -> int:
        return len(self._current)


class CandleCollector:
    """Continuous 5m candle collection — multi-TF aware.

    Polymarket: scanner.active_markets'tan iter, her market kendi TF'inde.
    Binance: tek 5m polling, runtime aggregation.
    """

    def __init__(self, db, odds_feed=None, ws_client=None,
                 external_feed=None, httpx_client=None, scanner=None):
        self.db = db
        self.odds_feed = odds_feed
        self.ws_client = ws_client
        self.external_feed = external_feed
        self._httpx_client = httpx_client
        self.scanner = scanner  # P0-08-E3: TF-aware market discovery
        self._running = False
        self._task = None
        self._poly_builder = CandleBuilder()
        self._candle_count = 0
        self._last_binance_fetch = 0
        self._enabled = True

    async def start(self):
        """Schema is provisioned by db.migrations v18 (P0-08-E2)."""
        if self._running:
            return
        self._running = True
        self._task = safe_create_task(
            self._collection_loop(), name="candle_collector_loop")
        logger.info("📊 CandleCollector: STARTED (5m base + multi-TF aggregation)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._flush_poly_candles()
        logger.info(
            f"📊 CandleCollector: STOPPED (total candles: {self._candle_count})")

    # ── MAIN LOOP ──

    async def _collection_loop(self):
        logger.info("📊 CandleCollector loop started")

        await self._backfill_binance(hours=24)

        while self._running:
            try:
                if not self._enabled:
                    await asyncio.sleep(30)
                    continue

                now = time.time()
                next_boundary = (int(now) // CANDLE_INTERVAL + 1) * CANDLE_INTERVAL
                wait_time = next_boundary - now + 2

                await asyncio.sleep(min(wait_time, 30))

                # Tick collection from WS + scanner
                await self._collect_poly_ticks()

                if time.time() >= next_boundary:
                    await self._flush_poly_candles()
                    await self._fetch_and_store_binance()

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"📊 CandleCollector error: {e}", exc_info=True)
                await asyncio.sleep(30)

    # ── POLYMARKET ODDS CANDLES (per-market, TF-aware) ──

    async def _collect_poly_ticks(self):
        """Scanner.active_markets'tan iter, her market için kendi TF'inde tick.

        active_markets key formatı: f"{asset}_{tf}" → list of market dicts.
        Her market dict: {slug, condition_id, clobTokenIds, ...}
        UP token = clobTokenIds[0], price = up_odds.
        """
        try:
            if self.scanner is None or not hasattr(self.scanner, "active_markets"):
                return

            # Iterate scanner.active_markets matrix
            for key, markets in list(self.scanner.active_markets.items()):
                # key = "BTC_5m" / "BTC_15m" / "BTC_1h" / "BTC_24h" / etc.
                if "_" not in key:
                    continue
                parts = key.split("_", 1)
                if len(parts) < 2:
                    continue
                asset, tf = parts[0], parts[1]

                if not isinstance(markets, list):
                    continue

                for m in markets[:2]:  # Top 2 closest markets per (asset, tf)
                    slug = m.get("slug", "")
                    if not slug:
                        continue
                    # Get up_odds from scanner cache
                    odds = (self.scanner.get_current_odds(slug)
                            if hasattr(self.scanner, "get_current_odds") else None)
                    if not odds:
                        continue
                    up_odds = odds.get("up_odds")
                    up_token = odds.get("up_token")
                    if up_odds is None or not up_token:
                        continue
                    try:
                        price = float(up_odds)
                    except (TypeError, ValueError):
                        continue
                    self._poly_builder.tick(
                        asset_id=str(up_token),
                        timeframe=tf,
                        price=price,
                        slug=slug,
                        asset=asset.upper(),
                    )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"poly tick collect: {e}")

    async def _flush_poly_candles(self):
        """Flush Polymarket candle builder → candles_poly (v18 schema)."""
        candles = self._poly_builder.flush_all()
        if not candles:
            return

        rows = []
        for c in candles:
            open_ts_int = int(c["open_ts"])
            rows.append((
                c["asset_id"],
                c["slug"],
                c["asset"],
                c["timeframe"],
                open_ts_int,
                c["open"],
                c["high"],
                c["low"],
                c["close"],
                c["volume"],
            ))

        if rows:
            try:
                await self.db.conn.executemany(
                    """INSERT OR REPLACE INTO candles_poly
                       (asset_id, slug, asset, timeframe, open_ts,
                        open, high, low, close, volume)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                await self.db.conn.commit()
                self._candle_count += len(rows)
                logger.info(
                    f"📊 Flushed {len(rows)} poly candles "
                    f"(total: {self._candle_count})"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"📊 poly candle write: {e}")

    # ── BINANCE EXTERNAL CANDLES (5m base only) ──

    async def _fetch_and_store_binance(self):
        if not self._httpx_client:
            return

        for coin, symbol in BINANCE_SYMBOLS.items():
            try:
                url = f"{BINANCE_BASE}/klines"
                params = {"symbol": symbol, "interval": "5m", "limit": 2}

                resp = await self._httpx_client.get(url, params=params, timeout=5.0)
                if resp.status_code != 200:
                    continue

                klines = resp.json()
                rows = []
                for k in klines:
                    rows.append((
                        symbol, "5m",
                        int(k[0]),  # open_ts in ms
                        float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                        float(k[5]),
                    ))
                if rows:
                    await self.db.conn.executemany(
                        """INSERT OR REPLACE INTO candles_ext
                           (symbol, interval, open_ts,
                            open, high, low, close, volume)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
                    await self.db.conn.commit()

            except Exception as e:  # noqa: BLE001
                logger.debug(f"📊 Binance {symbol}: {e}")

        self._last_binance_fetch = time.time()

    async def _backfill_binance(self, hours: int = 24):
        if not self._httpx_client:
            logger.info("📊 No httpx — skipping backfill")
            return

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (hours * 3600 * 1000)
        total = 0

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
                    logger.warning(f"📊 backfill {symbol}: HTTP {resp.status_code}")
                    continue

                klines = resp.json()
                rows = [(
                    symbol, "5m",
                    int(k[0]),
                    float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                    float(k[5]),
                ) for k in klines]

                if rows:
                    await self.db.conn.executemany(
                        """INSERT OR REPLACE INTO candles_ext
                           (symbol, interval, open_ts,
                            open, high, low, close, volume)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        rows,
                    )
                    await self.db.conn.commit()
                    total += len(rows)
                    logger.info(f"📊 Backfill {symbol}: {len(rows)} candles ({hours}h)")

                await asyncio.sleep(0.2)

            except Exception as e:  # noqa: BLE001
                logger.error(f"📊 backfill {symbol}: {e}")

        logger.info(f"📊 Backfill complete: {total} candles")

    # ── QUERY HELPERS — TF-aware ──

    async def get_ext_candles(self, symbol: str = "BTCUSDT",
                              interval: str = "5m",
                              limit: int = 100) -> list[dict]:
        """Get external (Binance) candles. interval='5m' direct read; otherwise aggregate."""
        if interval == "5m":
            return await self._read_ext_5m(symbol, limit)
        return await self.aggregate_ext_candles(symbol, interval, limit)

    async def _read_ext_5m(self, symbol: str, limit: int) -> list[dict]:
        rows = []
        try:
            async with self.db.conn.execute(
                """SELECT symbol, interval, open_ts, open, high, low, close, volume
                   FROM candles_ext
                   WHERE symbol = ? AND interval = '5m'
                   ORDER BY open_ts DESC LIMIT ?""",
                (symbol, limit),
            ) as c:
                async for row in c:
                    rows.append(dict(row))
        except Exception as e:  # noqa: BLE001
            logger.error(f"_read_ext_5m: {e}")
        rows.reverse()
        return rows

    async def aggregate_ext_candles(self, symbol: str, target_tf: str,
                                    limit: int = 100) -> list[dict]:
        """Aggregate 5m candles → target TF (15m/1h/24h).

        Returns most-recent N target-TF candles.
        Algorithm: read N×factor 5m candles, group by floor(open_ts / (factor*300_000)).
        """
        factor = AGGREGATION_FACTORS.get(target_tf)
        if not factor:
            logger.warning(f"aggregate_ext_candles: unknown TF {target_tf}")
            return []
        if factor == 1:
            return await self._read_ext_5m(symbol, limit)

        n_5m = limit * factor
        rows_5m = await self._read_ext_5m(symbol, n_5m)
        if not rows_5m:
            return []

        # Bucket size in ms
        bucket_ms = factor * CANDLE_INTERVAL * 1000
        buckets: dict[int, list[dict]] = {}
        for r in rows_5m:
            bucket_key = (r["open_ts"] // bucket_ms) * bucket_ms
            buckets.setdefault(bucket_key, []).append(r)

        aggregated = []
        for bucket_ts in sorted(buckets.keys()):
            group = buckets[bucket_ts]
            # Order by open_ts within bucket
            group.sort(key=lambda x: x["open_ts"])
            aggregated.append({
                "symbol": symbol,
                "interval": target_tf,
                "open_ts": bucket_ts,
                "open": group[0]["open"],
                "high": max(r["high"] for r in group),
                "low":  min(r["low"]  for r in group),
                "close": group[-1]["close"],
                "volume": sum(r["volume"] for r in group),
                "_n_5m_periods": len(group),
            })
        return aggregated[-limit:]

    async def get_poly_candles(self, asset: str = "BTC", timeframe: str = "5m",
                               limit: int = 100) -> list[dict]:
        """Get Polymarket odds candles for given asset+TF (per-market stream)."""
        rows = []
        try:
            async with self.db.conn.execute(
                """SELECT asset_id, slug, asset, timeframe, open_ts,
                          open, high, low, close, volume
                   FROM candles_poly
                   WHERE asset = ? AND timeframe = ?
                   ORDER BY open_ts DESC LIMIT ?""",
                (asset, timeframe, limit),
            ) as c:
                async for row in c:
                    rows.append(dict(row))
        except Exception as e:  # noqa: BLE001
            logger.error(f"get_poly_candles: {e}")
        rows.reverse()
        return rows

    async def get_candle_stats(self) -> dict:
        stats = {"poly_total": 0, "ext_total": 0,
                 "poly_by_tf": {}, "ext_oldest": None, "ext_newest": None}
        try:
            async with self.db.conn.execute(
                "SELECT COUNT(*) FROM candles_poly") as c:
                stats["poly_total"] = (await c.fetchone())[0]

            async with self.db.conn.execute(
                "SELECT COUNT(*) FROM candles_ext") as c:
                stats["ext_total"] = (await c.fetchone())[0]

            async with self.db.conn.execute(
                "SELECT timeframe, COUNT(*) FROM candles_poly GROUP BY timeframe") as c:
                async for row in c:
                    stats["poly_by_tf"][row[0]] = row[1]

            async with self.db.conn.execute(
                "SELECT MIN(open_ts), MAX(open_ts) FROM candles_ext") as c:
                row = await c.fetchone()
                if row and row[0]:
                    stats["ext_oldest"] = row[0]
                    stats["ext_newest"] = row[1]
        except Exception as e:  # noqa: BLE001
            logger.error(f"get_candle_stats: {e}")
        return stats

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "candle_count": self._candle_count,
            "active_builders": self._poly_builder.active_count(),
            "last_binance_fetch": self._last_binance_fetch,
        }
