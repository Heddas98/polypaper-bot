"""
PolyPaper Bot - Polymarket Client (Phase 8.5)
WS-first: if WebSocket has fresh price, skip REST entirely.
This reduces engine cycle from ~6s to <1s.
Phase 56: 429 retry + configurable timeout.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx
from config.settings import Settings
from core.observability.rest_timing import time_call

logger = logging.getLogger("polypaper.data.polymarket")

# Phase 56: Configurable CLOB timeout (default 5s, env override)
CLOB_TIMEOUT = float(os.getenv("CLOB_TIMEOUT", "5.0"))
# Phase 56: Max retries on 429 rate-limit
MAX_429_RETRIES = int(os.getenv("MAX_429_RETRIES", "3"))

INTERVAL_SECS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}
MAX_FUTURE = {"1h": timedelta(hours=3), "4h": timedelta(hours=12), "24h": timedelta(hours=50)}


def safe_float(val, default=None) -> Optional[float]:
    if val is None:
        return default
    try:
        f = float(val)
        return f if 0.0 <= f <= 1.0 else default
    except (ValueError, TypeError):
        return default


class PolymarketClient:
    GAMMA_BASE = "https://gamma-api.polymarket.com"
    CLOB_BASE = "https://clob.polymarket.com"
    SLUG_PREFIXES = {"BTC": "btc-updown", "ETH": "eth-updown",
                     "SOL": "sol-updown", "XRP": "xrp-updown"}

    def __init__(self, settings: Settings, ws_client=None):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=CLOB_TIMEOUT,
            headers={"Accept": "application/json"})
        self._events_cache: list[dict] = []
        self._events_ts: float = 0
        self.ws = ws_client
        self._429_count: int = 0  # Phase 56: rate-limit hit counter

    async def close(self):
        await self._client.aclose()

    async def _get_with_retry(self, url: str, params: dict | None = None,
                              timeout: float | None = None,
                              label: str | None = None) -> httpx.Response | None:
        """Phase 56: GET with 429 exponential backoff retry.
        Returns Response on success (any 2xx/4xx), None on total failure.

        T4.9 (2026-04-24): `label` param enables REST timing telemetry.
        When `REST_TIMING_TELEMETRY=true` (default OFF), each successful GET
        records RTT into `core.observability.rest_timing` rolling buffer.
        Zero overhead when telemetry disabled (context mgr no-op path).
        """
        t = timeout or CLOB_TIMEOUT
        for attempt in range(MAX_429_RETRIES + 1):
            try:
                async with time_call(label or "polymarket.http.get"):
                    r = await self._client.get(url, params=params, timeout=t)
                if r.status_code == 429:
                    self._429_count += 1
                    wait = min(2 ** attempt, 8)  # 1s, 2s, 4s, 8s
                    if self._429_count <= 5 or self._429_count % 50 == 0:
                        logger.warning(
                            f"⚠️ 429 rate-limited (#{self._429_count}) "
                            f"on {url.split('/')[-1]}, retry in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                return r
            except (httpx.HTTPError, asyncio.TimeoutError):
                # T11.8-B (2026-04-24): narrow from bare Exception. httpx
                # raises HTTPError (base of TimeoutException/ConnectError/
                # NetworkError); asyncio.TimeoutError covers older asyncio
                # cancellation. Return None lets caller fall back gracefully.
                return None
        return None  # exhausted retries

    # ═══ DUAL-SOURCE PRICING ═══

    async def get_live_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """WS first (0ms) → REST fallback (1-3s)."""
        # Source 1: WebSocket cache
        if self.ws is not None and self.ws.is_connected:
            p = self.ws.get_live_price(token_id)
            if p is not None and 0.01 < p < 0.99:
                return p

        # Source 2: REST (Phase 56: with 429 retry)
        r = await self._get_with_retry(
            f"{self.CLOB_BASE}/price",
            params={"token_id": token_id, "side": side}, timeout=3.0)
        if r and r.status_code == 200:
            p = safe_float(r.json().get("price"))
            if p and 0.01 < p < 0.99:
                return p
        return None

    async def get_live_midpoint(self, token_id: str) -> Optional[float]:
        if self.ws is not None and self.ws.is_connected:
            p = self.ws.get_live_price(token_id)
            if p is not None:
                return p
        try:
            async with time_call("clob.midpoint"):
                r = await self._client.get(
                    f"{self.CLOB_BASE}/midpoint",
                    params={"token_id": token_id}, timeout=3.0)
            if r.status_code == 200:
                return safe_float(r.json().get("mid"))
        except Exception:  # noqa: BLE001
            pass
        return None

    # ═══ MARKET ODDS ═══

    async def get_market_odds(self, market: dict) -> Optional[dict]:
        tokens = self._extract_token_ids(market)
        if not tokens:
            return None
        up_tok, dn_tok = tokens[0], (tokens[1] if len(tokens) > 1 else None)

        result = {"up_odds": None, "down_odds": None, "up_token": up_tok,
                  "down_token": dn_tok, "spread": None, "has_liquidity": False,
                  "best_ask_up": None, "best_bid_up": None}

        if up_tok:
            ask = await self.get_live_price(up_tok, "BUY")
            bid = await self.get_live_price(up_tok, "SELL")
            mid = await self.get_live_midpoint(up_tok)
            if mid and 0.02 < mid < 0.98:
                result["up_odds"] = mid
            if ask:
                result["best_ask_up"] = ask
            if bid:
                result["best_bid_up"] = bid
            if ask and bid:
                result["spread"] = round(ask - bid, 4)
                result["has_liquidity"] = True

        if dn_tok:
            mid = await self.get_live_midpoint(dn_tok)
            if mid and 0.02 < mid < 0.98:
                result["down_odds"] = mid

        if result["up_odds"] and not result["down_odds"]:
            result["down_odds"] = round(1.0 - result["up_odds"], 4)
        elif result["down_odds"] and not result["up_odds"]:
            result["up_odds"] = round(1.0 - result["down_odds"], 4)
        if result["up_odds"] and not result["has_liquidity"]:
            result["has_liquidity"] = True
        return result

    # ═══ MARKET DISCOVERY ═══

    async def discover_active_markets(self, asset="BTC", timeframe="15m"):
        if timeframe in ("5m", "15m"):
            return await self._discover_by_slug(asset, timeframe)
        return await self._discover_by_events(asset, timeframe)

    async def _discover_by_slug(self, asset, tf):
        prefix = self.SLUG_PREFIXES.get(asset.upper())
        if not prefix:
            return []
        interval = INTERVAL_SECS[tf]
        now_ts = int(datetime.now(timezone.utc).timestamp())
        current = now_ts - (now_ts % interval)
        found = []
        for ts in [current - interval, current, current + interval]:
            slug = f"{prefix}-{tf}-{ts}"
            m = await self._query_slug(slug)
            if m and not m.get("closed", False):
                end = self._parse_dt(m.get("endDate"))
                if end and end <= datetime.now(timezone.utc):
                    continue
                found.append(m)
        found.sort(key=lambda m: m.get("endDate", "z"))
        if found:
            logger.info(f"Slug: {len(found)} {asset} {tf}")
        return found

    async def _query_slug(self, slug):
        # Phase 56: 429 retry on Gamma API
        r = await self._get_with_retry(
            f"{self.GAMMA_BASE}/events", params={"slug": slug}, timeout=4.0)
        if r and r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                mkts = data[0].get("markets", [])
                return mkts[0] if mkts else data[0]
        r = await self._get_with_retry(
            f"{self.GAMMA_BASE}/markets", params={"slug": slug}, timeout=4.0)
        if r and r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
        return None

    async def _discover_by_events(self, asset, tf):
        await self._refresh_events_cache()
        prefix = self.SLUG_PREFIXES.get(asset.upper(), "btc-updown")
        target = f"{prefix}-{tf}-"
        now = datetime.now(timezone.utc)
        max_f = MAX_FUTURE.get(tf, timedelta(hours=12))
        found = []
        for ev in self._events_cache:
            for m in ev.get("markets", []):
                if m.get("slug", "").startswith(target) and not m.get("closed"):
                    end = self._parse_dt(m.get("endDate") or ev.get("endDate"))
                    if end and (end <= now or (end - now) > max_f):
                        continue
                    found.append(m)
        found.sort(key=lambda m: m.get("endDate", "z"))
        return found

    async def _refresh_events_cache(self):
        now = datetime.now(timezone.utc).timestamp()
        if self._events_cache and (now - self._events_ts) < 25:
            return
        events = []
        for offset in (0, 50, 100):
            try:
                async with time_call("gamma.events"):
                    r = await self._client.get(
                        f"{self.GAMMA_BASE}/events",
                        params={"active": "true", "closed": "false", "limit": 50,
                                "offset": offset, "order": "id", "ascending": "false"}, timeout=5.0)
                if r.status_code != 200:
                    break
                batch = r.json()
                if not isinstance(batch, list) or not batch:
                    break
                events.extend(batch)
                if len(batch) < 50:
                    break
            except (httpx.HTTPError, asyncio.TimeoutError,
                    json.JSONDecodeError, ValueError):
                # T11.8-B (2026-04-24): narrow from bare Exception. httpx
                # request errors + r.json() parse failures end the pagination
                # loop early. We keep partial `events` already collected.
                break
        self._events_cache = events
        self._events_ts = now
        logger.info(f"Events cache: {len(events)}")

    async def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Phase 18: Fetch L2 orderbook for depth simulation.
        Returns: {"asks": [[price, size], ...], "bids": [[price, size], ...]}
        Sorted: asks low→high, bids high→low."""
        try:
            async with time_call("clob.orderbook"):
                r = await self._client.get(
                    f"{self.CLOB_BASE}/book",
                    params={"token_id": token_id}, timeout=3.0)
            if r.status_code == 200:
                data = r.json()
                asks = []
                bids = []
                for a in (data.get("asks") or []):
                    p = float(a.get("price", 0))
                    s = float(a.get("size", 0))
                    if p > 0 and s > 0:
                        asks.append([p, s])
                for b in (data.get("bids") or []):
                    p = float(b.get("price", 0))
                    s = float(b.get("size", 0))
                    if p > 0 and s > 0:
                        bids.append([p, s])
                asks.sort(key=lambda x: x[0])      # low → high
                bids.sort(key=lambda x: -x[0])      # high → low
                return {"asks": asks, "bids": bids}
        except (httpx.HTTPError, asyncio.TimeoutError,
                json.JSONDecodeError, ValueError, TypeError,
                AttributeError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. httpx network
            # errors + JSON parse + float() coercion of dict-shape responses
            # surface here. Debug-log + None return is intentional (orderbook
            # fetch is best-effort, fill simulator falls back to point fill).
            logger.debug(f"Orderbook fetch: {type(e).__name__}: {e}")
        return None

    def calculate_vwap_fill(self, orderbook: dict, side: str, amount_usd: float) -> Optional[dict]:
        """Phase 18: VWAP fill simulation from real orderbook depth.
        side='BUY' uses asks, 'SELL' uses bids.
        Returns: {"vwap": float, "filled_usd": float, "filled_shares": float,
                  "levels_consumed": int, "partial": bool, "depth_usd": float}"""
        levels = orderbook.get("asks" if side == "BUY" else "bids", [])
        if not levels:
            return None

        total_cost = 0.0
        total_shares = 0.0
        levels_consumed = 0
        remaining = amount_usd

        for price, size in levels:
            if remaining <= 0:
                break
            level_cost = price * size  # USD value of this level
            if level_cost >= remaining:
                # Partial fill of this level
                shares_here = remaining / price
                total_cost += remaining
                total_shares += shares_here
                remaining = 0
            else:
                # Consume entire level
                total_cost += level_cost
                total_shares += size
                remaining -= level_cost
            levels_consumed += 1

        if total_shares <= 0:
            return None

        depth_usd = sum(p * s for p, s in levels)
        return {
            "vwap": round(total_cost / total_shares, 6),
            "filled_usd": round(total_cost, 4),
            "filled_shares": round(total_shares, 4),
            "levels_consumed": levels_consumed,
            "partial": remaining > 0,
            "depth_usd": round(depth_usd, 2),
        }

    async def check_market_resolved(self, slug):
        """Phase 82e Sprint 5 HOTFIX v4 — RESOLUTION PATH FIX.

        Eski buggy kod Gamma API'nin DONMEYEN tokens[i].winner field'ini
        ariyordu; resolved market bile her zaman None donuyordu.

        Gamma /markets gercek response fields:
          - closed: bool
          - outcomePrices: str (stringified JSON, orn. "[\\"1\\",\\"0\\"]")
          - outcomes: list or str (orn. ["Up","Down"])
          - clobTokenIds: str (stringified JSON)

        Winner: outcomePrices icinde >=0.99 olan index'in outcome'u.
        """
        try:
            async with time_call("gamma.markets.slug"):
                r = await self._client.get(
                    f"{self.GAMMA_BASE}/markets",
                    params={"slug": slug}, timeout=4.0)
            if not r or r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, list) and data:
                data = data[0]
            if not isinstance(data, dict):
                return None
            if not data.get("closed"):
                return None

            # outcomePrices — stringified JSON array (Gamma default)
            op = data.get("outcomePrices")
            if isinstance(op, str):
                try:
                    op = json.loads(op)
                except (json.JSONDecodeError, ValueError):
                    return None
            if not isinstance(op, list) or not op:
                return None

            # outcomes — list OR stringified JSON
            outcomes = data.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except (json.JSONDecodeError, ValueError):
                    outcomes = []

            # Primary path: outcomes + outcomePrices parallel
            if len(op) == len(outcomes) and len(op) >= 2:
                for i, price in enumerate(op):
                    try:
                        pf = float(price)
                    except (ValueError, TypeError):
                        continue
                    if pf >= 0.99:
                        return str(outcomes[i]).lower()

            # Fallback: 2-outcome up/down market, no outcomes list
            if len(op) == 2:
                try:
                    p0 = float(op[0])
                except (ValueError, TypeError):
                    return None
                if p0 >= 0.99:
                    return "up"
                if p0 <= 0.01:
                    return "down"
        except (httpx.HTTPError, asyncio.TimeoutError,
                json.JSONDecodeError, ValueError, TypeError,
                AttributeError, KeyError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Wraps full
            # gamma fetch + dict/list parse + outcome list iteration. Inner
            # try blocks already narrow individual coercion failures; outer
            # catch is the network/shape guard.
            logger.debug(f"check_market_resolved({slug}): "
                         f"{type(e).__name__}: {e}")
        return None

    async def get_resolution_price(self, token_id: str) -> Optional[float]:
        """Phase 82e Sprint 5 HOTFIX v4 — Unfiltered live price.

        get_live_price() 0.01-0.99 clamp'i uyguluyor; tam olarak resolved
        market fiyatlarini (0.0 / 1.0) disarida birakiyor -> engine_monitor
        CLOB-ORACLE fallback olu kod. Bu metot YALNIZCA oracle fallback'ta
        cagrilir; aktif trading path'inde kullanilmaz.

        Returns: float in [0.0, 1.0], or None on error/no data.
        """
        # Source 1: WebSocket cache (filtresiz)
        if self.ws is not None and self.ws.is_connected:
            p = self.ws.get_live_price(token_id)
            if p is not None:
                try:
                    pf = float(p)
                    if 0.0 <= pf <= 1.0:
                        return pf
                except (ValueError, TypeError):
                    pass

        # Source 2: REST CLOB /price (filtresiz)
        r = await self._get_with_retry(
            f"{self.CLOB_BASE}/price",
            params={"token_id": token_id, "side": "BUY"}, timeout=3.0)
        if r and r.status_code == 200:
            try:
                pf = float(r.json().get("price", 0))
                if 0.0 <= pf <= 1.0:
                    return pf
            except (ValueError, TypeError, AttributeError):
                pass
        return None

    async def get_server_time(self):
        try:
            async with time_call("clob.time"):
                r = await self._client.get(f"{self.CLOB_BASE}/time", timeout=5.0)
            return int(r.text) if r.status_code == 200 else int(datetime.now(timezone.utc).timestamp())
        except (httpx.HTTPError, asyncio.TimeoutError,
                ValueError, AttributeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. httpx
            # transport errors + int(r.text) ValueError + r.text on unset
            # response (AttributeError). Local time fallback is correct.
            return int(datetime.now(timezone.utc).timestamp())

    def _extract_token_ids(self, market):
        ct = market.get("clobTokenIds")
        if isinstance(ct, str):
            try:
                ct = json.loads(ct)
            except json.JSONDecodeError:
                ct = None
        if ct and isinstance(ct, list):
            return [t for t in ct if t]
        tokens = market.get("tokens", [])
        return [t["token_id"] for t in tokens if t.get("token_id")]

    def _parse_dt(self, val):
        if not val:
            return None
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def parse_market_info(self, market):
        slug = market.get("slug", "")
        parts = slug.split("-")
        asset = parts[0].upper() if parts else "BTC"
        tf = parts[2] if len(parts) > 2 else "15m"
        ct = self._extract_token_ids(market)
        op = market.get("outcomePrices")
        if isinstance(op, str):
            try:
                op = json.loads(op)
            except json.JSONDecodeError:
                op = []
        return {
            "slug": slug, "asset": asset, "timeframe": tf,
            "question": market.get("question", ""),
            "up_token_id": ct[0] if ct else None,
            "down_token_id": ct[1] if len(ct) > 1 else None,
            "up_odds": safe_float(op[0]) if op else None,
            "down_odds": safe_float(op[1]) if op and len(op) > 1 else None,
            "end_time": market.get("endDate"),
            "closed": market.get("closed", False),
            "condition_id": market.get("conditionId"),
        }

    async def get_price_history(self, token_id, interval="1h", fidelity=60):
        try:
            async with time_call("clob.prices_history"):
                r = await self._client.get(f"{self.CLOB_BASE}/prices-history",
                                           params={"market": token_id, "interval": interval, "fidelity": fidelity}, timeout=4.0)
            return r.json().get("history", []) if r.status_code == 200 else []
        except (httpx.HTTPError, asyncio.TimeoutError,
                json.JSONDecodeError, ValueError, AttributeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. httpx
            # transport + .json() parse + .get() on non-dict surface here.
            # Empty list is a valid signal-fusion fallback.
            return []
