"""
PolyPaper Bot - Auto-Optimizer (v34)
=======================================
Strateji sağlık izleyici ve adaptif eşik yöneticisi.

Otomatik durdurma tetikleyicileri:
  1. Startup health check — Bot açılışında kronik kayıp stratejileri durdurur
  2. Loss streak         — 5 arka arkaya kayıp → strateji duraklatılır
  3. PnL health          — 8+ trade, PnL < -$3.00 → duraklatılır
  4. Rolling WR auto-kill — son 20 trade WR < 40% → strateji duraklatılır (Phase 52)

Adaptif eşik (Phase 33):
  WR < 55% (son 20 trade) → odds_threshold +0.05 (daha seçici)
  WR > 70% (son 20 trade) → odds_threshold -0.03 (daha agresif)
  Korumalı stratejiler atlanır: M_BTC_5m_any_0.92, BTC High-Threshold Pure

BUG-01 FIXED (2026-04-09): SQL'e label sütunu eklendi, exception logging
  seviyesi debug→error'a yükseltildi. Adaptif eşik artık aktif.
"""
import logging
import os
from datetime import datetime, timezone
from typing import Optional

# T8.1: narrow DB exception handling — aiosqlite.Error is the canonical
# async-sqlite base class, used across core/ (see trade_journal, engine_*).
import aiosqlite

logger = logging.getLogger("polypaper.core.optimizer")

# Thresholds for auto-pause
# Sprint 0 S0-04: MIN_TRADES raised to 20 — new strategies need room to prove themselves
MIN_TRADES_FOR_EVAL = int(os.getenv("MIN_TRADES_BEFORE_PAUSE", "20"))


# Epic 6 T6.1: PNL_PAUSE_THRESHOLD is /env_toggle-whitelisted, so it must be
# read at runtime — not frozen at module import. Keeping the old module-top
# constant meant /env_toggle could patch os.environ but the optimizer would
# still use the import-time value (ghost toggle). The helper below re-reads
# every call so a hot-tune actually takes effect on the next health check.
# Phase 47f: env override. Sprint 0: default loosened from -3.0 to -8.0.
def _get_pnl_pause_threshold() -> float:
    """Return the current PNL_PAUSE_THRESHOLD from env (runtime re-read)."""
    try:
        return float(os.getenv("PNL_PAUSE_THRESHOLD", "-8.0"))
    except (TypeError, ValueError):
        return -8.0


LOSS_STREAK_LIMIT = 5          # Consecutive losses to trigger pause

# T7.6 B8 (2026-04-22): Phase 52 rolling-WR gates were module-top constants
# and therefore frozen at import. Same ghost-toggle class as T6.1 / T6.4 —
# a ``/env_toggle`` patch of ``ROLLING_WR_WINDOW`` or ``ROLLING_WR_KILL``
# would not take effect until restart. Helpers re-read on every call.
def _get_rolling_wr_window() -> int:
    """``ROLLING_WR_WINDOW`` — sample size for rolling WR check (default 20)."""
    try:
        return int(os.getenv("ROLLING_WR_WINDOW", "20"))
    except (TypeError, ValueError):
        return 20


def _get_rolling_wr_kill_threshold() -> float:
    """``ROLLING_WR_KILL`` — WR%% below this → pause (default 40.0)."""
    try:
        return float(os.getenv("ROLLING_WR_KILL", "40.0"))
    except (TypeError, ValueError):
        return 40.0


TYPES_TO_WATCH = {"momentum", "scalper", "contrarian", "martingale"}  # Extra scrutiny

# ═══════════════════════════════════════════════════════════════════
# Phase 82e HOTFIX: Protected strategy types — auto-optimizer must NOT
# pause/kill these regardless of PnL, WR, or loss streaks. Classic is
# user-driven (manual threshold/TP/SL); lifecycle/optimizer interference
# is counterproductive.
#   Env: PROTECTED_STRATEGY_TYPES=classic,another_type
#   Default: "classic"
# ═══════════════════════════════════════════════════════════════════
PROTECTED_STRATEGY_TYPES = {
    t.strip().lower() for t in
    os.getenv("PROTECTED_STRATEGY_TYPES", "classic").split(",")
    if t.strip()
}


def _is_protected_type(s) -> bool:
    """Return True if strategy's type is in PROTECTED_STRATEGY_TYPES.

    Protected strategies are skipped by auto-pause, PnL check,
    rolling WR kill, and loss-streak pause. They are entirely
    user-managed.
    """
    try:
        stype = (getattr(s, "strategy_type", "") or "").lower()
    except AttributeError:
        # T8.1: non-str `strategy_type` (e.g., mocked value) would fail
        # `.lower()`. False-negative here means a bugged strategy could
        # slip into auto-pause; accept the risk — narrow is deliberate.
        return False
    return stype in PROTECTED_STRATEGY_TYPES

