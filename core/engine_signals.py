"""
Phase 65 — Signals/evaluate mixin for TradingEngine (REFACTORED)
================================================================
Originally 1034-line monolith `_evaluate`. Now split into logical helpers:

  _evaluate()              — orchestrator (calls helpers in order)
  _eval_market_checks()    — market halt, dedup, timing, whipsaw
  _eval_signal()           — plugin/fusion signal + EMA + divergence
  _eval_signal_boosters()  — micro, funding, Becker, cascade, lag arb, whale
  _eval_gates()            — edge gate, slippage, risk check
  _eval_sizing()           — Kelly, conviction, event calendar, canary
  _eval_place_order()      — maker/taker routing, fee, lock, VirtualOrder append

All helpers return a dict (context bag) or None to abort. The orchestrator
threads context through, so each helper has all state from prior steps.
"""
from __future__ import annotations

import asyncio
import math
import os
import time
from datetime import datetime, timezone

from core.engine_support import (
    INTERVAL_SECS,
    MAX_MBE,
    WIDE_SPREAD,
    VirtualOrder,
    _slug_start,
    _stagger,
)
from core.fees_v2 import polymarket_fee_percent_v2  # Sprint 1 S1-02: fee-aware gate
from core.indicators import ema_direction_filter
from core.kelly import get_strategy_kelly
from core.strategy_plugins import MarketSnapshot
from core.trade_journal import log_rejection
from data.polymarket_client import safe_float
from db.models import Direction

import logging
logger = logging.getLogger("polypaper.core.engine")


