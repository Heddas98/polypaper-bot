"""
PolyPaper Bot - PolyBackTest API Client
Connects to https://api.polybacktest.com for historical orderbook snapshots.

Free tier limits:
  - Last 50 BTC 5m/15m markets
  - Last 24 BTC 1h/4h markets
  - Last 5 BTC 24h markets

Endpoints:
  GET /v2/markets?coin=btc&market_type=5m&limit=50
  GET /v1/markets/{market_id}
  GET /v1/markets/{market_id}/snapshots

Auth: "Authorization: Bearer pdm_xxx" or "X-API-Key: pdm_xxx"
"""

import asyncio
import logging
import os
from typing import Optional

import httpx

from backtest.data_sources.cache import TTL_MARKETS, BacktestCache

logger = logging.getLogger("polypaper.backtest.polybacktest")

BASE_URL = "https://api.polybacktest.com"

# Free tier limits per market_type
FREE_TIER_LIMITS = {
    "5m": 50,
    "15m": 50,
    "1h": 24,
    "4h": 24,
    "24h": 5,
}

# Rate limit: be conservative — max 2 req/sec
RATE_LIMIT_DELAY = 0.5


class PolyBackTestClient:
    """Async client for PolyBackTest API with caching and rate limiting."""

    def __init__(self, cache: Optional[BacktestCache] = None, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("POLYBACKTEST_API_KEY", "")
        self.cache = cache
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request = 0.0
        self._consecutive_snapshot_fails = 0
        self._snapshots_disabled = False

    async def init(self) -> "PolyBackTestClient":
        """Initialize HTTP client and cache."""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        if self.cache and not self.cache.conn:
            await self.cache.init()
        logger.info("PolyBackTest client initialized (key=%s)", "set" if self.api_key else "none")
        return self

    async def _rate_limit(self):
        """Enforce rate limit between requests."""
        import time

        now = time.time()
        elapsed = now - self._last_request
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.time()

    async def _get(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        """Make a GET request with rate limiting and error handling."""
        if not self._client:
            logger.error("Client not initialized — call init() first")
            return None
        await self._rate_limit()
        try:
            resp = await self._client.get(path, params=params)

            # Handle rate limit (429)
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", "5"))
                logger.warning("Rate limited, waiting %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                resp = await self._client.get(path, params=params)

            if resp.status_code == 401:
                logger.error("Auth failed — check POLYBACKTEST_API_KEY")
                return None

            if resp.status_code == 404:
                logger.warning("Not found: %s", path)
                return None

            resp.raise_for_status()
            return resp.json()

        except httpx.TimeoutException:
            logger.error("Timeout: %s", path)
            return None
        except httpx.HTTPStatusError as e:
            logger.error("HTTP %d: %s", e.response.status_code, path)
            return None
        except Exception as e:
            logger.error("Request failed: %s — %s", path, e)
            return None

    # ── Market list ──────────────────────────────────────────

    async def get_markets(self, coin: str = "btc", market_type: str = "5m", limit: int = 0) -> list:
        """
        Fetch available markets from PolyBackTest.

        Args:
            coin: "btc", "eth", "sol" etc.
            market_type: "5m", "15m", "1h", "4h", "24h"
            limit: max results (0 = use free tier limit)
        Returns:
            List of market dicts
        """
        if not limit:
            limit = FREE_TIER_LIMITS.get(market_type, 50)

        # Check cache first
        cache_key = f"pbt_markets_{coin}_{market_type}_{limit}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                logger.debug("Markets from cache: %s %s (%d)", coin, market_type, len(cached))
                return cached

        # API call
        data = await self._get(
            "/v2/markets",
            params={
                "coin": coin.lower(),
                "market_type": market_type,
                "limit": limit,
            },
        )

        if not data:
            return []

        # API may return {"markets": [...]} or just [...]
        markets = data if isinstance(data, list) else data.get("markets", [])

        # Cache results + individual markets
        if self.cache and markets:
            await self.cache.set(cache_key, markets, ttl=TTL_MARKETS, source="polybacktest")
            for m in markets:
                m["coin"] = coin.upper()
                m["market_type"] = market_type
                await self.cache.set_market(m)

        logger.info("Fetched %d markets: %s %s", len(markets), coin, market_type)
        return markets

    # ── Single market detail ─────────────────────────────────

    async def get_market(self, market_id: str) -> Optional[dict]:
        """Fetch single market detail."""
        # Check cache
        if self.cache:
            cached = await self.cache.get_market(market_id)
            if cached:
                return cached

        data = await self._get(f"/v1/markets/{market_id}")
        if data and self.cache:
            await self.cache.set_market(data)
        return data

    # ── Snapshots (orderbook history) ────────────────────────

    async def get_snapshots(
        self,
        market_id: str,
        force_refresh: bool = False,
        condition_id: str = "",
        market_dict: Optional[dict] = None,
    ) -> list:
        """
        Fetch orderbook snapshots for a market.
        These are the core data for backtesting — sub-second orderbook states.
        Tries multiple ID fields and endpoint versions to find snapshots.

        Args:
            market_id: PolyBackTest market ID
            force_refresh: skip cache and re-fetch
            condition_id: alternative ID (from market data)
            market_dict: raw market dict (to extract IDs from)
        Returns:
            List of snapshot dicts sorted by timestamp
        """
        # Fast-fail: if snapshots API consistently fails (free tier),
        # skip all further attempts to avoid rate limit spam
        if self._snapshots_disabled:
            return []

        # Check cache
        if self.cache and not force_refresh:
            if await self.cache.has_snapshots(market_id):
                cached = await self.cache.get_snapshots(market_id)
                if cached:
                    logger.debug("Snapshots from cache: %s (%d)", market_id, len(cached))
                    return cached

        # Build list of IDs to try (limit to market_id only for speed)
        ids_to_try = [market_id]
        if condition_id and condition_id != market_id:
            ids_to_try.append(condition_id)

        # Try endpoint patterns (simplified: one ID, one prefix)
        data = None
        for mid in ids_to_try[:2]:  # max 2 IDs
            if not mid:
                continue
            data = await self._get(f"/v2/markets/{mid}/snapshots")
            if data:
                logger.info("Snapshots found: %s", mid)
                self._consecutive_snapshot_fails = 0
                break

        if not data:
            self._consecutive_snapshot_fails += 1
            if self._consecutive_snapshot_fails >= 3:
                self._snapshots_disabled = True
                logger.warning(
                    "Snapshots disabled after %d consecutive fails "
                    "(free tier limitation). Engine will run without "
                    "orderbook data.",
                    self._consecutive_snapshot_fails,
                )
            return []

        snapshots = data if isinstance(data, list) else data.get("snapshots", [])

        # Sort by timestamp
        snapshots.sort(key=lambda s: s.get("timestamp_ms", s.get("timestamp", 0)))

        # Cache
        if self.cache and snapshots:
            count = await self.cache.store_snapshots(market_id, snapshots)
            logger.info("Cached %d snapshots for %s", count, market_id)

        logger.info("Fetched %d snapshots for market %s", len(snapshots), market_id)
        return snapshots

    # ── Spot price ───────────────────────────────────────────

    async def get_spot_price(self, coin: str = "btc") -> Optional[dict]:
        """Get latest spot price data from PolyBackTest."""
        cache_key = f"pbt_spot_{coin}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

        data = await self._get("/v2/spot/latest", params={"coin": coin.lower()})
        if data and self.cache:
            await self.cache.set(cache_key, data, ttl=60, source="polybacktest")
        return data

    # ── Bulk fetch for backtesting ───────────────────────────

    async def fetch_backtest_data(
        self, coin: str = "btc", market_type: str = "5m", max_markets: int = 0
    ) -> dict:
        """
        Convenience method: fetch markets + all their snapshots.
        Returns dict with markets list and snapshot count.

        Args:
            coin: "btc", "eth", etc.
            market_type: "5m", "15m", "1h", "4h", "24h"
            max_markets: 0 = fetch all available (free tier limit)
        Returns:
            {"markets": [...], "total_snapshots": int, "errors": int}
        """
        markets = await self.get_markets(coin, market_type, max_markets)
        if not markets:
            return {"markets": [], "total_snapshots": 0, "errors": 0}

        total_snapshots = 0
        errors = 0

        for i, market in enumerate(markets):
            mid = market.get("market_id") or market.get("id", "")
            if not mid:
                errors += 1
                continue
            try:
                snaps = await self.get_snapshots(mid, market_dict=market)
                total_snapshots += len(snaps)
                logger.info("[%d/%d] %s: %d snapshots", i + 1, len(markets), mid, len(snaps))
            except Exception as e:
                logger.error("Failed to fetch snapshots for %s: %s", mid, e)
                errors += 1

        return {
            "markets": markets,
            "total_snapshots": total_snapshots,
            "errors": errors,
        }

    # ── Lifecycle ────────────────────────────────────────────

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("PolyBackTest client closed")
