"""
PolyPaper Bot — Polymarket Real-Time Data Socket (RTDS)
========================================================
P0.12 (Heddas direktifi 2026-04-30 "en güncel ol")

Polymarket RTDS WS subscribe — endpoint: wss://ws-live-data.polymarket.com

İki farklı topic:
1. crypto_prices — Binance source (5m markets resolution oracle ile uyumlu)
2. crypto_prices_chainlink — Chainlink Data Stream (15m markets sponsored)

Bu modül `data/external_feed.py` Binance REST polling'in WS-push alternatifi.
Aynı zamanda 15m markets için Chainlink kanonik fiyat sağlar (resolution parity).

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
    - Binance (crypto_prices) — 5m markets resolution parity, low-latency push
    - Chainlink (crypto_prices_chainlink) — 15m markets resolution parity,
      Polymarket-sponsored API key required

    State:
        self._prices_binance[asset] = {"price": float, "ts": float}
        self._prices_chainlink[asset] = {"price": float, "ts": float}
    """

    def __init__(self, enable_chainlink: bool = True):
        self._prices_binance: dict[str, dict] = {}
        self._prices_chainlink: dict[str, dict] = {}
        self._available = False
        self._enable_chainlink = enable_chainlink
        self._ws = None
        self._consecutive_fails = 0
        self._stop_requested = False
        self._task = None
        self._last_msg_ts = 0.0

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
                        break
            elif topic == CHAINLINK_TOPIC:
                # Chainlink source — slash-separated (btc/usd, eth/usd)
                for asset, sym in CHAINLINK_SYMBOLS.items():
                    if symbol == sym:
                        self._prices_chainlink[asset] = {"price": price, "ts": now}
                        break

    def get_price(self, asset: str, source: str = "auto") -> Optional[float]:
        """Get most recent price for asset.

        source:
        - "binance"   — RTDS Binance feed (5m markets resolution parity)
        - "chainlink" — RTDS Chainlink feed (15m markets sponsored)
        - "auto"      — Chainlink if available, else Binance (15m markets prefer)

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
        """5m markets için kanonik price — Binance öncelik (resolution parity)."""
        return self.get_price(asset, source="binance")

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
