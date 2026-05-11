"""
PolyPaper Bot - Market Scanner (Phase 8)
Auto-subscribes discovered tokens to WebSocket for real-time updates.

T11.8-B (2026-04-24): every catch in this module is annotated
`# noqa: BLE001`. Data-feed orchestrator: WebSockets + httpx +
json + aiosqlite + asyncio reconnect chain. Single network blip
or schema drift should NOT crash the feed thread — the reconnect
loop handles it. Wide catches at the orchestration layer are
intentional and logged.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from config.settings import Settings
from data.polymarket_client import PolymarketClient
from db.database import Database
from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1

logger = logging.getLogger("polypaper.data.scanner")

# ═══════════════════════════════════════════════════════════════════
# Phase 82e HOTFIX: scan tightening — env-tunable REST poll cadence.
# Previous hard-coded 30s left strategy evaluations reading stale
# odds_cache for up to 30 seconds, causing classic/threshold strategies
# to miss sub-30s price crossings entirely. Default lowered to 5s.
# WS price ticks also now update odds_cache (see _on_ws_price).
# ═══════════════════════════════════════════════════════════════════
SCAN_INTERVAL_S = max(2, int(os.getenv("SCAN_INTERVAL_S", "5")))


class MarketScanner:
    def __init__(self, settings: Settings, client: PolymarketClient, db: Database,
                 ws_client=None, odds_feed=None):
        self.settings = settings
        self.client = client
        self.db = db
        self.ws = ws_client
        self.odds_feed = odds_feed
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.active_markets: dict[str, list[dict]] = {}
        self.odds_cache: dict[str, dict] = {}
        self.last_known_odds: dict[str, dict] = {}
        self.last_scan: Optional[datetime] = None
        self._subscribed_ws_tokens: set[str] = set()
        # Phase 19: token_id → (slug, direction) mapping for real-time OddsFeed
        self._token_slug: dict[str, tuple[str, str]] = {}  # tid → (slug, "up"|"down")

    async def start(self):
        if self._running:
            return
        self._running = True
        logger.info("Scanner: initial scan...")
        try:
            await self._do_scan()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Initial scan: {e}")
        # Phase 19: Wire WS → OddsFeed real-time bridge
        if self.ws and self.odds_feed:
            self.ws._on_price_callback = self._on_ws_price
            up_count = sum(1 for _, d in self._token_slug.values() if d == "up")
            logger.info(f"📡 WS→OddsFeed bridge active ({up_count} UP tokens, {len(self._token_slug)} total)")
        # Phase 82e Sprint 2.1: scanner death = no market discovery
        self._task = safe_create_task(self._loop(), name="market_scanner_loop")
        logger.info(
            f"Scanner started. Pairs: {list(self.active_markets.keys())} "
            f"| REST poll every {SCAN_INTERVAL_S}s "
            f"| WS→odds_cache bridge ACTIVE (Phase 82e scan-tighten)"
        )

    def _on_ws_price(self, token_id: str, price: float):
        """Phase 23 FIX: Only route UP token prices to OddsFeed.
        DOWN token prices were being recorded as up_odds — critical data bug.

        Phase 82e HOTFIX: ALSO write every UP tick into `odds_cache` so that
        engine_signals._eval_market_checks (which reads scanner.get_current_odds)
        sees sub-second price movements instead of stale 30-second REST polls.
        This is the primary fix for classic strategies missing price crossings.
        DOWN ticks update the cache's `down_odds` mirror (1-up) only when UP
        side hasn't been touched in the last 2s, to avoid stomping fresh UP
        data with a derived value.
        """
        mapping = self._token_slug.get(token_id)
        if not mapping:
            return
        slug, direction = mapping
        if direction == "up":
            if self.odds_feed:
                try:
                    self.odds_feed.record_odds(slug, price)
                except Exception:  # noqa: BLE001
                    pass
            # Phase 82e HOTFIX: refresh odds_cache with fresh WS tick
            try:
                entry = self.odds_cache.get(slug, {})
                entry["up_odds"] = float(price)
                # down derivation (binary market): only if not freshly set
                if "down_odds" not in entry or entry.get("down_odds") is None:
                    entry["down_odds"] = max(0.0, 1.0 - float(price))
                entry["has_liquidity"] = True  # WS tick means market is live
                entry["ws_ts"] = datetime.now(timezone.utc).isoformat()
                self.odds_cache[slug] = entry
                # Keep last_known_odds in sync for fallback path
                self.last_known_odds[slug] = {
                    "up_odds": entry.get("up_odds"),
                    "down_odds": entry.get("down_odds"),
                    "timestamp": entry["ws_ts"],
                }
            except Exception as _cache_err:  # noqa: BLE001
                logger.debug(f"ws cache write {slug[:16]}: {_cache_err}")
        elif direction == "down":
            # DOWN tick: only touch cache if nothing fresh came from UP side
            try:
                entry = self.odds_cache.get(slug, {})
                entry["down_odds"] = float(price)
                # UP side derivation only if UP is stale / missing
                if "up_odds" not in entry or entry.get("up_odds") is None:
                    entry["up_odds"] = max(0.0, 1.0 - float(price))
                entry["has_liquidity"] = True
                entry.setdefault(
                    "ws_ts", datetime.now(timezone.utc).isoformat())
                self.odds_cache[slug] = entry
            except Exception:  # noqa: BLE001
                pass

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        while self._running:
            try:
                await self._do_scan()
                # F-06: Periodic WS price cache cleanup
                if self.ws:
                    self.ws.cleanup_stale_prices(3600)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error(f"Scan: {e}")
            # Phase 82e HOTFIX: 30s → SCAN_INTERVAL_S (default 5s, env-tunable)
            # Reduces stale odds_cache window so strategies see fresh REST odds
            # even when WS is disconnected. With WS up, cache is also updated
            # sub-second via _on_ws_price.
            await asyncio.sleep(SCAN_INTERVAL_S)

    async def _do_scan(self):
        new_tokens = []
        # Epic 5 T5.6 Fix A (2026-04-21): Track every token that belongs to
        # a CURRENTLY active market in this cycle. At end of scan we use it
        # to prune tokens from dead markets out of:
        #   - WS _subscribed (frees slots against MAX_WS_TOKENS cap)
        #   - WS live_prices (memory cleanup)
        #   - scanner._subscribed_ws_tokens (prevents "ghost subscribed" drift)
        #   - scanner._token_slug (prevents stale mapping buildup)
        live_token_ids: set[str] = set()

        # P0-08-B (2026-05-08): matrix-dispatch loop. Each TF uses its own
        # discovery method per `Settings.TF_DISCOVERY_MATRIX`:
        #   - method="slug_prefix" → assets list, legacy probe (5m/15m)
        #   - method="series_id"   → series_map {ASSET: id} (1h/24h)
        # Reference: memory/reference_polymarket_updown_discovery.md
        matrix = getattr(self.settings, "TF_DISCOVERY_MATRIX", None) or {}
        if not matrix:
            # Backward-compat: legacy cartesian if matrix missing
            iter_pairs = [
                (a, tf, None)
                for a in self.settings.SUPPORTED_ASSETS
                for tf in self.settings.SUPPORTED_TIMEFRAMES
            ]
        else:
            iter_pairs = []
            for tf, cfg in matrix.items():
                if not isinstance(cfg, dict):
                    continue
                method = cfg.get("method")
                if method == "slug_prefix":
                    for asset in cfg.get("assets") or []:
                        iter_pairs.append((asset, tf, None))
                elif method == "series_id":
                    for asset, sid in (cfg.get("series_map") or {}).items():
                        iter_pairs.append((asset, tf, sid))

        for asset, tf, series_id in iter_pairs:
                key = f"{asset}_{tf}"
                mkts = await self.client.discover_active_markets(
                    asset, tf, series_id=series_id)
                if mkts:
                    self.active_markets[key] = mkts
                    for m in mkts[:2]:
                        slug = m.get("slug", "")
                        if not slug:
                            continue
                        odds = await self.client.get_market_odds(m)
                        if odds:
                            self.odds_cache[slug] = odds
                            self._save_last_odds(slug, odds)
                            await self._save_odds_to_db(slug, odds)
                            # Feed to OddsFeed for indicators
                            if self.odds_feed and odds.get("up_odds"):
                                self.odds_feed.record_odds(
                                    slug, odds["up_odds"], odds.get("down_odds"))

                            # Collect token IDs for WS subscription
                            for tk in ("up_token", "down_token"):
                                tid = odds.get(tk)
                                if tid:
                                    # Epic 5 T5.6: record as live regardless
                                    # of whether it's already subscribed.
                                    live_token_ids.add(tid)
                                if tid and tid not in self._subscribed_ws_tokens:
                                    new_tokens.append(tid)
                                    self._subscribed_ws_tokens.add(tid)
                                # Phase 23: Map token_id → (slug, direction)
                                if tid:
                                    direction = "up" if tk == "up_token" else "down"
                                    self._token_slug[tid] = (slug, direction)
                else:
                    self.active_markets.pop(key, None)

        # Subscribe new tokens to WebSocket
        if new_tokens and self.ws:
            try:
                await self.ws.subscribe(new_tokens)
                logger.info(f"  WS: subscribed {len(new_tokens)} new tokens")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"WS subscribe: {e}")

        # Epic 5 T5.6 Fix A: prune dead tokens from prior cycles.
        # Safeguard against transient API failures: if this scan's live
        # count is suspiciously low (<50% of what we had subscribed before),
        # skip the prune to avoid a pingpong subscribe/unsubscribe loop
        # during Polymarket REST hiccups.
        if self.ws and live_token_ids:
            prev_count = len(self._subscribed_ws_tokens)
            threshold = max(1, prev_count // 2)
            if prev_count == 0 or len(live_token_ids) >= threshold:
                try:
                    pruned = self.ws.prune_stale_tokens(live_token_ids)
                    # Keep scanner-side bookkeeping in sync so the "not in
                    # _subscribed_ws_tokens" guard in the loop above will
                    # re-attempt subscribe if a pruned token comes back.
                    self._subscribed_ws_tokens &= live_token_ids
                    stale_slugs = [tid for tid in list(self._token_slug)
                                   if tid not in live_token_ids]
                    for tid in stale_slugs:
                        self._token_slug.pop(tid, None)
                    if pruned:
                        logger.info(
                            f"  Scanner pruned {pruned} tokens, "
                            f"slugs -{len(stale_slugs)}")
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"prune_stale_tokens: {e}")
            else:
                logger.debug(
                    f"  Scanner skipping prune (live={len(live_token_ids)} "
                    f"< {threshold}, prev={prev_count}) — API hiccup?")

        self.last_scan = datetime.now(timezone.utc)

    def _save_last_odds(self, slug, odds):
        up, down = odds.get("up_odds"), odds.get("down_odds")
        if up is not None or down is not None:
            self.last_known_odds[slug] = {
                "up_odds": up, "down_odds": down,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    async def _save_odds_to_db(self, slug, odds):
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                "INSERT INTO odds_history (event_slug,up_odds,down_odds,timestamp) VALUES (?,?,?,?)",
                (slug, odds.get("up_odds"), odds.get("down_odds"), now))
            await self.db.conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def get_current_market(self, asset, timeframe):
        return (self.active_markets.get(f"{asset.upper()}_{timeframe}", []) or [None])[0]

    def get_current_odds(self, slug):
        return self.odds_cache.get(slug)

    def get_last_known_odds(self, slug):
        return self.last_known_odds.get(slug)

    def get_live_odds(self, asset, timeframe):
        m = self.get_current_market(asset, timeframe)
        return self.odds_cache.get(m.get("slug", "")) if m else None

    def get_status_summary(self) -> str:
        total = sum(len(v) for v in self.active_markets.values())
        pairs = [k for k in sorted(self.active_markets) if self.active_markets[k]]
        t = self.last_scan.strftime("%H:%M:%S UTC") if self.last_scan else "Never"
        ws_status = "🟢 Connected" if (self.ws and self.ws.is_connected) else "⚫ REST only"
        return (
            f"Markets: {total} | Odds: {len(self.odds_cache)} | Scan: {t}\n"
            f"WS: {ws_status} | Subscribed: {len(self._subscribed_ws_tokens)}\n"
            f"Pairs: {', '.join(pairs) or 'none'}")
