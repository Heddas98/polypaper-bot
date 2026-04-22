"""
PolyPaper Bot - WebSocket Client (Phase 8.5 BULLETPROOF)
Every message handler wrapped in try/except.
Supports: dict, list of dicts, nested structures.
NEVER crashes the receive loop.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.websocket")

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class PolymarketWebSocket:
    def __init__(self):
        self._ws = None
        self._running = False
        self._task = None
        self._subscribed: set[str] = set()
        self.live_prices: dict[str, dict] = {}
        self._connected = False
        # Epic 5 T5.4 (2026-04-21): reconnect invalidation marker.
        # Epoch seconds at which the current WS connection became live.
        # get_live_price() treats any cached entry older than this as
        # stale to prevent serving pre-reconnect prices. 0.0 = never
        # connected (legacy behavior: staleness handled by WS_STALE_THRESHOLD only).
        self._connected_since: float = 0.0
        self._reconnects = 0
        self._last_msg_ts: float = 0
        self._msg_count: int = 0
        self._errors: int = 0
        # Phase 19: Real-time callback for OddsFeed bridge
        self._on_price_callback = None  # callable(token_id, price)
        # Phase 39 (P1.1): Real-time callback for actual trade events
        # Polymarket WS sends `last_trade_price` events with size + side fields.
        # This callback fires only for genuine fills, not price-change ticks.
        self._on_trade_callback = None  # callable(token_id, price, size, side, ts_ms)
        self._trade_count: int = 0
        # Phase 50 P1-10: tick-loss detector. Counts gaps > threshold (sec) as
        # "lost ticks" (WS stream hiccup) and parse failures as decode errors.
        self._tick_gaps: int = 0
        self._tick_gap_threshold: float = 5.0  # secs without any message
        self._last_tick_log_ts: float = 0.0
        # Epic 5 T5.6 Fix C (2026-04-21): WS subscription cap telemetry.
        # Counts how many times the MAX_WS_TOKENS cap was hit (partial OR full)
        # and the cumulative number of tokens that were skipped because of it.
        # Exposed via get_status() so /h and /db_health can surface the leak.
        self._cap_hit_count: int = 0
        self._cap_skipped_total: int = 0
        self._last_cap_hit_ts: float = 0.0

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self):
        if self._running:
            return
        self._running = True
        # Phase 82e Sprint 2.1: WS loop death = no price data = trading halt
        self._task = safe_create_task(self._loop(), name="polymarket_ws_loop")
        logger.info(f"🔌 WS starting")

    async def stop(self):
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._connected = False

    async def subscribe(self, token_ids: list[str],
                        priority_first: Optional[list[str]] = None):
        """Subscribe tokens with deterministic cap-aware ordering.

        Phase 79b: Cap total subscriptions to prevent WS overload.
        Epic 5 T5.6 (2026-04-21) Fix B: deterministic ordering + priority.

        When the MAX_WS_TOKENS cap would be exceeded, tokens are admitted
        in this order:
          1. `priority_first` list (caller-supplied, e.g. open-position
             tokens or protected-strategy tokens) — NEVER dropped unless
             the cap can't fit even these.
          2. `token_ids` list (caller-supplied ordering preserved) —
             drops from the TAIL when cap is hit, so top-of-list (higher
             priority in the caller's view) always wins.

        Dedupe is order-preserving: the first occurrence of each tid is
        kept, subsequent duplicates (including cross-list duplicates)
        are filtered out.

        Previously the code used `set(list(new)[:avail])` which was
        nondeterministic because set iteration order depends on Python's
        hash seed. Same input could yield different "winners" on
        different runs, leaving protected tokens silently unsubscribed.
        """
        _max = int(os.getenv("MAX_WS_TOKENS", "200"))

        # Build ordered, deduped candidate list. Priority-first tokens
        # come first; existing subscriptions are filtered out.
        ordered: list[str] = []
        seen: set[str] = set()
        for tid in list(priority_first or []) + list(token_ids):
            if not tid:
                continue
            if tid in self._subscribed or tid in seen:
                continue
            ordered.append(tid)
            seen.add(tid)

        if not ordered:
            return

        avail = _max - len(self._subscribed)

        # Epic 5 T5.6 Fix C: telemetry on cap hit (partial OR full)
        if len(ordered) > avail:
            self._cap_hit_count += 1
            dropped = len(ordered) - max(0, avail)
            self._cap_skipped_total += dropped
            self._last_cap_hit_ts = time.time()

        if avail <= 0:
            logger.warning(
                f"  WS token cap reached ({len(self._subscribed)}/{_max}), "
                f"skipping {len(ordered)} new "
                f"(total skipped: {self._cap_skipped_total})")
            return

        if len(ordered) > avail:
            logger.warning(
                f"  WS token cap partial ({len(self._subscribed)}/{_max}): "
                f"admitting {avail}/{len(ordered)}, dropping "
                f"{len(ordered) - avail} from tail")
            ordered = ordered[:avail]

        for tid in ordered:
            await self._send(json.dumps({"type": "market", "assets_ids": [tid]}))
            self._subscribed.add(tid)
        logger.info(f"  WS +{len(ordered)} tokens (total: {len(self._subscribed)})")

    def prune_stale_tokens(self, active_token_ids: set[str]) -> int:
        """Phase 79b: Remove tokens no longer in active markets.

        Returns count of pruned tokens. Actual WS unsubscribe isn't
        supported by Polymarket, but pruning _subscribed prevents
        re-subscribe on reconnect.
        """
        stale = self._subscribed - active_token_ids
        if stale:
            self._subscribed -= stale
            # Also clean live_prices cache
            for tid in stale:
                self.live_prices.pop(tid, None)
            logger.info(f"  WS pruned {len(stale)} stale tokens (remaining: {len(self._subscribed)})")
        return len(stale)

    async def _loop(self):
        delay = 5
        while self._running:
            try:
                import websockets
            except ImportError:
                logger.error("websockets not installed")
                return

            try:
                async with websockets.connect(
                    WS_URL, ping_interval=30, ping_timeout=10,
                    close_timeout=5, max_size=2**20
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    self._last_msg_ts = time.time()  # Phase 62: fix cold-start stale gate
                    # Epic 5 T5.4: mark reconnect moment so get_live_price
                    # invalidates stale entries cached before this connection.
                    self._connected_since = time.time()
                    self._reconnects = 0
                    delay = 5
                    logger.info("✅ WS connected!")

                    if self._subscribed:
                        for tid in self._subscribed:
                            await ws.send(json.dumps({"type": "market", "assets_ids": [tid]}))
                        logger.info(f"  Re-subscribed {len(self._subscribed)} tokens")

                    async for raw in ws:
                        if not self._running:
                            break
                        now = time.time()
                        # Phase 50 P1-10: tick-loss detector
                        # Phase 78-fix: force reconnect on persistent stale (>300s)
                        if self._last_msg_ts and \
                                (now - self._last_msg_ts) > self._tick_gap_threshold:
                            self._tick_gaps += 1
                            gap_secs = now - self._last_msg_ts
                            if (now - self._last_tick_log_ts) > 60:
                                logger.warning(
                                    f"⚠️ WS tick-gap {gap_secs:.1f}s "
                                    f"(total gaps: {self._tick_gaps}, errors: {self._errors})"
                                )
                                self._last_tick_log_ts = now
                            # Force reconnect if gap exceeds threshold (Phase 79b: ENV-configurable)
                            _force_reconnect_sec = int(os.getenv("WS_FORCE_RECONNECT_SEC", "300"))
                            if gap_secs > _force_reconnect_sec:
                                logger.warning(f"🔌 WS forcing reconnect: {gap_secs:.0f}s stale (thr={_force_reconnect_sec}s)")
                                await ws.close()
                                break
                        self._last_msg_ts = now
                        self._msg_count += 1
                        # BULLETPROOF: entire handler in try/except
                        try:
                            self._parse(raw)
                        except Exception as _parse_err:
                            self._errors += 1
                            # Phase 54 P0-03: log parse failures (was silent)
                            # Phase 57: reduced to every 100th after initial 5
                            if self._errors <= 5 or self._errors % 100 == 0:
                                logger.warning(f"⚠️ WS parse error #{self._errors}: {type(_parse_err).__name__}: {_parse_err}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._reconnects += 1
                if self._reconnects <= 3 or self._reconnects % 20 == 0:
                    logger.warning(f"🔌 WS lost: {type(e).__name__}. #{self._reconnects} in {delay}s")
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 120)
        self._connected = False

    def _parse(self, raw):
        """Parse ANY message format. Never raises."""
        # Phase 57: guard against empty/whitespace-only messages from Polymarket
        if not raw or (isinstance(raw, (str, bytes)) and not raw.strip()):
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            # Silently skip malformed messages — already counted via _errors
            return

        # Flatten: could be dict, list, list of lists
        events = []
        if isinstance(data, dict):
            events.append(data)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    events.append(item)
                elif isinstance(item, list):
                    for sub in item:
                        if isinstance(sub, dict):
                            events.append(sub)

        now_iso = datetime.now(timezone.utc).isoformat()

        for ev in events:
            try:
                self._extract_price(ev, now_iso)
            except Exception as _px_err:
                logger.debug(f"WS _extract_price: {type(_px_err).__name__}: {_px_err}")
            # Phase 39 (P1.1): also check for trade events
            try:
                self._extract_trade(ev)
            except Exception as _tr_err:
                logger.debug(f"WS _extract_trade: {type(_tr_err).__name__}: {_tr_err}")

    def _extract_trade(self, ev):
        """Phase 39 (P1.1): Extract real trade fills from `last_trade_price`
        events. Polymarket WS sends:
            {"event_type":"last_trade_price","asset_id":"...","price":"0.52",
             "size":"123.4","side":"BUY","fee_rate_bps":"0","timestamp":"..."}
        We forward to the recorder via _on_trade_callback so the engine can
        track maker queue progress and we get a real trade tape for replay.
        """
        if not isinstance(ev, dict):
            return
        et = (ev.get("event_type") or ev.get("type") or "").lower()
        if et != "last_trade_price":
            return
        asset_id = str(ev.get("asset_id", "") or ev.get("market", "") or "")
        if not asset_id:
            return
        price = self._f(ev.get("price"))
        size = self._f(ev.get("size"))
        if price is None or size is None or size <= 0:
            return
        side = str(ev.get("side", "") or "").upper() or "?"
        ts = ev.get("timestamp") or ev.get("ts")
        try:
            ts_ms = int(float(ts)) if ts else int(time.time() * 1000)
        except Exception:
            ts_ms = int(time.time() * 1000)
        # Polymarket sometimes sends ts in seconds; normalize to ms
        if ts_ms < 10_000_000_000:
            ts_ms *= 1000
        self._trade_count += 1
        if self._on_trade_callback:
            try:
                self._on_trade_callback(asset_id, price, size, side, ts_ms)
            except Exception as _cb_err:
                logger.warning(f"⚠️ WS trade callback failed: {type(_cb_err).__name__}: {_cb_err}")

    def _extract_price(self, ev, now_iso: str):
        """Extract price from any known Polymarket WS format."""
        if not isinstance(ev, dict):
            return  # Safety: skip non-dict (list slipped through)
        asset_id = str(ev.get("asset_id", "") or ev.get("market", "") or "")
        if not asset_id:
            return

        price = None

        # Format 1: direct price field
        p = self._f(ev.get("price"))
        if p and 0.005 < p < 0.995:
            self.live_prices[asset_id] = {"price": p, "ts": now_iso}
            price = p

        # Format 2: changes array [{price, side}]
        if price is None:
            changes = ev.get("changes")
            if isinstance(changes, list):
                for ch in changes:
                    if isinstance(ch, dict):
                        p = self._f(ch.get("price"))
                        if p and 0.005 < p < 0.995:
                            self.live_prices[asset_id] = {"price": p, "ts": now_iso}
                            price = p

        # Format 3: outcome_prices
        if price is None:
            op = ev.get("outcome_prices")
            if isinstance(op, list) and op:
                p = self._f(op[0])
                if p:
                    self.live_prices[asset_id] = {"price": p, "ts": now_iso}
                    price = p

        # Phase 19: Fire real-time callback for OddsFeed bridge
        if price and self._on_price_callback:
            try:
                self._on_price_callback(asset_id, price)
            except Exception:
                pass

    def get_live_price(self, token_id: str) -> Optional[float]:
        """Get cached price. Returns None if stale (>WS_STALE_THRESHOLD, default 60s).
        Phase 62: relaxed from 20→60s. 20s was too aggressive for low-liquidity
        5-min crypto markets — tokens with sparse trading went stale every cycle,
        causing ALL pending fills to skip. 60s still prevents truly stale data
        while allowing fills to proceed.

        Epic 5 T5.4 (2026-04-21): also invalidate entries cached before the
        current WS connection's _connected_since marker. Without this gate a
        pre-reconnect tick could be served as "fresh" for up to WS_STALE_THRESHOLD
        after the reconnect completed, potentially triggering trades on dead
        prices that missed the gap's real movement.

        Epic 10 T10.5 (2026-04-22): malformed cache entries (missing 'ts' or
        un-parseable ISO string) now return None instead of falling through to
        serve an unknown-freshness price. Previous behavior served
        `data.get("price")` on Exception which violated the "fresh > stale"
        doctrine. All 3 cache-write sites (_handle_message L333, L344, L353)
        ALWAYS set 'ts' alongside 'price' — missing 'ts' can only happen via
        corruption / future refactor bug / hand-constructed test fixture. In
        those cases None (no-trade) is the safe fail mode."""
        data = self.live_prices.get(token_id)
        if not data:
            return None
        try:
            entry_dt = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
            # Epic 5 T5.4 Fix A: reconnect invalidation
            if self._connected_since and entry_dt.timestamp() < self._connected_since:
                return None
            age = (datetime.now(timezone.utc) - entry_dt).total_seconds()
            # T11.2 [C]: unify with core/engine._is_ws_fresh env name.
            # Canonical: WS_STALE_THRESHOLD (whitelisted, /envt-tunable).
            # Fallback: WS_STALE_SEC kept for legacy .env backward-compat.
            _stale = float(
                os.getenv("WS_STALE_THRESHOLD", os.getenv("WS_STALE_SEC", "60"))
            )  # Phase 62: 20→60s
            if age > _stale:
                return None
        except (KeyError, ValueError, TypeError, AttributeError) as e:
            # Epic 10 T10.5: malformed cache entry — freshness unknown.
            # KeyError: 'ts' missing. ValueError: bad ISO string.
            # TypeError: 'ts' not str (e.g. int). AttributeError: .replace
            # missing on non-string. In all cases, None = safe fail
            # (fresh > stale doctrine).
            logger.debug(
                "get_live_price malformed entry for %s: %s (%s); "
                "returning None", token_id, type(e).__name__, e)
            return None
        return data.get("price")

    def cleanup_stale_prices(self, max_age_seconds: int = 3600):
        """F-06: Remove stale token prices to prevent memory leak.
        Called periodically by engine or scanner."""
        now = datetime.now(timezone.utc)
        stale = []
        for tid, data in self.live_prices.items():
            try:
                ts = datetime.fromisoformat(data["ts"].replace("Z", "+00:00"))
                if (now - ts).total_seconds() > max_age_seconds:
                    stale.append(tid)
            except Exception:
                stale.append(tid)
        for tid in stale:
            del self.live_prices[tid]
        if stale:
            logger.debug(f"WS cleanup: removed {len(stale)} stale prices")

    def get_status(self) -> dict:
        return {
            "connected": self._connected,
            "subscribed": len(self._subscribed),
            "messages": self._msg_count,
            "errors": self._errors,
            "reconnects": self._reconnects,
            "cached_prices": len(self.live_prices),
            "last_msg_age": round(time.time() - self._last_msg_ts, 1) if self._last_msg_ts else None,
            "trade_events": self._trade_count,  # Phase 39 (P1.1)
            "tick_gaps": self._tick_gaps,  # Phase 50 P1-10
            # Epic 5 T5.6 Fix C: cap overflow telemetry
            "cap_hits": self._cap_hit_count,
            "cap_skipped": self._cap_skipped_total,
            "last_cap_hit_age": (
                round(time.time() - self._last_cap_hit_ts, 1)
                if self._last_cap_hit_ts else None),
        }

    async def _send(self, msg):
        if self._ws and self._connected:
            try:
                await self._ws.send(msg)
            except Exception:
                pass

    @staticmethod
    def _f(val) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
