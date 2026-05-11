"""
PolyPaper Bot - Gamma API Historical Data Client
Fetches resolved market metadata from Polymarket's Gamma API.

Data collected:
  - Resolved UP/DOWN market list (winner, volume, liquidity)
  - Market metadata (open/close time, token IDs, slug)
  - Market outcomes for backtest validation

API: https://gamma-api.polymarket.com
No auth required. Rate limit: ~60 req/min (handle 429s).

NOTE: Gamma API does NOT provide orderbook snapshots.
      For that, use PolyBackTest API (polybacktest.py).
      Gamma is for market METADATA + resolution outcomes.
"""

import asyncio
import logging
import time
from datetime import UTC
from typing import Optional

import httpx

from backtest.data_sources.cache import TTL_METADATA, BacktestCache

logger = logging.getLogger("polypaper.backtest.gamma")

GAMMA_BASE = "https://gamma-api.polymarket.com"

# Slug prefixes for crypto up/down markets
SLUG_PREFIXES = {
    "btc": "btc-updown",
    "eth": "eth-updown",
    "sol": "sol-updown",
    "xrp": "xrp-updown",
}

# Market type → slug suffix mapping
MARKET_TYPE_SUFFIXES = {
    "5m": "-5m-",
    "15m": "-15m-",
    "1h": "-1h-",
    "4h": "-4h-",
    "24h": "-24h-",
}

# Market type → interval in seconds
INTERVAL_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}

# Rate limit: conservative — 1 req/sec
RATE_LIMIT_DELAY = 1.0


