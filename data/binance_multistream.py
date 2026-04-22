"""
PolyPaper Bot - Phase 44a — Binance multi-stream microstructure feed
====================================================================

Augments the polling REST ExternalFeed with a single combined-stream
WebSocket carrying:

  • <pair>@depth5@100ms      → top-5 orderbook + microprice + imbalance
  • <pair>@aggTrade          → real trades  → trade-flow imbalance
  • <pair>@markPrice@1s      → futures mark + funding rate (perp)

For each tracked asset (BTC/ETH/SOL/XRP) the loop maintains a rolling
feature dict:

    {
        "mid":              float,
        "microprice":       float,   # depth-weighted mid
        "ob_imbalance":     float,   # ∈[-1,1] top-5 size ratio
        "spread_bps":       float,
        "trade_flow_60s":   float,   # ∈[-1,1] (buys-sells)/(buys+sells)
        "trade_count_60s":  int,
        "funding_rate":     float,   # 8h annualised
        "ts":               float,
    }

Engine consumes via `features(asset)` to fuse against the binary
Up/Down odds. Designed to be tolerant: if WS dies the consumer simply
gets None and falls back to existing REST feed signals.

Settings (config/settings.py — Phase 44a):
    BINANCE_MULTISTREAM_ENABLED   default True
    BINANCE_TRADE_WINDOW_SECONDS  default 60.0
    BINANCE_FUTURES_FUNDING       default True

Wired in main.py: started after Polymarket scanner, before AI brain.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.binance_multistream")

# Spot combined stream — depth + aggTrade
SPOT_WS = "wss://stream.binance.com:9443/stream?streams={streams}"
# USD-M futures stream — markPrice carries funding rate
FUT_WS = "wss://fstream.binance.com/stream?streams={streams}"

# Asset → spot symbol (lowercase for stream paths)
SPOT_SYMBOLS = {
    "BTC": "btcusdt",
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

DEPTH_INTERVAL_MS = 100
DEPTH_LEVELS = 5

PING_INTERVAL = 30
RECONNECT_BACKOFF = (2, 5, 10, 20, 30)


class _AssetState:
    """Rolling microstructure state for one asset."""
    __slots__ = (
        "asset", "best_bid", "best_ask", "bid_size", "ask_size",
        "depth_bid_usd", "depth_ask_usd", "trades", "funding_rate",
        "mark_price", "last_update_ts",
    )

    def __init__(self, asset: str):
        self.asset = asset
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.bid_size: float = 0.0
        self.ask_size: float = 0.0
        self.depth_bid_usd: float = 0.0
        self.depth_ask_usd: float = 0.0
        # (ts_ms, price, qty, is_buyer_maker)
        self.trades: deque = deque(maxlen=2000)
        self.funding_rate: float = 0.0
        self.mark_price: float = 0.0
        self.last_update_ts: float = 0.0

    def apply_depth(self, payload: dict) -> None:
        """Process depth5 update — payload['data'] = bids/asks lists."""
        data = payload.get("data") or payload
        bids = data.get("bids") or data.get("b") or []
        asks = data.get("asks") or data.get("a") or []
        if not bids or not asks:
            return
        try:
            self.best_bid = float(bids[0][0])
            self.bid_size = float(bids[0][1])
            self.best_ask = float(asks[0][0])
            self.ask_size = float(asks[0][1])
            self.depth_bid_usd = sum(float(p) * float(q) for p, q in bids[:DEPTH_LEVELS])
            self.depth_ask_usd = sum(float(p) * float(q) for p, q in asks[:DEPTH_LEVELS])
            self.last_update_ts = time.time()
        except (TypeError, ValueError, IndexError):
            pass

    def apply_trade(self, payload: dict) -> None:
        data = payload.get("data") or payload
        try:
            ts = int(data.get("T") or data.get("E") or time.time() * 1000)
            price = float(data["p"])
            qty = float(data["q"])
            is_maker = bool(data.get("m", False))  # True = sell, False = buy
            self.trades.append((ts, price, qty, is_maker))
        except (KeyError, TypeError, ValueError):
            pass

    def apply_mark(self, payload: dict) -> None:
        data = payload.get("data") or payload
        try:
            self.mark_price = float(data.get("p", 0))
            # 'r' = funding rate (per 8h)
            self.funding_rate = float(data.get("r", 0))
        except (KeyError, TypeError, ValueError):
            pass

    # ── feature extraction ───────────────────────────────────────────
    def features(self, trade_window_s: float) -> Optional[dict]:
        if self.best_bid <= 0 or self.best_ask <= 0:
            return None
        mid = (self.best_bid + self.best_ask) / 2.0
        spread = self.best_ask - self.best_bid
        spread_bps = (spread / mid) * 1e4 if mid > 0 else 0.0

        # Microprice = depth-weighted mid (favours the heavier side)
        size_total = self.bid_size + self.ask_size
        if size_total > 0:
            microprice = (self.best_ask * self.bid_size + self.best_bid * self.ask_size) / size_total
        else:
            microprice = mid

        # OB imbalance ∈ [-1, 1] from top-5 USD depth
        depth_total = self.depth_bid_usd + self.depth_ask_usd
        if depth_total > 0:
            ob_imb = (self.depth_bid_usd - self.depth_ask_usd) / depth_total
        else:
            ob_imb = 0.0

        # Trade flow imbalance over last N seconds
        cutoff_ms = (time.time() - trade_window_s) * 1000
        buy_qty = 0.0
        sell_qty = 0.0
        n_trades = 0
        for ts, price, qty, is_maker in self.trades:
            if ts < cutoff_ms:
                continue
            n_trades += 1
            notional = price * qty
            if is_maker:
                sell_qty += notional
            else:
                buy_qty += notional
        flow_total = buy_qty + sell_qty
        trade_flow = (buy_qty - sell_qty) / flow_total if flow_total > 0 else 0.0

        return {
            "mid": round(mid, 4),
            "microprice": round(microprice, 4),
            "spread_bps": round(spread_bps, 3),
            "ob_imbalance": round(ob_imb, 4),
            "trade_flow_60s": round(trade_flow, 4),
            "trade_count_60s": n_trades,
            "funding_rate": round(self.funding_rate, 6),
            "mark_price": round(self.mark_price, 4),
            "ts": self.last_update_ts,
        }


class BinanceMultiStream:
    """Single-task combined websocket subscription for all 4 assets."""

    def __init__(self, trade_window_seconds: float = 60.0,
                 enable_funding: bool = True):
        self.trade_window = trade_window_seconds
        self.enable_funding = enable_funding
        self._states: dict[str, _AssetState] = {a: _AssetState(a) for a in SPOT_SYMBOLS}
        self._spot_task: Optional[asyncio.Task] = None
        self._fut_task: Optional[asyncio.Task] = None
        self._running = False
        self._spot_msgs = 0
        self._fut_msgs = 0
        self._reconnects = 0
        self._connected_at: float = 0.0

    # symbol routing helper
    def _symbol_to_asset(self, symbol: str) -> Optional[str]:
        s = symbol.lower()
        for asset, sym in SPOT_SYMBOLS.items():
            if sym == s:
                return asset
        return None

    async def start(self):
        if self._running:
            return
        try:
            import websockets  # noqa: F401
        except ImportError:
            logger.warning("⚠ Phase 44a: websockets package not installed — multistream skipped")
            return
        self._running = True
        # Phase 82e Sprint 2.1: Binance feeds die silently = no external price
        # confirmation. Notify on death.
        self._spot_task = safe_create_task(
            self._spot_loop(), name="binance_spot_ms")
        if self.enable_funding:
            self._fut_task = safe_create_task(
                self._fut_loop(), name="binance_fut_ms")
        logger.info("📡 Phase 44a: Binance multistream STARTED "
                    f"(spot+{len(SPOT_SYMBOLS)} pairs, funding={self.enable_funding})")

    async def stop(self):
        self._running = False
        for t in (self._spot_task, self._fut_task):
            if t and not t.done():
                t.cancel()

    # ── spot loop (depth + aggTrade combined stream) ─────────────────
    async def _spot_loop(self):
        import websockets
        streams = []
        for sym in SPOT_SYMBOLS.values():
            streams.append(f"{sym}@depth{DEPTH_LEVELS}@{DEPTH_INTERVAL_MS}ms")
            streams.append(f"{sym}@aggTrade")
        url = SPOT_WS.format(streams="/".join(streams))
        backoff_idx = 0
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=PING_INTERVAL,
                                              ping_timeout=10, max_size=2**20) as ws:
                    self._connected_at = time.time()
                    backoff_idx = 0
                    logger.info(f"📡 Binance spot multistream connected ({len(streams)} streams)")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._spot_msgs += 1
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        self._dispatch_spot(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._reconnects += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning(f"📡 Binance spot WS lost ({type(e).__name__}: {e}); retry in {wait}s")
                backoff_idx += 1
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    break

    def _dispatch_spot(self, msg: dict):
        # combined stream wraps payload as {"stream": "...", "data": {...}}
        stream = msg.get("stream", "")
        data = msg.get("data") or msg
        if not stream:
            return
        # stream like "btcusdt@depth5@100ms" or "btcusdt@aggTrade"
        sym, _, kind = stream.partition("@")
        asset = self._symbol_to_asset(sym)
        if not asset:
            return
        state = self._states[asset]
        if kind.startswith("depth"):
            state.apply_depth(data)
        elif kind.startswith("aggTrade"):
            state.apply_trade(data)

    # ── futures loop (markPrice with funding) ────────────────────────
    async def _fut_loop(self):
        import websockets
        streams = "/".join(f"{sym}@markPrice@1s" for sym in SPOT_SYMBOLS.values())
        url = FUT_WS.format(streams=streams)
        backoff_idx = 0
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=PING_INTERVAL,
                                              ping_timeout=10) as ws:
                    backoff_idx = 0
                    logger.info("📡 Binance futures markPrice stream connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        self._fut_msgs += 1
                        try:
                            msg = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        stream = msg.get("stream", "")
                        data = msg.get("data") or msg
                        sym = stream.split("@", 1)[0]
                        asset = self._symbol_to_asset(sym)
                        if asset:
                            self._states[asset].apply_mark(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._reconnects += 1
                wait = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
                logger.warning(f"📡 Binance fut WS lost ({type(e).__name__}: {e}); retry in {wait}s")
                backoff_idx += 1
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    break

    # ── public read API ─────────────────────────────────────────────
    def features(self, asset: str) -> Optional[dict]:
        st = self._states.get(asset.upper())
        if not st:
            return None
        return st.features(self.trade_window)

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "spot_msgs": self._spot_msgs,
            "fut_msgs": self._fut_msgs,
            "reconnects": self._reconnects,
            "trade_window_s": self.trade_window,
            "uptime_s": int(time.time() - self._connected_at) if self._connected_at else 0,
            "assets": {a: bool(st.last_update_ts) for a, st in self._states.items()},
        }
