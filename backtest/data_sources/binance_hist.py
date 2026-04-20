"""
PolyPaper Bot - Binance Historical Data Client
Public API — no auth required, 1200 req/min limit.

Data types collected:
  - Kline (OHLCV): 1m, 5m, 15m, 1h candles
  - aggTrades: taker buy/sell flow (for taker_flow strategy)
  - Funding Rate: 8h perpetual funding (for funding_rate strategy)

All data cached to SQLite via BacktestCache — same data never fetched twice.
Binance API docs: https://binance-docs.github.io/apidocs/spot/en/
"""
import logging
import asyncio
import time
from typing import Optional
import httpx

from backtest.data_sources.cache import BacktestCache

logger = logging.getLogger("polypaper.backtest.binance")

BASE_URL = "https://api.binance.com"
FAPI_URL = "https://fapi.binance.com"

# Binance kline intervals
VALID_INTERVALS = ["1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h",
                    "6h", "8h", "12h", "1d"]

# Rate limit: stay well under 1200/min → ~15 req/sec max
RATE_LIMIT_DELAY = 0.1  # 100ms between requests

# Binance returns max 1000 klines per request
MAX_KLINES_PER_REQUEST = 1000

# Symbol mapping for our coins
SYMBOL_MAP = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}


class BinanceHistClient:
    """Async client for Binance public historical data with caching."""

    def __init__(self, cache: Optional[BacktestCache] = None):
        self.cache = cache
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request = 0.0
        self._request_count = 0

    async def init(self) -> "BinanceHistClient":
        """Initialize HTTP client and cache."""
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if self.cache and not self.cache.conn:
            await self.cache.init()
        logger.info("Binance historical client initialized")
        return self

    async def _rate_limit(self):
        """Enforce rate limit."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.time()
        self._request_count += 1

    async def _get(self, url: str, params: dict) -> Optional[list | dict]:
        """Make GET request with rate limiting and retry on 429."""
        if not self._client:
            logger.error("Client not initialized — call init() first")
            return None
        await self._rate_limit()

        try:
            resp = await self._client.get(url, params=params)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "10"))
                logger.warning("Binance rate limited, waiting %ds", retry_after)
                await asyncio.sleep(retry_after)
                resp = await self._client.get(url, params=params)

            if resp.status_code == 418:
                # IP banned — wait longer
                logger.error("Binance IP ban (418). Waiting 120s.")
                await asyncio.sleep(120)
                return None

            resp.raise_for_status()
            return resp.json()

        except httpx.TimeoutException:
            logger.error("Binance timeout: %s", url)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("Binance HTTP %d: %s", e.response.status_code, url)
            return None
        except Exception as e:
            logger.error("Binance request failed: %s", e)
            return None

    # ── Kline (OHLCV) ───────────────────────────────────────

    async def get_klines(self, coin: str = "btc", interval: str = "5m",
                         start_ms: int = 0, end_ms: int = 0,
                         limit: int = 500) -> list:
        """
        Fetch kline/candlestick data.

        Args:
            coin: "btc", "eth", "sol", "xrp"
            interval: "1m", "5m", "15m", "1h" etc.
            start_ms: start time in milliseconds (0 = latest)
            end_ms: end time in milliseconds (0 = now)
            limit: max candles (1-1000)
        Returns:
            List of kline dicts
        """
        symbol = SYMBOL_MAP.get(coin.lower(), f"{coin.upper()}USDT")

        # Check cache first
        if self.cache and start_ms and end_ms:
            cached = await self.cache.get_klines(symbol, interval,
                                                  start_ms, end_ms)
            if cached:
                logger.debug("Klines from cache: %s %s (%d)",
                             symbol, interval, len(cached))
                return cached

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, MAX_KLINES_PER_REQUEST),
        }
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms

        data = await self._get(f"{BASE_URL}/api/v3/klines", params)
        if not data:
            return []

        # Binance kline format: [open_time, open, high, low, close, volume,
        #   close_time, quote_vol, trades, taker_buy_base, taker_buy_quote, _]
        klines = []
        for k in data:
            klines.append({
                "open_time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_vol": float(k[9]),
                "taker_buy_quote": float(k[10]),
            })

        # Cache
        if self.cache and klines:
            count = await self.cache.store_klines(symbol, interval, klines)
            logger.debug("Cached %d klines for %s %s", count, symbol, interval)

        logger.info("Fetched %d klines: %s %s", len(klines), symbol, interval)
        return klines

    async def get_klines_range(self, coin: str = "btc",
                                interval: str = "5m",
                                start_ms: int = 0,
                                end_ms: int = 0) -> list:
        """
        Fetch ALL klines in a time range (auto-pagination).
        Handles Binance's 1000-per-request limit.

        Args:
            coin: "btc", "eth", "sol"
            interval: "1m", "5m", "15m", "1h"
            start_ms: range start (required)
            end_ms: range end (0 = now)
        Returns:
            Complete list of kline dicts
        """
        if not start_ms:
            logger.error("start_ms required for range fetch")
            return []
        if not end_ms:
            end_ms = int(time.time() * 1000)

        all_klines = []
        current_start = start_ms

        while current_start < end_ms:
            batch = await self.get_klines(
                coin=coin, interval=interval,
                start_ms=current_start, end_ms=end_ms,
                limit=MAX_KLINES_PER_REQUEST
            )
            if not batch:
                break

            all_klines.extend(batch)

            # Move to next batch: last candle's close_time + 1
            last_close = batch[-1]["close_time"]
            if last_close >= end_ms:
                break
            current_start = last_close + 1

            # Safety: prevent infinite loop
            if len(batch) < 2:
                break

        logger.info("Range fetch complete: %s %s → %d klines",
                     coin, interval, len(all_klines))
        return all_klines

    # ── Taker Flow (aggTrades) ───────────────────────────────

    async def get_taker_flow(self, coin: str = "btc",
                              start_ms: int = 0, end_ms: int = 0,
                              limit: int = 500) -> dict:
        """
        Fetch aggregate trades and calculate taker buy/sell flow.
        Key for taker_flow strategy.

        Args:
            coin: "btc", "eth", "sol"
            start_ms, end_ms: time range
            limit: max trades (1-1000)
        Returns:
            {"buy_volume": float, "sell_volume": float,
             "buy_count": int, "sell_count": int,
             "net_flow": float, "ratio": float,
             "trades": list}
        """
        symbol = SYMBOL_MAP.get(coin.lower(), f"{coin.upper()}USDT")

        # Check cache
        if self.cache and start_ms:
            cache_key = f"taker_flow_{symbol}_{start_ms}_{end_ms}"
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

        params = {"symbol": symbol, "limit": min(limit, 1000)}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms

        data = await self._get(f"{BASE_URL}/api/v3/aggTrades", params)
        if not data:
            return {"buy_volume": 0, "sell_volume": 0, "buy_count": 0,
                    "sell_count": 0, "net_flow": 0, "ratio": 0.5, "trades": []}

        buy_vol = 0.0
        sell_vol = 0.0
        buy_count = 0
        sell_count = 0

        for t in data:
            qty = float(t.get("q", 0))
            price = float(t.get("p", 0))
            value = qty * price
            # isBuyerMaker=True means taker is SELLER
            if t.get("m", False):
                sell_vol += value
                sell_count += 1
            else:
                buy_vol += value
                buy_count += 1

        total = buy_vol + sell_vol
        result = {
            "buy_volume": round(buy_vol, 2),
            "sell_volume": round(sell_vol, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
            "net_flow": round(buy_vol - sell_vol, 2),
            "ratio": round(buy_vol / total, 4) if total > 0 else 0.5,
            "total_trades": len(data),
        }

        # Cache for 7 days (historical data doesn't change)
        if self.cache and start_ms:
            cache_key = f"taker_flow_{symbol}_{start_ms}_{end_ms}"
            await self.cache.set(cache_key, result, ttl=86400 * 7,
                                  source="binance")

        logger.info("Taker flow %s: buy=%.0f sell=%.0f ratio=%.3f",
                     symbol, buy_vol, sell_vol, result["ratio"])
        return result

    # ── Funding Rate ─────────────────────────────────────────

    async def get_funding_rate(self, coin: str = "btc",
                                limit: int = 100) -> list:
        """
        Fetch perpetual futures funding rate history.
        Key for funding_rate strategy.

        Args:
            coin: "btc", "eth", "sol"
            limit: number of records (max 1000)
        Returns:
            List of {"fundingTime": int, "fundingRate": float, "symbol": str}
        """
        symbol = SYMBOL_MAP.get(coin.lower(), f"{coin.upper()}USDT")

        # Check cache
        if self.cache:
            cache_key = f"funding_{symbol}_{limit}"
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

        data = await self._get(
            f"{FAPI_URL}/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": min(limit, 1000)}
        )
        if not data:
            return []

        rates = []
        for r in data:
            rates.append({
                "symbol": r.get("symbol", symbol),
                "funding_time": int(r.get("fundingTime", 0)),
                "funding_rate": float(r.get("fundingRate", 0)),
                "mark_price": float(r.get("markPrice", 0))
                              if "markPrice" in r else 0.0,
            })

        # Cache for 1 hour
        if self.cache and rates:
            cache_key = f"funding_{symbol}_{limit}"
            await self.cache.set(cache_key, rates, ttl=3600,
                                  source="binance")

        logger.info("Fetched %d funding rates for %s", len(rates), symbol)
        return rates

    # ── Current Price (quick helper) ─────────────────────────

    async def get_price(self, coin: str = "btc") -> Optional[float]:
        """Get current spot price."""
        symbol = SYMBOL_MAP.get(coin.lower(), f"{coin.upper()}USDT")
        data = await self._get(
            f"{BASE_URL}/api/v3/ticker/price",
            {"symbol": symbol}
        )
        if data and "price" in data:
            return float(data["price"])
        return None

    # ── Stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Return client statistics."""
        return {
            "total_requests": self._request_count,
        }

    # ── Lifecycle ────────────────────────────────────────────

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Binance historical client closed (reqs=%d)",
                     self._request_count)