class GammaHistClient:
    """Async client for Polymarket Gamma API — resolved market data."""

    def __init__(self, cache: Optional[BacktestCache] = None):
        self.cache = cache
        self._client: Optional[httpx.AsyncClient] = None
        self._last_request = 0.0
        self._request_count = 0

    async def init(self) -> "GammaHistClient":
        """Initialize HTTP client and cache."""
        self._client = httpx.AsyncClient(
            base_url=GAMMA_BASE,
            timeout=15.0,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        if self.cache and not self.cache.conn:
            await self.cache.init()
        logger.info("Gamma historical client initialized")
        return self

    async def _rate_limit(self):
        """Enforce rate limit between requests."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request = time.time()
        self._request_count += 1

    async def _get(
        self, path: str, params: Optional[dict] = None, retries: int = 3
    ) -> Optional[list | dict]:
        """GET with rate limiting and exponential backoff on 429."""
        if not self._client:
            logger.error("Client not initialized — call init() first")
            return None

        for attempt in range(retries):
            await self._rate_limit()
            try:
                resp = await self._client.get(path, params=params)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("Gamma rate limited (429), waiting %ds", wait)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 404:
                    return None

                resp.raise_for_status()
                return resp.json()

            except httpx.TimeoutException:
                logger.error("Gamma timeout: %s (attempt %d)", path, attempt + 1)
            except httpx.HTTPStatusError as e:
                logger.error("Gamma HTTP %d: %s", e.response.status_code, path)
                break
            except Exception as e:
                logger.error("Gamma request failed: %s — %s", path, e)
                break

        return None

    @staticmethod
    def _get_interval_seconds(slug_prefix: str) -> int:
        """Get interval in seconds based on slug prefix.
        e.g. 'btc-updown' → tries 5m (300s) by default."""
        for suffix, seconds in INTERVAL_SECONDS.items():
            if suffix in slug_prefix:
                return seconds
        return 300  # default to 5m

    # ── Resolved Markets ─────────────────────────────────────

    async def get_resolved_markets(
        self, coin: str = "btc", limit: int = 10, market_type: str = "5m", offset: int = 0
    ) -> list:
        """
        Fetch resolved crypto up/down markets from Gamma API.
        Uses direct slug pattern queries (btc-updown-5m-{unix_ts}).

        Args:
            coin: "btc", "eth", "sol", "xrp"
            limit: max results to return
            market_type: "5m", "15m", "1h", "4h", "24h"
            offset: skip first N intervals
        Returns:
            List of market dicts with resolution data
        """
        coin_prefix = SLUG_PREFIXES.get(coin.lower(), f"{coin.lower()}-updown")
        slug_base = f"{coin_prefix}-{market_type}"

        # Check cache
        cache_key = f"gamma_resolved_{coin}_{market_type}_{limit}_{offset}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                logger.debug(
                    "Resolved markets from cache: %s %s (%d)", coin, market_type, len(cached)
                )
                return cached

        # Generate slug patterns based on timestamp intervals.
        # Crypto slugs follow: btc-updown-5m-{unix_timestamp}
        import time as _time

        now_ts = int(_time.time())
        interval = INTERVAL_SECONDS.get(market_type, 300)
        base_ts = (now_ts // interval) * interval

        # Search backwards from current time
        markets = []
        max_attempts = min(limit * 3, 100)  # try more since some may not exist
        miss_streak = 0

        for i in range(offset, offset + max_attempts):
            ts = base_ts - (i * interval)
            slug = f"{slug_base}-{ts}"

            data = await self._get(
                "/markets",
                params={
                    "slug": slug,
                    "limit": 2,  # UP and DOWN markets
                },
            )
            if data and isinstance(data, list) and data:
                miss_streak = 0
                for m in data:
                    if m.get("closed", False):
                        market_info = self._parse_market(m, coin)
                        if market_info:
                            markets.append(market_info)
                            if self.cache:
                                await self.cache.set_market(market_info)
            else:
                miss_streak += 1
                # If we miss 10 consecutive, the market type may not exist
                if miss_streak >= 10:
                    logger.info("10 consecutive misses at %s, stopping", slug)
                    break

            if len(markets) >= limit:
                break

        # Cache the list
        if self.cache and markets:
            await self.cache.set(cache_key, markets, ttl=TTL_METADATA, source="gamma")

        logger.info("Fetched %d resolved markets for %s", len(markets), coin)
        return markets

    async def get_all_resolved(self, coin: str = "btc", max_pages: int = 20) -> list:
        """
        Fetch ALL resolved markets with pagination.
        Since we filter by slug in Python, we may need more pages
        to collect enough coin-specific results.

        Args:
            coin: "btc", "eth", "sol"
            max_pages: safety limit on pages (100 events/page from API)
        Returns:
            Complete list of resolved markets for this coin
        """
        all_markets = []
        offset = 0
        page_size = 100
        empty_pages = 0  # Track consecutive pages with 0 matching markets

        for page in range(max_pages):
            batch = await self.get_resolved_markets(coin=coin, limit=page_size, offset=offset)
            if batch:
                all_markets.extend(batch)
                empty_pages = 0
            else:
                empty_pages += 1
                # After 3 consecutive empty pages, stop
                if empty_pages >= 3:
                    break

            offset += page_size

            logger.info(
                "Page %d: %d markets (total: %d)",
                page + 1,
                len(batch) if batch else 0,
                len(all_markets),
            )

        logger.info("Total resolved markets for %s: %d", coin, len(all_markets))
        return all_markets

    # ── Single Event Detail ──────────────────────────────────

    async def get_event(self, event_slug: str) -> Optional[dict]:
        """Fetch single event by slug."""
        cache_key = f"gamma_event_{event_slug}"
        if self.cache:
            cached = await self.cache.get(cache_key)
            if cached:
                return cached

        data = await self._get(
            "/events",
            params={
                "slug": event_slug,
                "limit": 1,
            },
        )
        if not data:
            return None

        events = data if isinstance(data, list) else [data]
        if not events:
            return None

        event = events[0]
        if self.cache:
            await self.cache.set(cache_key, event, ttl=TTL_METADATA, source="gamma")
        return event

    # ── Market Parsing ───────────────────────────────────────

    def _parse_event(self, event: dict, coin: str) -> Optional[dict]:
        """Parse a Gamma event into our standardized market dict."""
        try:
            markets = event.get("markets", [])
            if not markets:
                return None

            # Find UP and DOWN tokens
            up_market = None
            down_market = None
            for m in markets:
                outcome = (m.get("outcome", "") or m.get("groupItemTitle", "")).lower()
                if "up" in outcome or "yes" in outcome:
                    up_market = m
                elif "down" in outcome or "no" in outcome:
                    down_market = m

            # Determine winner
            winner = ""
            if up_market and up_market.get("resolved_at"):
                if (
                    up_market.get("winner", False)
                    or str(up_market.get("resolution", "")).lower() == "yes"
                ):
                    winner = "UP"
                else:
                    winner = "DOWN"
            elif down_market and down_market.get("resolved_at"):
                if (
                    down_market.get("winner", False)
                    or str(down_market.get("resolution", "")).lower() == "yes"
                ):
                    winner = "DOWN"
                else:
                    winner = "UP"

            # Detect market type from slug or timestamps
            # Use event slug, or fall back to first market's slug
            slug = event.get("slug", "")
            if not slug and markets:
                slug = markets[0].get("slug", "")
            market_type = self._detect_market_type(event, markets)

            return {
                "market_id": str(event.get("id", "")),
                "event_slug": slug,
                "coin": coin.upper(),
                "market_type": market_type,
                "question": event.get("title", ""),
                "start_time": event.get("start_date_time", ""),
                "end_time": event.get("end_date_time", ""),
                "winner": winner,
                "volume": float(event.get("volume", 0) or 0),
                "liquidity": float(event.get("liquidity", 0) or 0),
                "up_token_id": up_market.get("clobTokenIds", [""])[0]
                if up_market and up_market.get("clobTokenIds")
                else "",
                "down_token_id": down_market.get("clobTokenIds", [""])[0]
                if down_market and down_market.get("clobTokenIds")
                else "",
                "source": "gamma",
            }
        except Exception as e:
            logger.error("Failed to parse event: %s", e)
            return None

    def _parse_market(self, m: dict, coin: str) -> Optional[dict]:
        """Parse a Gamma individual market object into our format.
        Used when fetching from /markets endpoint directly."""
        try:
            slug = m.get("slug", "")
            question = m.get("question", "") or m.get("title", "")

            # Determine winner from resolution
            winner = ""
            resolution = str(m.get("resolution", "")).lower()
            outcome = (m.get("outcome", "") or m.get("groupItemTitle", "")).lower()
            if resolution == "yes":
                if "up" in outcome:
                    winner = "UP"
                elif "down" in outcome:
                    winner = "DOWN"
                else:
                    winner = "YES"
            elif resolution == "no":
                if "up" in outcome:
                    winner = "DOWN"
                elif "down" in outcome:
                    winner = "UP"
                else:
                    winner = "NO"

            # Detect market type from slug
            market_type = self._detect_market_type_from_slug(slug, question)

            # Token IDs
            clob_ids = m.get("clobTokenIds", [])
            token_id = clob_ids[0] if clob_ids else ""

            # Extract start_time: prefer API field, fallback to slug timestamp
            start_time = m.get("startDate", "") or ""
            if not start_time and slug:
                # Slug pattern: btc-updown-5m-{unix_ts}
                parts = slug.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    from datetime import datetime as _dt

                    ts_val = int(parts[1])
                    start_time = _dt.fromtimestamp(ts_val, tz=UTC).isoformat()

            return {
                "market_id": str(m.get("id", "")),
                "condition_id": m.get("conditionId", ""),
                "event_slug": slug,
                "coin": coin.upper(),
                "market_type": market_type,
                "question": question,
                "outcome": outcome,
                "start_time": start_time,
                "end_time": m.get("endDate", ""),
                "winner": winner,
                "volume": float(m.get("volume", 0) or 0),
                "liquidity": float(m.get("liquidity", 0) or 0),
                "token_id": token_id,
                "source": "gamma",
            }
        except Exception as e:
            logger.error("Failed to parse market: %s", e)
            return None

    def _detect_market_type_from_slug(self, slug: str, title: str = "") -> str:
        """Detect market type from slug string and title."""
        slug_l = slug.lower()
        title_l = title.lower()
        if "5m" in slug_l or "5-minute" in title_l:
            return "5m"
        elif "15m" in slug_l or "15-minute" in title_l:
            return "15m"
        elif "1h" in slug_l or "1-hour" in title_l or "hourly" in title_l:
            return "1h"
        elif "4h" in slug_l or "4-hour" in title_l:
            return "4h"
        elif "24h" in slug_l or "24-hour" in title_l or "daily" in title_l:
            return "24h"
        return "unknown"

    def _detect_market_type(self, event: dict, markets: list = None) -> str:
        """Detect market type (5m, 15m, 1h, etc.) from event data."""
        slug = event.get("slug", "").lower()
        # Also check market-level slugs
        if markets:
            for m in markets:
                ms = (m.get("slug", "") or "").lower()
                if ms:
                    slug = slug + " " + ms
        title = event.get("title", "").lower()

        if "5-minute" in title or "5m" in slug or "5min" in slug:
            return "5m"
        elif "15-minute" in title or "15m" in slug or "15min" in slug:
            return "15m"
        elif "1-hour" in title or "1h" in slug or "hourly" in title:
            return "1h"
        elif "4-hour" in title or "4h" in slug:
            return "4h"
        elif "24-hour" in title or "24h" in slug or "daily" in title:
            return "24h"

        # Fallback: check timestamps
        try:
            from datetime import datetime

            start = event.get("start_date_time", "")
            end = event.get("end_date_time", "")
            if start and end:
                s = datetime.fromisoformat(start.replace("Z", "+00:00"))
                e = datetime.fromisoformat(end.replace("Z", "+00:00"))
                diff_min = (e - s).total_seconds() / 60
                if diff_min <= 6:
                    return "5m"
                elif diff_min <= 16:
                    return "15m"
                elif diff_min <= 65:
                    return "1h"
                elif diff_min <= 250:
                    return "4h"
                else:
                    return "24h"
        except Exception:
            pass

        return "unknown"

    # ── Stats ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {"total_requests": self._request_count}

    # ── Lifecycle ────────────────────────────────────────────

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("Gamma historical client closed (reqs=%d)", self._request_count)
