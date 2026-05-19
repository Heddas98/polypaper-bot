"""
PolyPaper Bot — Polymarket Real-Time Data Socket (RTDS)
========================================================
P0.12 (Heddas direktifi 2026-04-30 "en güncel ol")

Polymarket RTDS WS subscribe — endpoint: wss://ws-live-data.polymarket.com

İki farklı topic:
1. crypto_prices — Binance source (düşük gecikmeli spot referansı)
2. crypto_prices_chainlink — Chainlink Data Stream — 5m + 15m crypto
   market'lerin RESOLUTION feed'i. Market kuralları aynen: "Chainlink data
   stream BTC/USD, not according to other sources or spot markets"
   (2026-05-19 Gamma API ile doğrulandı; eski "5m=Binance" varsayımı yanlıştı).

Bu modül `data/external_feed.py` Binance REST polling'in WS-push alternatifi.
Asıl değeri: 5m + 15m markets'in settle olduğu Chainlink kanonik fiyatını sağlar.

Polymarket docs:
- https://docs.polymarket.com/market-data/websocket/rtds
- 4 asset: BTC, ETH, SOL, XRP (slash-separated for Chainlink: btc/usd vs btcusdt)
- Heartbeat: PING her 5s (server-side ping, client-side pong)
- Sponsored Chainlink API key Polymarket form ile alınır (15m crypto markets için)

Mimari:
- ExternalFeed (REST polling 10s) → mevcut, geri-uyumluluk için kalır
- PolymarketRTDS (WS push <1s latency) → 5m + 15m parity
- Bot consumer: get_price(asset, source="binance"|"chainlink"|"auto")

Reconnect chain (T11.8-B doktrini):
- WS disconnect → 5s exponential backoff → 60s cap
- 10 ardışık fail → modül offline (noqa BLE001 boot orchestrator pattern)
- Heartbeat 5s expected, miss timeout 15s

Usage:
    rtds = PolymarketRTDS()
    await rtds.start()
    price = rtds.get_price("BTC", source="chainlink")  # btc/usd kanonik
    spot = rtds.get_price("BTC", source="binance")     # btcusdt low-latency
    rtds.stop()
"""

import asyncio
import json
import logging
import time
from typing import Optional

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore

from core.bg_task import safe_create_task

logger = logging.getLogger("polypaper.data.polymarket_rtds")


RTDS_WS_URL = "wss://ws-live-data.polymarket.com"

# Polymarket RTDS Binance source: lowercase concat (btcusdt, ethusdt, ...)
BINANCE_TOPIC = "crypto_prices"
BINANCE_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

# Polymarket RTDS Chainlink source: slash-separated (btc/usd, eth/usd, ...)
# 15m markets için sponsored API key gerekiyor (Polymarket form'dan al).
CHAINLINK_TOPIC = "crypto_prices_chainlink"
CHAINLINK_SYMBOLS = {
    "BTC": "btc/usd",
    "ETH": "eth/usd",
    "SOL": "sol/usd",
    "XRP": "xrp/usd",
}

# Reconnect / heartbeat tuning (Phase 47f.7 + T5.6 doctrine)
RECONNECT_BACKOFF_INITIAL_S = 5
RECONNECT_BACKOFF_MAX_S = 60
HEARTBEAT_INTERVAL_S = 5
PRICE_FRESHNESS_S = 30  # >30s stale → return None
MAX_CONSECUTIVE_FAILS = 10