class EngineSignalsMixin:
    """`_evaluate` hot-path methods for TradingEngine."""

    MIN_ORDER_SHARES = float(os.getenv("MIN_ORDER_SHARES", "1.0"))

    # ── S3-02: Zone-Filtered Trading ──
    ALLOWED_ZONES_STR = os.getenv("ALLOWED_ZONES", "")  # e.g., "0-35,50-55" (cents)

    # ── Phase 82c Task #19: Per-strategy-type blocked zones ──
    # Diagnosed loss pattern: AI_F (fusion) strategies hit 28% WR in 30-40c
    # entries (21L/8W, net -$6). Block that bucket for fusion-type strategies
    # until lifecycle learner catches up. ENV format same as ALLOWED_ZONES.
    # Default "30-40" blocks the offending bucket; set empty to disable.
    FUSION_BLOCKED_ZONES_STR = os.getenv("FUSION_BLOCKED_ZONES", "30-40")

    # ── Phase 79 S4-04: Brier Calibration Alarm ──
    BRIER_GAP_MAX = float(os.getenv("BRIER_GAP_MAX", "0.30"))
    _brier_cache = None  # {bin: gap_value} — loaded once at startup
    _brier_cache_time = None  # timestamp of last refresh

    @staticmethod
    def _parse_zones(zones_str: str) -> list[tuple[float, float]]:
        """Parse '0-35,50-55' into [(0.0, 0.35), (0.50, 0.55)].
        Returns empty list if zones_str is empty (allow all).
        """
        if not zones_str.strip():
            return []
        zones = []
        try:
            for part in zones_str.split(","):
                lo, hi = part.strip().split("-")
                lo_cents = float(lo)
                hi_cents = float(hi)
                zones.append((lo_cents / 100.0, hi_cents / 100.0))
        except (ValueError, AttributeError):
            logger.warning(f"Invalid ALLOWED_ZONES format: {zones_str}. Using no filter.")
            return []
        return zones

    _ALLOWED_ZONES = _parse_zones(ALLOWED_ZONES_STR)
    _FUSION_BLOCKED_ZONES = _parse_zones(FUSION_BLOCKED_ZONES_STR)

    @staticmethod
    def _in_allowed_zone(price: float, zones: list[tuple[float, float]]) -> bool:
        """Check if price falls in any allowed zone.
        If zones is empty, return True (no filter).
        """
        if not zones:
            return True
        return any(lo <= price <= hi for lo, hi in zones)

    # ═══════════════════════════════════════════════════════════════════
    # Sprint 5 HOTFIX v3 (2026-04-19): Classic FREE-MODE
    # ═══════════════════════════════════════════════════════════════════
    # Classic strategies are USER-directed. The user explicitly chose the
    # trigger (e.g. 0.85 DOWN on BTC 5m) and all engine-level filters (zone,
    # becker, brier, conviction, kelly, EV, regime, TS, capital, unsellable,
    # edge, fee-gate, oracle-parity, event-waves, whipsaw, too-late) would
    # block exactly the scenarios that classic targets.
    #
    # BYPASS BEHAVIOR (default: CLASSIC_BYPASS_ALL_GATES=true):
    #   - For stype=="classic", skip all 14+ strategic gates
    #   - HARD SAFETY is always enforced: MARKET_HALT, NO_LIQ, BAD_PRICE,
    #     RISK, MIN_SIZE, MIN_SHARES, FEE_TAIL, STP, TOKEN_CAP, PLUGIN_ERROR
    #   - STRATEGY-LEVEL fields still apply when user sets them: EMA_BLOCK,
    #     LOW_VOL, PRICE_DIFF, TOO_EARLY, SLIPPAGE, MAX_EXEC/LOSS — defaults
    #     are off anyway
    #
    # OPT-OUT: set CLASSIC_BYPASS_ALL_GATES=false → classic behaves like
    # any other strategy type.
    @staticmethod
    def _classic_free_mode(ctx_or_stype) -> bool:
        """Return True if classic stype AND env opt-in not disabled.

        Accepts either ctx dict (reads ctx["stype"]) OR bare stype string.
        """
        if isinstance(ctx_or_stype, dict):
            _stype = ctx_or_stype.get("stype", "") or ""
        else:
            _stype = str(ctx_or_stype or "")
        if _stype != "classic":
            return False
        return os.getenv("CLASSIC_BYPASS_ALL_GATES", "true").lower() != "false"

    # ───────────────────────────────────────────────────────────────────
    # Phase 79 S4-04: Brier Calibration Alarm Helpers
    # ───────────────────────────────────────────────────────────────────
    async def _load_brier_calibration_cache(self):
        """Load Brier calibration data into {bin: gap_value} cache.
        Cached for 6 hours to avoid hot-path DB queries.
        """
        import time as _time_module

        # Skip if cache is fresh (< 6 hours old)
        now = _time_module.time()
        if self._brier_cache is not None and self._brier_cache_time is not None:
            if now - self._brier_cache_time < 21600:  # 6 hours
                return

        try:
            from utils.brier_tracker import BrierTracker
            tracker = BrierTracker(self.db)
            report = await tracker.get_report(source=None, hours=168)

            # Extract calibration gaps per bin
            cache = {}
            if "calibration_curve" in report:
                for item in report["calibration_curve"]:
                    if item["count"] > 0:
                        bin_label = item["bin"]  # e.g., "0.5-0.6"
                        gap = item["gap"]
                        cache[bin_label] = gap

            self._brier_cache = cache
            self._brier_cache_time = now
            logger.debug(f"Brier cache refreshed: {len(cache)} bins")
        except Exception as _be:
            logger.debug(f"Brier cache load failed (non-critical): {_be}")
            self._brier_cache = {}

    def _get_brier_bin(self, price: float) -> str:
        """Map price (0.0-1.0) to calibration bin label.
        E.g., 0.62 -> "0.6-0.7"
        """
        price = max(0.0, min(1.0, price))
        bin_idx = int(price * 10)
        if bin_idx >= 10:
            bin_idx = 9
        lo = bin_idx / 10.0
        hi = (bin_idx + 1) / 10.0
        return f"{lo:.1f}-{hi:.1f}"

    async def _check_brier_alarm(self, price: float) -> tuple[bool, str]:
        """Check if calibration gap for this price bin exceeds BRIER_GAP_MAX.
        Returns (should_skip, reason).
        """
        if self._brier_cache is None:
            await self._load_brier_calibration_cache()

        if not self._brier_cache:
            # No cached data, proceed normally
            return False, ""

        bin_label = self._get_brier_bin(price)
        gap = self._brier_cache.get(bin_label, 0.0)

        if gap > self.BRIER_GAP_MAX:
            return True, f"BRIER_ALARM (gap={gap:.3f} > {self.BRIER_GAP_MAX})"

        return False, ""

    # ═══════════════════════════════════════════════════════════════════
    #  Phase 82: Cycle-level Orderbook Cache
    # ═══════════════════════════════════════════════════════════════════
    async def _get_ob_cached(self, token_id: str):
        """Fetch Polymarket /book with a TTL cache (default 2.0s).

        Prevents redundant API calls when multiple strategies evaluate
        the same market in the same cycle. Cache lives on the engine
        instance (self._ob_cache) and is populated lazily.

        Returns the orderbook dict ({"asks": [[price, size], ...],
        "bids": [[price, size], ...]}) or None on fetch failure.
        """
        if not token_id:
            return None
        now = time.time()
        cached = self._ob_cache.get(token_id)
        if cached and (now - cached[0]) < self._OB_CACHE_TTL:
            return cached[1]
        try:
            data = await self.client.get_orderbook(token_id)
        except Exception as _oe:
            logger.debug(f"_get_ob_cached fetch failed for {token_id[:16]}…: {_oe}")
            return None
        if data:
            self._ob_cache[token_id] = (now, data)
        return data

    # ═══════════════════════════════════════════════════════════════════
    #  ORCHESTRATOR
    # ═══════════════════════════════════════════════════════════════════
    async def _evaluate(self, s, verbose=False):
        """Per-strategy evaluation cycle. Calls helpers in order."""
        # ── Phase 74b: Load per-strategy lifecycle params ──
        try:
            _lc_params = await self.lifecycle.get_params(s.id)
        except Exception:
            from core.strategy_lifecycle import StrategyParams
            _lc_params = StrategyParams()  # Global defaults as fallback

        # ── Step 1: Market & dedup checks ──
        ctx = await self._eval_market_checks(s, verbose)
        if ctx is None:
            return
        ctx["lifecycle"] = _lc_params

        # ── Step 2: Signal evaluation (fusion / plugin) ──
        ctx = await self._eval_signal(s, ctx, verbose)
        if ctx is None:
            return

        # ── Step 3: Signal boosters (micro, Becker, cascade, lag, whale) ──
        ctx = await self._eval_signal_boosters(s, ctx, verbose)
        if ctx is None:
            return

        # ── Step 4: Gates (edge, slippage, risk) ──
        ctx = await self._eval_gates(s, ctx, verbose)
        if ctx is None:
            return

        # ── Step 5: Position sizing (Kelly, conviction, event, canary) ──
        ctx = await self._eval_sizing(s, ctx, verbose)
        if ctx is None:
            return

        # ── Step 6: Order placement (maker/taker, fee, append) ──
        await self._eval_place_order(s, ctx, verbose)

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 1: MARKET CHECKS
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_market_checks(self, s, verbose=False):
        """Market halt, dedup, timing, price validation.
        Returns context dict or None to abort."""
        sid = s.id[:8]
        asset, tf = s.asset.value, s.timeframe.value
        market = self.scanner.get_current_market(asset, tf)
        if not market:
            return None
        slug = market.get("slug", "")
        if not slug:
            return None

        # Market halt detection
        if (not market.get("active", True)
                or market.get("closed", False)
                or market.get("archived", False)):
            self.skips.record("MARKET_HALT")
            if verbose:
                logger.info(f"  [{sid}] ❌ MARKET_HALT: market not tradeable")
            return None

        # Record Binance open price ONCE
        if self.external_feed and self.external_feed.is_available:
            asset_name = s.asset.value if hasattr(s.asset, 'value') else str(s.asset)
            if slug not in self._market_open_recorded:
                self.external_feed.record_market_open(asset_name, slug)
                self._market_open_recorded.add(slug)

        # Dedup
        if f"{s.id}:{slug}" in self._open_positions:
            return None
        if f"{s.id}:{slug}" in self._settled_slugs:
            return None
        cd = self._cooldowns.get(f"{s.id}:{asset}_{tf}")
        if cd and datetime.now(timezone.utc) < cd:
            return None

        # Pending lock check
        async with self._trade_lock:
            if any(o.strategy_id == s.id and o.slug == slug for o in self._pending):
                return None

        # Odds
        cached = self.scanner.get_current_odds(slug)
        if not cached:
            return None
        up = safe_float(cached.get("up_odds"))
        down = safe_float(cached.get("down_odds"))
        if up is None and down is None:
            return None
        if not cached.get("has_liquidity", False):
            self.skips.record("NO_LIQ")
            return None

        # Lag arb recorder
        if getattr(self, "_lag_arb", None) is not None and up is not None:
            try:
                self._lag_arb.record(asset.lower(), up)
            except Exception:
                pass

        now = datetime.now(timezone.utc)

        # Sprint 5 HOTFIX v5: compute classic-free early so both TOO_EARLY
        # and TOO_LATE can opt out for user-directed triggers.
        _stype_early = getattr(s, 'strategy_type', '') or ''
        _classic_free = self._classic_free_mode(_stype_early)

        # BET_FROM
        start_time = _slug_start(slug)
        if start_time and s.minutes_after_start and not _classic_free:
            elapsed = (now - start_time).total_seconds() / 60
            if elapsed < s.minutes_after_start:
                self.skips.record("TOO_EARLY")
                return None

        # BET_TO
        # Sprint 5 HOTFIX v3: classic FREE-MODE bypasses TOO_LATE since
        # classic strategies often target literally the final seconds.
        mbe = min(s.minutes_before_end or 0.5, MAX_MBE.get(tf, 1.0))
        end_str = market.get("endDate")
        minutes_remaining = None
        total_minutes = INTERVAL_SECS.get(tf, 300) / 60
        if end_str:
            try:
                end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
                minutes_remaining = (end_dt - now).total_seconds() / 60
                if not _classic_free and (minutes_remaining < mbe or minutes_remaining < 0):
                    self.skips.record("TOO_LATE")
                    return None
            except Exception:
                pass

        threshold = safe_float(s.odds_threshold)
        if not threshold:
            return None

        # WS-Aware Threshold — Phase 82e Sprint 5 (FINAL): ENV-tunable
        # When the WS feed is stale (>N seconds silent), force threshold
        # up so we only trade on very-high-confidence REST snapshots.
        # Default 0.70 is the old hardcoded value.
        ws_fresh = self._is_ws_fresh()
        _ws_stale_thr = float(os.getenv("WS_STALE_MIN_THRESHOLD", "0.70"))
        if not ws_fresh and threshold < _ws_stale_thr:
            threshold = _ws_stale_thr

        # Anti-Whipsaw Guard — Phase 82e Sprint 5 (FINAL): ENV-tunable band
        # If the last-trade slug matches AND odds are INSIDE the coin-flip
        # band (WHIPSAW_BAND_LO..WHIPSAW_BAND_HI), reset the whipsaw guard;
        # otherwise skip. Defaults 0.40-0.60 preserve legacy behavior.
        _wb_lo = float(os.getenv("WHIPSAW_BAND_LO", "0.40"))
        _wb_hi = float(os.getenv("WHIPSAW_BAND_HI", "0.60"))
        last_slug = self._last_trade_slug.get(s.id)
        if last_slug == slug and not _classic_free:
            if up and _wb_lo < up < _wb_hi:
                self._last_trade_slug.pop(s.id, None)
            else:
                self.skips.record("WHIPSAW")
                return None

        # PRICE_DIFFERENCE
        if s.price_difference and s.price_difference > 0:
            odds_series = self.odds_feed.get_odds_series(slug, "up")
            if len(odds_series) >= 2:
                opening_odds = odds_series[0]
                current_odds = odds_series[-1]
                if opening_odds > 0:
                    pct_move = abs(current_odds - opening_odds) / opening_odds * 100
                    if pct_move < s.price_difference:
                        self.skips.record("PRICE_DIFF")
                        return None

        return {
            "sid": sid, "asset": asset, "tf": tf,
            "market": market, "slug": slug, "cached": cached,
            "up": up, "down": down,
            "threshold": threshold, "ws_fresh": ws_fresh,
            "minutes_remaining": minutes_remaining,
            "total_minutes": total_minutes,
        }

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 2: SIGNAL EVALUATION
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_signal(self, s, ctx, verbose=False):
        """Plugin or fusion signal + EMA + Binance divergence.
        Returns updated ctx or None to abort."""
        sid = ctx["sid"]
        slug, cached = ctx["slug"], ctx["cached"]
        up, down = ctx["up"], ctx["down"]
        threshold = ctx["threshold"]
        minutes_remaining = ctx["minutes_remaining"]
        total_minutes = ctx["total_minutes"]

        odds_series = self.odds_feed.get_odds_series(slug, "up")
        stype = getattr(s, 'strategy_type', 'fusion') or 'fusion'
        psig = None

        # ── Phase 82: Unified orderbook fetch (cached, shared across paths) ──
        # UP token OB is fetched for both fusion and plugin paths; DOWN token
        # OB only for plugin path (used by OrderbookImbalance strategy for
        # full up↔down depth comparison). `_get_ob_cached` dedupes redundant
        # /book calls within the same cycle (TTL default 2.0s).
        up_tok = cached.get("up_token")
        down_tok = cached.get("down_token")
        ob_data = await self._get_ob_cached(up_tok) if up_tok else None
        ob_down = None  # populated only for plugin path below

        if stype != "fusion" and self.plugins.get(stype):
            ob_down = await self._get_ob_cached(down_tok) if down_tok else None

            # ── Phase 82: Rich plugin metadata bundle ─────────────────
            # Each section is guarded by its own try/except: a single
            # data-source failure cannot nuke the whole pipeline — the
            # strategy simply receives as much context as is available.
            plugin_meta: dict = {}

            # 1. TIME
            try:
                _now_utc = datetime.now(timezone.utc)
                plugin_meta["hour_utc"] = _now_utc.hour
                plugin_meta["minute_utc"] = _now_utc.minute
                if total_minutes and total_minutes > 0:
                    _elapsed = total_minutes - (minutes_remaining or 0)
                    plugin_meta["time_pct"] = round(
                        max(0.0, min(1.0, _elapsed / total_minutes)), 4)
            except Exception:
                pass

            # 2. POLYMARKET ORDERBOOK — UP token
            real_best_bid = 0.48
            real_best_ask = 0.50
            try:
                if ob_data:
                    _bids = ob_data.get("bids") or []
                    _asks = ob_data.get("asks") or []
                    if _bids:
                        real_best_bid = float(_bids[0][0])
                        plugin_meta["up_best_bid"] = real_best_bid
                    if _asks:
                        real_best_ask = float(_asks[0][0])
                        plugin_meta["up_best_ask"] = real_best_ask
                    if _bids and _asks:
                        plugin_meta["up_spread"] = round(real_best_ask - real_best_bid, 4)
                    plugin_meta["up_bid_depth"] = sum(float(sz) for _px, sz in _bids[:5])
                    plugin_meta["up_ask_depth"] = sum(float(sz) for _px, sz in _asks[:5])
            except Exception as _obe:
                logger.debug(f"plugin_meta up_ob err: {_obe}")

            # 3. POLYMARKET ORDERBOOK — DOWN token
            try:
                if ob_down:
                    _dbids = ob_down.get("bids") or []
                    _dasks = ob_down.get("asks") or []
                    if _dbids:
                        plugin_meta["down_best_bid"] = float(_dbids[0][0])
                    if _dasks:
                        plugin_meta["down_best_ask"] = float(_dasks[0][0])
                    if _dbids and _dasks:
                        plugin_meta["down_spread"] = round(
                            float(_dasks[0][0]) - float(_dbids[0][0]), 4)
                    plugin_meta["down_bid_depth"] = sum(float(sz) for _px, sz in _dbids[:5])
                    plugin_meta["down_ask_depth"] = sum(float(sz) for _px, sz in _dasks[:5])
            except Exception as _obe:
                logger.debug(f"plugin_meta down_ob err: {_obe}")

            # 4. SPOT + MOMENTUM (external_feed)
            _asset_name = s.asset.value if hasattr(s.asset, "value") else str(s.asset)
            _asset_up = _asset_name.upper()
            try:
                if self.external_feed and self.external_feed.is_available:
                    _spot = self.external_feed.get_price(_asset_up)
                    if _spot:
                        plugin_meta["asset_spot_price"] = _spot
                    _mom = self.external_feed.get_spot_momentum(_asset_up, 60)
                    if _mom:
                        plugin_meta["asset_price_change"] = _mom.get("change_pct")
                        plugin_meta["spot_momentum_strength"] = _mom.get("strength")
                        _move = (_mom.get("latest_price", 0.0)
                                 - _mom.get("oldest_price", 0.0))
                        if _asset_up == "BTC":
                            plugin_meta["btc_price_change"] = _mom.get("change_pct")
                            plugin_meta["btc_move_usd"] = round(_move, 4)
                    # Cross-asset BTC context (always surface for non-BTC markets)
                    if _asset_up != "BTC":
                        _btc_mom = self.external_feed.get_spot_momentum("BTC", 60)
                        if _btc_mom:
                            plugin_meta["btc_price_change"] = _btc_mom.get("change_pct")
                            _bmove = (_btc_mom.get("latest_price", 0.0)
                                      - _btc_mom.get("oldest_price", 0.0))
                            plugin_meta["btc_move_usd"] = round(_bmove, 4)
            except Exception as _se:
                logger.debug(f"plugin_meta spot err: {_se}")

            # 5. BINANCE MICROSTRUCTURE (multistream)
            try:
                _bms = getattr(self, "binance_multistream", None)
                if _bms and hasattr(_bms, "features"):
                    _mf = _bms.features(_asset_up)
                    if _mf:
                        plugin_meta["binance_mid"] = _mf.get("mid")
                        plugin_meta["binance_microprice"] = _mf.get("microprice")
                        plugin_meta["binance_ob_imbalance"] = _mf.get("ob_imbalance")
                        plugin_meta["binance_spread_bps"] = _mf.get("spread_bps")
                        plugin_meta["binance_trade_flow_60s"] = _mf.get("trade_flow_60s")
                        plugin_meta["binance_trade_count_60s"] = _mf.get("trade_count_60s")
                        plugin_meta["funding_rate"] = _mf.get("funding_rate")
                        plugin_meta["mark_price"] = _mf.get("mark_price")
            except Exception as _me:
                logger.debug(f"plugin_meta micro err: {_me}")

            # 6. PRE-COMPUTED DIVERGENCE (spot vs odds)
            try:
                if self.external_feed and self.external_feed.is_available:
                    _div = self.external_feed.get_divergence(
                        _asset_up, up or 0.5, slug=slug)
                    if _div:
                        plugin_meta["divergence_signal"] = _div.get("signal")
                        plugin_meta["divergence_confidence"] = _div.get("confidence")
                        plugin_meta["divergence_active"] = _div.get("divergence")
            except Exception as _de:
                logger.debug(f"plugin_meta div err: {_de}")

            # 7. STRATEGY-SPECIFIC (martingale)
            if stype == "martingale":
                plugin_meta["loss_streak"] = self._mg_streak.get(s.id, 0)
                plugin_meta["base_amount"] = s.trade_amount

            # 8. RISK STATE (engine-wide)
            try:
                _rs = getattr(self.risk, "state", None)
                if _rs is not None:
                    plugin_meta["total_exposure"] = getattr(_rs, "total_exposure", 0.0)
                    plugin_meta["daily_pnl"] = getattr(_rs, "daily_pnl", 0.0)
                    plugin_meta["open_position_count"] = getattr(_rs, "open_position_count", 0)
                    plugin_meta["consecutive_losses"] = getattr(_rs, "consecutive_losses", 0)
                    plugin_meta["daily_trade_count"] = getattr(_rs, "daily_trade_count", 0)
                    _pme = getattr(_rs, "per_market_exposure", None) or {}
                    if slug in _pme:
                        plugin_meta["market_exposure"] = _pme[slug]
            except Exception as _re:
                logger.debug(f"plugin_meta risk err: {_re}")

            # 9. LIFECYCLE PHASE (per-strategy adaptive)
            try:
                import json as _json
                _pjson = getattr(s, "strategy_params", None)
                if _pjson:
                    _params = _json.loads(_pjson) if isinstance(_pjson, str) else _pjson
                    plugin_meta["strategy_phase"] = _params.get("phase", "unknown")
            except Exception:
                pass

            snap = MarketSnapshot(
                up_odds=up or 0.5, down_odds=down or 0.5,
                threshold=threshold, direction_filter=s.direction.value,
                odds_series=odds_series,
                minutes_remaining=minutes_remaining or 2.5,
                total_minutes=total_minutes,
                spread=cached.get("spread") or 0.02,
                best_ask=real_best_ask,
                best_bid=real_best_bid,
                metadata=plugin_meta)
            # Phase 82a hotfix: plugin evaluate isolated — a crashing plugin
            # now skips this strategy instead of bubbling up and silently
            # killing the engine main loop (root cause of the 01:28 freeze
            # observed after Phase 82a deploy).
            try:
                psig = self.plugins.evaluate(stype, snap)
            except Exception as _pe:
                logger.warning(
                    f"  [{sid}] ⚠️ PLUGIN_ERROR [{stype}]: {type(_pe).__name__}: {_pe}"
                )
                self.skips.record("PLUGIN_ERROR")
                return None
            should_trade = psig.should_trade
            trade_direction = psig.direction
            signal_score = psig.confidence
            signal_reason = f"[{stype}] {psig.reason}"
        else:
            sig = self.signals.evaluate(
                up_odds=up or 0.5, down_odds=down or 0.5,
                threshold=threshold, direction=s.direction.value,
                odds_series=odds_series,
                minutes_remaining=minutes_remaining, total_minutes=total_minutes,
                orderbook=ob_data)
            should_trade = sig.should_trade
            trade_direction = sig.direction
            signal_score = sig.composite_score
            signal_reason = sig.reason

        # EMA OVERRIDE
        # Sprint 5 HOTFIX v5: classic stype bypasses EMA filter — classic
        # snapshot trigger is user-directed, EMA is a technical filter.
        _stype_ema = getattr(s, 'strategy_type', '') or ''
        _classic_free_ema = self._classic_free_mode(_stype_ema)
        if (s.ma_filter_enabled and trade_direction and len(odds_series) >= 12
                and not _classic_free_ema):
            ema_dir = ema_direction_filter(odds_series)
            if ema_dir and ema_dir != trade_direction:
                self.skips.record("EMA_BLOCK")
                if self.skips.should_log(sid, "EMA_BLOCK"):
                    logger.info(f"  [{sid}] ❌ EMA_BLOCK: signal={trade_direction} ema={ema_dir}")
                return None

        # BINANCE DIVERGENCE + Phase 79b: Spot Momentum Boost
        if self.external_feed and self.external_feed.is_available:
            asset_name = s.asset.value if hasattr(s.asset, 'value') else str(s.asset)
            div = self.external_feed.get_divergence(asset_name, up or 0.5, slug=slug)
            if div:
                if div["divergence"] and div["signal"]:
                    spot_dir = div["signal"]
                    if trade_direction and spot_dir != trade_direction and div["confidence"] >= 0.10:
                        old_dir = trade_direction
                        trade_direction = spot_dir
                        signal_score = min(signal_score + 0.2, 1.0)
                        signal_reason += f" | DIV:{div['spot_change_pct']:+.2f}%→{spot_dir}"
                        if self.skips.should_log(sid, "DIVERGENCE"):
                            logger.info(f"  [{sid}] 🌐 DIVERGENCE: {old_dir}→{spot_dir} "
                                        f"(spot {div['spot_change_pct']:+.2f}% conf={div['confidence']:.2f})")
                    elif not trade_direction and div["confidence"] >= 0.30:
                        trade_direction = spot_dir
                        signal_score = div["confidence"]
                        should_trade = True
                        signal_reason += f" | DIV_NEW:{div['spot_change_pct']:+.2f}%→{spot_dir}"
                        logger.info(f"  [{sid}] 🌐 DIV_NEW: →{spot_dir} "
                                    f"(spot {div['spot_change_pct']:+.2f}% conf={div['confidence']:.2f})")
                elif not div["divergence"] and trade_direction:
                    signal_score = min(signal_score + 0.1, 1.0)
                    signal_reason += f" | SPOT_OK:{div['spot_change_pct']:+.2f}%"

            # Phase 79b: Short-term spot momentum — strongest edge signal
            # Son 60sn Binance fiyat degisimini composite score'a yansit
            spot_mom = self.external_feed.get_spot_momentum(asset_name, lookback_seconds=60)
            if spot_mom and spot_mom["strength"] >= 0.3:
                _mom_dir = spot_mom["direction"]
                _mom_str = spot_mom["strength"]
                _mom_pct = spot_mom["change_pct"]
                # Momentum yönü trade yönüyle uyuşuyorsa boost, uyuşmuyorsa penalize
                if trade_direction == _mom_dir:
                    _boost = _mom_str * 0.15  # Max +0.15 boost
                    signal_score = min(signal_score + _boost, 1.0)
                    signal_reason += f" | MOM_OK:{_mom_pct:+.3f}%"
                elif trade_direction and trade_direction != _mom_dir:
                    _penalty = _mom_str * 0.10  # Max -0.10 penalty
                    signal_score = max(signal_score - _penalty, 0.0)
                    signal_reason += f" | MOM_AGAINST:{_mom_pct:+.3f}%"

        # MIN_VOLATILITY
        # Sprint 5 HOTFIX v5: classic bypasses LOW_VOL — user-directed
        # trigger should fire regardless of book volatility.
        if (s.min_volatility and s.min_volatility > 0 and len(odds_series) >= 5
                and not _classic_free_ema):
            mean = sum(odds_series[-10:]) / min(len(odds_series), 10)
            vol = math.sqrt(sum((x - mean)**2 for x in odds_series[-10:]) / min(len(odds_series), 10))
            if vol < s.min_volatility / 100:
                self.skips.record("LOW_VOL")
                if verbose:
                    logger.info(f"  [{sid}] ❌ LOW_VOL: {vol:.4f} < {s.min_volatility/100:.4f}")
                return None

        # Phase 74b: Per-strategy lifecycle min_composite override
        # HOTFIX: disabled by default — exploration phase was leaking weak signals
        _lc_override_enabled = os.getenv("LIFECYCLE_MIN_OVERRIDE", "false").lower() == "true"
        _lc = ctx.get("lifecycle")
        if _lc_override_enabled and not should_trade and trade_direction and _lc:
            _lc_min = _lc.min_composite
            _global_min = float(os.getenv("MIN_COMPOSITE", "0.45"))
            if _lc_min < _global_min and signal_score >= _lc_min:
                should_trade = True
                signal_reason += f" | LC_OVERRIDE({_lc.phase}:{_lc_min:.2f})"
                logger.info(f"  [{sid}] 🔬 LIFECYCLE: override min_composite "
                            f"{_global_min:.2f}→{_lc_min:.2f} (phase={_lc.phase})")

        if not should_trade or not trade_direction:
            if "no_direction" in signal_reason:
                self.skips.record("NO_DIR")
            else:
                self.skips.record("SIG_WEAK")
            if verbose:
                logger.info(f"  [{sid} {ctx['asset']}/{ctx['tf']}] ❌ {signal_reason} (data={len(odds_series)}pts)")
                log_rejection(slug, "SIGNAL", signal_reason, s.id)
            return None

        direction = Direction(trade_direction)

        # Sprint 5 HOTFIX v3: classic FREE-MODE check for downstream gates
        _classic_free = self._classic_free_mode(stype)

        # REGIME GATE
        if not _classic_free and self.regime.should_skip(stype):
            self.skips.record("REGIME")
            if verbose and self.skips.should_log(sid, "REGIME"):
                logger.info(f"  [{sid}] ❌ REGIME: {stype} unfit for {self.regime.regime} "
                           f"(fit={self.regime.strategy_fit(stype):.1f})")
            return None

        # THOMPSON SAMPLING GATE
        # P2-01 FIX: Pass engine=self so brain_flags['thompson_sampling'] toggle works
        if not _classic_free and not self.selector.should_trade(s.id, engine=self):
            self.skips.record("TS_SKIP")
            if verbose and self.skips.should_log(sid, "TS_SKIP"):
                arm = self.selector.get_or_create(s.id)
                logger.info(f"  [{sid}] ❌ TS: low rank (WR={arm.win_rate:.0%})")
            return None

        # MAX_EXECUTIONS
        if s.max_executions_per_event:
            if await self._count(s.id, slug) >= s.max_executions_per_event:
                self.skips.record("MAX_EXEC")
                return None

        # MAX_LOSSES
        if s.max_losses_per_event:
            if await self._count_losses(s.id, slug) >= s.max_losses_per_event:
                self.skips.record("MAX_LOSS")
                return None

        # Live price
        token_id = cached.get("up_token") if direction == Direction.UP else cached.get("down_token")
        if not token_id:
            return None
        best_ask = await self.client.get_live_price(token_id, "BUY")
        # Phase 82e Sprint 5 (FINAL): ENV-tunable sanity bounds. Prices
        # below lo or above hi are thin illiquid books — skip.
        _price_lo = float(os.getenv("PRICE_SANITY_LO", "0.02"))
        _price_hi = float(os.getenv("PRICE_SANITY_HI", "0.99"))
        if not best_ask or best_ask <= _price_lo or best_ask >= _price_hi:
            self.skips.record("BAD_PRICE")
            return None

        # S3-02: Zone Filter — Block trades in unprofitable zones
        # Phase 82e Sprint 5 (FINAL) — Classic stype is USER-directed:
        # the user explicitly chose the trigger (e.g. 0.85). Honour their
        # intent and bypass the global ALLOWED_ZONES filter for classic.
        # Override via CLASSIC_RESPECT_ZONES=true if user wants to apply
        # zones to classic too.
        _classic_bypass_zones = (
            stype == "classic"
            and os.getenv("CLASSIC_RESPECT_ZONES", "false").lower() != "true"
        )
        if (self._ALLOWED_ZONES and not _classic_bypass_zones
                and not self._in_allowed_zone(best_ask, self._ALLOWED_ZONES)):
            self.skips.record("ZONE_BLOCKED")
            if verbose and self.skips.should_log(sid, "ZONE_BLOCKED"):
                zone_str = ",".join(f"{lo*100:.0f}-{hi*100:.0f}c" for lo, hi in self._ALLOWED_ZONES)
                logger.info(f"  [{sid}] ❌ ZONE_BLOCKED: price {best_ask*100:.2f}c not in [{zone_str}]")
            return None

        # Phase 82c Task #19: Per-strategy-type blocked zone (fusion/AI_F)
        # AI_F strategies lose ~72% of 30-40c entries in production. Block
        # this range for fusion-type strats unless FUSION_BLOCKED_ZONES is
        # cleared. Applies to AI_F_* (label) and any strategy_type==fusion.
        if (stype == "fusion" and self._FUSION_BLOCKED_ZONES
                and self._in_allowed_zone(best_ask, self._FUSION_BLOCKED_ZONES)):
            self.skips.record("FUSION_ZONE_BLOCKED")
            if verbose and self.skips.should_log(sid, "FUSION_ZONE_BLOCKED"):
                zone_str = ",".join(f"{lo*100:.0f}-{hi*100:.0f}c" for lo, hi in self._FUSION_BLOCKED_ZONES)
                logger.info(f"  [{sid}] ❌ FUSION_ZONE_BLOCKED: price {best_ask*100:.2f}c in [{zone_str}] (loss-prone bucket)")
            return None

        ctx.update({
            "stype": stype, "psig": psig,
            "should_trade": should_trade, "trade_direction": trade_direction,
            "signal_score": signal_score, "signal_reason": signal_reason,
            "direction": direction, "token_id": token_id,
            "best_ask": best_ask, "ob_data": ob_data,
            "odds_series": odds_series,
        })
        return ctx

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 3: SIGNAL BOOSTERS
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_signal_boosters(self, s, ctx, verbose=False):
        """Micro, funding, Becker, cascade, lag arb, whale boosters.
        Returns updated ctx or None to abort."""
        sid = ctx["sid"]
        asset = ctx["asset"]
        slug = ctx["slug"]
        cached = ctx["cached"]
        trade_direction = ctx["trade_direction"]
        signal_score = ctx["signal_score"]
        signal_reason = ctx["signal_reason"]
        direction = ctx["direction"]
        best_ask = ctx["best_ask"]
        token_id = ctx["token_id"]
        minutes_remaining = ctx["minutes_remaining"]

        # Sprint 5 HOTFIX v3: classic FREE-MODE — bypass ORACLE_PARITY,
        # BECKER veto/flip, EVENT_WAVES_QUALITY. Boosters themselves
        # (micro/funding/cascade/lag/whale/markov/memory) still apply —
        # they only tweak signal_score, never block.
        _classic_free = self._classic_free_mode(ctx)

        micro_boost_value = 0.0
        becker_delta_value = 0.0

        bms = getattr(self, "binance_multistream", None)
        clo = getattr(self, "chainlink_oracle", None)
        micro_features = None
        _settings_46 = getattr(self, "settings", None)
        if _settings_46 is None:
            try:
                from config.settings import Settings as _SettingsCls46
                _settings_46 = _SettingsCls46()
            except Exception:
                _settings_46 = None

        if bms is not None and trade_direction:
            try:
                micro_features = bms.features(asset.upper()) if hasattr(bms, "features") else None
            except Exception as _be:
                logger.debug(f"  [{sid}] micro_features_error: {_be}")
                micro_features = None

        # ── Chainlink parity gate ──
        if (not _classic_free and clo is not None and micro_features and trade_direction
                and (_settings_46 is None or getattr(_settings_46, "PARITY_GATE_ENABLED", True))):
            try:
                ref_mid = micro_features.get("mid") or micro_features.get("microprice")
                if ref_mid and clo.parity_break(asset.upper(), float(ref_mid)):
                    delta_bps = clo.parity_delta_bps(asset.upper(), float(ref_mid)) or 0.0
                    self.skips.record("ORACLE_PARITY")
                    if verbose:
                        logger.info(f"  [{sid}] ❌ ORACLE_PARITY: Δ={delta_bps:.1f}bps")
                    return None
            except Exception as _pe:
                logger.debug(f"  [{sid}] parity_gate_error: {_pe}")

        # ── Microstructure boost ──
        if (micro_features and trade_direction
                and (_settings_46 is None or getattr(_settings_46, "MICRO_BOOST_ENABLED", True))):
            try:
                weight = float(getattr(_settings_46, "MICRO_BOOST_WEIGHT", 0.15)) if _settings_46 else 0.15
                clamp = float(getattr(_settings_46, "MICRO_BOOST_CLAMP", 0.20)) if _settings_46 else 0.20
                if getattr(self, "micro_weight", None) is not None:
                    try:
                        weight *= float(self.micro_weight.get_multiplier())
                    except Exception:
                        pass
                micro = float(micro_features.get("microprice") or 0.0)
                mid = float(micro_features.get("mid") or 0.0)
                ob_imb = float(micro_features.get("ob_imbalance") or 0.0)
                tflow = float(micro_features.get("trade_flow_60s") or 0.0)
                micro_tilt = 0.0
                if mid > 0 and micro > 0:
                    micro_tilt = (micro - mid) / mid * 100.0
                    micro_tilt = max(min(micro_tilt * 2.0, 1.0), -1.0)
                composite = (0.4 * micro_tilt) + (0.35 * ob_imb) + (0.25 * tflow)
                if trade_direction == "down":
                    composite = -composite
                boost = max(min(composite * weight, clamp), -clamp)
                if abs(boost) > 1e-4:
                    signal_score = max(min(signal_score + boost, 1.0), -1.0)
                    signal_reason += f" | μ={boost:+.3f}"
                    micro_boost_value = boost
                    if verbose:
                        logger.info(f"  [{sid}] 🔬 micro={boost:+.3f} "
                                    f"(tilt={micro_tilt:+.2f} imb={ob_imb:+.2f} flow={tflow:+.2f})")
            except Exception as _me:
                logger.debug(f"  [{sid}] micro_boost_error: {_me}")

        # ── Funding rate tilt ──
        if (micro_features and trade_direction
                and (_settings_46 is None or getattr(_settings_46, "FUNDING_TILT_ENABLED", True))):
            try:
                fr = micro_features.get("funding_rate")
                if fr is not None:
                    fr_val = float(fr)
                    threshold_fr = float(getattr(_settings_46, "FUNDING_TILT_THRESHOLD", 0.0005)) if _settings_46 else 0.0005
                    fweight = float(getattr(_settings_46, "FUNDING_TILT_WEIGHT", 0.05)) if _settings_46 else 0.05
                    if abs(fr_val) >= threshold_fr:
                        bear_bias = (fr_val > 0)
                        tilt = -fweight if (bear_bias and trade_direction == "up") else (
                            -fweight if ((not bear_bias) and trade_direction == "down") else fweight)
                        signal_score = max(min(signal_score + tilt, 1.0), -1.0)
                        signal_reason += f" | fr={fr_val*100:+.3f}%→{tilt:+.2f}"
                        if verbose:
                            logger.info(f"  [{sid}] 💸 funding={fr_val*100:+.3f}% tilt={tilt:+.2f}")
            except Exception as _fe:
                logger.debug(f"  [{sid}] funding_tilt_error: {_fe}")

        # ── Phase 70: 2D Calibration Surface C(K,τ) or 1D Becker δ(p) boost ──
        _surface_2d = getattr(self, "_calib_surface_2d", None)
        _surface_2d_used = False
        if _surface_2d is not None and os.getenv("SURFACE_2D_ENABLED", "true").lower() == "true":
            try:
                from calibration.surface_2d import surface_delta as _surf_delta
                _surf_result = _surf_delta(
                    _surface_2d,
                    best_ask,
                    hours_remaining=minutes_remaining / 60.0 if minutes_remaining else None,
                    fallback_1d_curve=self._becker_poly_curve or None,
                )
                if _surf_result.source not in ("disabled", "no_surface", "out_of_range", "1d_no_data"):
                    bboost = _surf_result.boost
                    if getattr(self, "becker_weight", None) is not None:
                        try:
                            bboost *= float(self.becker_weight.get_multiplier(asset))
                        except Exception:
                            pass
                    if abs(bboost) > 1e-4:
                        signal_score = max(min(signal_score + bboost, 1.0), -1.0)
                        signal_reason += f" | 2d={bboost:+.3f}"
                        becker_delta_value = bboost
                        _surface_2d_used = True
                        if verbose:
                            logger.info(
                                f"  [{sid}] 📊 surface_2d[{_surf_result.source}] "
                                f"δ(p={best_ask:.3f},τ={minutes_remaining:.0f}m)="
                                f"{_surf_result.delta:+.4f} "
                                f"(C_K={_surf_result.c_k:+.4f} C_τ={_surf_result.c_tau:+.4f} "
                                f"C_int={_surf_result.c_int:+.4f}) "
                                f"conf={_surf_result.confidence:.2f} "
                                f"→ boost={bboost:+.3f}"
                                f"{' ⚠️antisym' if not _surf_result.antisym_ok else ''}")
            except Exception as _s2e:
                logger.debug(f"  [{sid}] surface_2d_error: {_s2e}")

        # ── Becker δ(p) calibration boost (1D fallback when 2D not used) ──
        if (not _surface_2d_used
                and getattr(self.settings, "BECKER_CALIB_ENABLED", False)
                and self._becker_poly_curve):
            try:
                delta_poly = self._becker_delta(best_ask, source="poly")
                delta_kalshi = self._becker_delta(best_ask, source="kalshi") if self._becker_kalshi_curve else None
                if delta_poly is not None:
                    k_w = float(getattr(self.settings, "BECKER_KALSHI_WEIGHT", 0.30))
                    if delta_kalshi is not None:
                        delta = delta_poly * (1.0 - k_w) + delta_kalshi * k_w
                        src_tag = "p+k"
                    else:
                        delta = delta_poly
                        src_tag = "p"
                    # S3-03: Becker Calibration Weight Increase
                    # Default 0.10 → ENV override via BECKER_CALIB_WEIGHT
                    # When |δ| > 3c, apply higher weight (0.25 recommended)
                    bweight = float(getattr(self.settings, "BECKER_CALIB_WEIGHT", 0.10))
                    # Apply higher weight if delta is strong (|δ| > 0.03)
                    if abs(delta) > 0.03:
                        bweight_boost = float(os.getenv("BECKER_SIGNAL_WEIGHT", "0.25"))
                        if bweight_boost > bweight:
                            bweight = bweight_boost
                    if getattr(self, "becker_weight", None) is not None:
                        try:
                            bweight *= float(self.becker_weight.get_multiplier(asset))
                        except Exception:
                            pass
                    bclamp = float(getattr(self.settings, "BECKER_CALIB_CLAMP", 0.15))
                    bboost = max(min(delta * bweight, bclamp), -bclamp)
                    if abs(bboost) > 1e-4:
                        signal_score = max(min(signal_score + bboost, 1.0), -1.0)
                        signal_reason += f" | δ={bboost:+.3f}"
                        becker_delta_value = bboost
                        if verbose:
                            logger.info(
                                f"  [{sid}] 📈 becker[{src_tag}] δ(p={best_ask:.3f})={delta:+.3f} "
                                f"(poly={delta_poly:+.3f}"
                                f"{', kalshi=%+.3f' % delta_kalshi if delta_kalshi is not None else ''}) "
                                f"→ boost={bboost:+.3f}")
            except Exception as _de:
                logger.debug(f"  [{sid}] becker_delta_error: {_de}")

        # ── Becker decision-mode (veto / flip) ──
        decision_mode = (getattr(self.settings, "BECKER_DECISION_MODE", "boost") or "boost").strip().lower()
        if (not _classic_free
                and decision_mode in ("veto", "flip")
                and getattr(self.settings, "BECKER_CALIB_ENABLED", False)
                and self._becker_poly_curve):
            wl_raw = (getattr(self.settings, "BECKER_DECISION_STRATEGY_WHITELIST", "") or "")
            wl = {x.strip().lower() for x in wl_raw.split(",") if x.strip()}
            stype = ctx["stype"]
            if wl and (stype or "").strip().lower() in wl:
                try:
                    delta_poly_d = self._becker_delta(best_ask, source="poly")
                    delta_kalshi_d = (self._becker_delta(best_ask, source="kalshi")
                                      if self._becker_kalshi_curve else None)
                    if delta_poly_d is not None:
                        k_w_d = float(getattr(self.settings, "BECKER_KALSHI_WEIGHT", 0.30))
                        if delta_kalshi_d is not None:
                            delta_d = delta_poly_d * (1.0 - k_w_d) + delta_kalshi_d * k_w_d
                        else:
                            delta_d = delta_poly_d
                        thresh_d = float(getattr(self.settings, "BECKER_DECISION_THRESHOLD", 0.01))
                        if delta_d <= -thresh_d:
                            if decision_mode == "veto":
                                self.skips.record("BECKER_VETO")
                                if verbose:
                                    logger.info(f"  [{sid}] ⛔ becker veto: δ={delta_d:+.3f} ≤ -{thresh_d}")
                                return None
                            # flip mode
                            new_dir = Direction.DOWN if direction == Direction.UP else Direction.UP
                            new_tok = (cached.get("up_token") if new_dir == Direction.UP
                                       else cached.get("down_token"))
                            if not new_tok:
                                self.skips.record("BECKER_FLIP_NO_TOK")
                                return None
                            new_ask = await self.client.get_live_price(new_tok, "BUY")
                            if not new_ask or new_ask <= 0.02 or new_ask >= 0.99:
                                self.skips.record("BECKER_FLIP_BAD_PRICE")
                                return None
                            if verbose:
                                logger.info(
                                    f"  [{sid}] 🔄 becker flip: {direction.value}→{new_dir.value} "
                                    f"δ={delta_d:+.3f} ask {best_ask:.3f}→{new_ask:.3f}")
                            direction = new_dir
                            token_id = new_tok
                            best_ask = new_ask
                            signal_reason += f" | flip(δ={delta_d:+.3f})"
                except Exception as _ed:
                    logger.debug(f"  [{sid}] becker_decision_error: {_ed}")

        # ── Probability Gap log (observation only) ──
        _pgap_log = os.getenv("PROB_GAP_LOG", "true").lower() == "true"
        if _pgap_log and getattr(self.settings, "BECKER_CALIB_ENABLED", False) and self._becker_poly_curve:
            try:
                _pg_delta = self._becker_delta(best_ask, source="poly")
                if _pg_delta is not None and abs(_pg_delta) > 0.001:
                    _gap_bps = _pg_delta * 10000
                    _gap_dir = "UP_EDGE" if _pg_delta > 0 else "DOWN_EDGE"
                    logger.info(
                        f"  [{sid}] 📊 PROB_GAP: δ={_pg_delta:+.4f} ({_gap_bps:+.1f}bps) "
                        f"model_wr={best_ask + _pg_delta:.3f} crowd={best_ask:.3f} "
                        f"→ {_gap_dir} | dir={trade_direction}")
            except Exception:
                pass

        # ── Cascade Overshoot Contrarian ──
        if getattr(self, "_cascade_detector", None) is not None:
            try:
                cascade_sig = self._cascade_detector.get_signal(slug)
                if cascade_sig > 0:
                    event = self._cascade_detector.check(slug)
                    if event and trade_direction == event.contrarian_direction:
                        cascade_boost = cascade_sig * 0.15
                        signal_score = min(signal_score + cascade_boost, 1.0)
                        signal_reason += f" | cascade_ctr={cascade_boost:+.3f}"
                        if verbose:
                            logger.info(
                                f"  [{sid}] 🌊 CASCADE BOOST: {event.direction} cascade "
                                f"Δ={event.magnitude:.3f} vol={event.volume_ratio:.1f}x "
                                f"→ contrarian {event.contrarian_direction} +{cascade_boost:.3f}")
                    elif event and trade_direction == event.direction:
                        cascade_penalty = cascade_sig * 0.10
                        signal_score = max(signal_score - cascade_penalty, -1.0)
                        signal_reason += f" | cascade_with={-cascade_penalty:+.3f}"
            except Exception as _ce:
                logger.debug(f"  [{sid}] cascade_signal_error: {_ce}")

        # ── Lag Arbitrage ──
        if getattr(self, "_lag_arb", None) is not None and trade_direction:
            try:
                lag_sig = self._lag_arb.check_lag(asset.lower())
                if lag_sig and lag_sig.signal_strength > 0.01:
                    _lag_weight = float(os.getenv("LAG_SIGNAL_WEIGHT", "0.10"))
                    if lag_sig.expected_direction == trade_direction:
                        lag_boost = lag_sig.signal_strength * _lag_weight
                        signal_score = min(signal_score + lag_boost, 1.0)
                        signal_reason += f" | lag_{lag_sig.leader}={lag_boost:+.3f}"
                        if verbose:
                            logger.info(
                                f"  [{sid}] 🔗 LAG_ARB: {lag_sig.leader}→{asset} "
                                f"move={lag_sig.leader_move:+.3f} ρ={lag_sig.correlation:.2f} "
                                f"→ +{lag_boost:.3f}")
                    else:
                        lag_pen = lag_sig.signal_strength * _lag_weight * 0.5
                        signal_score = max(signal_score - lag_pen, -1.0)
                        signal_reason += f" | lag_against={-lag_pen:+.3f}"
            except Exception as _le:
                logger.debug(f"  [{sid}] lag_arb_error: {_le}")

        # ── Whale Signal ──
        if getattr(self, "_whale_signal", None) is not None and trade_direction:
            try:
                whale_boost = self._whale_signal.get_signal(
                    slug, trade_direction, minutes_remaining)
                if abs(whale_boost) > 0.001:
                    signal_score = max(min(signal_score + whale_boost, 1.0), -1.0)
                    signal_reason += f" | whale={whale_boost:+.3f}"
                    if verbose and abs(whale_boost) > 0.01:
                        flow = self._whale_signal.analyze_flow(slug)
                        logger.info(
                            f"  [{sid}] 🐋 WHALE: flow={flow.net_direction} "
                            f"${flow.up_volume_usd:.0f}↑/${flow.down_volume_usd:.0f}↓ "
                            f"n={flow.trade_count} late={flow.is_late_entry} "
                            f"→ {whale_boost:+.3f}")
            except Exception as _we:
                logger.debug(f"  [{sid}] whale_signal_error: {_we}")

        # ── Phase 71: Spread Signal (orderbook imbalance) ──
        if os.getenv("SPREAD_SIGNAL_ENABLED", "false").lower() == "true" and trade_direction:
            try:
                from data_feeds.spread_signal import analyze_spread
                _ob = cached.get("orderbook")
                if _ob:
                    _spread_result = analyze_spread(
                        _ob, trade_direction=trade_direction)
                    if abs(_spread_result.signal) > 0.001:
                        signal_score = max(min(
                            signal_score + _spread_result.signal, 1.0), -1.0)
                        signal_reason += f" | spread={_spread_result.signal:+.3f}"
            except Exception as _se:
                logger.debug(f"  [{sid}] spread_signal_error: {_se}")

        # ── Phase 76: Markov Chain probability boost ──
        _markov = getattr(self, "_markov", None)
        if _markov is not None and best_ask is not None:
            try:
                from core.markov_estimator import MARKOV_ENABLED, MARKOV_WEIGHT
                if MARKOV_ENABLED:
                    _odds_series = self.odds_feed.get_odds_series(slug, "up") or []
                    if len(_odds_series) >= 5:
                        _m_result = _markov.estimate(_odds_series, best_ask)
                        if _m_result.direction is not None:
                            _m_boost = _m_result.edge * MARKOV_WEIGHT
                            _m_boost = max(min(_m_boost, 0.10), -0.10)  # clamp
                            signal_score = max(min(signal_score + _m_boost, 1.0), -1.0)
                            signal_reason += f" | markov={_m_boost:+.3f}(e={_m_result.edge:+.3f})"
                            if verbose:
                                logger.info(f"  [{sid}] 🔮 MARKOV: est={_m_result.estimated_prob:.3f} "
                                            f"mkt={best_ask:.3f} edge={_m_result.edge:+.3f} "
                                            f"boost={_m_boost:+.3f}")
            except Exception as _me:
                logger.debug(f"  [{sid}] markov_boost_error: {_me}")

        # ── Phase 77: Trade Memory pattern lookup ──
        _tm = getattr(self, "_trade_memory", None)
        if _tm is not None and best_ask is not None:
            try:
                _pattern = await _tm.get_pattern(s.id, slug, best_ask)
                if _pattern is not None:
                    _mem_adj = (_pattern.confidence_mult - 1.0)
                    _mem_adj = max(min(_mem_adj, 0.15), -0.20)  # clamp
                    signal_score = max(min(signal_score + _mem_adj, 1.0), -1.0)
                    signal_reason += (f" | memory={_mem_adj:+.3f}"
                                      f"(wr={_pattern.win_rate:.0f}%,n={_pattern.total_trades})")
                    if verbose:
                        logger.info(f"  [{sid}] 🧠 MEMORY: {_pattern.pattern_key} "
                                    f"wr={_pattern.win_rate:.0f}% n={_pattern.total_trades} "
                                    f"adj={_mem_adj:+.3f}")
            except Exception as _tme:
                logger.debug(f"  [{sid}] trade_memory_error: {_tme}")

        # ── Phase 71: EventWaves Market Quality Gate ──
        if not _classic_free and os.getenv("EVENT_WAVES_ENABLED", "false").lower() == "true":
            try:
                from data_feeds.event_waves import assess_market_quality
                _vol_24h = cached.get("volume_24h", 0)
                _spread_val = cached.get("spread", 0.05)
                _mq = assess_market_quality(
                    slug=slug, volume_24h=_vol_24h, spread=_spread_val,
                    minutes_remaining=minutes_remaining or 60,
                    total_minutes=300, up_odds=best_ask)
                if not _mq.should_trade:
                    self.skips.record("EVENT_WAVES_QUALITY")
                    if verbose:
                        logger.info(f"  [{sid}] ⛔ MARKET_QUALITY: {_mq.reason}")
                    return None
            except Exception as _ewe:
                logger.debug(f"  [{sid}] event_waves_error: {_ewe}")

        ctx.update({
            "signal_score": signal_score,
            "signal_reason": signal_reason,
            "direction": direction,
            "token_id": token_id,
            "best_ask": best_ask,
            "micro_boost_value": micro_boost_value,
            "becker_delta_value": becker_delta_value,
        })
        return ctx

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 4: GATES (edge, slippage, risk)
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_gates(self, s, ctx, verbose=False):
        """Edge gate, slippage gate, risk manager check.
        Returns updated ctx or None to abort."""
        sid = ctx["sid"]
        slug = ctx["slug"]
        best_ask = ctx["best_ask"]
        signal_score = ctx["signal_score"]
        threshold = ctx["threshold"]

        # Sprint 5 HOTFIX v3: classic FREE-MODE — EDGE_GATE, LOW_EDGE_VS_FEE,
        # BRIER_ALARM, UNSELLABLE are all engine-level filters meaningless
        # to user-directed classic triggers. RISK_ERROR/RISK/STP remain
        # as hard safety (wallet/balance state).
        _classic_free = self._classic_free_mode(ctx)

        # SLIPPAGE GATE (Phase 75: per-strategy override via lifecycle)
        # Sprint 5 HOTFIX v5: classic bypasses SLIPPAGE — user expects the
        # buy to fire as soon as price crosses the trigger, regardless of
        # how far past the threshold the best_ask has run.
        _lc = ctx.get("lifecycle")
        _slip_enabled = os.getenv("SLIPPAGE_GATE_ENABLED", "true").lower() != "false"
        if _lc and _lc.slippage_gate is not None:
            _slip_enabled = _lc.slippage_gate
        if _slip_enabled and not _classic_free:
            if s.max_entry_slippage and s.max_entry_slippage > 0:
                if best_ask > threshold + s.max_entry_slippage:
                    self.skips.record("SLIPPAGE")
                    logger.info(f"  [{sid}] ❌ SLIP: ask={best_ask:.3f} > {threshold}+{s.max_entry_slippage}")
                    return None

        # EDGE GATE (Phase 74b: lifecycle edge_gate_mult adjusts thresholds)
        _zone_min = float(os.getenv("EDGE_ZONE_5065_MIN", "0.45"))
        _lc = ctx.get("lifecycle")
        _edge_mult = _lc.edge_gate_mult if _lc else 1.0
        min_sig = 0.20
        if 0.48 <= best_ask <= 0.65:
            min_sig = _zone_min
        elif best_ask < 0.48:
            min_sig = 0.35
        elif 0.65 < best_ask <= 0.85:
            min_sig = 0.40
        elif best_ask > 0.85:
            min_sig = 0.30
        # Apply lifecycle multiplier (< 1.0 = looser, > 1.0 = tighter)
        if _edge_mult != 1.0:
            min_sig = round(min_sig * _edge_mult, 3)
        if not _classic_free and signal_score < min_sig:
            self.skips.record("EDGE_GATE")
            if verbose:
                logger.info(f"  [{sid}] ❌ EDGE: ask={best_ask:.3f} min={min_sig} sig={signal_score:.2f}")
            return None

        # Sprint 1 S1-02: FEE-AWARE ENTRY GATE
        # Block trades where edge doesn't cover fee by MIN_EDGE_OVER_FEE multiplier.
        # Crypto fee is ~2-7%. If signal edge < fee * multiplier → net negative EV trade.
        _fee_gate_enabled = os.getenv("FEE_GATE_ENABLED", "true").lower() == "true"
        if not _classic_free and _fee_gate_enabled:
            _min_edge_over_fee = float(os.getenv("MIN_EDGE_OVER_FEE", "2.0"))
            _estimated_edge = abs(signal_score - 0.5)  # signal's distance from random
            _fee_pct = polymarket_fee_percent_v2(best_ask) / 100.0  # convert % to decimal
            if _fee_pct > 0 and _estimated_edge < _fee_pct * _min_edge_over_fee:
                self.skips.record("LOW_EDGE_VS_FEE")
                if verbose:
                    logger.info(f"  [{sid}] ❌ FEE_GATE: edge={_estimated_edge:.4f} < "
                                f"fee={_fee_pct:.4f}×{_min_edge_over_fee}")
                return None

        # Phase 79 S4-04: BRIER CALIBRATION ALARM
        # Block trades in high-calibration-gap zones (bot confidence = actual accuracy gap)
        _brier_enabled = os.getenv("BRIER_ALARM_ENABLED", "true").lower() != "false"
        if not _classic_free and _brier_enabled and self.BRIER_GAP_MAX > 0:
            try:
                skip_trade, reason = await self._check_brier_alarm(best_ask)
                if skip_trade:
                    self.skips.record("BRIER_ALARM")
                    if verbose:
                        logger.info(f"  [{sid}] ❌ {reason}")
                    return None
            except Exception as _be:
                logger.debug(f"brier alarm check failed (non-critical): {_be}")

        # Phase 66: UNSELLABLE TOKEN CHECK (pre-entry liquidity gate)
        # Source: A9 — sovereign2013 lost $23→$1.50 on illiquid position
        #
        # Sprint 5 HOTFIX v3 (2026-04-19): unified under _classic_free_mode.
        # v2 env CLASSIC_RESPECT_UNSELLABLE still honored for back-compat:
        # if set to "true", classic respects UNSELLABLE even when FREE_MODE
        # is on (narrower opt-in).
        _unsellable_bypass = _classic_free or (
            ctx.get("stype") == "classic"
            and os.getenv("CLASSIC_RESPECT_UNSELLABLE", "false").lower() != "true"
        )
        if (os.getenv("UNSELLABLE_CHECK_ENABLED", "true").lower() == "true"
                and not _unsellable_bypass):
            try:
                ob_data = ctx.get("orderbook")
                mins_remaining = ctx.get("minutes_remaining")
                unsellable = self.risk.check_unsellable_risk(
                    market_odds=best_ask,
                    orderbook=ob_data,
                    minutes_to_close=mins_remaining)
                if not unsellable.approved:
                    self.skips.record("UNSELLABLE")
                    logger.info(f"  [{sid}] ❌ {unsellable.reason}")
                    return None
            except Exception as _ue:
                logger.debug(f"unsellable check: {_ue}")

        # RISK CHECK
        wallet = await self.db.get_wallet(s.wallet_id)
        if not wallet:
            return None
        pending_reserved = sum(
            o.amount for o in self._pending
            if o.wallet_id == s.wallet_id)
        effective_balance = max(wallet.balance - pending_reserved, 0.0)
        try:
            verdict = self.risk.check_trade(s.trade_amount, slug, effective_balance, strategy_id=s.id)
        except Exception as _risk_err:
            logger.warning(f"  [{sid}] ❌ RISK_ERROR: {type(_risk_err).__name__}: {_risk_err}")
            self.skips.record("RISK_ERROR")
            return None
        if verdict is None or not verdict.approved:
            self.skips.record("RISK")
            reason = verdict.reason if verdict is not None else "verdict=None"
            logger.info(f"  [{sid}] ❌ RISK: {reason}")
            return None

        ctx["wallet"] = wallet
        return ctx

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 5: POSITION SIZING
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_sizing(self, s, ctx, verbose=False):
        """Kelly, conviction, event calendar, canary sizing.
        Returns updated ctx or None to abort."""
        sid = ctx["sid"]
        stype = ctx["stype"]
        psig = ctx["psig"]
        signal_score = ctx["signal_score"]
        signal_reason = ctx["signal_reason"]
        best_ask = ctx["best_ask"]
        wallet = ctx["wallet"]

        # Sprint 5 HOTFIX v3: classic FREE-MODE — skip KELLY_NO_EDGE,
        # LOW_CONVICTION, EV_NEGATIVE, CAPITAL_BUDGET skip-returns. The
        # sizing MULTIPLIERS (kelly boost, conviction shrink, event shrink,
        # canary cap, lifecycle, capital cap) still apply — they only
        # adjust trade_amount, never block the trade.
        _classic_free = self._classic_free_mode(stype)

        trade_amount = s.trade_amount

        # Martingale amount override
        if stype == "martingale" and psig and psig.metadata.get("sized_amount"):
            trade_amount = psig.metadata["sized_amount"]
            mg_level = psig.metadata.get("level", 0)
            if mg_level > 0:
                logger.info(f"  🎰 [{sid}] Martingale L{mg_level}: ${trade_amount:.2f}")

        # Kelly position sizing (Phase 73: regime-aware fraction)
        # Phase 75: Per-strategy kelly override
        _lc = ctx.get("lifecycle")
        _kelly_on = self._kelly_mode
        if _lc and _lc.kelly_enabled is not None:
            _kelly_on = _lc.kelly_enabled
        kelly = {}
        if _kelly_on and stype not in ("martingale",):
            try:
                _current_regime = getattr(self, "regime_classifier", None)
                _regime_str = _current_regime.regime if _current_regime else "ranging"
                kelly = await get_strategy_kelly(self.db, s.id, wallet.balance,
                                                  regime=_regime_str)
                if kelly.get("skip") and not _classic_free:
                    if verbose:
                        logger.info(f"  ⛔ [{sid}] KELLY_SKIP: {kelly.get('reason', 'no edge')}")
                    self._stats["skipped"] = self._stats.get("skipped", 0) + 1
                    self.skips.record("KELLY_NO_EDGE")
                    return None
                if kelly["confidence"] != "low" and kelly["size"] > trade_amount:
                    old_amt = trade_amount
                    if trade_amount >= 5.0:
                        max_kelly = trade_amount * 2.0
                    else:
                        max_kelly = trade_amount * 3.0
                    trade_amount = min(kelly["size"], max_kelly)

                    # Hard cap as % of bankroll
                    max_bet_pct = getattr(self.settings, "KELLY_MAX_BET_PCT", 0.05)
                    if max_bet_pct > 0 and wallet.balance > 0:
                        bankroll_cap = wallet.balance * max_bet_pct
                        if trade_amount > bankroll_cap:
                            trade_amount = bankroll_cap

                    # Correlated position halving
                    if getattr(self.settings, "KELLY_CORRELATED_HALVING", True):
                        slug_lower = (ctx["slug"] or "").lower()
                        asset_tag = None
                        for tok in ("btc", "eth", "sol", "xrp"):
                            if tok in slug_lower:
                                asset_tag = tok
                                break
                        if asset_tag:
                            already = sum(
                                1 for ex in self._pending
                                if ex.wallet_id == s.wallet_id
                                and asset_tag in (ex.slug or "").lower())
                            if already >= 1:
                                trade_amount *= 0.5
                                if verbose:
                                    logger.info(f"  🔗 [{sid}] CORR_HALF: "
                                                f"existing {asset_tag} exposure → size /2")

                    trade_amount = round(trade_amount, 2)
                    if trade_amount != old_amt:
                        logger.info(f"  🎯 [{sid}] KELLY: ${old_amt:.2f}→${trade_amount:.2f} "
                                    f"({kelly['quarter_kelly_pct']:.1f}% QK, {kelly['confidence']})")
            except Exception:
                pass

        # Event calendar sizing
        _event_sizing_mult = 1.0
        if hasattr(self, '_event_monitor') and self._event_monitor:
            try:
                ev_alert = self._event_monitor.get_active_event()
                if ev_alert:
                    _event_sizing_mult = max(0.5, 1.0 - ev_alert.severity * 0.5)
                    if verbose:
                        logger.info(
                            f"  [{sid}] 📅 EVENT: {ev_alert.name} in {ev_alert.hours_until:.1f}h "
                            f"(sev={ev_alert.severity:.2f}) → size×{_event_sizing_mult:.2f}")
            except Exception:
                pass

        # Conviction-based sizing
        _conv_enabled = os.getenv("CONVICTION_ENABLED", "true").lower() == "true"
        if _conv_enabled and stype not in ("martingale",):
            try:
                _sig_norm = min(max(signal_score, 0.0), 1.0)
                _conf_map = {"low": 0.5, "medium": 0.75, "high": 1.0}
                _conf = _conf_map.get(
                    kelly.get("confidence", "low") if self._kelly_mode else "medium", 0.5)
                _zone_mult = 1.0
                if 0.35 <= best_ask <= 0.50:
                    _zone_mult = 1.15
                elif 0.48 <= best_ask <= 0.65:
                    _zone_mult = 0.70
                conviction = min(max(_sig_norm * _conf * _zone_mult, 0.0), 1.0)
                # Phase 74b: lifecycle conviction_min override
                _lc = ctx.get("lifecycle")
                _conv_min = _lc.conviction_min if _lc else float(os.getenv("CONVICTION_MIN", "0.3"))
                if conviction < _conv_min and not _classic_free:
                    self.skips.record("LOW_CONVICTION")
                    if verbose:
                        logger.info(
                            f"  [{sid}] ❌ CONVICTION: {conviction:.2f} < {_conv_min}"
                            f" (sig={_sig_norm:.2f} conf={_conf} zone={_zone_mult})")
                    return None
                pre_conv = trade_amount
                trade_amount = round(trade_amount * conviction, 2)
                if trade_amount < 1.0:
                    trade_amount = 1.0
                if trade_amount != pre_conv:
                    logger.info(
                        f"  🎯 [{sid}] CONVICTION: {conviction:.2f} "
                        f"${pre_conv:.2f}→${trade_amount:.2f} "
                        f"(sig={_sig_norm:.2f} conf={_conf} zone={_zone_mult:.2f})")
            except Exception as _conv_err:
                logger.debug(f"Conviction calc: {_conv_err}")

        # Event sizing reduction
        if _event_sizing_mult < 1.0:
            pre_event = trade_amount
            trade_amount = round(trade_amount * _event_sizing_mult, 2)
            if trade_amount < 1.0:
                trade_amount = 1.0
            if trade_amount != pre_event:
                logger.info(
                    f"  📅 [{sid}] EVENT_SIZE: ${pre_event:.2f}→${trade_amount:.2f} "
                    f"(×{_event_sizing_mult:.2f})")

        # Phase 70: EV Threshold check — filter or reduce size for EV- trades
        _ev_enabled = os.getenv("EV_THRESHOLD_ENABLED", "true").lower() == "true"
        if _ev_enabled:
            try:
                from calibration.ev_threshold import compute_ev
                # Use Bayesian posterior as model_wr if available, else use simple estimate
                _sig_result = ctx.get("signal_result")
                _model_wr = 0.0
                if _sig_result and hasattr(_sig_result, "bayesian_posterior") and _sig_result.bayesian_posterior > 0:
                    _model_wr = _sig_result.bayesian_posterior
                else:
                    # Rough estimate: price + signal_score * 0.05
                    _model_wr = best_ask + signal_score * 0.05
                _model_wr = max(0.01, min(0.99, _model_wr))

                # Determine if maker (from signal pipeline)
                _is_maker = ctx.get("is_maker", False)
                _fee_pct = 0.005 if _is_maker else 0.02  # maker rebate vs taker fee

                ev_result = compute_ev(
                    model_wr=_model_wr,
                    market_price=best_ask,
                    fee_pct=_fee_pct,
                    is_maker=_is_maker,
                )
                if not ev_result.should_trade and not _classic_free:
                    self.skips.record("EV_NEGATIVE")
                    if verbose:
                        logger.info(
                            f"  [{sid}] ⛔ EV_SKIP: ev={ev_result.ev_per_dollar:+.4f} "
                            f"(model_wr={_model_wr:.3f} price={best_ask:.3f})")
                    return None
                if ev_result.size_multiplier < 1.0:
                    pre_ev = trade_amount
                    trade_amount = round(trade_amount * ev_result.size_multiplier, 2)
                    if trade_amount < 1.0:
                        trade_amount = 1.0
                    if verbose:
                        logger.info(
                            f"  [{sid}] 📊 EV: {ev_result.ev_per_dollar:+.4f} "
                            f"→ size×{ev_result.size_multiplier:.2f} "
                            f"${pre_ev:.2f}→${trade_amount:.2f}")
                # Track EV stats
                _ev_tracker = getattr(self, "_ev_tracker", None)
                if _ev_tracker:
                    _ev_tracker.record(ev_result)
            except Exception as _ev_err:
                logger.debug(f"ev_threshold: {_ev_err}")

        # Canary sizing cap
        try:
            deploy_stage = getattr(s, "deploy_stage", None) or "promoted"
            if deploy_stage == "canary":
                canary_mult = float(os.getenv("CANARY_SIZE_MULT", "1.0"))
                pre_cap = trade_amount
                trade_amount = round(trade_amount * canary_mult, 2)
                if trade_amount < self.MIN_ORDER_USD:
                    trade_amount = self.MIN_ORDER_USD
                if trade_amount != pre_cap and verbose:
                    logger.info(
                        f"  🕯 [{sid}] CANARY: ${pre_cap:.2f}→${trade_amount:.2f} (×{canary_mult})")
        except Exception as _ce:
            logger.debug(f"canary cap: {_ce}")

        # Phase 74b: Lifecycle trade_amount_mult for proven winners
        _lc = ctx.get("lifecycle")
        if _lc and _lc.trade_amount_mult != 1.0:
            pre_lc = trade_amount
            trade_amount = round(trade_amount * _lc.trade_amount_mult, 2)
            if trade_amount < 1.0:
                trade_amount = 1.0
            if trade_amount != pre_lc:
                logger.info(f"  📊 [{sid}] LIFECYCLE_SIZE: ${pre_lc:.2f}→${trade_amount:.2f} "
                            f"(×{_lc.trade_amount_mult:.2f}, phase={_lc.phase})")

        # Phase 76: Capital Allocator budget check
        _ca = getattr(self, "_capital_allocator", None)
        if _ca is not None:
            try:
                _ca_result = await _ca.can_trade(s.id, trade_amount)
                if not _ca_result["allowed"] and not _classic_free:
                    self.skips.record("CAPITAL_BUDGET")
                    if verbose:
                        logger.info(f"  [{sid}] 💰 CAPITAL_BUDGET: {_ca_result['reason']}")
                    return None
                if _ca_result["max_size"] < trade_amount:
                    pre_ca = trade_amount
                    trade_amount = round(_ca_result["max_size"], 2)
                    if trade_amount < 1.0:
                        trade_amount = 1.0
                    if verbose:
                        logger.info(f"  [{sid}] 💰 CAPITAL_CAP: ${pre_ca:.2f}→${trade_amount:.2f}")
            except Exception as _cae:
                logger.debug(f"capital_allocator check: {_cae}")

        ctx.update({
            "trade_amount": trade_amount,
            "kelly": kelly,
            "signal_score": signal_score,
            "signal_reason": signal_reason,
        })
        return ctx

    # ═══════════════════════════════════════════════════════════════════
    #  STEP 6: ORDER PLACEMENT
    # ═══════════════════════════════════════════════════════════════════
    async def _eval_place_order(self, s, ctx, verbose=False):
        """Maker/taker routing, fee calc, lock, VirtualOrder append."""
        sid = ctx["sid"]
        slug = ctx["slug"]
        cached = ctx["cached"]
        direction = ctx["direction"]
        token_id = ctx["token_id"]
        best_ask = ctx["best_ask"]
        signal_score = ctx["signal_score"]
        signal_reason = ctx["signal_reason"]
        trade_amount = ctx["trade_amount"]
        market = ctx["market"]
        minutes_remaining = ctx["minutes_remaining"]
        ob_data = ctx["ob_data"]
        up = ctx["up"]
        down = ctx["down"]
        kelly = ctx["kelly"]
        micro_boost_value = ctx["micro_boost_value"]
        becker_delta_value = ctx["becker_delta_value"]

        # Sprint 5 HOTFIX v5 (2026-04-20): classic FREE-MODE order placement.
        # User explicitly asked for a "no-protection" strategy: when price is
        # at/above their trigger (e.g. 0.85), the trade MUST fire. The
        # FEE_TAIL gate below would reject exactly this condition (best_ask
        # > fee_tail_high) and is the reason classic saw trigger hits at
        # 0.92 and 0.97 but never placed orders. Classic also bypasses the
        # cross-strategy TOKEN_CAP below. Exchange minimums (MIN_SIZE,
        # MIN_SHARES) and safety primitives (STP, pending overflow cap) are
        # kept — they are exchange/sanity invariants, not "protections".
        _classic_free = self._classic_free_mode(ctx)

        # ── Maker/Taker routing (Phase 68: Adaptive Selection) ──
        # Source: A1 (72.1M trade analysis — maker +0.77-1.25% excess return)
        # Logic: Use maker when conditions favor it (wide spread, enough time,
        # moderate signal), taker when urgency is high.
        spread = cached.get("spread") or 0
        wide_spread_th = getattr(self.settings, "MAKER_WIDE_SPREAD", WIDE_SPREAD)
        fallback_mins = getattr(self.settings, "MAKER_TAKER_FALLBACK_MINS", 1.0)
        fallback_sig = getattr(self.settings, "MAKER_TAKER_FALLBACK_SIGNAL", 0.60)
        _adaptive_maker = os.getenv("ADAPTIVE_MAKER_ENABLED", "true").lower() == "true"
        is_maker = False
        force_taker = False

        if minutes_remaining is not None and minutes_remaining < fallback_mins:
            force_taker = True
            if verbose:
                logger.info(f"  ⏱  [{sid}] TAKER_FORCE: {minutes_remaining:.1f}m < {fallback_mins:.1f}m")
        elif abs(signal_score) > fallback_sig:
            force_taker = True
            if verbose:
                logger.info(f"  💪 [{sid}] TAKER_FORCE: |sig|={abs(signal_score):.2f} > {fallback_sig:.2f}")

        if not force_taker and spread and spread > wide_spread_th:
            mid = up if direction == Direction.UP else down
            if mid:
                limit = round(mid + _stagger(s.id), 4)
                is_maker = True
            else:
                limit = round(best_ask + _stagger(s.id), 4)
        elif not force_taker and _adaptive_maker:
            # Phase 68: Adaptive maker in borderline cases
            # If spread is moderate (> half of wide threshold) AND
            # we have enough time AND signal is not urgent → try maker
            _half_wide = wide_spread_th * 0.5
            _adaptive_min_mins = float(os.getenv("ADAPTIVE_MAKER_MIN_MINS", "2.0"))
            _adaptive_max_sig = float(os.getenv("ADAPTIVE_MAKER_MAX_SIGNAL", "0.45"))
            if (spread and spread > _half_wide and
                    minutes_remaining is not None and minutes_remaining > _adaptive_min_mins and
                    abs(signal_score) < _adaptive_max_sig):
                mid = up if direction == Direction.UP else down
                if mid:
                    # Place slightly better than mid for higher fill probability
                    _improve = float(os.getenv("ADAPTIVE_MAKER_IMPROVE_TICKS", "1"))
                    limit = round(mid + _stagger(s.id) + _improve * self.PRICE_TICK, 4)
                    is_maker = True
                    if verbose:
                        logger.info(f"  🎯 [{sid}] ADAPTIVE_MAKER: spread={spread:.4f} > "
                                    f"{_half_wide:.4f}, mins={minutes_remaining:.1f}, "
                                    f"|sig|={abs(signal_score):.2f}")
                else:
                    limit = round(best_ask + _stagger(s.id), 4)
            else:
                limit = round(best_ask + _stagger(s.id), 4)
        else:
            limit = round(best_ask + _stagger(s.id), 4)

        # Optimism Tax (NO-side maker edge)
        _optax_enabled = os.getenv("OPTIMISM_TAX_ENABLED", "true").lower() == "true"
        _optax_tick_bonus = int(os.getenv("OPTIMISM_TAX_TICKS", "1"))
        if _optax_enabled and is_maker and direction == Direction.DOWN:
            limit = round(limit + _optax_tick_bonus * self.PRICE_TICK, 4)
            signal_reason += " | optax_no"
            if verbose:
                logger.info(f"  [{sid}] 💰 OPTIMISM_TAX: NO-side maker → limit+{_optax_tick_bonus}tick ({limit:.4f})")

        # Sprint 5 HOTFIX v6 (2026-04-20): classic TAKER fill ceiling.
        # User observed "trade açık değil" (no open trade) — root cause
        # traced to engine_fills._check_pending rejecting fill when
        # live_ask > o.limit_price. With limit = best_ask + stagger (~0.900)
        # and subsequent best_ask rise to 0.95, the order starves forever.
        # Fix: for classic free-mode TAKER, raise the limit to a ceiling
        # (default 0.99) so the fill check (engine_fills.py:202) always
        # passes and the spread-based fill path (engine_fills.py:271)
        # cleanly resolves to live_ask + slippage, not the ceiling itself.
        # Fee/signal_price stay pinned to best_ask → no fee inflation.
        # Opt-out: CLASSIC_TAKER_LIMIT_CEIL=0 disables (falls back to v5).
        if _classic_free and not is_maker:
            try:
                _classic_ceil = float(
                    os.getenv("CLASSIC_TAKER_LIMIT_CEIL", "0.99"))
            except ValueError:
                _classic_ceil = 0.99
            if _classic_ceil > 0 and _classic_ceil > limit:
                if verbose:
                    logger.info(
                        f"  🆓 [{sid}] CLASSIC_TAKER_CEIL: "
                        f"limit {limit:.4f} -> {_classic_ceil:.4f}")
                limit = _classic_ceil

        # Exchange constraints
        limit = self._snap_to_tick(limit)
        if trade_amount < self.MIN_ORDER_USD:
            self.skips.record("MIN_SIZE")
            if verbose:
                logger.info(f"  [{sid}] ❌ MIN_SIZE: ${trade_amount:.2f} < ${self.MIN_ORDER_USD}")
            return

        # MIN_SHARES check
        shares_estimate = trade_amount / limit if limit > 0 else 0.0
        if shares_estimate < self.MIN_ORDER_SHARES:
            self.skips.record("MIN_SHARES")
            if verbose:
                logger.info(f"  [{sid}] ❌ MIN_SHARES: ${trade_amount:.2f}/{limit:.3f}"
                            f" = {shares_estimate:.2f} < {self.MIN_ORDER_SHARES}")
            return

        # Fee tail-zone gate
        # Sprint 5 HOTFIX v5: classic stype bypasses this gate. User's classic
        # strategy trigger is typically in the tail zone (>=0.85 for cheap
        # YES) — FEE_TAIL was the exact reason classic observed price hits
        # but never fired. Opt back in with CLASSIC_RESPECT_FEE_TAIL=true.
        _classic_respect_fee_tail = (
            os.getenv("CLASSIC_RESPECT_FEE_TAIL", "false").lower() == "true"
        )
        _fee_tail_bypass = _classic_free and not _classic_respect_fee_tail
        fee_tail_low = getattr(self.settings, "FEE_TAIL_LOW", 0.15)
        fee_tail_high = getattr(self.settings, "FEE_TAIL_HIGH", 0.85)
        if (fee_tail_low > 0 and not _fee_tail_bypass
                and (best_ask < fee_tail_low or best_ask > fee_tail_high)):
            self.skips.record("FEE_TAIL")
            if verbose:
                logger.info(f"  [{sid}] ❌ FEE_TAIL: price={best_ask:.3f} outside [{fee_tail_low:.2f},{fee_tail_high:.2f}]")
            return
        if _fee_tail_bypass and verbose and (best_ask < fee_tail_low or best_ask > fee_tail_high):
            logger.info(f"  [{sid}] 🆓 FEE_TAIL bypass (classic): price={best_ask:.3f} outside [{fee_tail_low:.2f},{fee_tail_high:.2f}]")

        # Fee calculation
        market_category = market.get("category") if isinstance(market, dict) else None
        if is_maker:
            fee = 0.0
        else:
            fee = self._taker_fee(best_ask, trade_amount, market_category)

        # Maker queue depth snapshot
        queue_ahead_usd = 0.0
        if is_maker:
            try:
                ob_q = await asyncio.wait_for(
                    self.client.get_orderbook(token_id), timeout=2.0)
                queue_ahead_usd = self._compute_queue_ahead_usd(ob_q, limit, side="BUY")
            except Exception:
                queue_ahead_usd = 0.0

        # REST submit latency
        await self._rest_latency_sleep()

        # ── Append under lock ──
        async with self._trade_lock:
            if any(o.strategy_id == s.id and o.slug == slug for o in self._pending):
                return
            # Self-trade prevention
            if getattr(self.settings, "SELF_TRADE_PREVENTION", True):
                for ex in self._pending:
                    if (ex.wallet_id == s.wallet_id
                            and ex.token_id == token_id
                            and ex.direction != direction.value):
                        self.skips.record("STP")
                        if verbose:
                            logger.info(f"  [{sid}] ❌ STP: opposite pending on token")
                        return
            # Cross-strategy token exposure cap
            # Sprint 5 HOTFIX v5: classic bypasses cross-strategy TOKEN_CAP.
            # Opt back in with CLASSIC_RESPECT_TOKEN_CAP=true.
            _classic_respect_token_cap = (
                os.getenv("CLASSIC_RESPECT_TOKEN_CAP", "false").lower() == "true"
            )
            max_token_exp = getattr(self.settings, "MAX_TOKEN_EXPOSURE_USD", 50.0)
            if max_token_exp > 0 and not (_classic_free and not _classic_respect_token_cap):
                token_exposure = sum(
                    ex.amount for ex in self._pending
                    if ex.wallet_id == s.wallet_id and ex.token_id == token_id)
                if token_exposure + trade_amount > max_token_exp:
                    self.skips.record("TOKEN_CAP")
                    if verbose:
                        logger.info(f"  [{sid}] ❌ TOKEN_CAP: ${token_exposure + trade_amount:.2f} > ${max_token_exp:.0f}")
                    return
            # Safety cap
            if len(self._pending) >= 50:
                self._pending.pop(0)
                logger.warning("⚠️ Pending overflow — dropped oldest order")

            # Build reasoning JSON
            _reasoning = None
            if os.getenv("TRADE_REASONING_LOG", "true").lower() == "true":
                try:
                    import json as _json
                    _rdata = {
                        "signals": {
                            "composite": round(signal_score, 4),
                            "reason": signal_reason[:200],
                        },
                        "market_context": {
                            "price": round(best_ask, 4),
                            "spread": round(spread, 4) if spread else None,
                            "is_maker": is_maker,
                        },
                        "becker_delta": round(becker_delta_value, 4) if abs(becker_delta_value) > 1e-4 else None,
                    }
                    try:
                        _rdata["conviction"] = round(ctx.get("_conviction", 0), 3)
                    except Exception:
                        _rdata["conviction"] = None
                    try:
                        _rdata["kelly"] = {
                            "size": kelly.get("size", 0),
                            "confidence": kelly.get("confidence"),
                            "qk_pct": kelly.get("quarter_kelly_pct", 0),
                        }
                    except Exception:
                        _rdata["kelly"] = None
                    _reasoning = _json.dumps(_rdata, default=str)
                except Exception:
                    pass

            self._pending.append(VirtualOrder(
                strategy_id=s.id, slug=slug, token_id=token_id,
                direction=direction.value, limit_price=limit,
                amount=trade_amount, fee=fee, is_maker=is_maker,
                signal_score=signal_score,
                signal_price=best_ask,
                queue_ahead_usd=queue_ahead_usd,
                cum_traded_at_price_usd=0.0,
                placement_ts_ms=int(time.time() * 1000),
                category=market_category,
                wallet_id=s.wallet_id, user_id=s.user_id,
                sl_pct=s.stop_loss_percent, sl_odds=s.stop_loss_odds,
                tp_pct=s.take_profit_percent, tp_odds=s.take_profit_odds,
                threshold=s.odds_threshold,
                reasoning_json=_reasoning))

        # Sprint 2 S2-01: Log trade OPEN decision (with regime)
        try:
            from core.trade_journal import log_decision_open
            _regime = getattr(self.regime, "regime", "unknown") if hasattr(self, "regime") else "unknown"
            log_decision_open(s.id, slug, direction.value, signal_score,
                              signal_reason, limit, trade_amount, fee, regime=_regime)
        except Exception:
            pass

        # Phase 47a: micro boost tracker
        if getattr(self, "micro_weight", None) is not None and abs(micro_boost_value) > 1e-4:
            try:
                self.micro_weight.record_open(
                    order_key=f"{s.id}:{slug}", asset=ctx["asset"].upper(),
                    signed_boost=micro_boost_value)
            except Exception as _mwo:
                logger.debug(f"micro_weight.record_open: {_mwo}")
        # Phase 48: Becker δ tracker
        if getattr(self, "becker_weight", None) is not None and abs(becker_delta_value) > 1e-4:
            try:
                self.becker_weight.record_open(
                    order_key=f"{s.id}:{slug}", asset=ctx["asset"].upper(),
                    signed_delta=becker_delta_value)
            except Exception as _bwo:
                logger.debug(f"becker_weight.record_open: {_bwo}")

        mode = "MAKER" if is_maker else "TAKER"
        fee_pct = (fee / trade_amount * 100) if trade_amount > 0 else 0
        self._last_trade_slug[s.id] = slug
        q_tag = f" q=${queue_ahead_usd:.0f}" if is_maker and queue_ahead_usd > 0 else ""
        imb = self._compute_ob_imbalance(ob_data) if ob_data else 0.0
        imb_tag = f" imb={imb:+.2f}" if abs(imb) >= 0.05 else ""
        logger.info(
            f"  📝 [{sid}] {mode} PENDING {direction.value.upper()} {slug} "
            f"limit={limit:.4f} sig={signal_score:+.3f} "
            f"${trade_amount:.2f} fee=${fee:.4f}({fee_pct:.1f}%)"
            f"{q_tag}{imb_tag}"
        )