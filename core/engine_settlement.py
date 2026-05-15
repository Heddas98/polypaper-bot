"""
Phase 51 P51-02 — Settlement mixin for TradingEngine
====================================================
Houses the settlement / exit / close / notify cluster carved out of the
original monolithic ``core/engine.py``. Methods reference ``self.*`` state
that lives on the concrete :class:`~core.engine.TradingEngine` — the mixin
itself is stateless and is mixed in via multiple inheritance.

Public runtime behaviour is unchanged; every method below is a verbatim
copy of the original engine.py body.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite  # T1.4 Faz 1: narrow DB exception handling

if TYPE_CHECKING:
    # P1-07 Round-3 (2026-05-11): static attribute hints for the mixin.
    # These are provided at runtime by ``core.engine.TradingEngine``; the
    # imports stay TYPE_CHECKING-only to avoid the engine → mixin import
    # cycle. mypy sees the attribute set on the mixin class body below.
    from core.live_trader import LiveTrader
    from core.risk_manager import RiskManager
    from core.strategy_selector import StrategySelector
    from data.database import Database

try:
    from telegram.error import TelegramError
except ImportError:  # pragma: no cover - python-telegram-bot is a hard dep

    class TelegramError(Exception):  # type: ignore[no-redef]
        ...


from core.bg_task import safe_create_task  # Phase 82e Sprint 2.1
from core.fees_v2 import (
    polymarket_maker_rebate,
    polymarket_taker_fee_v2,
)
from core.slug_utils import infer_asset_from_slug, infer_tf_from_slug
from core.trade_journal import log_exit, log_settlement

logger = logging.getLogger("polypaper.core.engine")


class EngineSettlementMixin:
    """Exit / settle / close / notify methods for TradingEngine."""

    # P1-07 Round-3 (2026-05-11): static attribute hints — TradingEngine
    # provides these at runtime via multiple inheritance + __init__.
    # Declarations only, no assignment (mypy reads, runtime ignores).
    if TYPE_CHECKING:
        db: Database
        risk: RiskManager
        selector: StrategySelector
        live: LiveTrader
        micro_weight: Any  # MicroWeightTracker | None — TYPE_CHECKING-only
        _open_positions: set[str]
        _settled_slugs: dict[str, datetime]
        _cooldowns: dict[str, datetime]

        def _pop_max_moves(self, slug: str) -> tuple[float, float] | None: ...

    def _get_settle_lock(self, slug: str) -> asyncio.Lock:
        """Phase 54 P0-05: per-market lock to prevent settlement race conditions."""
        if not hasattr(self, "_settle_locks"):
            self._settle_locks: dict[str, asyncio.Lock] = {}
        if slug not in self._settle_locks:
            self._settle_locks[slug] = asyncio.Lock()
        return self._settle_locks[slug]

    async def _exit(self, row, shares, price, reason):
        # Phase 54 P0-05: per-market lock prevents race with concurrent settlement
        slug = row.get("event_slug", "")
        async with self._get_settle_lock(slug):
            # Phase 34: REALISTIC exit — taker fee on exit + slippage
            entry_fee = row["fee_amount"] or 0
            # Exit fee: taker fee at exit price
            # Phase 43a: route exit fee through v2 model (crypto default — row
            # doesn't carry category; scanner owns only crypto Up/Down for now).
            exit_fee = self._taker_fee(price, row["trade_amount"], "crypto")
            # Phase 52: Dynamic slippage from realized_slippage rolling average.
            # Falls back to 0.3% if no data yet.
            slip_pct = await self._get_avg_slippage()
            slip = row["trade_amount"] * slip_pct
            payout = round(shares * price, 4)
            pnl = round(payout - row["trade_amount"] - entry_fee - exit_fee - slip, 4)
            await self._close(row, pnl, payout, reason)
        log_exit(
            row["event_slug"], row["direction"], reason, row["execution_price"], price, pnl, payout
        )
        if exit_fee > 0.001 or slip > 0.001:
            logger.debug(f"  💸 Exit costs: fee={exit_fee:.4f} slip={slip:.4f}")
        # Phase 27: AI per-trade analysis
        # Phase 82e Sprint 2.1: guarded (AI death = no learning from trades)
        safe_create_task(self._ai_trade_analysis(row, pnl, reason), name="ai_trade_analysis_exit")
        # Sprint 5 HOTFIX v5 (2026-04-20): classic exit ping.
        # Same as resolution notify but fires on early exit paths (TP/SL,
        # forced close, etc.) so user sees PnL regardless of whether the
        # position settled to market close or exited early.
        if os.getenv("CLASSIC_NOTIFY_RESOLUTION", "true").lower() != "false":
            safe_create_task(
                self._classic_exit_notify(row, pnl, payout, price, reason, exit_fee, slip),
                name="classic_exit_notify",
            )

    async def _settle(self, row, resolution, shares, last_odds=None):
        # Phase 54 P0-05: per-market lock prevents race with concurrent exit/trade
        slug = row.get("event_slug", "")
        async with self._get_settle_lock(slug):
            await self._settle_inner(row, resolution, shares, last_odds)

    async def _settle_inner(self, row, resolution, shares, last_odds=None):
        won = row["direction"] == resolution
        fee = row["fee_amount"] or 0
        payout = round(shares * 1.0, 4) if won else 0.0

        # Phase 47f.9: maker rebate credit on settlement.
        # Polymarket pays makers a category-pct share of the realized taker
        # fee pool. Detect maker fills via fee_amount == 0 (taker fees are
        # strictly > 0 under v2 crypto curve), compute the theoretical taker
        # fee that would have been paid at entry, and credit rebate_pct of it.
        # Env gate: MAKER_REBATE_ENABLED (default on, can turn off for A/B).
        rebate = 0.0
        if (
            float(fee) == 0.0
            and row["trade_amount"] > 0
            and os.getenv("MAKER_REBATE_ENABLED", "true").lower() == "true"
        ):
            try:
                entry_px = row.get("execution_price") or 0.5
                cat = row.get("category") or "crypto"
                theo_taker = polymarket_taker_fee_v2(entry_px, row["trade_amount"], cat)
                rebate = polymarket_maker_rebate(theo_taker, cat)
                if rebate > 0:
                    logger.info(
                        f"  💰 MAKER REBATE: {row['id'][:6]} "
                        f"+${rebate:.4f} ({cat} {entry_px:.3f})"
                    )
            except (TypeError, KeyError, ValueError) as _rex:
                # T1.4 Faz 1: narrowed — rebate calc is pure arithmetic on row dict.
                logger.debug(f"maker rebate calc ({type(_rex).__name__}): {_rex}")
                rebate = 0.0

        pnl = round(payout - row["trade_amount"] - fee + rebate, 4)
        # Settle the final payout+rebate together so wallet balance reflects both.
        total_credit = round(payout + rebate, 4) if (won or rebate > 0) else 0.0
        await self._close(row, pnl, total_credit, "won" if won else "lost", rebate=rebate)
        log_settlement(row["event_slug"], row["direction"], resolution, won, pnl, payout, last_odds)
        # ══ Phase 18.5: Martingale streak tracking ══
        sid = row.get("strategy_id")
        if sid:
            if won:
                old = self._mg_streak.get(sid, 0)
                self._mg_streak[sid] = 0
                if old > 0:
                    logger.info(f"  🎰 Martingale RESET: {sid[:8]} streak {old}→0 (WON)")
            else:
                self._mg_streak[sid] = self._mg_streak.get(sid, 0) + 1
                streak = self._mg_streak[sid]
                if streak >= 2:
                    logger.info(f"  🎰 Martingale streak: {sid[:8]} L{streak}")
        # Phase 75: Per-strategy trade journal — short reason log
        sid = row.get("strategy_id", "")
        _entry_px = row.get("execution_price") or 0
        _sig = row.get("signal_score") or 0
        _reason_short = "WIN" if won else "LOSS"
        if not won and _entry_px > 0.65:
            _reason_short = "LOSS:HIGH_ENTRY"
        elif not won and fee > 0.03:
            _reason_short = "LOSS:HIGH_FEE"
        elif won and pnl < 0.02:
            _reason_short = "WIN:MARGINAL"
        logger.info(
            f"  {'🟢' if won else '🔴'} {'WON' if won else 'LOST'}: "
            f"{row['event_slug']} PnL={pnl:+.2f} "
            f"@{_entry_px:.2f} sig={_sig:.2f} [{_reason_short}]"
        )
        # Phase 27: AI per-trade analysis
        # Phase 82e Sprint 2.1: guarded
        safe_create_task(
            self._ai_trade_analysis(row, pnl, "won" if won else "lost"),
            name="ai_trade_analysis_settle",
        )
        # Sprint 5 HOTFIX v5 (2026-04-20): classic-specific Telegram ping.
        # User asked for a "no-protection" strategy that notifies on every
        # resolution: direction, entry, exit, PnL, fee. Fire-and-forget so
        # notification failure never blocks settlement. Opt-out via
        # CLASSIC_NOTIFY_RESOLUTION=false.
        if os.getenv("CLASSIC_NOTIFY_RESOLUTION", "true").lower() != "false":
            safe_create_task(
                self._classic_resolution_notify(
                    row, won, pnl, payout, fee, rebate, last_odds, _entry_px, resolution
                ),
                name="classic_resolve_notify",
            )

        # P0-07-b (2026-05-09): reference price audit — settle anında bot'un
        # local Binance/Chainlink reference feed'i ile Polymarket'in resolved
        # boundary fiyatı arasındaki sapmayı snapshot et. Fire-and-forget;
        # audit task asla settle path'ini bloklamaz. Resolution price NULL
        # bırakılır (data_quality='missing_resolution') — backfill job veya
        # report generator daha sonra Polymarket Gamma'dan doldurur.
        if os.getenv("REFERENCE_PRICE_AUDIT_ENABLED", "true").lower() != "false":
            safe_create_task(
                self._record_reference_audit(row, resolution), name="reference_price_audit"
            )

    async def _record_reference_audit(self, row, resolution):
        """P0-07-b: Per-settle reference price audit row.

        Strategy:
          1. settle_ts_ms = current epoch ms (close to Polymarket boundary —
             actual boundary may be 0-60s earlier; backfill job can refine
             from market metadata.endDate later).
          2. external_prices ±5s lookup for each source:
              - source='binance_spot_ws' (preferred — sub-second freshness)
              - source='binance' (REST fallback)
              - source='chainlink' (separate oracle for cross-check)
          3. Compute deviation in basis points if ALSO have an
             official_resolution_price. Initially NULL →
             data_quality='missing_resolution'. Report generator (P0-07-d)
             back-fills via Gamma `/markets/{condition_id}` boundary field.
          4. Insert idempotent — same (condition_id, settle_ts_ms) replaces
             so re-runs after backfill update existing row, not duplicate.

        Defensive: every step wrapped in try/except. Settlement path NEVER
        sees a failure here.
        """
        try:
            import time

            settle_ts_ms = int(time.time() * 1000)
            slug = row.get("event_slug", "") or ""
            asset_id = row.get("market_token_id") or ""
            asset = infer_asset_from_slug(slug) or ""
            tf = infer_tf_from_slug(slug) or ""

            # Slug = canonical market identifier in bot codebase.
            # condition_id field stores it for the audit table primary key.
            condition_id = slug

            # ±5s lookup window in external_prices
            window_start = settle_ts_ms - 5000
            window_end = settle_ts_ms + 5000

            symbol = f"{asset}USD" if asset else None
            bot_binance_ws_price = None
            bot_binance_rest_price = None
            bot_chainlink_price = None
            data_quality = "ok"

            if symbol:
                # Look up the closest tick per source within ±5s
                async with self.db.conn.execute(
                    """SELECT source, price, ts_ms
                       FROM external_prices
                       WHERE symbol=? AND ts_ms BETWEEN ? AND ?
                       ORDER BY ABS(ts_ms - ?) ASC""",
                    (symbol, window_start, window_end, settle_ts_ms),
                ) as c:
                    seen = set()
                    async for src, price, _ts in c:
                        if src in seen:
                            continue
                        seen.add(src)
                        if src == "binance_spot_ws":
                            bot_binance_ws_price = float(price)
                        elif src == "binance":
                            bot_binance_rest_price = float(price)
                        elif src == "chainlink":
                            bot_chainlink_price = float(price)
                        # Stop early if all 3 sources captured
                        if len(seen) >= 3:
                            break

                if (
                    bot_binance_ws_price is None
                    and bot_binance_rest_price is None
                    and bot_chainlink_price is None
                ):
                    data_quality = "missing_external"
            else:
                data_quality = "missing_external"

            # Resolution price not fetched at settle time (kept off the hot
            # path). Report/backfill job fills via Gamma API. Mark accordingly.
            if data_quality == "ok":
                data_quality = "missing_resolution"

            created_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

            # INSERT OR REPLACE — backfill can later UPDATE official price
            # without losing this baseline; same (condition_id, settle_ts_ms)
            # idempotent.
            await self.db.conn.execute(
                """INSERT OR REPLACE INTO reference_price_audit
                   (settle_ts_ms, condition_id, asset_id, slug, asset,
                    timeframe, official_resolution_price,
                    bot_binance_rest_price, bot_binance_ws_price,
                    bot_chainlink_price, dev_binance_bps, dev_chainlink_bps,
                    settle_outcome, data_quality, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    settle_ts_ms,
                    condition_id,
                    asset_id,
                    slug,
                    asset,
                    tf,
                    None,  # official_resolution_price (backfill later)
                    bot_binance_rest_price,
                    bot_binance_ws_price,
                    bot_chainlink_price,
                    None,
                    None,  # dev bps (computed when official is known)
                    str(resolution) if resolution is not None else None,
                    data_quality,
                    created_at,
                ),
            )
            await self.db.conn.commit()
            logger.info(
                f"  📊 ref_audit: {slug[:40]} src=ws/rest/cl="
                f"{bot_binance_ws_price}/{bot_binance_rest_price}/"
                f"{bot_chainlink_price} q={data_quality}"
            )
        except (aiosqlite.Error, KeyError, TypeError, ValueError, AttributeError) as e:
            # Audit is best-effort. Settlement path already succeeded.
            logger.warning(f"reference_price_audit failed ({type(e).__name__}): {e}")

    async def _classic_exit_notify(self, row, pnl, payout, exit_price, reason, exit_fee, slip):
        """Sprint 5 HOTFIX v5: Telegram ping for every classic trade that
        exits early (TP/SL/forced-close), separate from settlement.
        Silent on any failure.
        """
        try:
            if not getattr(self, "bot_app", None):
                return
            notify_all = os.getenv("CLASSIC_NOTIFY_ALL_STYPES", "false").lower() == "true"
            sid = row.get("strategy_id", "")
            stype = ""
            label = ""
            if sid:
                try:
                    rows = await self.db.conn.execute_fetchall(
                        "SELECT label, strategy_type FROM strategies WHERE id=?", (sid,)
                    )
                    if rows:
                        label = rows[0][0] or ""
                        stype = (rows[0][1] or "").lower()
                except aiosqlite.Error:
                    # T1.4 Faz 1: narrowed — only DB errors expected here.
                    pass
            if not notify_all and stype != "classic":
                return
            admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
            if not admin_id:
                return
            try:
                admin_id_int = int(admin_id)
            except (ValueError, TypeError):
                # T1.4 Faz 1: narrowed — int() cast failures only.
                return

            slug = row.get("event_slug", "?")
            direction = (row.get("direction", "?") or "?").upper()
            entry_px = float(row.get("execution_price", 0) or 0)
            trade_amount = float(row.get("trade_amount", 0) or 0)
            exit_fee_val = float(exit_fee or 0)
            slip_val = float(slip or 0)
            try:
                from telegram_bot.templates.safe_html import esc

                slug_safe = esc(slug)
                label_safe = esc(label or stype or "classic")
                reason_safe = esc(reason or "exit")
            except (ImportError, AttributeError, TypeError):
                # T1.4 Faz 1: narrowed — module missing or esc() type coercion.
                def _esc(x):
                    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                slug_safe = _esc(slug)
                label_safe = _esc(label or stype or "classic")
                reason_safe = _esc(reason or "exit")

            emoji = "🟢" if pnl >= 0 else "🔴"
            text = (
                f"{emoji} <b>CLASSIC EXIT</b>  •  <code>{reason_safe}</code>\n"
                f"🏷 <i>{label_safe}</i>\n"
                f"🎯 <b>{direction}</b>  •  slug: <code>{slug_safe}</code>\n"
                f"💵 Giriş: <code>{entry_px:.3f}</code>  →  "
                f"Çıkış: <code>{float(exit_price):.3f}</code>\n"
                f"📥 Miktar: <code>${trade_amount:.2f}</code>\n"
                f"📤 Ödeme: <code>${float(payout):.2f}</code>\n"
                f"💸 Çıkış ücreti: <code>${exit_fee_val:.4f}</code>  •  "
                f"Slip: <code>${slip_val:.4f}</code>\n"
                f"💰 <b>PnL: ${pnl:+.2f}</b>"
            )
            try:
                await self.bot_app.bot.send_message(
                    chat_id=admin_id_int,
                    text=text,
                    parse_mode="HTML",
                )
            except TelegramError as _send_err:
                # T1.4 Faz 1: narrowed — telegram transport errors only.
                logger.debug(f"classic exit notify send: {_send_err}")
        except Exception as _outer:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — notify wrapper must never raise into
            # the settlement path (fire-and-forget side effect). Log type for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"classic exit notify failed ({type(_outer).__name__}): {_outer}")

    async def _classic_resolution_notify(
        self, row, won, pnl, payout, fee, rebate, last_odds, entry_px, resolution
    ):
        """Sprint 5 HOTFIX v5: Telegram ping for every classic trade that
        resolves. Shows entry price, resolution outcome, PnL components
        (payout, fee, rebate). Never raises — failure is silent.

        Env knobs:
          CLASSIC_NOTIFY_RESOLUTION   Default: true
          CLASSIC_NOTIFY_ALL_STYPES   If true, also notify for non-classic
                                      stypes (default: false — classic only).
        """
        try:
            if not getattr(self, "bot_app", None):
                return
            # Resolve classic-only vs. all-stypes
            notify_all = os.getenv("CLASSIC_NOTIFY_ALL_STYPES", "false").lower() == "true"
            sid = row.get("strategy_id", "")
            stype = ""
            label = ""
            if sid:
                try:
                    rows = await self.db.conn.execute_fetchall(
                        "SELECT label, strategy_type FROM strategies WHERE id=?", (sid,)
                    )
                    if rows:
                        label = rows[0][0] or ""
                        stype = (rows[0][1] or "").lower()
                except aiosqlite.Error:
                    # T1.4 Faz 1: narrowed — DB errors only.
                    pass
            if not notify_all and stype != "classic":
                return

            admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
            if not admin_id:
                return
            try:
                admin_id_int = int(admin_id)
            except (ValueError, TypeError):
                # T1.4 Faz 1: narrowed — int() cast failures only.
                return

            slug = row.get("event_slug", "?")
            direction = (row.get("direction", "?") or "?").upper()
            trade_amount = float(row.get("trade_amount", 0) or 0)
            fee_val = float(fee or 0)
            rebate_val = float(rebate or 0)
            last_odds_line = ""
            if last_odds is not None:
                try:
                    last_odds_line = f"\n💹 Son fiyat: <code>{float(last_odds):.3f}</code>"
                except (ValueError, TypeError):
                    # T1.4 Faz 1: narrowed — float() cast failures only.
                    last_odds_line = ""
            emoji = "🟢" if won else "🔴"
            result_word = "KAZANDI" if won else "KAYBETTI"
            # Escape slug minimally for HTML
            try:
                from telegram_bot.templates.safe_html import esc

                slug_safe = esc(slug)
                label_safe = esc(label or stype or "classic")
            except (ImportError, AttributeError, TypeError):
                # T1.4 Faz 1: narrowed — module missing or esc() type coercion.
                def _esc(x):
                    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

                slug_safe = _esc(slug)
                label_safe = _esc(label or stype or "classic")

            text = (
                f"{emoji} <b>CLASSIC {result_word}</b>\n"
                f"🏷 <i>{label_safe}</i>\n"
                f"🎯 <b>{direction}</b>  •  slug: <code>{slug_safe}</code>\n"
                f"⚖️ Çözüm: <code>{(resolution or '?').upper()}</code>"
                f"{last_odds_line}\n"
                f"💵 Giriş fiyatı: <code>{entry_px:.3f}</code>\n"
                f"📥 Giriş tutarı: <code>${trade_amount:.2f}</code>\n"
                f"📤 Ödeme: <code>${payout:.2f}</code>\n"
                f"💸 Ücret: <code>${fee_val:.4f}</code>"
                + (f"  •  Rebate: <code>+${rebate_val:.4f}</code>" if rebate_val > 0 else "")
                + f"\n💰 <b>PnL: ${pnl:+.2f}</b>"
            )
            try:
                await self.bot_app.bot.send_message(
                    chat_id=admin_id_int,
                    text=text,
                    parse_mode="HTML",
                )
            except TelegramError as _send_err:
                # T1.4 Faz 1: narrowed — telegram transport errors only.
                logger.debug(f"classic notify send: {_send_err}")
        except Exception as _outer:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — notify wrapper must never raise into
            # the settlement path. Upgrade to logger.exception for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"classic notify failed ({type(_outer).__name__}): {_outer}")

    async def _ai_trade_analysis(self, row, pnl, result):
        """Phase 27: Send trade to AI analyst for per-trade analysis."""
        try:
            if not self.analyst or not self.analyst._running:
                return
            # Get strategy label
            label_row = await self.db.conn.execute_fetchall(
                "SELECT label, strategy_type FROM strategies WHERE id=?",
                (row.get("strategy_id", ""),),
            )
            label = label_row[0][0] if label_row else "?"
            stype = label_row[0][1] if label_row else "?"
            await self.analyst.analyze_trade(
                {
                    "label": label,
                    "type": stype,
                    "direction": row.get("direction", "?"),
                    "price": row.get("execution_price", 0),
                    "result": result,
                    "pnl": pnl,
                    "fee": row.get("fee_amount", 0) or 0,
                    "amount": row.get("trade_amount", 0),
                    "slug": row.get("event_slug", ""),
                }
            )
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — AI analyst call wraps httpx (Anthropic
            # SDK), JSON, and dict access. Log type for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.debug(f"AI trade analysis ({type(e).__name__}): {e}")

    async def _close(self, row, pnl, payout, result, rebate: float = 0.0):
        """F-03: Single transaction for execution update + wallet credit."""
        now_iso = datetime.now(UTC).isoformat()
        try:
            # Calculate EV for this trade (Phase 75+)
            from core.ev_tracker import EVTracker

            ev_tracker = EVTracker(self.db)

            _raw = row.get("signal_score")
            win_prob = _raw if _raw is not None else 0.5  # None guard: NULL → 0.5 default
            expected_ev = await ev_tracker.calculate_trade_ev(
                win_probability=win_prob,
                execution_price=row["execution_price"],
                trade_amount=row["trade_amount"],
                total_fee=row.get("fee_amount", 0.0),
            )

            # Sprint 2 S2-03: Calculate enrichment metrics
            # P1-07 Round-3 (2026-05-11): annotate _dur once (re-used at line ~647).
            _dur: float | None = None
            _created = row.get("created_at")
            if _created:
                try:
                    _t0 = datetime.fromisoformat(str(_created))
                    if _t0.tzinfo is None:
                        _t0 = _t0.replace(tzinfo=UTC)
                    _dur = int((datetime.now(UTC) - _t0).total_seconds())
                except (ValueError, TypeError):
                    # T1.4 Faz 1: narrowed — datetime.fromisoformat parse only.
                    pass
            _max_fav, _max_adv = None, None
            try:
                _moves = self._pop_max_moves(row["id"])
                if _moves:
                    _max_fav, _max_adv = round(_moves[0], 6), round(_moves[1], 6)
            except (KeyError, TypeError, IndexError):
                # T1.4 Faz 1: narrowed — row lookup + tuple unpack only.
                pass

            # Both operations in single transaction
            await self.db.conn.execute(
                "UPDATE executions SET status='claimed',pnl=?,payout=?,result=?,closed_at=?,"
                "updated_at=?,expected_ev=?,win_probability=?,duration_sec=?,"
                "max_favorable_move=?,max_adverse_move=? WHERE id=?",
                (
                    pnl,
                    payout,
                    result,
                    now_iso,
                    now_iso,
                    expected_ev,
                    win_prob,
                    _dur,
                    _max_fav,
                    _max_adv,
                    row["id"],
                ),
            )
            if payout > 0:
                # F-02: Atomic credit — single SQL, no read-modify-write
                await self.db.conn.execute(
                    "UPDATE wallets SET balance = balance + ? WHERE id = ?",
                    (payout, row["wallet_id"]),
                )
            await self.db.conn.commit()  # Single commit for both
        except Exception as e:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — outer body includes EVTracker
            # calculation (may hit httpx/DB) plus the critical UPDATE+credit
            # transaction. Use logger.exception so any regression is traceable.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.exception(f"Close transaction failed ({type(e).__name__}): {e}")
            try:
                await self.db.conn.rollback()
            except aiosqlite.Error:
                # T1.4 Faz 1: narrowed — rollback only raises DB errors.
                pass
            return

        pk = f"{row['strategy_id']}:{row['event_slug']}"
        self._open_positions.discard(pk)
        self._settled_slugs[pk] = datetime.now(UTC)
        self.risk.record_trade_closed(
            row["trade_amount"], pnl, row["event_slug"], strategy_id=row["strategy_id"]
        )

        # Sprint 2 S2-01: Log CLOSE event to decisions.jsonl
        try:
            from core.trade_journal import log_decision_close

            _created = row.get("created_at")
            _dur = None  # P1-07 Round-3: typed at line 570 (first declaration).
            if _created:
                from datetime import datetime as _dt

                try:
                    _t0 = _dt.fromisoformat(str(_created))
                    _dur = (datetime.now(UTC) - _t0).total_seconds()
                except (ValueError, TypeError):
                    # T1.4 Faz 1: narrowed — datetime.fromisoformat parse only.
                    pass
            log_decision_close(
                row["strategy_id"], row.get("event_slug", ""), result, float(pnl), duration_sec=_dur
            )
        except Exception as _ldc:  # noqa: BLE001
            # T1.4 Faz 1: catch-all kept — journal writes are fire-and-forget
            # side effects (file I/O + import). Log type for triage.
            # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
            logger.debug(f"log_decision_close skipped ({type(_ldc).__name__}): {_ldc}")

        # Phase 76 capital_allocator integration removed in T1.3 (ghost module purge,
        # 2026-04-20). Attribute `_capital_allocator` is never set on the engine any
        # more, so this branch was dead code. Kept comment for history.

        # Phase 77: Record trade outcome into Trade Memory
        _tm = getattr(self, "_trade_memory", None)
        if _tm is not None:
            try:
                await _tm.record(
                    strategy_id=row["strategy_id"],
                    slug=row.get("event_slug", ""),
                    direction=row.get("direction", ""),
                    result=result,
                    pnl=float(pnl),
                    signal_score=float(
                        row.get("signal_score", 0) or 0
                    ),  # Phase 79 BUG-03: use original signal_score
                    entry_price=float(row.get("execution_price", 0) or 0),
                )
            except (aiosqlite.Error, KeyError, TypeError, ValueError) as _tme:
                # T1.4 Faz 1: narrowed — DB write, row lookup, or float() cast.
                logger.debug(f"trade_memory.record skipped ({type(_tme).__name__}): {_tme}")

        # Phase 47a: feed realized PnL back into the adaptive micro tracker
        if getattr(self, "micro_weight", None) is not None:
            try:
                slug = row.get("event_slug", "") or ""
                asset_guess = "BTC"
                for a in ("BTC", "ETH", "SOL", "XRP"):
                    if a.lower() in slug.lower():
                        asset_guess = a
                        break
                self.micro_weight.record_close(
                    order_key=pk,
                    asset=asset_guess,
                    pnl_usd=float(pnl),
                )
            except Exception as _mwc:  # noqa: BLE001
                # T1.4 Faz 1: catch-all kept — adaptive weight tracker internals
                # (Phase 47a). Log type so regressions are visible.
                # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                logger.debug(f"micro_weight.record_close ({type(_mwc).__name__}): {_mwc}")
        # Phase 48 becker_weight tracker removed 2026-04-28 (Becker tam silme)
        # Phase 28: Persist risk state to DB
        await self.risk.save_state(self.db)
        # Phase 33: Update Thompson Sampling
        won = pnl > 0
        self.selector.record_result(row["strategy_id"], won, pnl)
        # Phase 34: Notify live trader of settlement
        if self.live.is_enabled():
            try:
                await self.live.check_settlement(
                    row["event_slug"],
                    won,
                    pnl,
                    paper_amount=float(row.get("trade_amount", 0) or 25.0),
                )
            except Exception as _lse:  # noqa: BLE001
                # T1.4 Faz 1: catch-all kept — live.check_settlement body spans
                # CLOB + DB + telegram; must not raise into paper close path.
                # T7.6 Faz 3: yeniden değerlendirildi, Faz 1 kararı doğru — bilinçli umbrella.
                logger.exception(f"live.check_settlement failed ({type(_lse).__name__}): {_lse}")
        parts = row["event_slug"].split("-")
        if len(parts) >= 3 and row.get("strategy_id"):
            self._cooldowns[f"{row['strategy_id']}:{parts[0].upper()}_{parts[2]}"] = datetime.now(
                UTC
            ) + timedelta(seconds=30)
        emoji = {"won": "✅", "lost": "❌", "tp_exit": "🤑", "sl_exit": "🛑"}.get(result, "📊")
        title = {
            "won": "Kazandik!",
            "lost": "Kaybettik",
            "tp_exit": "TP Hit!",
            "sl_exit": "SL Hit!",
        }.get(result, "Kapandi")
        # Phase 79b: Enriched close notification
        _dir_emoji = "📈" if row["direction"].lower() == "up" else "📉"
        _entry_px = row.get("execution_price") or 0
        _fee = row.get("fee_amount") or 0
        _amount = row.get("trade_amount") or 0
        _sig = row.get("signal_score") or 0
        # Duration
        _dur_str = ""
        _created = row.get("created_at")
        if _created:
            try:
                _t0 = datetime.fromisoformat(str(_created))
                if _t0.tzinfo is None:
                    _t0 = _t0.replace(tzinfo=UTC)
                _secs = int((datetime.now(UTC) - _t0).total_seconds())
                if _secs < 120:
                    _dur_str = f"{_secs}sn"
                else:
                    _dur_str = f"{_secs // 60}dk"
            except (ValueError, TypeError):
                # T1.4 Faz 1: narrowed — datetime.fromisoformat parse only.
                pass
        # Strategy label
        _label = ""
        try:
            _lbl_row = await self.db.conn.execute_fetchall(
                "SELECT label FROM strategies WHERE id=?", (row.get("strategy_id", ""),)
            )
            _label = _lbl_row[0][0] if _lbl_row else row.get("strategy_id", "?")[:8]
        except aiosqlite.Error:
            # T1.4 Faz 1: narrowed — only DB errors expected.
            _label = row.get("strategy_id", "?")[:8]
        # P0-08-D (2026-05-08): slug_utils ile TF/asset inference (4 TF).
        # Eski `_parts = row["event_slug"].split("-")` + `_parts[0/2]` indexing
        # btc-updown-{tf}-{epoch} formatına kilitliydi; bitcoin-up-or-down-on-*
        # (24h) ve bitcoin-up-or-down-*-Hpm-et (1h) slug'larında "or" döndürürdü.
        _slug = row.get("event_slug") or ""
        _asset = infer_asset_from_slug(_slug)
        _tf = infer_tf_from_slug(_slug)
        # ROI calculation
        _roi_str = ""
        if _amount > 0:
            _roi = (pnl / _amount) * 100
            _roi_str = f" ({_roi:+.1f}%)"
        await self._notify(
            row["user_id"],
            f"{emoji} <b>{title}</b>{_roi_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Strateji: <b>{_label}</b>\n"
            f"{_dir_emoji} {row['direction'].upper()} | {_asset}/{_tf}\n"
            f"Giris: {_entry_px:.4f} | Tutar: ${_amount:.2f}\n"
            f"Fee: ${_fee:.4f} | Rebate: ${rebate:.4f}\n"
            f"Odeme: ${payout:.2f} | <b>PnL: {pnl:+.4f}</b>\n"
            f"Sinyal: {_sig:+.2f} | Sure: {_dur_str or '?'}\n"
            f"<code>{row['event_slug']}</code>",
        )

    async def _notify(self, uid, text):
        if not self.bot_app:
            return
        try:
            u = await self.db.get_user_by_id(uid)
            if u:
                await self.bot_app.bot.send_message(
                    chat_id=u.telegram_id, text=text, parse_mode="HTML"
                )
        except (aiosqlite.Error, TelegramError) as _ne:
            # T1.4 Faz 1: narrowed — DB user lookup + telegram transport only.
            logger.debug(f"_notify skipped ({type(_ne).__name__}): {_ne}")

    async def _count(self, sid, slug):
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM executions WHERE strategy_id=? AND event_slug=?", (sid, slug)
        ) as c:
            return (await c.fetchone())[0]

    async def _count_losses(self, sid, slug):
        async with self.db.conn.execute(
            "SELECT COUNT(*) FROM executions WHERE strategy_id=? AND event_slug=? AND result='lost'",
            (sid, slug),
        ) as c:
            return (await c.fetchone())[0]

    # ═══ Phase 52: Dynamic Slippage ═══
    _cached_avg_slip: float | None = None
    _cached_avg_slip_ts: float = 0.0

    async def _get_avg_slippage(self, ttl: float = 300.0) -> float:
        """Rolling average of realized_slippage from last 200 fills.

        Caches the result for *ttl* seconds (default 5 min) to avoid
        hitting SQLite on every exit.  Falls back to 0.3% if no data.
        """
        import time

        now = time.monotonic()
        if self._cached_avg_slip is not None and (now - self._cached_avg_slip_ts) < ttl:
            return self._cached_avg_slip
        try:
            async with self.db.conn.execute(
                """SELECT AVG(ABS(realized_slippage))
                   FROM (SELECT realized_slippage FROM executions
                         WHERE realized_slippage IS NOT NULL
                           AND realized_slippage != 0
                         ORDER BY closed_at DESC LIMIT 200)"""
            ) as c:
                row = await c.fetchone()
                avg = row[0] if row and row[0] else None
            if avg is not None and avg > 0:
                # Clamp between 0.05% and 2% to prevent runaway
                slip_pct = max(0.0005, min(avg, 0.02))
            else:
                slip_pct = 0.003  # fallback 0.3%
        except (aiosqlite.Error, ValueError, TypeError):
            # T1.4 Faz 1: narrowed — DB read or numeric coercion only.
            slip_pct = 0.003
        self._cached_avg_slip = slip_pct
        self._cached_avg_slip_ts = now
        return slip_pct
