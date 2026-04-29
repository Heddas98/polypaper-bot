"""
Phase 51 P51-02 — Monitor mixin for TradingEngine
==================================================
Houses the open-position monitor / TP-SL / oracle-settle cluster carved out
of the original monolithic ``core/engine.py``. Methods reference ``self.*``
state on the concrete :class:`~core.engine.TradingEngine` and call into the
settlement and fills mixins (``self._settle``, ``self._exit``).

Public runtime behaviour is unchanged; every method below is a verbatim
copy of the original engine.py body.

Phase 60: Smart Exit — 3-tier exit logic:
  Tier 1: Remaining Edge (Becker δ < threshold → exit)
  Tier 2: Stop Loss (δ < entry - delta → exit)
  Tier 3: Forced Exit (time-based, legacy Phase 53b)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from core.engine_support import _slug_end
from data.polymarket_client import safe_float

logger = logging.getLogger("polypaper.core.engine")

# Phase 53b: forced exit N seconds before market close
# 0 = disabled (default). Typical: 10-15 for 5m markets.
FORCE_EXIT_SECONDS = int(os.getenv("FORCE_EXIT_SECONDS", "0"))

# ── Phase 60: Smart Exit ENV controls ──
# NOTE: SMART_EXIT_ENABLED and REMAINING_EDGE_MIN are read at runtime
# from os.environ so /filters panel can toggle them without restart.
def _smart_exit_enabled() -> bool:
    return os.getenv("SMART_EXIT_ENABLED", "true").lower() == "true"

def _remaining_edge_min() -> float:
    return float(os.getenv("REMAINING_EDGE_MIN", "0.05"))

# If estimated prob drops below entry - this → stop loss exit
STOP_LOSS_DELTA = float(os.getenv("STOP_LOSS_DELTA", "0.12"))
# Track disposition coefficient (max_unrealized per position)
DISPOSITION_TRACKING = os.getenv("DISPOSITION_TRACKING", "true").lower() == "true"
# Phase 66: Grace period after fill — skip smart-exit for N seconds after open
SMART_EXIT_GRACE_SEC = int(os.getenv("SMART_EXIT_GRACE_SEC", "60"))


class EngineMonitorMixin:
    """Open-position monitor + TP/SL + oracle settle methods for TradingEngine.

    Sprint 2 S2-03: ``self._max_moves`` max-move tracker is initialized on the
    concrete ``TradingEngine`` in ``core/engine.py``'s ``__init__``. Previously
    declared as a class-level ``dict = {}`` here which leaked state across
    instances (test fixtures, multi-restart).
    """

    def _track_max_moves(self, exec_id: str, entry: float, cur: float):
        """Track max favorable and max adverse price moves for an open position."""
        if cur is None or entry <= 0:
            return
        move = cur - entry  # positive = favorable (price went up from entry)
        prev = self._max_moves.get(exec_id, (0.0, 0.0))
        fav = max(prev[0], move) if move > 0 else prev[0]
        adv = min(prev[1], move) if move < 0 else prev[1]
        self._max_moves[exec_id] = (fav, adv)

    def _pop_max_moves(self, exec_id: str):
        """Get and remove max moves for a closed position. Returns (fav, adv) or None."""
        return self._max_moves.pop(exec_id, None)

    # ═══ MONITOR + SETTLE ═══
    async def _monitor(self):
        rows = []
        try:
            async with self.db.conn.execute(
                "SELECT * FROM executions WHERE status='bet_placed'") as c:
                async for row in c:
                    rows.append(dict(row))
        except (aiosqlite.Error, AttributeError, TypeError):
            # T1.4 Faz 3: DB read + dict(row) iter. Realistic modes:
            # aiosqlite.Error (SELECT), AttributeError (self.db.conn None),
            # TypeError (Row→dict cast).
            return
        now = datetime.now(timezone.utc)
        for row in rows:
            try:
                await self._check(row, now)
            except (aiosqlite.Error, AttributeError, KeyError, TypeError,
                    ValueError, IndexError) as e:
                # T1.4 Faz 3: _check iç yüzey — DB reads, dict access,
                # datetime, float arithmetic, slicing. Realistic modes:
                # aiosqlite.Error (DB), AttributeError (self.client / self.db
                # None), KeyError (row dict eksik), TypeError/ValueError
                # (arithmetic on None), IndexError (slug.split slice).
                logger.error(f"Pos {row['id'][:8]}: {e}")

    async def _check(self, row, now):
        slug, direction = row["event_slug"], row["direction"]
        entry = safe_float(row["execution_price"]) or 0.5
        shares = row["trade_amount"] / entry if entry > 0 else 0
        end = _slug_end(slug)

        # ══ Phase 53b: Forced exit N seconds before market close ══
        if FORCE_EXIT_SECONDS > 0 and end:
            secs_left = (end - now).total_seconds()
            if 0 < secs_left <= FORCE_EXIT_SECONDS:
                # Get current price for exit
                token_id = row.get("market_token_id")
                cur = None
                if token_id:
                    cur = await self.client.get_live_price(token_id, "SELL")
                if cur is None:
                    odds = self.scanner.get_current_odds(slug)
                    if odds:
                        cur = safe_float(odds.get("up_odds") if direction == "up" else odds.get("down_odds"))
                if cur is None:
                    cur = entry  # fallback: exit at entry (no slippage data)
                logger.info(
                    f"  ⚡ FORCE-EXIT: {slug} {secs_left:.0f}s before close "
                    f"@ {cur:.4f} (entry={entry:.4f})")
                # Track daily count for heartbeat
                if not hasattr(self, '_force_exits_today'):
                    self._force_exits_today = 0
                self._force_exits_today += 1
                return await self._exit(row, shares, cur, "force_exit")

        # ══ Phase 60: Smart Exit — Remaining Edge + Stop Loss ══
        # Uses Becker δ(p) as the estimated probability adjustment.
        # Tier 1: if remaining edge (δ) drops below threshold → exit (edge exhausted)
        # Tier 2: if δ indicates we're on wrong side → stop loss exit
        # Runs BEFORE oracle/settlement checks — we want to exit live positions.
        # Phase 66: grace period — skip smart-exit for SMART_EXIT_GRACE_SEC after fill.
        if _smart_exit_enabled() and end and now < end:
            # Check fill grace period
            _skip_grace = False
            if SMART_EXIT_GRACE_SEC > 0:
                try:
                    _cat = row.get("created_at", "")
                    if _cat:
                        _fill_time = datetime.fromisoformat(_cat.replace("Z", "+00:00"))
                        if _fill_time.tzinfo is None:
                            _fill_time = _fill_time.replace(tzinfo=timezone.utc)
                        _age = (now - _fill_time).total_seconds()
                        if _age < SMART_EXIT_GRACE_SEC:
                            _skip_grace = True
                except (TypeError, ValueError, AttributeError):
                    # T1.4 Faz 3: datetime.fromisoformat + tzinfo coerce +
                    # timedelta arithmetic. Realistic modes: ValueError
                    # (bozuk ISO string), TypeError (_cat int/float ise
                    # .replace yok → AttributeError yerine str(_cat)
                    # handling ileri gürbüzlük için dahil), AttributeError
                    # (_cat int/None durumunda .replace yok).
                    pass
            # _smart_exit_check call removed 2026-04-28 (Becker silindi —
            # bu Phase 60 smart exit Becker δ'ya bağlıydı). Aşama 3'te
            # surface_2d-based smart exit eklenecek (forward work).
            _ = _skip_grace  # silenced unused-var warning

        if end and now > end:
            # ══ Phase 18: UMA Oracle resolution — API FIRST ══
            # Try Gamma API for official resolved winner (mirrors UMA Oracle)
            resolved = await self.client.check_market_resolved(slug)
            if resolved:
                logger.info(f"  🏛️ ORACLE: {slug} resolved='{resolved}'")
                await self._settle(row, resolved, shares, None)
                return
            # Phase 34: CLOB price-based oracle fallback
            # After market closes, orderbook is empty → use last cached odds.
            # Sprint 5 HOTFIX v4: use get_resolution_price (no [0.01, 0.99] clamp)
            # so that resolved 0.0/1.0 prices can trigger settlement here.
            token_id = row.get("market_token_id")
            if token_id:
                cur_p = await self.client.get_resolution_price(token_id)
                if cur_p is not None and (cur_p >= 0.95 or cur_p <= 0.05):
                    d = row["direction"]
                    if cur_p >= 0.95:
                        logger.info(f"  🏛️ CLOB-ORACLE: {slug} token@{cur_p:.3f} → {d} won")
                        await self._settle(row, d, shares, cur_p)
                    else:
                        opp = "down" if d == "up" else "up"
                        logger.info(f"  🏛️ CLOB-ORACLE: {slug} token@{cur_p:.3f} → {opp} won")
                        await self._settle(row, opp, shares, cur_p)
                    return

            # API returned None — market not yet officially resolved.
            elapsed = (now - end).total_seconds()

            if elapsed < 120:
                # Under 2 min: ONLY oracle (most markets resolve in 30-60s)
                return

            # Phase 41a: configurable UMA timing (defaults match real liveness)
            extreme_window = getattr(
                self.settings, "UMA_EXTREME_ODDS_WINDOW_SECONDS", 1800)
            force_after = getattr(
                self.settings, "UMA_FORCE_SETTLE_SECONDS", 7200)

            # Sprint 5 HOTFIX v4: timeframe-aware force-settle deadline.
            # 5m/15m markets resolve fast; 2-hour UMA wait is absurd — stuck
            # positions pile up. Short-TF markets get 15min window + 10min
            # extreme-odds window so oracle gets priority but we don't hang.
            _tf_parts = slug.split("-")
            _tf = _tf_parts[2] if len(_tf_parts) > 2 else "15m"
            if _tf in ("5m", "15m"):
                force_after = getattr(
                    self.settings, "UMA_FORCE_SETTLE_SHORT_SEC", 900)
                if extreme_window > 600:
                    extreme_window = 600

            if elapsed < extreme_window:
                # 2min-extreme_window: settle with high-confidence odds
                # Threshold: 0.85+/0.15- = near-certain outcome
                last = self.scanner.get_last_known_odds(slug)
                if last:
                    lu = safe_float(last.get("up_odds"))
                    if lu is not None and (lu > 0.85 or lu < 0.15):
                        logger.info(f"  ⏳ EXTREME-ODDS-SETTLE: {slug} lu={lu:.3f} (>{0.85} or <{0.15})")
                        await self._settle(row, "up" if lu > 0.5 else "down", shares, lu)
                        return
                # Not extreme enough or no data — keep waiting
                if elapsed > 600 and int(elapsed) % 300 < 2:
                    logger.warning(f"  ⏳ Waiting for oracle: {slug} ({elapsed:.0f}s elapsed)")
                return

            if elapsed < force_after:
                # extreme_window → force_after: still in UMA liveness period.
                # Keep polling but don't force-settle yet — real disputes can
                # last hours. Periodic warning log every 5min.
                if int(elapsed) % 300 < 2:
                    logger.warning(
                        f"  ⏳ UMA liveness: {slug} elapsed={elapsed/60:.0f}min "
                        f"(force at {force_after/60:.0f}min)")
                return

            # Past UMA force-settle deadline — fall back to last known odds
            logger.warning(f"  ⚠️ FORCE-SETTLE: {slug} (no oracle after {elapsed/60:.0f}min)")
            last = self.scanner.get_last_known_odds(slug)
            lu = safe_float(last.get("up_odds")) if last else None
            if lu is None:
                try:
                    async with self.db.conn.execute(
                        "SELECT up_odds FROM odds_history WHERE event_slug=? ORDER BY timestamp DESC LIMIT 1",
                        (slug,)) as c:
                        r = await c.fetchone()
                        if r and r["up_odds"]:
                            lu = float(r["up_odds"])
                except (aiosqlite.Error, KeyError, TypeError, ValueError,
                        AttributeError, IndexError):
                    # T1.4 Faz 3: odds_history fallback SELECT + named row
                    # access + float coerce. Realistic modes:
                    # aiosqlite.Error (DB), KeyError/IndexError (r["up_odds"]
                    # eksik kolon), TypeError/ValueError (float(None)),
                    # AttributeError (self.db.conn None).
                    pass
            if lu is not None:
                await self._settle(row, "up" if lu > 0.5 else "down", shares, lu)
            else:
                await self._settle(row, "up" if entry > 0.5 else "down", shares, None)
            return

        token_id = row.get("market_token_id")
        cur = None
        if token_id:
            cur = await self.client.get_live_price(token_id, "SELL")
        if cur is None:
            odds = self.scanner.get_current_odds(slug)
            if odds:
                cur = safe_float(odds.get("up_odds") if direction == "up" else odds.get("down_odds"))
        if cur is None:
            return

        # Sprint 2 S2-03: Track max favorable/adverse price moves
        self._track_max_moves(row["id"], entry, cur)

        tp_o = safe_float(row.get("take_profit_odds"))
        tp_p = safe_float(row.get("take_profit_percent"))
        if tp_o and cur >= (entry + tp_o):
            return await self._exit(row, shares, cur, "tp_exit")
        if tp_p and cur >= entry * (1 + tp_p):
            return await self._exit(row, shares, cur, "tp_exit")
        sl_o = safe_float(row.get("stop_loss_odds"))
        sl_p = safe_float(row.get("stop_loss_percent"))
        if sl_o and cur <= sl_o:
            return await self._exit(row, shares, cur, "sl_exit")
        if sl_p and cur <= entry * (1 - sl_p):
            return await self._exit(row, shares, cur, "sl_exit")

        # ── Phase 60: Disposition Tracking — update peak unrealized PnL ──
        if DISPOSITION_TRACKING and cur is not None:
            await self._update_disposition(row, entry, cur)

    # _smart_exit_check method removed 2026-04-29 (Heddas direktifi: Becker
    # tam silme). Phase 60 3-tier exit Becker δ'ya bağlıydı, ~80 satır body
    # silindi. Forward work: surface_2d-based smart exit (Aşama 4 backlog).

    # ═══════════════════════════════════════════════════════════════════════
    # Phase 60: Disposition Coefficient Tracking
    # ═══════════════════════════════════════════════════════════════════════
    async def _update_disposition(self, row, entry, cur):
        """Track peak unrealized PnL per position for disposition coefficient.

        Stores max_unrealized_price in the DB for later analysis.
        D = avg(capture% for winners) - avg(|loss%| for losers)
        Top wallets: D=+0.79, Average: D=+0.17. This metric helps us
        measure how well our exits capture available profit.
        """
        exec_id = row["id"]
        try:
            # Read current max from DB (stored as extra field)
            async with self.db.conn.execute(
                "SELECT max_unrealized_price FROM executions WHERE id=?",
                (exec_id,)
            ) as c:
                r = await c.fetchone()
            old_max = None
            if r:
                old_max = safe_float(r[0]) if r[0] is not None else None

            direction = row["direction"]
            # For UP positions: higher cur = better unrealized
            # For DOWN positions: lower cur = better (we hold down token, its price going up = win)
            if old_max is None or cur > old_max:
                await self.db.conn.execute(
                    "UPDATE executions SET max_unrealized_price=? WHERE id=?",
                    (round(cur, 6), exec_id))
                await self.db.conn.commit()
        except (aiosqlite.Error, KeyError, TypeError, ValueError,
                AttributeError) as e:
            # T1.4 Faz 3: disposition SELECT + UPDATE max_unrealized_price.
            # Realistic modes: aiosqlite.OperationalError (schema: "no such
            # column" fresh DB), KeyError (row["id"]/row["direction"]),
            # TypeError/ValueError (safe_float / round on None),
            # AttributeError (self.db.conn None).
            # Column might not exist yet — silently skip
            if "no such column" not in str(e).lower():
                logger.debug(f"disposition tracking: {e}")
