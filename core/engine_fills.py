"""
Phase 51 P51-02 — Fills mixin for TradingEngine
================================================
Houses pending-order management, maker queue accounting, and the order-fill
pipeline carved out of the original monolithic ``core/engine.py``. Methods
reference ``self.*`` state and class constants (``PRICE_TICK``,
``PRICE_TICK_TOL``, ``PARTIAL_FILL_MIN_USD``) that live on the concrete
:class:`~core.engine.TradingEngine` — the mixin itself is stateless and is
mixed in via multiple inheritance.

Public runtime behaviour is unchanged; every method below is a verbatim
copy of the original engine.py body.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from typing import Optional

import aiosqlite  # T1.4 Faz 1: narrow DB exception handling

from core.engine_support import _slug_end
# Phase 65: fees.py v1 removed — only v2 active
from core.fees_v2 import polymarket_taker_fee_v2
from core.trade_journal import log_entry
from db.models import Direction, Execution, ExecutionStatus

logger = logging.getLogger("polypaper.core.engine")


class EngineFillsMixin:
    """Pending-order, maker-queue and fill-pipeline methods for TradingEngine."""

    @classmethod
    def _snap_to_tick(cls, price: float) -> float:
        """Round price to nearest valid Polymarket tick ($0.01).
        Clamps to [0.01, 0.99] — extreme prices that the CLOB also rejects."""
        if price is None:
            return 0.0
        snapped = round(round(price / cls.PRICE_TICK) * cls.PRICE_TICK, 2)
        if snapped < 0.01:
            snapped = 0.01
        elif snapped > 0.99:
            snapped = 0.99
        return snapped

    def _taker_fee(self, price: float, amount_usd: float,
                   category: str | None = None) -> float:
        """Phase 65: Always uses v2 fee model (Mart 2026 linear).
        Legacy v1 quadratic model removed."""
        return polymarket_taker_fee_v2(price, amount_usd, category=category)

    @staticmethod
    def _compute_ob_imbalance(orderbook: dict) -> float:
        """Phase 42c: Top-of-book imbalance ∈ [-1, 1].

        +1 = all bid pressure, -1 = all ask pressure, 0 = balanced.
        Uses top 3 levels each side, weighted by price proximity.
        Used as an observability metric on PENDING log lines and
        available to future signal-fusion plugins.
        """
        if not orderbook:
            return 0.0
        bids = (orderbook.get("bids") or [])[:3]
        asks = (orderbook.get("asks") or [])[:3]
        bid_sum = 0.0
        for lvl in bids:
            try:
                bid_sum += float(lvl.get("size", 0)) * float(lvl.get("price", 0))
            except (TypeError, ValueError):
                # T1.4 Faz 1: malformed level (None/non-numeric); skip
                continue
        ask_sum = 0.0
        for lvl in asks:
            try:
                ask_sum += float(lvl.get("size", 0)) * float(lvl.get("price", 0))
            except (TypeError, ValueError):
                # T1.4 Faz 1: malformed level (None/non-numeric); skip
                continue
        total = bid_sum + ask_sum
        if total <= 0:
            return 0.0
        return (bid_sum - ask_sum) / total

    # _becker_delta removed 2026-04-28 (Heddas direktifi: Becker tam silme)

    @staticmethod
    def _compute_queue_ahead_usd(orderbook: dict, limit_price: float,
                                  side: str = "BUY") -> float:
        """USD already resting at or better than `limit_price`.
        For a maker BUY at p, queue ahead = total bid USD with price >= p
        (FIFO across the touched price levels). The simulator treats this
        as the volume that must trade through before our order fills."""
        if not orderbook:
            return 0.0
        if side == "BUY":
            levels = orderbook.get("bids", []) or []
            ahead = 0.0
            for px, sz in levels:
                if px >= limit_price - 1e-9:
                    ahead += float(px) * float(sz)
                else:
                    break  # bids are sorted high→low
            return round(ahead, 4)
        else:
            levels = orderbook.get("asks", []) or []
            ahead = 0.0
            for px, sz in levels:
                if px <= limit_price + 1e-9:
                    ahead += float(px) * float(sz)
                else:
                    break
            return round(ahead, 4)

    def on_real_trade(self, token_id: str, price: float, size: float,
                       side: str, ts_ms: int):
        """Phase 39 (P1.2): Called by MarketRecorder for every WS
        last_trade_price event. Advances maker-queue counters on any
        pending maker order whose price level was touched.

        Polymarket trade `side` is the taker side:
          taker SELL → hits the bid stack (helps our maker BUY orders)
          taker BUY  → hits the ask stack (helps our maker SELL orders)
        """
        try:
            traded_usd = float(price) * float(size)
            for o in self._pending:
                if not o.is_maker or o.token_id != token_id:
                    continue
                # Maker BUY: only taker SELLs at price >= our limit help us
                if side == "SELL" and price >= o.limit_price - self.PRICE_TICK_TOL:
                    o.cum_traded_at_price_usd += traded_usd
                elif side == "BUY" and price <= o.limit_price + self.PRICE_TICK_TOL:
                    # Future symmetry for SELL maker orders
                    o.cum_traded_at_price_usd += traded_usd

            # Phase 60 whale_signal + cascade_detector integration removed in
            # T1.3 Commit 1 (ghost module purge, 2026-04-20). Attributes
            # `_whale_signal` and `_cascade_detector` are never set on the
            # engine any more, so those branches were dead code. Kept comment
            # for history — if the signals come back, mount new trackers on
            # the engine and re-add the record() calls here.
        except (TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 1: malformed trade (non-numeric price/size) or missing
            # tick-tolerance attr. Keep silent on debug-level to avoid WS
            # spam; real issues surface via cum_traded_at_price_usd stalling.
            logger.debug(f"on_real_trade: {type(e).__name__}: {e}")

    async def _rest_latency_sleep(self):
        """Phase 39 (P1.3): Sleep for a gaussian-jittered REST round-trip
        before paper-trade order create/cancel. Mirrors real Polymarket
        REST latency so paper fills don't get a free 0ms head-start.

        ⚠ HEURISTIC, NOT EMPIRICALLY MEASURED. Defaults
        (REST_LATENCY_MS=200, REST_LATENCY_JITTER_MS=80) are plausible
        regional medians chosen at Phase 39 — pending Epic 4 T4.7 Faz B
        live telemetry calibration via `core/observability/rest_timing.py`.
        Override via ENV when running fairness-sensitive backtests."""
        try:
            mean = max(0, int(self.settings.REST_LATENCY_MS))
            sigma = max(0, int(self.settings.REST_LATENCY_JITTER_MS))
            if mean == 0 and sigma == 0:
                return
            ms = random.gauss(mean, sigma)
            if ms < 0:
                ms = 0
            await asyncio.sleep(ms / 1000.0)
        except (TypeError, ValueError):
            # T1.4 Faz 1: non-numeric settings value (misconfigured .env).
            # Silent pass matches prior behaviour — REST latency is a fairness
            # simulator, not a correctness gate.
            pass

    async def _check_pending(self):
        if not self._pending:
            return
        filled, expired, cancelled = [], [], []  # filled: (o, fp, usd)
        now = datetime.now(timezone.utc)
        # Phase 40b: TIF — auto-cancel makers older than MAKER_TIF_SECONDS
        tif = getattr(self.settings, "MAKER_TIF_SECONDS", 300)
        now_ms = int(time.time() * 1000)
        for o in self._pending:
            end = _slug_end(o.slug)
            if end and now > end:
                expired.append(o)
                continue
            # TIF expiry — only applies to maker orders (takers fill ~immediately)
            if (o.is_maker and tif > 0
                    and (now_ms - getattr(o, "placement_ts_ms", now_ms)) >= tif * 1000):
                cancelled.append(o)
                continue

            try:
                cur = await self.client.get_live_price(o.token_id, "BUY")
                if not cur:
                    continue
                if cur > o.limit_price:
                    # Sprint 5 HOTFIX v6 (2026-04-20): stuck TAKER auto-cancel.
                    # Original comment here said "takers fill ~immediately" and
                    # relied on it by only applying TIF to makers. That
                    # assumption breaks when a TAKER limit was snapped below
                    # market (e.g. classic free-mode placed limit=0.90 then
                    # best_ask moved to 0.95). Such orders starve forever and
                    # show up as pend=N open=0 in heartbeats. Cancel after
                    # TAKER_STUCK_TIMEOUT_SEC (default 120s) so the parent
                    # strategy can repost at a fresh limit on the next cycle.
                    # Opt-out: TAKER_STUCK_TIMEOUT_SEC=0 disables.
                    try:
                        _stuck_tout = float(
                            os.getenv("TAKER_STUCK_TIMEOUT_SEC", "120"))
                    except (TypeError, ValueError):
                        _stuck_tout = 120.0
                    if (not o.is_maker and _stuck_tout > 0
                            and (now_ms - getattr(o, "placement_ts_ms", now_ms))
                            >= _stuck_tout * 1000):
                        cancelled.append(o)
                    continue

                odds = self.scanner.get_current_odds(o.slug)
                has_liq = (odds.get("has_liquidity", False)) if odds else False
                if not has_liq:
                    continue

                # Phase 39 (P1.2): Maker queue position gate.
                # A maker BUY only fills once enough volume has traded
                # through bids at >= our limit to clear the queue ahead.
                # If queue_ahead_usd is 0 (no orderbook at placement, or we
                # were the only resting bid), this is a no-op and behavior
                # matches the legacy fill path.
                if o.is_maker and o.queue_ahead_usd > 0:
                    if o.cum_traded_at_price_usd < o.queue_ahead_usd:
                        # still queued — don't fill yet
                        continue

                # ══ Phase 18: VWAP from real orderbook depth ══
                # Phase 21: 3s timeout to prevent event loop blocking
                try:
                    ob = await asyncio.wait_for(
                        self.client.get_orderbook(o.token_id), timeout=3.0)
                except asyncio.TimeoutError:
                    ob = None
                if ob and ob.get("asks"):
                    vwap_result = self.client.calculate_vwap_fill(ob, "BUY", o.amount)
                    if vwap_result and not vwap_result["partial"]:
                        # Full fill possible — use VWAP as fill price
                        fill_price = vwap_result["vwap"]
                        if fill_price <= o.limit_price:
                            depth = vwap_result["depth_usd"]
                            lvls = vwap_result["levels_consumed"]
                            # Phase 38c: signal→fill slippage
                            sig_slip = self._compute_slippage(o, fill_price)
                            logger.info(
                                f"  📊 VWAP fill: {fill_price:.4f} ({lvls}lvl, "
                                f"depth=${depth:.0f}) slip={sig_slip:+.2f}%")
                            filled.append((o, round(fill_price, 4), o.amount))
                        continue
                    elif vwap_result and vwap_result["partial"]:
                        # Phase 38c: PARTIAL FILL — execute what depth allows
                        avail_usd = vwap_result["depth_usd"]
                        if avail_usd < self.PARTIAL_FILL_MIN_USD:
                            logger.debug(
                                f"  [{o.strategy_id[:8]}] Depth<${self.PARTIAL_FILL_MIN_USD} "
                                f"(${avail_usd:.2f}) — skip")
                            continue
                        # Re-compute VWAP for the actual available amount
                        partial_result = self.client.calculate_vwap_fill(
                            ob, "BUY", avail_usd)
                        if not partial_result:
                            continue
                        fill_price = partial_result["vwap"]
                        if fill_price > o.limit_price:
                            continue
                        sig_slip = self._compute_slippage(o, fill_price)
                        logger.info(
                            f"  ⚠️ PARTIAL fill: {fill_price:.4f} "
                            f"${avail_usd:.2f}/${o.amount:.2f} "
                            f"({avail_usd/o.amount*100:.0f}%) slip={sig_slip:+.2f}%")
                        filled.append((o, round(fill_price, 4), round(avail_usd, 2)))
                        continue

                # Fallback: spread-based fill (when orderbook unavailable)
                spread = (odds.get("spread") or 0.01) if odds else 0.01
                # Phase 34: Add realistic slippage (0.2% adverse)
                slip = cur * 0.002 if not o.is_maker else 0
                fill_price = min(cur + spread * 0.5 + slip, o.limit_price) if not o.is_maker else cur
                sig_slip = self._compute_slippage(o, fill_price)
                if sig_slip != 0:
                    logger.debug(f"  📊 fallback fill slip={sig_slip:+.2f}%")
                filled.append((o, round(fill_price, 4), o.amount))
            except Exception as e:  # noqa: BLE001 - T1.4 Faz 1: broad on purpose
                # Per-order fill path pulls from many sources: CLOB REST,
                # WS orderbook, scanner odds, settings. A single order's
                # failure must not abort the whole cycle. Surface the type
                # so silent drops get noticed in logs.
                # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                logger.warning(
                    f"  [{o.strategy_id[:8]}] check_pending order {o.slug}: "
                    f"{type(e).__name__}: {e}")
        for item in filled:
            # Support both legacy 2-tuples and new 3-tuples (defensive)
            if len(item) == 3:
                o, fp, fill_usd = item
            else:
                o, fp = item
                fill_usd = o.amount
            self._pending.remove(o)
            await self._fill(o, fp, fill_amount_usd=fill_usd)
        for o in expired:
            self._pending.remove(o)
        # Phase 40b: TIF cancellations — pay the same REST latency cost as
        # placement so the simulator pays for cancel→repost cycles too.
        for o in cancelled:
            try:
                self._pending.remove(o)
                self._cancel_count += 1
                # Sprint 5 HOTFIX v6: distinguish maker TIF vs taker-stuck
                _mode = "maker" if o.is_maker else "taker-stuck"
                logger.info(
                    f"  🚫 [{o.strategy_id[:8]}] CANCEL {_mode} {o.slug} "
                    f"limit={o.limit_price:.4f} age={(now_ms - o.placement_ts_ms)//1000}s")
                await self._rest_latency_sleep()
            except ValueError:
                # T1.4 Faz 1: self._pending.remove(o) raises ValueError when
                # already removed (double-cancel race). Idempotent; ignore.
                pass

    async def cancel_pending(self, strategy_id: str, slug: str = None) -> int:
        """Phase 40b: Manually cancel pending orders for a strategy.
        Returns number of orders cancelled. Pays REST latency per cancel."""
        targets = []
        async with self._trade_lock:
            for o in list(self._pending):
                if o.strategy_id != strategy_id:
                    continue
                if slug and o.slug != slug:
                    continue
                targets.append(o)
            for o in targets:
                try:
                    self._pending.remove(o)
                except ValueError:
                    pass
        for _o in targets:
            self._cancel_count += 1
            await self._rest_latency_sleep()
        if targets:
            logger.info(f"  🚫 cancel_pending({strategy_id[:8]}, {slug or '*'}) → {len(targets)}")
        return len(targets)

    def _compute_slippage(self, o, fill_price: float) -> float:
        """Return signal→fill slippage as signed percent (adverse = positive)."""
        sig_px = getattr(o, "signal_price", 0.0) or 0.0
        if sig_px <= 0:
            return 0.0
        return (fill_price - sig_px) / sig_px * 100.0

    async def _fill(self, o, fill_price, fill_amount_usd=None):
        # Phase 38c: fill_amount_usd defaults to full order amount; partial
        # fills pass the actual depth-limited amount. Fee is recomputed against
        # the actual filled notional so a $1 fill on a $5 order doesn't carry
        # the fee of a $5 trade.
        if fill_amount_usd is None:
            fill_amount_usd = o.amount
        fill_amount_usd = min(fill_amount_usd, o.amount)
        shares = round(fill_amount_usd / fill_price, 6)

        # Recompute fee for actual filled notional (Phase 38c)
        if o.is_maker:
            actual_fee = 0.0
        else:
            # Phase 43a: category-aware fee router (v1 legacy / v2 Mart 2026)
            category = getattr(o, "category", None)
            actual_fee = self._taker_fee(fill_price, fill_amount_usd, category)

        # ══ F-02: Atomic wallet balance deduction (actual filled amount) ══
        result = await self.db.atomic_deduct_balance(o.wallet_id, fill_amount_usd)
        if not result:
            # Phase 54 P0-06: log warning + record skip (order already removed from _pending)
            logger.warning(
                f"  [{o.strategy_id[:8]}] ❌ INSUFFICIENT_BALANCE for fill "
                f"${fill_amount_usd:.2f} on {o.slug} — trade dropped")
            self.skips.record("BALANCE_FAIL")
            return

        # T4.10 (2026-04-24): regime_at_entry write path populate.
        # self.regime is RegimeClassifier set in core/engine.py:142.
        # Snapshot regime str now (not after settle, regime can drift).
        # Empty/missing -> None (DB schema esnek).
        _regime_at_entry = None
        try:
            _regime_obj = getattr(self, "regime", None)
            if _regime_obj is not None:
                _regime_at_entry = getattr(_regime_obj, "regime", None)
        except (AttributeError, TypeError):
            _regime_at_entry = None

        ex = Execution(
            user_id=o.user_id, wallet_id=o.wallet_id, strategy_id=o.strategy_id,
            event_slug=o.slug, market_token_id=o.token_id,
            direction=Direction(o.direction), trade_amount=fill_amount_usd,
            fee_amount=actual_fee, odds_threshold=o.threshold,
            execution_price=fill_price, status=ExecutionStatus.BET_PLACED,
            is_maker=1 if o.is_maker else 0,  # Phase 79 BUG-02: populate is_maker
            signal_score=o.signal_score or 0.0,  # Phase 79 BUG-03: store original signal score
            stop_loss_percent=o.sl_pct, stop_loss_odds=o.sl_odds,
            take_profit_percent=o.tp_pct, take_profit_odds=o.tp_odds,
            regime_at_entry=_regime_at_entry)  # T4.10
        await self.db.create_execution(ex)
        # Phase 79 BUG-03: Persist decision reasoning when trade is placed
        try:
            _explainer = getattr(self, "_explainer", None)
            if _explainer is not None:
                _chain = getattr(o, "_reasoning_chain", None)
                if _chain is not None:
                    _chain.direction = o.direction
                    _chain.final_score = o.signal_score
                    _chain.trade_amount = fill_amount_usd
                    _chain.decision = "trade"
                    await _explainer.persist(_chain, ex.id)
        except (aiosqlite.Error, AttributeError) as _rce:
            # T1.4 Faz 1: explainer.persist hits DB; attr set on _chain may
            # AttributeError if reasoning class shape drifts. Non-critical
            # telemetry — log and move on.
            logger.debug(f"reasoning persist at fill: {type(_rce).__name__}: {_rce}")
        # Phase 47f.9 P4#18 — persist realized_slippage (signal → fill)
        try:
            slip_pct = self._compute_slippage(o, fill_price)
            await self.db.conn.execute(
                "UPDATE executions SET realized_slippage=? WHERE id=?",
                (slip_pct, ex.id))
            await self.db.conn.commit()
        except aiosqlite.Error as _se:
            # T1.4 Faz 1: pure DB write — narrow to DB errors. Telemetry only.
            logger.debug(f"slippage persist: {type(_se).__name__}: {_se}")
        # Phase 59: persist trade reasoning JSON alongside the execution
        try:
            _rj = getattr(o, "reasoning_json", None)
            if _rj:
                await self.db.conn.execute(
                    "UPDATE executions SET reasoning_json=? WHERE id=?",
                    (_rj, ex.id))
                await self.db.conn.commit()
        except aiosqlite.Error as _rje:
            # T1.4 Faz 1: pure DB write — narrow to DB errors. Telemetry only.
            logger.debug(f"reasoning_json persist: {type(_rje).__name__}: {_rje}")
        self._open_positions.add(f"{o.strategy_id}:{o.slug}")
        self.risk.record_trade_opened(fill_amount_usd, o.slug, strategy_id=o.strategy_id)
        # Phase 76 capital_allocator integration removed in T1.3 (ghost module
        # purge, 2026-04-20). Attribute `_capital_allocator` is never set on
        # the engine any more, so the reserve() branch was dead code. Mirror
        # of the release() deletion in engine_settlement.py. Kept comment for
        # history.
        log_entry(o.slug, o.direction, fill_price, fill_amount_usd, shares,
                  actual_fee, o.strategy_id, o.token_id)
        mode = "MAKER" if o.is_maker else "TAKER"
        partial_tag = " (PARTIAL)" if fill_amount_usd < o.amount else ""
        logger.info(f"  🎯 {mode} FILL{partial_tag}! {o.direction.upper()} {o.slug} "
                     f"limit={o.limit_price:.4f} fill={fill_price:.4f} "
                     f"sig={o.signal_score:+.2f} | ${fill_amount_usd:.2f}"
                     f"{'/$'+str(o.amount) if fill_amount_usd < o.amount else ''}")

        # Phase 34: Mirror to live trader (shadow mode)
        if self.live.is_enabled():
            try:
                label_row = await self.db.conn.execute_fetchall(
                    "SELECT label FROM strategies WHERE id=?", (o.strategy_id,))
                label = label_row[0][0] if label_row else ""
                await self.live.maybe_mirror(
                    strategy_label=label, signal_score=o.signal_score,
                    direction=o.direction, token_id=o.token_id,
                    odds=fill_price, slug=o.slug)
            except Exception as e:  # noqa: BLE001 - T1.4 Faz 1: CLOB + DB + telegram wrap
                # live.maybe_mirror chains: DB label lookup, CLOB REST,
                # telegram notify. Broad on purpose; emit full traceback so
                # mainnet shadow mirror issues surface cleanly.
                # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                logger.exception(f"Live mirror failed [{type(e).__name__}]: {e}")

        parts = o.slug.split("-")
        asset = parts[0].upper() if parts else "?"
        tf = parts[2] if len(parts) > 2 else "?"
        notif_partial = f" ({fill_amount_usd/o.amount*100:.0f}%)" if fill_amount_usd < o.amount else ""
        # Phase 79b: Enriched fill notification
        _dir_emoji = "📈" if o.direction.lower() == "up" else "📉"
        _slip_pct = ((fill_price - o.limit_price) / o.limit_price * 100) if o.limit_price > 0 else 0
        _slip_str = f"slip={_slip_pct:+.1f}%" if abs(_slip_pct) >= 0.5 else "no slip"
        # Get strategy label
        _label = ""
        try:
            _lbl_row = await self.db.conn.execute_fetchall(
                "SELECT label FROM strategies WHERE id=?", (o.strategy_id,))
            _label = _lbl_row[0][0] if _lbl_row else o.strategy_id[:8]
        except aiosqlite.Error:
            # T1.4 Faz 1: pure DB query; fallback to short strategy id if
            # strategies row is missing or the conn is temporarily locked.
            _label = o.strategy_id[:8]
        await self._notify(o.user_id,
            f"{_dir_emoji} <b>{mode} Fill{notif_partial}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Strateji: <b>{_label}</b>\n"
            f"Yon: <b>{o.direction.upper()}</b> | {asset}/{tf}\n"
            f"Limit: {o.limit_price:.4f} → Fill: <b>{fill_price:.4f}</b> ({_slip_str})\n"
            f"Tutar: ${fill_amount_usd:.2f} | {shares:.2f} shares\n"
            f"Fee: ${actual_fee:.4f} ({actual_fee/fill_amount_usd*100:.1f}%)\n"
            f"Sinyal: {o.signal_score:+.2f}\n"
            f"<code>{o.slug}</code>")
