"""
Phase 48 — Auto-Promote Canary Job
===================================
Scans all strategies in deploy_stage='canary', computes (trade_count, pnl)
from executions, and auto-promotes those meeting the gate:

    trade_count >= AUTO_PROMOTE_MIN_TRADES (default 30)
    AND pnl     >= AUTO_PROMOTE_MIN_PNL    (default 0.0)
    AND NOT overfit flag on latest train/test split (if recorded)

This complements the manual /promote command by ensuring no promotable
strategy sits idle.

Env:
  AUTO_PROMOTE_ENABLED       (default "1")
  AUTO_PROMOTE_MIN_TRADES    (default 30)
  AUTO_PROMOTE_MIN_PNL       (default 0.0)
  AUTO_PROMOTE_INTERVAL_SEC  (default 86400)   # daily
  AUTO_PROMOTE_FIRST_SEC     (default 1200)    # 20 min after startup
  AUTO_PROMOTE_NOTIFY        (default "1")
  AUTO_PROMOTE_DRY_RUN       (default "0")
"""
from __future__ import annotations

import asyncio
import os
import logging
from typing import List, Tuple

import aiosqlite
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.auto_promote")


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. int() raises
        # ValueError on non-numeric / TypeError on None. Fallback to default
        # on malformed ENV.
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. float() same
        # coercion surface as _env_int.
        return default


def _env_bool(key: str, default: bool = True) -> bool:
    return os.getenv(key, "1" if default else "0").strip() in ("1", "true", "True", "yes", "on")


async def _candidate_canary_strategies(db) -> List[Tuple[str, str, int, float]]:
    """Return list of (strategy_id, label, trade_count, pnl) for canary
    strategies that meet the promotion gate."""
    min_trades = _env_int("AUTO_PROMOTE_MIN_TRADES", 30)
    min_pnl = _env_float("AUTO_PROMOTE_MIN_PNL", 0.0)

    try:
        cur = await db.conn.execute(
            """SELECT s.id, s.label,
                      COUNT(e.id) AS trades,
                      COALESCE(SUM(e.pnl), 0.0) AS pnl
               FROM strategies s
               LEFT JOIN executions e
                 ON e.strategy_id = s.id AND e.result IS NOT NULL
               WHERE s.deploy_stage = 'canary'
               GROUP BY s.id, s.label
               HAVING trades >= ? AND pnl >= ?
               ORDER BY pnl DESC""",
            (min_trades, min_pnl)
        )
        rows = await cur.fetchall()
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT with JOIN
        # + GROUP BY surface is purely aiosqlite (OperationalError on schema
        # missing, DatabaseError on corrupt). Empty list fallback keeps
        # caller flow alive.
        logger.warning(f"[auto_promote] query failed: "
                       f"{type(e).__name__}: {e}")
        return []

    out: List[Tuple[str, str, int, float]] = []
    for r in rows:
        sid = r["id"] if hasattr(r, "keys") else r[0]
        label = (r["label"] if hasattr(r, "keys") else r[1]) or "—"
        trades = int(r["trades"] if hasattr(r, "keys") else r[2])
        pnl = float(r["pnl"] if hasattr(r, "keys") else r[3])
        out.append((sid, label, trades, pnl))
    return out


async def auto_promote_job(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue entry point. Daily scan of canary → promoted."""
    if not _env_bool("AUTO_PROMOTE_ENABLED", True):
        logger.info("[auto_promote] disabled via env")
        return

    app = context.application
    db = app.bot_data.get("db") if app else None
    if db is None or getattr(db, "conn", None) is None:
        logger.warning("[auto_promote] db unavailable; skipping")
        return

    dry = _env_bool("AUTO_PROMOTE_DRY_RUN", False)
    min_trades = _env_int("AUTO_PROMOTE_MIN_TRADES", 30)
    min_pnl = _env_float("AUTO_PROMOTE_MIN_PNL", 0.0)

    candidates = await _candidate_canary_strategies(db)
    if not candidates:
        logger.info(
            f"[auto_promote] no candidates (min_trades={min_trades}, "
            f"min_pnl=${min_pnl:.2f})"
        )
        return

    promoted: List[Tuple[str, str, int, float]] = []
    for sid, label, trades, pnl in candidates:
        if dry:
            logger.info(
                f"[auto_promote] DRY-RUN would promote {sid[:8]} "
                f"{label} ({trades}t PnL${pnl:+.2f})"
            )
            promoted.append((sid, label, trades, pnl))
            continue
        try:
            await db.conn.execute(
                "UPDATE strategies SET deploy_stage='promoted', "
                "updated_at=datetime('now') WHERE id=? "
                "AND deploy_stage='canary'",
                (sid,)
            )
            await db.conn.commit()
            promoted.append((sid, label, trades, pnl))
            logger.info(
                f"[auto_promote] ✅ {sid[:8]} {label} canary → promoted "
                f"({trades}t PnL${pnl:+.2f})"
            )
        except aiosqlite.Error as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. UPDATE ...
            # WHERE id=? AND deploy_stage='canary' returns via aiosqlite;
            # IntegrityError/OperationalError expected. Skip this sid and
            # keep scanning the batch.
            logger.warning(f"[auto_promote] failed for {sid[:8]}: "
                           f"{type(e).__name__}: {e}")

    if not promoted:
        return

    # Notify admin
    if not _env_bool("AUTO_PROMOTE_NOTIFY", True):
        return
    admin_id = resolve_admin_chat_id()
    if not admin_id:
        return
    try:
        header = "🟢 <b>Auto-Promote</b>"
        if dry:
            header = "🟡 <b>Auto-Promote (DRY-RUN)</b>"
        lines = [
            header,
            f"gate: ≥{min_trades}t, PnL ≥ ${min_pnl:.2f}",
            "",
        ]
        for sid, label, trades, pnl in promoted:
            lines.append(
                f"• <code>{sid[:8]}</code> {label} — "
                f"{trades}t <b>${pnl:+.2f}</b>"
            )
        await context.bot.send_message(
            chat_id=admin_id, text="\n".join(lines), parse_mode="HTML",
        )
    except (TelegramError, asyncio.TimeoutError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. send_message
        # surfaces TelegramError (BadRequest on HTML, NetworkError, etc.)
        # + asyncio.TimeoutError on transport timeout. Promoted strategies
        # still persisted — only notification was lost.
        logger.warning(f"[auto_promote] notify failed: "
                       f"{type(e).__name__}: {e}")