# Phase 56 P1-05: Adaptive PnL pause threshold — strategies with more trades
# get more rope. A strategy with 100+ trades at -$3 is far less concerning
# than one with 8 trades at -$3. Scale: -$3 base, loosens -$0.50 per 20 trades,
# capped at -$10.
ADAPTIVE_PNL_ENABLED = os.getenv("ADAPTIVE_PNL_ENABLED", "true").lower() == "true"
ADAPTIVE_PNL_STEP = float(os.getenv("ADAPTIVE_PNL_STEP", "0.5"))   # loosen per step
ADAPTIVE_PNL_TRADES_PER_STEP = int(os.getenv("ADAPTIVE_PNL_TRADES_PER_STEP", "20"))
ADAPTIVE_PNL_FLOOR = float(os.getenv("ADAPTIVE_PNL_FLOOR", "-10.0"))  # max looseness


def _adaptive_pnl_threshold(trade_count: int) -> float:
    """Phase 56: Return adaptive PnL pause threshold based on trade count.
    More trades → more lenient threshold (strategy has earned trust).

    Epic 6 T6.1: base threshold is now read fresh per call via
    `_get_pnl_pause_threshold()` so /env_toggle changes take effect at
    runtime instead of being frozen at module import.
    """
    base = _get_pnl_pause_threshold()
    if not ADAPTIVE_PNL_ENABLED or ADAPTIVE_PNL_TRADES_PER_STEP <= 0:
        return base
    steps = trade_count // ADAPTIVE_PNL_TRADES_PER_STEP
    threshold = base - (steps * ADAPTIVE_PNL_STEP)
    return max(threshold, ADAPTIVE_PNL_FLOOR)


