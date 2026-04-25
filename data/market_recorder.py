"""
PolyPaper Bot - Market Data Recorder (Phase 36: Hardcore Backtest)
==================================================================
Her aktif market icin 10 saniyede bir TAM veri kaydeder:
  - L2 Orderbook (tum bid/ask seviyeleri, JSON)
  - Best bid/ask, spread, toplam depth
  - Binance spot fiyat + volume
  - Polymarket UP/DOWN odds
  - Timestamp (ms hassasiyet)

Bu veri, gercek "canli backtest" icin temel kaynak olacak.
Backtest motoru bu snapshot'lari replay ederek gercek trade
atiyormus gibi sonuc uretecek.

Tablolar:
  ob_snapshots — L2 orderbook + fiyat + depth snapshot'lari
  ob_trades    — Polymarket'ten tick-level fiyat degisimleri (WS)

T11.8-B (2026-04-24): every catch in this module is annotated
`# noqa: BLE001`. Data-feed orchestrator: WebSockets + httpx +
json + aiosqlite + asyncio reconnect chain. Single network blip
or schema drift should NOT crash the feed thread — the reconnect
loop handles it. Wide catches at the orchestration layer are
intentional and logged.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.market_recorder")

# Recording interval in seconds
# Phase 38d: 10s → 2s — captures 5× more intra-interval price moves for
# realistic backtesting. CLOB API rate is comfortable with 2s per-market
# given we only track top 2 markets per asset/tf pair (~10 markets max).
SNAPSHOT_INTERVAL = 2   # Her 2 saniyede 1 snapshot
CLEANUP_DAYS = 30       # 30 gunden eski veriyi sil
# Phase 57: 20 → 50 levels for deeper L2 capture.
# More levels = more realistic VWAP fills in REAL_ORDERBOOK backtest mode.
# Storage impact: ~+40% per snapshot JSON blob (~2KB → ~2.8KB).
MAX_OB_LEVELS = int(os.getenv("MAX_OB_LEVELS", "50"))


class MarketRecorder:
    """
    High-fidelity market data recorder for realistic backtesting.

    Her aktif market icin periyodik olarak:
    1. Polymarket CLOB'dan L2 orderbook ceker
    2. Best bid/ask/spread/depth hesaplar
    3. Binance spot fiyat ve volume ekler
    4. Hepsini ob_snapshots tablosuna yazar

    Ayrica WS price tick'lerini ob_trades'e kaydeder
    (tick-level resolution icin).
    """

    def __init__(self, db, polymarket_client, scanner=None,
                 external_feed=None, ws_client=None):
        self.db = db
        self.pm_client = polymarket_client
        self.scanner = scanner
        self.ext_feed = external_feed
        self.ws_client = ws_client
        self._running = False
        self._task = None
        self._snapshot_count = 0
        self._trade_count = 0
        self._enabled = True  # brain_flags toggle
        # Track last tick prices for trade recording
        self._last_prices: dict[str, float] = {}  # token_id -> last_price
        # Stats
        self._errors = 0
        self._last_error = ""
        # Phase 39 (P1.1): real-trade counter + engine listener hook
        self._real_trade_count = 0
        self._engine_trade_listener = None  # set by engine.start()

    # ═══════════════════════════════════════════════
    #  DB INITIALIZATION
    # ═══════════════════════════════════════════════

    async def initialize_tables(self):
        """Create recorder tables if they don't exist."""
        await self.db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ob_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- Market identification
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                up_token_id TEXT,
                down_token_id TEXT,

                -- Timestamp (ms precision)
                ts_ms INTEGER NOT NULL,
                ts_iso TEXT NOT NULL,

                -- UP token orderbook
                up_best_bid REAL,
                up_best_ask REAL,
                up_spread REAL,
                up_bid_depth_usd REAL,
                up_ask_depth_usd REAL,
                up_bid_levels INTEGER,
                up_ask_levels INTEGER,
                up_bids_json TEXT,
                up_asks_json TEXT,

                -- DOWN token orderbook
                down_best_bid REAL,
                down_best_ask REAL,
                down_spread REAL,
                down_bid_depth_usd REAL,
                down_ask_depth_usd REAL,
                down_bid_levels INTEGER,
                down_ask_levels INTEGER,
                down_bids_json TEXT,
                down_asks_json TEXT,

                -- Derived metrics
                mid_price_up REAL,
                mid_price_down REAL,
                implied_prob_up REAL,
                implied_prob_down REAL,

                -- External data (Binance spot)
                binance_price REAL,
                binance_volume_24h REAL,
                binance_price_change_pct REAL,

                -- Market context
                market_volume REAL,
                market_liquidity REAL,
                market_start_time TEXT,
                market_end_time TEXT,
                elapsed_pct REAL,

                created_at TEXT NOT NULL
            );

            -- Performance indexes
            CREATE INDEX IF NOT EXISTS idx_ob_snap_slug_ts
                ON ob_snapshots(slug, ts_ms);
            CREATE INDEX IF NOT EXISTS idx_ob_snap_asset_tf_ts
                ON ob_snapshots(asset, timeframe, ts_ms);
            CREATE INDEX IF NOT EXISTS idx_ob_snap_ts
                ON ob_snapshots(ts_ms);

            -- Tick-level trade/price changes from WS
            -- Phase 39 (P1.1): event_type distinguishes 'price_tick' (from
            -- price_change events, no real fill) vs 'trade' (real
            -- last_trade_price fill with size + side).
            CREATE TABLE IF NOT EXISTS ob_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                token_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                price REAL NOT NULL,
                prev_price REAL,
                price_change REAL,
                ts_ms INTEGER NOT NULL,
                ts_iso TEXT NOT NULL,
                source TEXT DEFAULT 'ws',
                size REAL,
                side TEXT,
                event_type TEXT DEFAULT 'price_tick'
            );

            CREATE INDEX IF NOT EXISTS idx_ob_trades_slug_ts
                ON ob_trades(slug, ts_ms);
            CREATE INDEX IF NOT EXISTS idx_ob_trades_ts
                ON ob_trades(ts_ms);

            -- Phase 47f.10 P4#19: whale trades ($1000+ notional)
            CREATE TABLE IF NOT EXISTS whale_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                token_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                size REAL NOT NULL,
                notional_usd REAL NOT NULL,
                ts_ms INTEGER NOT NULL,
                ts_iso TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_whale_slug_ts
                ON whale_trades(slug, ts_ms);
            CREATE INDEX IF NOT EXISTS idx_whale_notional
                ON whale_trades(notional_usd DESC);

            -- Recorder metadata
            CREATE TABLE IF NOT EXISTS recorder_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        await self.db.conn.commit()

        # Phase 39 (P1.1): migrate ob_trades for existing DBs
        for col, ddl in (
            ("size", "ALTER TABLE ob_trades ADD COLUMN size REAL"),
            ("side", "ALTER TABLE ob_trades ADD COLUMN side TEXT"),
            ("event_type",
             "ALTER TABLE ob_trades ADD COLUMN event_type TEXT DEFAULT 'price_tick'"),
        ):
            try:
                await self.db.conn.execute(ddl)
            except Exception:  # noqa: BLE001
                pass  # column already exists
        try:
            await self.db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ob_trades_event_type "
                "ON ob_trades(event_type, ts_ms)")
        except Exception:  # noqa: BLE001
            pass
        await self.db.conn.commit()
        logger.info("📸 MarketRecorder: tables initialized (P1.1 trades schema)")

    # ═══════════════════════════════════════════════
    #  START / STOP
    # ═══════════════════════════════════════════════

    async def start(self):
        """Start the background recording loop."""
        if self._running:
            return
        await self.initialize_tables()

        # Phase 38d: Wire WS tick recording.
        # Captures the scanner's existing _on_price_callback (which must be
        # installed first — see main.py startup order) and wraps it so both
        # scanner bridge AND recorder tick-logging fire on every WS price.
        # Uses the running loop (asyncio.get_event_loop() is deprecated inside
        # callbacks started from outside an asyncio coroutine).
        if self.ws_client:
            original_cb = getattr(self.ws_client, '_on_price_callback', None)
            self._original_ws_callback = original_cb
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            def combined_callback(token_id: str, price: float):
                # 1. Call original scanner bridge callback (preserves odds flow)
                if original_cb:
                    try:
                        original_cb(token_id, price)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"scanner cb error: {e}")
                # 2. Schedule async tick recording on the running loop
                try:
                    loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._record_tick(token_id, price), loop=loop)
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"tick schedule error: {e}")

            self.ws_client._on_price_callback = combined_callback
            logger.info(
                f"📸 MarketRecorder: WS tick callback wired "
                f"(scanner_cb={'yes' if original_cb else 'NONE!'})"
            )

            # Phase 39 (P1.1): wire real trade event callback
            def trade_callback(token_id: str, price: float, size: float,
                                side: str, ts_ms: int):
                # 1. Forward to engine for maker queue tracking (P1.2)
                if hasattr(self, "_engine_trade_listener") and \
                        self._engine_trade_listener:
                    try:
                        self._engine_trade_listener(
                            token_id, price, size, side, ts_ms)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"engine trade listener: {e}")
                # 2. Persist to ob_trades
                try:
                    loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(
                            self._record_trade(
                                token_id, price, size, side, ts_ms),
                            loop=loop)
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"trade schedule error: {e}")

            self.ws_client._on_trade_callback = trade_callback
            logger.info("📸 MarketRecorder: WS trade callback wired (P1.1)")

        self._running = True
        # Phase 82e Sprint 2.1: safe_create_task — if the recording loop dies
        # silently we lose ALL backtest data. Must be notified.
        self._task = safe_create_task(
            self._recording_loop(), name="market_recorder_loop")
        logger.info(f"📸 MarketRecorder: STARTED ({SNAPSHOT_INTERVAL}s interval)")

    async def stop(self):
        """Stop recording."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Restore original WS callback
        if self.ws_client and hasattr(self, '_original_ws_callback'):
            self.ws_client._on_price_callback = self._original_ws_callback

        await self._update_meta("total_snapshots", str(self._snapshot_count))
        await self._update_meta("total_trades", str(self._trade_count))
        logger.info(f"📸 MarketRecorder: STOPPED "
                     f"(snapshots={self._snapshot_count}, trades={self._trade_count})")

    # ═══════════════════════════════════════════════
    #  MAIN RECORDING LOOP
    # ═══════════════════════════════════════════════

    async def _recording_loop(self):
        """Main loop: every SNAPSHOT_INTERVAL seconds, record all active markets."""
        while self._running:
            try:
                if not self._enabled:
                    await asyncio.sleep(30)
                    continue

                loop_start = time.time()

                # Get all active markets from scanner
                markets = self._get_active_markets()

                if markets:
                    # Record snapshots for all active markets concurrently
                    tasks = [self._record_market_snapshot(m) for m in markets]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    success = sum(1 for r in results if r is True)
                    errors = sum(1 for r in results if isinstance(r, Exception))

                    if errors > 0:
                        self._errors += errors
                        logger.debug(f"📸 Snapshot batch: {success} ok, {errors} err")

                # Periodic cleanup (every hour)
                if self._snapshot_count > 0 and self._snapshot_count % 360 == 0:
                    await self._cleanup_old_data()

                # Update meta
                if self._snapshot_count % 60 == 0 and self._snapshot_count > 0:
                    await self._update_meta("total_snapshots", str(self._snapshot_count))
                    await self._update_meta("total_trades", str(self._trade_count))
                    await self._update_meta("last_recording",
                        datetime.now(timezone.utc).isoformat())

                # Sleep until next interval
                elapsed = time.time() - loop_start
                sleep_time = max(1, SNAPSHOT_INTERVAL - elapsed)
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self._errors += 1
                self._last_error = str(e)
                logger.error(f"📸 Recording loop error: {e}", exc_info=True)
                await asyncio.sleep(SNAPSHOT_INTERVAL)

    # ═══════════════════════════════════════════════
    #  SNAPSHOT RECORDING
    # ═══════════════════════════════════════════════

    def _get_active_markets(self) -> list[dict]:
        """Get list of active markets from scanner with token IDs."""
        if not self.scanner:
            return []

        markets = []
        for key, market_list in self.scanner.active_markets.items():
            for m in market_list[:2]:  # Top 2 per asset/tf pair
                slug = m.get("slug", "")
                if not slug:
                    continue

                # Get odds cache for token IDs
                odds = self.scanner.odds_cache.get(slug, {})
                if not odds:
                    odds = self.scanner.last_known_odds.get(slug, {})

                up_token = odds.get("up_token", "")
                down_token = odds.get("down_token", "")

                if not up_token:
                    continue  # Need at least UP token

                # Parse asset and timeframe from key
                parts = key.split("_")
                asset = parts[0] if parts else "BTC"
                tf = parts[1] if len(parts) > 1 else "5m"

                markets.append({
                    "slug": slug,
                    "asset": asset,
                    "timeframe": tf,
                    "up_token": up_token,
                    "down_token": down_token,
                    "market_data": m,
                    "odds": odds,
                })

        return markets

    async def _record_market_snapshot(self, market: dict) -> bool:
        """Record a single market's full orderbook + price snapshot."""
        try:
            slug = market["slug"]
            up_token = market["up_token"]
            down_token = market.get("down_token", "")
            asset = market["asset"]
            tf = market["timeframe"]
            mdata = market.get("market_data", {})
            odds = market.get("odds", {})

            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat()

            # ── 1. Fetch UP orderbook ──
            up_ob = await self._fetch_orderbook(up_token)

            # ── 2. Fetch DOWN orderbook ──
            down_ob = None
            if down_token:
                down_ob = await self._fetch_orderbook(down_token)

            # ── 3. Calculate metrics ──
            up_metrics = self._calc_ob_metrics(up_ob)
            down_metrics = self._calc_ob_metrics(down_ob)

            # Mid prices
            mid_up = ((up_metrics["best_bid"] + up_metrics["best_ask"]) / 2
                      if up_metrics["best_bid"] > 0 and up_metrics["best_ask"] > 0
                      else up_metrics["best_ask"])
            mid_down = ((down_metrics["best_bid"] + down_metrics["best_ask"]) / 2
                        if down_metrics["best_bid"] > 0 and down_metrics["best_ask"] > 0
                        else down_metrics["best_ask"])

            # Implied probability (UP odds = UP best_ask for buyer)
            implied_up = mid_up if mid_up > 0 else odds.get("up_odds", 0)
            implied_down = mid_down if mid_down > 0 else odds.get("down_odds", 0)

            # ── 4. Binance spot price ──
            binance_price = 0.0
            binance_vol = 0.0
            binance_change = 0.0
            if self.ext_feed:
                bp = self.ext_feed.get_price(asset)
                if bp:
                    binance_price = bp
                div = self.ext_feed.get_divergence(asset, implied_up, slug)
                if div and isinstance(div, dict):
                    binance_change = div.get("spot_change_pct", 0)

            # ── 5. Market timing ──
            start_time = mdata.get("start_time", mdata.get("game_start_time", ""))
            end_time = mdata.get("end_time", mdata.get("game_end_time",
                        mdata.get("expiration", "")))
            elapsed_pct = 0.0
            if start_time and end_time:
                try:
                    from datetime import datetime as dt
                    st = dt.fromisoformat(start_time.replace("Z", "+00:00"))
                    et = dt.fromisoformat(end_time.replace("Z", "+00:00"))
                    now_utc = datetime.now(timezone.utc)
                    total = (et - st).total_seconds()
                    elapsed = (now_utc - st).total_seconds()
                    elapsed_pct = max(0, min(1, elapsed / total)) if total > 0 else 0
                except Exception:  # noqa: BLE001
                    pass

            market_vol = float(mdata.get("volume", 0) or 0)
            market_liq = float(mdata.get("liquidity", 0) or 0)

            # ── 6. Write snapshot ──
            await self.db.conn.execute(
                """INSERT INTO ob_snapshots
                   (slug, asset, timeframe, up_token_id, down_token_id,
                    ts_ms, ts_iso,
                    up_best_bid, up_best_ask, up_spread,
                    up_bid_depth_usd, up_ask_depth_usd,
                    up_bid_levels, up_ask_levels,
                    up_bids_json, up_asks_json,
                    down_best_bid, down_best_ask, down_spread,
                    down_bid_depth_usd, down_ask_depth_usd,
                    down_bid_levels, down_ask_levels,
                    down_bids_json, down_asks_json,
                    mid_price_up, mid_price_down,
                    implied_prob_up, implied_prob_down,
                    binance_price, binance_volume_24h, binance_price_change_pct,
                    market_volume, market_liquidity,
                    market_start_time, market_end_time, elapsed_pct,
                    created_at)
                   VALUES (?,?,?,?,?, ?,?,
                           ?,?,?, ?,?, ?,?, ?,?,
                           ?,?,?, ?,?, ?,?, ?,?,
                           ?,?, ?,?,
                           ?,?,?, ?,?, ?,?,?, ?)""",
                (slug, asset, tf, up_token, down_token,
                 now_ms, now_iso,
                 up_metrics["best_bid"], up_metrics["best_ask"], up_metrics["spread"],
                 up_metrics["bid_depth"], up_metrics["ask_depth"],
                 up_metrics["bid_levels"], up_metrics["ask_levels"],
                 up_metrics["bids_json"], up_metrics["asks_json"],
                 down_metrics["best_bid"], down_metrics["best_ask"], down_metrics["spread"],
                 down_metrics["bid_depth"], down_metrics["ask_depth"],
                 down_metrics["bid_levels"], down_metrics["ask_levels"],
                 down_metrics["bids_json"], down_metrics["asks_json"],
                 mid_up, mid_down,
                 implied_up, implied_down,
                 binance_price, binance_vol, binance_change,
                 market_vol, market_liq,
                 start_time, end_time, elapsed_pct,
                 now_iso)
            )
            await self.db.conn.commit()
            self._snapshot_count += 1
            return True

        except Exception as e:  # noqa: BLE001
            self._errors += 1
            self._last_error = str(e)
            logger.debug(f"📸 Snapshot error [{market.get('slug','')}]: {e}")
            return False

    async def _fetch_orderbook(self, token_id: str) -> Optional[dict]:
        """Fetch L2 orderbook from Polymarket CLOB."""
        if not token_id or not self.pm_client:
            return None
        try:
            return await self.pm_client.get_orderbook(token_id)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"OB fetch error: {e}")
            return None

    def _calc_ob_metrics(self, ob: Optional[dict]) -> dict:
        """Calculate orderbook metrics from raw L2 data."""
        empty = {
            "best_bid": 0.0, "best_ask": 0.0, "spread": 0.0,
            "bid_depth": 0.0, "ask_depth": 0.0,
            "bid_levels": 0, "ask_levels": 0,
            "bids_json": "[]", "asks_json": "[]",
        }
        if not ob:
            return empty

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        if not bids and not asks:
            return empty

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        spread = (best_ask - best_bid) if best_bid > 0 and best_ask > 0 else 0.0

        # Total depth in USD (price × size for each level)
        bid_depth = sum(p * s for p, s in bids)
        ask_depth = sum(p * s for p, s in asks)

        # Trim to max levels for storage
        trimmed_bids = bids[:MAX_OB_LEVELS]
        trimmed_asks = asks[:MAX_OB_LEVELS]

        return {
            "best_bid": round(best_bid, 6),
            "best_ask": round(best_ask, 6),
            "spread": round(spread, 6),
            "bid_depth": round(bid_depth, 2),
            "ask_depth": round(ask_depth, 2),
            "bid_levels": len(bids),
            "ask_levels": len(asks),
            "bids_json": json.dumps(trimmed_bids),
            "asks_json": json.dumps(trimmed_asks),
        }

    # ═══════════════════════════════════════════════
    #  TICK-LEVEL TRADE RECORDING
    # ═══════════════════════════════════════════════

    async def _record_tick(self, token_id: str, price: float):
        """Record a WS price tick as a trade event."""
        if not self._enabled or not self._running:
            return

        try:
            # Find slug and direction from scanner mapping
            if not self.scanner:
                return
            mapping = self.scanner._token_slug.get(token_id)
            if not mapping:
                return

            slug, direction = mapping

            # Calculate price change
            prev_price = self._last_prices.get(token_id)
            price_change = 0.0
            if prev_price is not None and prev_price > 0:
                price_change = price - prev_price

            # Skip if no change (reduces noise)
            if prev_price is not None and abs(price_change) < 0.0001:
                return

            self._last_prices[token_id] = price
            now_ms = int(time.time() * 1000)
            now_iso = datetime.now(timezone.utc).isoformat()

            await self.db.conn.execute(
                """INSERT INTO ob_trades
                   (slug, token_id, direction, price, prev_price,
                    price_change, ts_ms, ts_iso, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slug, token_id, direction, price, prev_price,
                 price_change, now_ms, now_iso, "ws")
            )
            # Commit in batches (every 10 trades)
            self._trade_count += 1
            if self._trade_count % 10 == 0:
                await self.db.conn.commit()

        except Exception as e:  # noqa: BLE001
            logger.debug(f"Tick record error: {e}")

    async def _record_trade(self, token_id: str, price: float, size: float,
                             side: str, ts_ms: int):
        """Phase 39 (P1.1): Record a real `last_trade_price` fill event.
        Unlike _record_tick (price_change derived), this is an actual trade
        with size + side, used for the maker queue simulation and as a real
        trade tape for backtest replay."""
        if not self._enabled or not self._running:
            return
        try:
            if not self.scanner:
                return
            mapping = self.scanner._token_slug.get(token_id)
            if not mapping:
                return
            slug, direction = mapping
            now_iso = datetime.fromtimestamp(
                ts_ms / 1000.0, tz=timezone.utc).isoformat()

            await self.db.conn.execute(
                """INSERT INTO ob_trades
                   (slug, token_id, direction, price, prev_price,
                    price_change, ts_ms, ts_iso, source,
                    size, side, event_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (slug, token_id, direction, price, None, 0.0,
                 ts_ms, now_iso, "ws", size, side, "trade")
            )
            self._real_trade_count += 1
            if self._real_trade_count % 5 == 0:
                await self.db.conn.commit()

            # Phase 47f.10 P4#19: whale detection — $1000+ notional trades
            try:
                whale_thresh = float(os.getenv("WHALE_USD_THRESHOLD", "1000"))
                notional = float(size) * float(price)
                if notional >= whale_thresh:
                    await self.db.conn.execute(
                        """INSERT INTO whale_trades
                           (slug, token_id, direction, side, price, size,
                            notional_usd, ts_ms, ts_iso)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (slug, token_id, direction, side, price, size,
                         notional, ts_ms, now_iso)
                    )
                    logger.info(
                        f"🐋 WHALE: {slug} {side.upper()} {size:.1f}sh "
                        f"@ {price:.4f} = ${notional:.0f}")
            except Exception as _we:  # noqa: BLE001
                logger.debug(f"Whale detect: {_we}")
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Real trade record error: {e}")

    # ═══════════════════════════════════════════════
    #  MAINTENANCE
    # ═══════════════════════════════════════════════

    async def _cleanup_old_data(self):
        """Remove data older than CLEANUP_DAYS."""
        try:
            cutoff_ms = int((time.time() - CLEANUP_DAYS * 86400) * 1000)
            r1 = await self.db.conn.execute(
                "DELETE FROM ob_snapshots WHERE ts_ms < ?", (cutoff_ms,))
            r2 = await self.db.conn.execute(
                "DELETE FROM ob_trades WHERE ts_ms < ?", (cutoff_ms,))
            await self.db.conn.commit()
            logger.info(f"📸 Cleanup: removed old data before {CLEANUP_DAYS}d")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Cleanup error: {e}")

    async def _update_meta(self, key: str, value: str):
        """Update recorder_meta table."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                """INSERT OR REPLACE INTO recorder_meta (key, value, updated_at)
                   VALUES (?, ?, ?)""",
                (key, value, now)
            )
            await self.db.conn.commit()
        except Exception:  # noqa: BLE001
            pass

    # ═══════════════════════════════════════════════
    #  QUERY HELPERS (for Backtest Engine)
    # ═══════════════════════════════════════════════

    async def get_snapshots_for_market(self, slug: str,
                                       start_ms: int = 0,
                                       end_ms: int = 0) -> list[dict]:
        """
        Retrieve all orderbook snapshots for a given market slug.
        Returns chronologically ordered list of snapshot dicts.

        This is the primary data source for realistic backtest replay.
        """
        query = "SELECT * FROM ob_snapshots WHERE slug = ?"
        params = [slug]

        if start_ms > 0:
            query += " AND ts_ms >= ?"
            params.append(start_ms)
        if end_ms > 0:
            query += " AND ts_ms <= ?"
            params.append(end_ms)

        query += " ORDER BY ts_ms ASC"

        rows = await self.db.conn.execute_fetchall(query, params)
        if not rows:
            return []

        # Get column names
        cursor = await self.db.conn.execute(
            "PRAGMA table_info(ob_snapshots)")
        columns_info = await cursor.fetchall()
        columns = [c[1] for c in columns_info]

        return [dict(zip(columns, row)) for row in rows]

    async def get_trades_for_market(self, slug: str,
                                     start_ms: int = 0,
                                     end_ms: int = 0) -> list[dict]:
        """Retrieve tick-level trades for a market."""
        query = "SELECT * FROM ob_trades WHERE slug = ?"
        params = [slug]

        if start_ms > 0:
            query += " AND ts_ms >= ?"
            params.append(start_ms)
        if end_ms > 0:
            query += " AND ts_ms <= ?"
            params.append(end_ms)

        query += " ORDER BY ts_ms ASC"

        rows = await self.db.conn.execute_fetchall(query, params)
        if not rows:
            return []

        cursor = await self.db.conn.execute("PRAGMA table_info(ob_trades)")
        columns_info = await cursor.fetchall()
        columns = [c[1] for c in columns_info]

        return [dict(zip(columns, row)) for row in rows]

    async def get_stats(self) -> dict:
        """Get recorder statistics."""
        try:
            snap_count = 0
            trade_count = 0
            oldest_ts = None
            newest_ts = None

            r = await self.db.conn.execute_fetchall(
                "SELECT COUNT(*) FROM ob_snapshots")
            if r:
                snap_count = r[0][0]

            r = await self.db.conn.execute_fetchall(
                "SELECT COUNT(*) FROM ob_trades")
            if r:
                trade_count = r[0][0]

            r = await self.db.conn.execute_fetchall(
                "SELECT MIN(ts_iso), MAX(ts_iso) FROM ob_snapshots")
            if r and r[0][0]:
                oldest_ts = r[0][0]
                newest_ts = r[0][1]

            # Unique markets
            r = await self.db.conn.execute_fetchall(
                "SELECT COUNT(DISTINCT slug) FROM ob_snapshots")
            unique_markets = r[0][0] if r else 0

            # DB size estimate (rows × avg row size)
            snap_size_mb = snap_count * 2.0 / 1024  # ~2KB per snapshot row

            # Phase 39 (P1.1): real trade count
            r = await self.db.conn.execute_fetchall(
                "SELECT COUNT(*) FROM ob_trades WHERE event_type='trade'")
            real_trade_count = r[0][0] if r else 0

            return {
                "snapshots": snap_count,
                "trades": trade_count,
                "real_trades": real_trade_count,
                "unique_markets": unique_markets,
                "oldest": oldest_ts,
                "newest": newest_ts,
                "est_size_mb": round(snap_size_mb, 1),
                "session_snapshots": self._snapshot_count,
                "session_trades": self._trade_count,
                "session_real_trades": self._real_trade_count,
                "errors": self._errors,
                "enabled": self._enabled,
            }
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