class PolymarketRTDS:
    """Polymarket Real-Time Data Socket WS client.

    Two-source price aggregator:
    - Chainlink (crypto_prices_chainlink) — 5m + 15m crypto market RESOLUTION
      feed (market kuralı: "Chainlink data stream BTC/USD, not spot")
    - Binance (crypto_prices) — düşük gecikmeli spot referansı; resolution
      feed DEĞİL (2026-05-19 Gamma API doğrulaması)

    State:
        self._prices_binance[asset] = {"price": float, "ts": float}
        self._prices_chainlink[asset] = {"price": float, "ts": float}
    """

    def __init__(self, enable_chainlink: bool = True, db=None):
        self._prices_binance: dict[str, dict] = {}
        self._prices_chainlink: dict[str, dict] = {}
        self._available = False
        self._enable_chainlink = enable_chainlink
        self._ws = None
        self._consecutive_fails = 0
        self._stop_requested = False
        self._task = None
        self._last_msg_ts = 0.0
        # P1.10 (2026-05-19): external_prices persist. RTDS tick'leri DB'ye
        # 'rtds_chainlink' / 'rtds_binance' source'larıyla yazılır.
        self.db = db

    async def start(self):
        """Bot startup'ta çağrılır. WS connection task spawn eder."""
        if websockets is None:
            logger.warning("📡 RTDS: websockets package not available, RTDS disabled")
            return
        self._stop_requested = False
        # Phase 82e Sprint 2.1: guarded background task
        self._task = safe_create_task(self._connect_loop(), name="polymarket_rtds")
        logger.info("📡 RTDS: WS task spawned (Binance + Chainlink topics)")

    async def stop(self):
        """Bot shutdown'da çağrılır. Graceful WS close."""
        self._stop_requested = True
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task is not None:
            try:
                self._task.cancel()
            except Exception:  # noqa: BLE001
                pass

    async def _connect_loop(self):
        """Reconnect chain — T11.8-B data/* doctrine."""
        backoff = RECONNECT_BACKOFF_INITIAL_S
        while not self._stop_requested:
            try:
                async with websockets.connect(RTDS_WS_URL, ping_interval=None) as ws:
                    self._ws = ws
                    self._available = True
                    self._consecutive_fails = 0
                    backoff = RECONNECT_BACKOFF_INITIAL_S
                    logger.info(f"📡 RTDS: connected to {RTDS_WS_URL}")
                    await self._subscribe(ws)
                    # Spawn heartbeat sender
                    hb_task = safe_create_task(self._heartbeat_loop(ws), name="rtds_heartbeat")
                    try:
                        await self._receive_loop(ws)
                    finally:
                        try:
                            hb_task.cancel()
                        except Exception:  # noqa: BLE001
                            pass
            except (
                TimeoutError,
                websockets.exceptions.ConnectionClosed,
                websockets.exceptions.WebSocketException,
                ConnectionError,
                OSError,
            ) as e:
                self._consecutive_fails += 1
                logger.warning(
                    f"📡 RTDS: connection error ({type(e).__name__}): {e}; "
                    f"reconnect in {backoff}s (fails={self._consecutive_fails})"
                )
            except Exception as e:  # noqa: BLE001
                # Boot orchestrator pattern: data feed reconnect
                # tüm hataları yakalar, log + retry. Single blip için modül kapatma.
                self._consecutive_fails += 1
                logger.warning(
                    f"📡 RTDS: unexpected error ({type(e).__name__}): {e}; "
                    f"reconnect in {backoff}s (fails={self._consecutive_fails})"
                )
            finally:
                self._available = False
                self._ws = None

            if self._consecutive_fails > MAX_CONSECUTIVE_FAILS:
                logger.error("📡 RTDS: too many fails, going offline (manual restart needed)")
                break

            if not self._stop_requested:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)

    async def _subscribe(self, ws):
        """Topic subscriptions — Binance (always) + Chainlink (opt-in)."""
        binance_filter = ",".join(BINANCE_SYMBOLS.values())
        subs = [
            {"topic": BINANCE_TOPIC, "type": "update", "filters": binance_filter},
        ]
        if self._enable_chainlink:
            for sym in CHAINLINK_SYMBOLS.values():
                subs.append(
                    {
                        "topic": CHAINLINK_TOPIC,
                        "type": "*",
                        "filters": json.dumps({"symbol": sym}),
                    }
                )
        msg = {"action": "subscribe", "subscriptions": subs}
        await ws.send(json.dumps(msg))
        logger.info(
            f"📡 RTDS: subscribed Binance({len(BINANCE_SYMBOLS)}) "
            f"+ Chainlink({len(CHAINLINK_SYMBOLS) if self._enable_chainlink else 0})"
        )

    async def _heartbeat_loop(self, ws):
        """Send PING every 5s (Polymarket RTDS spec)."""
        while not self._stop_requested:
            try:
                await ws.send("PING")
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            except (websockets.exceptions.ConnectionClosed, OSError):
                return
            except Exception:  # noqa: BLE001
                return

    async def _receive_loop(self, ws):
        """Process incoming messages — price updates only."""
        async for raw in ws:
            self._last_msg_ts = time.time()
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(msg, dict):
                continue

            topic = msg.get("topic", "")
            payload = msg.get("payload", {}) or {}
            symbol = str(payload.get("symbol", "")).lower()
            value = payload.get("value")

            if value is None:
                continue
            try:
                price = float(value)
            except (TypeError, ValueError):
                continue

            now = time.time()
            if topic == BINANCE_TOPIC:
                # Binance source — lowercase concat (btcusdt, ethusdt)
                for asset, sym in BINANCE_SYMBOLS.items():
                    if symbol == sym:
                        self._prices_binance[asset] = {"price": price, "ts": now}
                        if self.db is not None:
                            safe_create_task(
                                self._persist_async(
                                    int(now * 1000), f"{asset}USDT", "rtds_binance", price
                                ),
                                name="rtds_persist",
                            )
                        break
            elif topic == CHAINLINK_TOPIC:
                # Chainlink source — slash-separated (btc/usd, eth/usd)
                for asset, sym in CHAINLINK_SYMBOLS.items():
                    if symbol == sym:
                        self._prices_chainlink[asset] = {"price": price, "ts": now}
                        if self.db is not None:
                            safe_create_task(
                                self._persist_async(
                                    int(now * 1000), f"{asset}USD", "rtds_chainlink", price
                                ),
                                name="rtds_persist",
                            )
                        break

    async def _persist_async(self, ts_ms: int, symbol: str, source: str, price: float):
        """external_prices'a yaz — feed loop'unu bloklamadan (T11.8-B doktrini).

        Kaynak: 'rtds_chainlink' (5m/15m resolution feed) veya 'rtds_binance'
        (düşük gecikmeli spot referansı). chainlink_oracle.py / external_feed.py
        / binance_multistream.py'deki _persist_async desenini birebir izler.
        """
        if self.db is None or getattr(self.db, "conn", None) is None:
            return
        try:
            await self.db.conn.execute(
                "INSERT OR REPLACE INTO external_prices "
                "(ts_ms, symbol, source, price) VALUES (?, ?, ?, ?)",
                (ts_ms, symbol, source, price),
            )
            await self.db.conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def get_price(self, asset: str, source: str = "auto") -> Optional[float]:
        """Get most recent price for asset.

        source:
        - "chainlink" — RTDS Chainlink feed — 5m + 15m crypto RESOLUTION feed
        - "binance"   — RTDS Binance feed — düşük gecikmeli spot referansı
        - "auto"      — Chainlink varsa o, yoksa Binance fallback

        Returns None if stale (>PRICE_FRESHNESS_S) or missing.
        """
        asset_u = asset.upper()
        now = time.time()

        if source == "binance":
            d = self._prices_binance.get(asset_u)
        elif source == "chainlink":
            d = self._prices_chainlink.get(asset_u)
        else:  # auto
            d = self._prices_chainlink.get(asset_u) or self._prices_binance.get(asset_u)

        if not d or now - d.get("ts", 0) > PRICE_FRESHNESS_S:
            return None
        return d.get("price")

    def get_price_15m(self, asset: str) -> Optional[float]:
        """15m markets için kanonik price — Chainlink öncelik (resolution parity).

        Eğer Chainlink yok (sponsorsuz veya stale), Binance fallback.
        """
        return self.get_price(asset, source="chainlink") or self.get_price(asset, source="binance")

    def get_price_5m(self, asset: str) -> Optional[float]:
        """5m markets — Chainlink data-stream kanonik fiyatı (resolution parity).

        2026-05-19 düzeltme: 5m BTC up/down market kuralları AÇIKÇA
        "Chainlink data stream BTC/USD, not spot markets" diyor (Gamma API ile
        doğrulandı). Eski kod Binance kullanıyordu — yanlıştı. Chainlink
        öncelik; stale ise Binance fallback.
        """
        return self.get_price(asset, source="chainlink") or self.get_price(asset, source="binance")

    def get_status(self) -> dict:
        """Telegram /h status için snapshot."""
        return {
            "available": self._available,
            "consecutive_fails": self._consecutive_fails,
            "binance_prices": {k: v.get("price") for k, v in self._prices_binance.items()},
            "chainlink_prices": {k: v.get("price") for k, v in self._prices_chainlink.items()},
            "last_msg_age_s": int(time.time() - self._last_msg_ts) if self._last_msg_ts else None,
            "chainlink_enabled": self._enable_chainlink,
        }