class AutoOptimizer:
    """Watches strategy performance and auto-manages."""

    def __init__(self, db, engine=None):
        self.db = db
        self.engine = engine
        self._last_daily_report: Optional[str] = None
        self._startup_done = False
        self._milestone_sent: set = set()  # Phase 65: milestone trade counts already notified

    async def run_check(self, cycle: int):
        """Called every N cycles by engine."""
        # One-time startup health check
        if not self._startup_done:
            await self._startup_health_check()
            self._startup_done = True

        # Every 300 cycles (~5 min): check loss streaks + PnL + rolling WR
        if cycle % 300 == 0:
            await self._check_loss_streaks()
            await self._check_pnl_health()
            await self._check_rolling_wr()

        # Phase 65: Every 600 cycles (~10 min): milestone trade count check
        if cycle % 600 == 0:
            await self._check_trade_milestones()

    # ═══ STARTUP HEALTH CHECK ═══
    async def _startup_health_check(self):
        """One-time check on startup: pause chronically losing strategies.

        Phase 49 A-03: Also runs an auto-resume pass FIRST so that healthy
        stopped strategies come back online after a restart. Otherwise the
        bot silently runs with strats=0 for hours when every strategy is
        stopped (observed 2026-04-09).
        """
        try:
            # ─── Phase 49 A-03: auto-resume healthy stopped strategies ───
            await self._startup_auto_resume()

            strategies = await self.db.get_active_strategies()

            # Phase 49 A-03: loud warning if we still have zero active strats
            if not strategies:
                try:
                    async with self.db.conn.execute(
                        "SELECT COUNT(*) FROM strategies"
                    ) as c:
                        total_row = await c.fetchone()
                    total = total_row[0] if total_row else 0
                except (aiosqlite.Error, TypeError):
                    # T8.1: DB execute (aiosqlite.Error) or total_row[0]
                    # subscript if row shape is unexpected (TypeError).
                    total = -1
                logger.warning(
                    f"⚠️ STARTUP: zero active strategies (total in DB={total}). "
                    f"Bot will scan markets but place no trades until /resume_all "
                    f"or AUTO_RESUME_ON_STARTUP is enabled."
                )
                await self._notify_paused(
                    [f"Total in DB: {total} — all stopped. "
                     f"Use /resume_all or set AUTO_RESUME_ON_STARTUP=true."],
                    "Zero Active Strategies"
                )
            paused = []
            for s in strategies:
                # Phase 82e HOTFIX: skip protected types (e.g., classic)
                if _is_protected_type(s):
                    continue
                stats = await self._get_strategy_stats(s.id)
                if not stats or stats["trades"] < MIN_TRADES_FOR_EVAL:
                    continue
                stype = getattr(s, 'strategy_type', 'fusion') or 'fusion'

                # Auto-pause if PnL below threshold (Phase 56: adaptive)
                threshold = _adaptive_pnl_threshold(stats["trades"])
                if stats["pnl"] < threshold:
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(s.id, StrategyStatus.STOPPED)
                    reason = f'PnL {stats["pnl"]:+.2f} < {threshold:.1f} (adaptive, {stats["trades"]}t)'
                    paused.append(f'{s.id[:8]} [{stype}] {s.asset.value}/{s.timeframe.value}: {reason}')
                    logger.warning(f"🛑 Startup pause: {s.id[:8]} [{stype}] "
                                   f"{s.asset.value}/{s.timeframe.value} → {reason}")

                # Extra scrutiny for risky types — only after enough trades
                elif (stype in TYPES_TO_WATCH and stats["trades"] >= MIN_TRADES_FOR_EVAL
                      and stats["pnl"] < -5.0 and stats["wr"] < 45):
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(s.id, StrategyStatus.STOPPED)
                    reason = f'{stype} WR={stats["wr"]:.0f}% PnL={stats["pnl"]:+.2f}'
                    paused.append(f'{s.id[:8]} [{stype}]: {reason}')
                    logger.warning(f"🛑 Startup pause: {s.id[:8]} [{stype}] → {reason}")

            if paused:
                logger.info(f"📋 Startup health check paused {len(paused)} strategies")
                await self._notify_paused(paused, "Startup Health Check")
            else:
                logger.info("✅ Startup health check: all strategies healthy")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: umbrella guard for startup health-check.
            # Inner blocks already narrow aiosqlite/ImportError paths; this
            # catches anything unexpected so a single bad strategy row can't
            # abort the entire startup sequence. Log and continue.
            logger.error(f"Startup health: {e}")

    # ═══ PNL-BASED AUTO-PAUSE ═══
    async def _check_pnl_health(self):
        """Periodically check if any strategy has gone chronically negative."""
        try:
            strategies = await self.db.get_active_strategies()
            for s in strategies:
                # Phase 82e HOTFIX: skip protected types (e.g., classic)
                if _is_protected_type(s):
                    continue
                stats = await self._get_strategy_stats(s.id)
                if not stats or stats["trades"] < MIN_TRADES_FOR_EVAL:
                    continue
                threshold = _adaptive_pnl_threshold(stats["trades"])
                if stats["pnl"] < threshold:
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(s.id, StrategyStatus.STOPPED)
                    stype = getattr(s, 'strategy_type', 'fusion') or 'fusion'
                    logger.warning(f"⚠️ PnL pause: {s.id[:8]} [{stype}] "
                                   f"PnL={stats['pnl']:+.2f} < {threshold:.1f} after {stats['trades']}t")
                    await self._notify_paused(
                        [f"{s.id[:8]} [{stype}] {s.asset.value}/{s.timeframe.value}: "
                         f"PnL={stats['pnl']:+.2f} WR={stats['wr']:.0f}%"],
                        "PnL Health Check")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: periodic-job umbrella. Inner `_get_strategy_stats`
            # + update_strategy_status narrows aiosqlite.Error; this outer
            # guard keeps the run_check cycle alive on unexpected exceptions.
            logger.error(f"PnL check: {e}")

    # ═══ Phase 52: ROLLING WIN-RATE AUTO-KILL ═══
    async def _check_rolling_wr(self):
        """Pause strategies whose recent WR has tanked below ROLLING_WR_KILL_THRESHOLD.

        This catches strategies that have enough total PnL to stay above
        PNL_PAUSE_THRESHOLD but are actively bleeding on recent trades.
        The rolling window (default 20) is env-tunable via ROLLING_WR_WINDOW.
        """
        rolling_window = _get_rolling_wr_window()
        kill_threshold = _get_rolling_wr_kill_threshold()
        if rolling_window < 10:
            return  # Safety: need a meaningful window
        try:
            strategies = await self.db.get_active_strategies()
            for s in strategies:
                # Phase 82e HOTFIX: skip protected types (e.g., classic)
                if _is_protected_type(s):
                    continue
                try:
                    rows = await self.db.conn.execute_fetchall(
                        """SELECT pnl FROM executions
                           WHERE strategy_id=? AND result IS NOT NULL
                           ORDER BY closed_at DESC LIMIT ?""",
                        (s.id, rolling_window))
                except aiosqlite.Error:
                    # T8.1: pure DB read. Per-strategy continue so one bad
                    # row doesn't break the rolling-WR sweep for others.
                    continue
                if len(rows) < rolling_window:
                    continue  # Not enough recent data
                wins = sum(1 for r in rows if r[0] > 0)
                wr = wins / len(rows) * 100
                if wr < kill_threshold:
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(s.id, StrategyStatus.STOPPED)
                    stype = getattr(s, 'strategy_type', 'fusion') or 'fusion'
                    logger.warning(
                        f"⚠️ Rolling WR kill: {s.id[:8]} [{stype}] "
                        f"{s.asset.value}/{s.timeframe.value}: "
                        f"WR={wr:.0f}% (last {len(rows)}t) < {kill_threshold}%")
                    try:
                        from core.changelog import log_change
                        await log_change(self.db, s.id, "ROLLING_WR_KILL", "adaptive_optimizer",
                                         old={"status": "active"}, new={"status": "stopped"},
                                         reason=f"WR={wr:.0f}% < {kill_threshold}% (last {len(rows)}t)",
                                         label=getattr(s, 'label', ''), wr=wr)
                    except (ImportError, AttributeError, aiosqlite.Error) as _ce:
                        # T8.1: import (ImportError on partial deploy),
                        # getattr(s,'label','') shouldn't raise but keep
                        # AttributeError defensively, aiosqlite.Error for
                        # the DB write inside log_change. Non-fatal audit
                        # trail — strategy is already stopped by L281.
                        logger.debug(f"rolling_wr changelog failed: {_ce}")
                    await self._notify_paused(
                        [f"{s.id[:8]} [{stype}] {s.asset.value}/{s.timeframe.value}: "
                         f"WR={wr:.0f}% (last {len(rows)}t)"],
                        f"Rolling WR &lt; {kill_threshold:.0f}%")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: periodic-job umbrella. Inner blocks narrow
            # aiosqlite.Error + changelog import/DB chain; this outer guard
            # keeps the 300-cycle tick alive on truly unexpected errors.
            logger.error(f"Rolling WR check: {e}")

    async def _get_strategy_stats(self, sid: str) -> Optional[dict]:
        """Get trade count, wins, losses, PnL for a strategy."""
        try:
            async with self.db.conn.execute(
                """SELECT COUNT(*) as trades,
                   COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as wins,
                   COALESCE(SUM(CASE WHEN pnl<=0 AND result IS NOT NULL THEN 1 ELSE 0 END),0) as losses,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN pnl ELSE 0 END),0) as pnl
                   FROM executions WHERE strategy_id=? AND result IS NOT NULL""",
                (sid,)) as c:
                r = await c.fetchone()
                if r and r["trades"] > 0:
                    t = r["trades"]
                    w = r["wins"]
                    return {"trades": t, "wins": w, "losses": r["losses"],
                            "pnl": r["pnl"], "wr": w / t * 100 if t > 0 else 0}
        except (aiosqlite.Error, KeyError, TypeError, ZeroDivisionError):
            # T8.1: DB execute (aiosqlite.Error), r["col"] indexing if the
            # row shape drifted (KeyError), type coercion in w/t arithmetic
            # (TypeError if None), defensive ZeroDivisionError even though
            # the `if t > 0` guard should prevent it.
            pass
        return None

    # ═══ LOSS STREAK CHECK (existing) ═══
    async def _check_loss_streaks(self):
        """Pause strategies on 5+ consecutive losses."""
        try:
            strategies = await self.db.get_active_strategies()
            for s in strategies:
                # Phase 82e HOTFIX: skip protected types (e.g., classic)
                if _is_protected_type(s):
                    continue
                recent = await self._get_recent_results(s.id, LOSS_STREAK_LIMIT)
                if len(recent) >= LOSS_STREAK_LIMIT and all(r == "lost" for r in recent):
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(s.id, StrategyStatus.STOPPED)
                    stype = getattr(s, 'strategy_type', 'fusion') or 'fusion'
                    logger.warning(f"⚠️ Streak pause: {s.id[:8]} [{stype}] "
                                   f"{s.asset.value}/{s.timeframe.value}: {LOSS_STREAK_LIMIT} consecutive losses")
                    await self._notify_paused(
                        [f"{s.id[:8]} [{stype}] {s.asset.value}/{s.timeframe.value}: "
                         f"{LOSS_STREAK_LIMIT} consecutive losses"],
                        "Loss Streak")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: periodic-job umbrella. Inner `_get_recent_results`
            # + update_strategy_status narrow aiosqlite.Error; this outer guard
            # keeps the 300-cycle tick alive on unexpected errors.
            logger.error(f"Streak check: {e}")

    async def _get_recent_results(self, strategy_id: str, limit: int) -> list:
        results = []
        try:
            async with self.db.conn.execute(
                "SELECT result FROM executions WHERE strategy_id=? AND result IS NOT NULL "
                "ORDER BY closed_at DESC LIMIT ?",
                (strategy_id, limit)) as c:
                async for row in c:
                    results.append(row["result"])
        except aiosqlite.Error:
            # T8.1: pure DB read (cursor + async iteration). Silent return
            # of partial/empty list is intentional — caller treats empty
            # as "not enough data".
            pass
        return results

    # ═══ Phase 49 A-03: STARTUP AUTO-RESUME ═══
    async def _startup_auto_resume(self):
        """Resume stopped strategies that have recovered.

        Gated behind AUTO_RESUME_ON_STARTUP env flag (default: false) so user
        intent is not overridden. When enabled, resumes strategies that meet
        ALL of:
          - status == STOPPED
          - trades >= MIN_TRADES_FOR_EVAL (8)
          - realized PnL >= AUTO_RESUME_MIN_PNL (default 0.0 — break-even)
          - win rate >= AUTO_RESUME_MIN_WR (default 50.0)
          - strategy_type NOT in TYPES_TO_WATCH (too risky to auto-revive)

        Only runs when AUTO_RESUME_ON_STARTUP=true. Otherwise logs nothing.
        """
        if os.getenv("AUTO_RESUME_ON_STARTUP", "false").lower() != "true":
            return
        try:
            min_pnl = float(os.getenv("AUTO_RESUME_MIN_PNL", "0.0"))
            min_wr = float(os.getenv("AUTO_RESUME_MIN_WR", "50.0"))
            rows = []
            async with self.db.conn.execute(
                "SELECT id, label, strategy_type FROM strategies WHERE status='stopped'"
            ) as c:
                async for row in c:
                    rows.append((row["id"], row["label"],
                                 row["strategy_type"] or "fusion"))

            if not rows:
                return

            resumed = []
            for sid, label, stype in rows:
                if stype in TYPES_TO_WATCH:
                    continue
                stats = await self._get_strategy_stats(sid)
                if not stats or stats["trades"] < MIN_TRADES_FOR_EVAL:
                    continue
                if stats["pnl"] < min_pnl:
                    continue
                if stats["wr"] < min_wr:
                    continue
                try:
                    from db.models import StrategyStatus
                    await self.db.update_strategy_status(sid, StrategyStatus.ACTIVE)
                    resumed.append(
                        f'{sid[:8]} [{stype}] {label or "?"}: '
                        f'PnL={stats["pnl"]:+.2f} WR={stats["wr"]:.0f}% '
                        f'({stats["trades"]}t)'
                    )
                    logger.info(
                        f"♻️ Auto-resumed: {sid[:8]} [{stype}] "
                        f"PnL={stats['pnl']:+.2f} WR={stats['wr']:.0f}%"
                    )
                except (ImportError, AttributeError, aiosqlite.Error) as e:
                    # T8.1: `from db.models import StrategyStatus` (ImportError
                    # on partial deploy), update_strategy_status (aiosqlite.Error
                    # on DB failure), defensive AttributeError if db shape drifts.
                    logger.warning(f"auto-resume {sid[:8]} failed: {e}")

            if resumed:
                logger.info(f"♻️ Auto-resumed {len(resumed)} strategies on startup")
                await self._notify_paused(resumed, "Auto-Resume (Phase 49)")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: startup umbrella. Inner per-strategy block
            # narrows ImportError/AttributeError/aiosqlite.Error; this outer
            # guard prevents one rogue row from blocking startup auto-resume.
            logger.error(f"startup auto-resume: {e}")

    # ═══ Phase 65: TRADE MILESTONE MONITOR ═══
    async def _check_trade_milestones(self):
        """Send Telegram report at 50, 100, 200, 500 trade milestones.
        Helps evaluate WR and PnL at key checkpoints."""
        milestones = [50, 100, 200, 500, 1000]
        try:
            async with self.db.conn.execute(
                """SELECT COUNT(*) as total,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN 1 ELSE 0 END),0) as settled,
                   COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),0) as wins,
                   COALESCE(SUM(CASE WHEN pnl <= 0 AND result IS NOT NULL THEN 1 ELSE 0 END),0) as losses,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN pnl ELSE 0 END),0) as pnl,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN fee ELSE 0 END),0) as fees
                   FROM executions"""
            ) as c:
                row = await c.fetchone()

            if not row:
                return

            settled = row["settled"] or 0
            for m in milestones:
                if settled >= m and m not in self._milestone_sent:
                    self._milestone_sent.add(m)
                    wins = row["wins"] or 0
                    losses = row["losses"] or 0
                    pnl = row["pnl"] or 0
                    fees = row["fees"] or 0
                    wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

                    # Per-strategy breakdown (top 5)
                    strat_lines = []
                    try:
                        async with self.db.conn.execute(
                            """SELECT s.label, COALESCE(s.strategy_type,'fusion') as stype,
                               COUNT(e.id) as t,
                               COALESCE(SUM(CASE WHEN e.pnl>0 THEN 1 ELSE 0 END),0) as w,
                               COALESCE(SUM(CASE WHEN e.pnl<=0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as l,
                               COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as p
                               FROM strategies s
                               JOIN executions e ON e.strategy_id=s.id
                               WHERE e.result IS NOT NULL
                               GROUP BY s.id ORDER BY p DESC LIMIT 5"""
                        ) as c:
                            strats = await c.fetchall()
                        for st in strats:
                            st_wr = (st["w"]/(st["w"]+st["l"])*100) if (st["w"]+st["l"])>0 else 0
                            emoji = "🟢" if st["p"] > 0 else "🔴"
                            strat_lines.append(
                                f"  {emoji} [{st['stype']}] {st['label'] or '?'}: "
                                f"{st['t']}t {st_wr:.0f}% WR {st['p']:+.2f}$")
                    except aiosqlite.Error as _me:
                        # T8.1: pure DB read for top-5 strategy breakdown.
                        # Empty strat_lines handled by the caller below
                        # ("(veri yok)" fallback).
                        logger.debug(f"milestone top-5 fetch failed: {_me}")

                    # Wallet balance
                    bal_text = ""
                    try:
                        async with self.db.conn.execute(
                            "SELECT balance FROM wallets WHERE is_primary=1 LIMIT 1"
                        ) as c:
                            w = await c.fetchone()
                            if w:
                                bal_text = f"\n💰 Bakiye: <b>${w['balance']:.2f}</b>"
                    except aiosqlite.Error as _we:
                        # T8.1: pure DB read for primary wallet balance.
                        # Missing bal_text falls through to empty string.
                        logger.debug(f"milestone wallet fetch failed: {_we}")

                    # Verdict
                    if wr >= 55:
                        verdict = "🟢 Shadow live'a geçiş için uygun"
                    elif wr >= 52:
                        verdict = "🟡 Marjinal — fee sonrası breakeven riski"
                    else:
                        verdict = "🔴 WR düşük — strateji optimizasyonu gerekli"

                    text = (
                        f"🏆 <b>MILESTONE: {m} Trade Tamamlandı!</b>\n\n"
                        f"📊 <b>Özet</b>\n"
                        f"Settled: {settled} | {wins}W/{losses}L\n"
                        f"WR: <b>{wr:.1f}%</b>\n"
                        f"PnL: <b>{pnl:+.2f}$</b>\n"
                        f"Fees: {fees:.2f}$\n"
                        f"{bal_text}\n\n"
                        f"<b>Top 5 Strateji</b>\n"
                    )
                    text += "\n".join(strat_lines) if strat_lines else "  (veri yok)"
                    text += f"\n\n<b>Karar:</b> {verdict}"

                    logger.info(f"🏆 Milestone {m}: WR={wr:.1f}% PnL={pnl:+.2f}")
                    await self._send_admin_message(text)
                    break  # Only send one milestone per check
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: periodic-job umbrella. Outer sum-aggregation
            # query + row[*] access; inner breakdown/wallet blocks narrow
            # aiosqlite.Error. Non-fatal — milestones are informational.
            logger.debug(f"milestone check: {e}")

    async def _send_admin_message(self, text: str):
        """Send message to admin via Telegram."""
        if not self.engine or not self.engine.bot_app:
            return
        try:
            admin_id = os.getenv("ADMIN_TELEGRAM_ID")
            if admin_id:
                await self.engine.bot_app.bot.send_message(
                    chat_id=int(admin_id), text=text, parse_mode="HTML")
            else:
                async with self.db.conn.execute("SELECT telegram_id FROM users LIMIT 1") as c:
                    user = await c.fetchone()
                    if user:
                        await self.engine.bot_app.bot.send_message(
                            chat_id=user["telegram_id"], text=text, parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: telegram send + DB fallback umbrella.
            # telegram.error.* hierarchy varies by ptb version (NetworkError,
            # RetryAfter, TimedOut, BadRequest, Forbidden) — catch-all is
            # safer than binding to ptb's internal types. Also covers the
            # int(admin_id) ValueError + aiosqlite.Error from the fallback
            # query. Non-fatal — notifications are best-effort.
            logger.debug(f"send_admin_message: {e}")

    async def _notify_paused(self, items: list, reason: str):
        """Send Telegram notification about paused strategies."""
        if not self.engine or not self.engine.bot_app:
            return
        try:
            text = f"🛑 <b>Auto-Pause: {reason}</b>\n\n"
            for item in items:
                text += f"• {item}\n"
            text += "\nUse /strategies to review and restart."

            # Get first user (admin)
            async with self.db.conn.execute("SELECT telegram_id FROM users LIMIT 1") as c:
                user = await c.fetchone()
                if user:
                    await self.engine.bot_app.bot.send_message(
                        chat_id=user["telegram_id"], text=text, parse_mode="HTML")
        except Exception as _ne:  # noqa: BLE001
            # T8.1 Faz 3 audit: telegram send + DB fetch umbrella.
            # Same rationale as _send_admin_message — ptb exception
            # hierarchy varies and aiosqlite.Error covers the DB side.
            # Upgraded from silent `pass` to debug log so audit trail
            # exists when notifications silently drop.
            logger.debug(f"notify_paused failed: {_ne}")

    # ═══ DAILY SUMMARY ═══
    async def generate_daily_summary(self, user_id: str) -> str:
        """Generate daily performance summary."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            async with self.db.conn.execute(
                """SELECT COUNT(*) as trades,
                   COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as wins,
                   COALESCE(SUM(CASE WHEN pnl<=0 AND result IS NOT NULL THEN 1 ELSE 0 END),0) as losses,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN pnl ELSE 0 END),0) as pnl,
                   COALESCE(SUM(trade_amount),0) as volume
                   FROM executions WHERE user_id=?
                   AND created_at >= ?""",
                (user_id, today + "T00:00:00")) as c:
                day = await c.fetchone()

            async with self.db.conn.execute(
                """SELECT s.id, s.asset, s.timeframe,
                   COALESCE(s.strategy_type, 'fusion') as stype,
                   COUNT(e.id) as trades,
                   COALESCE(SUM(CASE WHEN e.pnl>0 THEN 1 ELSE 0 END),0) as wins,
                   COALESCE(SUM(CASE WHEN e.pnl<=0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as losses,
                   COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
                   FROM strategies s
                   LEFT JOIN executions e ON e.strategy_id=s.id AND e.created_at >= ?
                   WHERE s.user_id=? AND s.status='active'
                   GROUP BY s.id ORDER BY pnl DESC""",
                (today + "T00:00:00", user_id)) as c:
                strats = await c.fetchall()

            async with self.db.conn.execute(
                """SELECT COALESCE(SUM(pnl),0) as total_pnl,
                   COUNT(*) as total_trades
                   FROM executions WHERE user_id=? AND result IS NOT NULL""",
                (user_id,)) as c:
                alltime = await c.fetchone()

            wallet = await self.db.get_active_wallet(user_id)
            balance = wallet.balance if wallet else 0

            trades = day["trades"] or 0
            wins = day["wins"] or 0
            losses = day["losses"] or 0
            day_pnl = day["pnl"] or 0
            wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

            text = (
                f"📊 <b>Daily Summary — {today}</b>\n\n"
                f"<b>Today</b>\n"
                f"Trades: {trades} | {wins}W/{losses}L ({wr:.0f}%)\n"
                f"PnL: <b>{day_pnl:+.2f} USDC</b>\n"
                f"Volume: ${day['volume'] or 0:.2f}\n\n"
                f"<b>By Strategy</b>\n")

            type_emoji = {"momentum": "📈", "contrarian": "🔄", "scalper": "⚡",
                          "sniper": "🎯", "fusion": "🔬"}
            for s in strats:
                st = s["trades"] or 0
                if st == 0:
                    continue
                sw = s["wins"] or 0
                sl = s["losses"] or 0
                sp = s["pnl"] or 0
                swr = (sw / (sw + sl) * 100) if (sw + sl) > 0 else 0
                te = type_emoji.get(s["stype"], "🔬")
                pe = "📈" if sp > 0 else "📉" if sp < 0 else "➖"
                text += f"{pe}{te} {s['id'][:6]} {s['asset']}/{s['timeframe']}: {st}t {swr:.0f}% <b>{sp:+.2f}</b>\n"

            text += (
                f"\n<b>All Time</b>\n"
                f"Total PnL: {alltime['total_pnl']:+.2f} | Trades: {alltime['total_trades']}\n"
                f"Balance: <b>${balance:.2f}</b>\n")

            return text

        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: user-facing report umbrella. Three DB
            # queries + wallet fetch + f-string assembly; narrow here
            # would hide partial-failure scenarios. Return-as-error
            # string is user-visible, so keep catch-all + log.
            logger.error(f"Daily summary: {e}")
            return f"Error generating summary: {e}"

    # ═══ Phase 26: Adaptive Threshold ═══

    async def adaptive_threshold_check(self):
        """Self-tuning: adjust strategy thresholds based on recent WR.
        If WR < 55% over last 20 trades → raise threshold by 0.05
        If WR > 70% over last 20 trades → lower threshold by 0.03

        Phase 79b: Added ADAPTIVE_DEAD_THRESHOLD — strategies that reach
        this threshold are auto-stopped (they can never fire anyway).
        Bounds: 0.40 minimum, ADAPTIVE_MAX_THRESHOLD maximum.
        """
        _MAX_THR = float(os.getenv("ADAPTIVE_MAX_THRESHOLD", "0.85"))
        _DEAD_THR = float(os.getenv("ADAPTIVE_DEAD_THRESHOLD", "0.85"))
        try:
            strats = await self.db.conn.execute_fetchall(
                "SELECT id, odds_threshold, strategy_type, label FROM strategies WHERE status='active'")
            for s in strats:
                sid, threshold, stype, label = s[0], s[1], s[2], (s[3] or "")
                # Phase 82e HOTFIX: skip protected types (e.g., classic)
                # This path auto-stops AND auto-bumps threshold; both are
                # harmful for user-managed strategies.
                if (stype or "").lower() in PROTECTED_STRATEGY_TYPES:
                    continue
                # Get last 20 trades
                recent = await self.db.conn.execute_fetchall(
                    """SELECT pnl FROM executions
                       WHERE strategy_id=? AND result IS NOT NULL
                       ORDER BY created_at DESC LIMIT 20""", (sid,))
                if len(recent) < 15:
                    continue  # Not enough data
                wins = sum(1 for r in recent if r[0] > 0)
                wr = wins / len(recent) * 100
                old_thr = threshold

                # Phase 79b: Auto-stop dead strategies (threshold already at ceiling)
                if threshold >= _DEAD_THR and wr < 45:
                    PROTECTED = {"M_BTC_5m_any_0.92", "BTC High-Threshold Pure"}
                    if label in PROTECTED:
                        continue
                    await self.db.conn.execute(
                        "UPDATE strategies SET status='stopped' WHERE id=?", (sid,))
                    await self.db.conn.commit()
                    logger.warning(
                        f"💀 ADAPTIVE_DEAD: {sid[:8]} [{stype}] {label} "
                        f"auto-stopped (thr={threshold:.2f} WR={wr:.0f}% — unreachable)")
                    try:
                        from core.changelog import log_change
                        await log_change(self.db, sid, "ADAPTIVE_DEAD", "adaptive_optimizer",
                                         old={"status": "active", "odds_threshold": threshold},
                                         new={"status": "stopped"},
                                         reason=f"thr={threshold:.2f} WR={wr:.0f}% — unreachable",
                                         label=label, wr=wr)
                    except (ImportError, AttributeError, aiosqlite.Error) as _ce:
                        # T8.1: import + DB write inside log_change. Already
                        # stopped above so failure is audit-trail only.
                        logger.debug(f"adaptive_dead changelog failed: {_ce}")
                    # Telegram notification
                    try:
                        if hasattr(self, 'engine') and self.engine and hasattr(self.engine, 'analyst'):
                            brain = self.engine.analyst
                            if brain:
                                await brain._send(
                                    f"💀 <b>Strateji Otomatik Durduruldu</b>\n"
                                    f"{label or sid[:8]} [{stype}]\n"
                                    f"Threshold {threshold:.2f} + WR {wr:.0f}% = kurtarilamaz")
                    except AttributeError as _ae:
                        # T8.1: `self.engine.analyst._send` attribute chain
                        # narrowed. Telegram send errors propagate from ptb
                        # and bubble up to the outer # noqa: BLE001 umbrella.
                        logger.debug(f"adaptive_dead notify attr-miss: {_ae}")
                    continue

                if wr < 55 and threshold < _MAX_THR:
                    # Phase 33: Skip protected strategies
                    PROTECTED = {"M_BTC_5m_any_0.92", "BTC High-Threshold Pure"}
                    if label in PROTECTED:
                        continue
                    threshold = min(threshold + 0.05, _MAX_THR)
                elif wr > 70 and threshold > 0.45:
                    threshold = max(threshold - 0.03, 0.40)
                else:
                    continue  # No change needed
                if abs(threshold - old_thr) >= 0.01:
                    await self.db.conn.execute(
                        "UPDATE strategies SET odds_threshold=? WHERE id=?",
                        (round(threshold, 2), sid))
                    await self.db.conn.commit()
                    logger.info(
                        f"🎯 ADAPTIVE: {sid[:8]} [{stype}] WR={wr:.0f}% "
                        f"threshold {old_thr:.2f}→{threshold:.2f}")
                    try:
                        from core.changelog import log_change
                        _dir = "raised" if threshold > old_thr else "lowered"
                        await log_change(self.db, sid, "ADAPTIVE_THRESHOLD", "adaptive_optimizer",
                                         old={"odds_threshold": old_thr}, new={"odds_threshold": round(threshold, 2)},
                                         reason=f"WR={wr:.0f}% → threshold {_dir}",
                                         label=label, wr=wr)
                    except (ImportError, AttributeError, aiosqlite.Error) as _ce:
                        # T8.1: import + DB write inside log_change. Threshold
                        # was already persisted above; this is audit-trail only.
                        logger.debug(f"adaptive_threshold changelog failed: {_ce}")
        except Exception as e:  # noqa: BLE001
            # T8.1 Faz 3 audit: adaptive threshold umbrella (`exc_info=True`
            # preserved). execute_fetchall + per-strategy update_strategy_status
            # + changelog — inner blocks narrow, this outer guard keeps the
            # optimizer cycle alive on unexpected errors.
            logger.error(f"Adaptive threshold FAILED: {e}", exc_info=True)
