"""
PolyPaper Bot - External Price Feed (Phase 34+ — Windows PC)
Fetches real BTC/ETH/SOL/XRP spot prices from Binance REST API.

Windows: httpx is primary. curl fallback kept for Linux/Replit compat.
"""
import asyncio
import json
import logging
import subprocess
import sys
import time
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.external_feed")

BINANCE_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

BINANCE_BASE = "https://api.binance.com/api/v3"


class ExternalFeed:
    def __init__(self):
        self._prices: dict[str, dict] = {}
        self._open_prices: dict[str, float] = {}  # slug → price at market open
        self._price_history: dict[str, list[tuple[float, float]]] = {}  # Phase 79b: asset → [(ts, price), ...]
        self._HISTORY_MAX = 12  # 12 samples × 10s interval = 120s lookback
        self._available = False
        self._poll_interval = 10
        self._method = "curl"
        self._httpx_client = None
        self._consecutive_fails = 0

    async def start(self, httpx_client=None):
        self._httpx_client = httpx_client
        # Method 1: httpx (works on Windows + most platforms)
        if httpx_client:
            try:
                r = await httpx_client.get(f"{BINANCE_BASE}/ping", timeout=3.0)
                if r.status_code == 200:
                    self._available = True
                    self._method = "httpx"
                    logger.info("🌐 Binance feed: CONNECTED (httpx)")
                    # Phase 82e Sprint 2.1: guarded
                    safe_create_task(self._poll_loop(), name="external_feed_httpx")
                    return
            except Exception as e:
                logger.debug(f"Binance httpx test: {e}")
        # Method 2: curl fallback (Replit/Linux)
        if sys.platform != "win32":
            try:
                result = subprocess.run(
                    ["curl", "-s", "--max-time", "3", f"{BINANCE_BASE}/ping"],
                    capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and "{}" in result.stdout:
                    self._available = True
                    self._method = "curl"
                    logger.info("🌐 Binance feed: CONNECTED (curl)")
                    # Phase 82e Sprint 2.1: guarded
                    safe_create_task(self._poll_loop(), name="external_feed_curl")
                    return
            except Exception as e:
                logger.debug(f"Binance curl test: {e}")
        logger.warning("🌐 Binance feed: unavailable")

    async def _poll_loop(self):
        while self._available:
            try:
                await self._fetch_all()
                self._consecutive_fails = 0
            except Exception:
                self._consecutive_fails += 1
                if self._consecutive_fails > 10:
                    logger.warning("🌐 Binance: too many fails, stopping")
                    self._available = False
                    break
            await asyncio.sleep(self._poll_interval)

    async def _fetch_all(self):
        if self._method == "curl":
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._curl_fetch)
            now = time.time()
            for asset, symbol in BINANCE_SYMBOLS.items():
                price = result.get(symbol)
                if price and price > 0:
                    self._prices[asset] = {"price": price, "ts": now}
                    self._record_history(asset, now, price)
        else:
            await self._fetch_httpx()

    def _curl_fetch(self) -> dict:
        prices = {}
        for asset, symbol in BINANCE_SYMBOLS.items():
            try:
                r = subprocess.run(
                    ["curl", "-s", "--max-time", "3",
                     f"{BINANCE_BASE}/ticker/price?symbol={symbol}"],
                    capture_output=True, text=True, timeout=5)
                if r.returncode == 0 and r.stdout:
                    data = json.loads(r.stdout)
                    p = float(data.get("price", 0))
                    if p > 0:
                        prices[symbol] = p
            except Exception:
                pass
        return prices

    async def _fetch_httpx(self):
        if not self._httpx_client:
            return
        now = time.time()
        for asset, symbol in BINANCE_SYMBOLS.items():
            try:
                r = await self._httpx_client.get(
                    f"{BINANCE_BASE}/ticker/price",
                    params={"symbol": symbol}, timeout=3.0)
                if r.status_code == 200:
                    price = float(r.json().get("price", 0))
                    if price > 0:
                        self._prices[asset] = {"price": price, "ts": now}
                        # Phase 79b: Record price history for momentum calc
                        self._record_history(asset, now, price)
            except Exception:
                pass

    def _record_history(self, asset: str, ts: float, price: float):
        """Phase 79b: Append to ring buffer for short-term momentum."""
        if asset not in self._price_history:
            self._price_history[asset] = []
        buf = self._price_history[asset]
        buf.append((ts, price))
        if len(buf) > self._HISTORY_MAX:
            buf.pop(0)

    def get_price(self, asset: str) -> Optional[float]:
        data = self._prices.get(asset.upper())
        if not data or time.time() - data["ts"] > 30:
            return None
        return data["price"]

    def get_spot_momentum(self, asset: str, lookback_seconds: int = 60) -> Optional[dict]:
        """Phase 79b: Calculate short-term spot price momentum.

        Returns dict with:
          - change_pct: price change % over lookback window
          - direction: "up" or "down"
          - strength: 0.0-1.0 normalized momentum strength
          - samples: number of data points used

        Returns None if insufficient data.
        """
        buf = self._price_history.get(asset.upper(), [])
        if len(buf) < 3:
            return None
        now = time.time()
        cutoff = now - lookback_seconds
        # Find oldest sample within lookback window
        recent = [(ts, px) for ts, px in buf if ts >= cutoff]
        if len(recent) < 2:
            return None
        oldest_price = recent[0][1]
        latest_price = recent[-1][1]
        if oldest_price <= 0:
            return None
        change_pct = (latest_price - oldest_price) / oldest_price * 100
        direction = "up" if change_pct >= 0 else "down"
        # Normalize strength: 0.1% = strong for 60s crypto move
        strength = min(abs(change_pct) / 0.10, 1.0)
        return {
            "change_pct": round(change_pct, 4),
            "direction": direction,
            "strength": round(strength, 3),
            "samples": len(recent),
            "oldest_price": oldest_price,
            "latest_price": latest_price,
        }

    def record_market_open(self, asset: str, slug: str = ""):
        """Record spot price at market open. Called ONCE per slug."""
        price = self.get_price(asset)
        if price:
            key = slug or asset.upper()
            self._open_prices[key] = price

    def get_divergence(self, asset: str, polymarket_up_odds: float, slug: str = "") -> Optional[dict]:
        current = self.get_price(asset)
        key = slug or asset.upper()
        open_price = self._open_prices.get(key)
        if not current or not open_price or open_price <= 0:
            return None
        spot_change = (current - open_price) / open_price
        spot_dir = "up" if spot_change >= 0 else "down"
        odds_dir = "up" if polymarket_up_odds >= 0.50 else "down"
        divergence = (spot_dir != odds_dir)
        confidence = min(abs(spot_change) * 200, 1.0)
        signal = spot_dir if (divergence and confidence >= 0.10) else None
        return {
            "spot_price": current, "open_price": open_price,
            "spot_direction": spot_dir, "spot_change_pct": round(spot_change * 100, 3),
            "odds_direction": odds_dir, "polymarket_up": polymarket_up_odds,
            "divergence": divergence, "confidence": round(confidence, 3),
            "signal": signal,
        }

    @property
    def is_available(self) -> bool:
        return self._available

    def get_status(self) -> dict:
        return {
            "available": self._available, "method": self._method,
            "prices": {k: round(v["price"], 2) for k, v in self._prices.items()},
            "open_prices": {k: round(v, 2) for k, v in self._open_prices.items()},
        }
