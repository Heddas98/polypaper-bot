"""
Phase 66: Paper vs Live PnL Divergence Daily Alert
===================================================
Source: A5 ($2.38M bot risk framework)

Runs daily. Compares aggregate paper trading PnL with shadow live PnL.
If divergence exceeds threshold → Telegram alert to admin.

This is DIFFERENT from shadow_vs_paper_job.py (which is per-strategy hourly).
This is an AGGREGATE daily summary: "Is our paper simulation trustworthy?"

Divergence > 5% means paper results can't be trusted for live scaling.

Env:
    PNL_DIVERGENCE_ENABLED=true
    PNL_DIVERGENCE_WINDOW_H=24          # look-back hours
    PNL_DIVERGENCE_ALERT_PCT=5.0        # % divergence threshold
    PNL_DIVERGENCE_MIN_TRADES=5         # min trades per bucket
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timedelta, timezone

import asyncio

from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.pnl_divergence")


def _resolve_admin():
    """Resolve admin chat ID from various env vars."""
    for key in ("ADMIN_TELEGRAM_ID", "ADMIN_CHAT_ID", "TELEGRAM_ADMIN_ID"):
        val = os.getenv(key)
        if val:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


async def pnl_divergence_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — daily paper-vs-live aggregate PnL divergence check."""
    if os.getenv("PNL_DIVERGENCE_ENABLED", "true").lower() != "true":
        return

    try:
        app = context.application
        db = app.bot_data.get("db")
        if db is None:
            return

        window_h = float(os.getenv("PNL_DIVERGENCE_WINDOW_H", "24"))
        alert_pct = float(os.getenv("PNL_DIVERGENCE_ALERT_PCT", "5.0"))
        min_trades = int(os.getenv("PNL_DIVERGENCE_MIN_TRADES", "5"))

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_h)).isoformat()

        # ═══ Paper PnL (executions table) ═══
        paper_row = await db.conn.execute_fetchall(
            """SELECT COUNT(*), COALESCE(SUM(pnl), 0),
                      COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0)
               FROM executions
               WHERE result IS NOT NULL AND closed_at >= ?""",
            (cutoff,))

        paper_trades = paper_row[0][0] if paper_row else 0
        paper_pnl = paper_row[0][1] if paper_row else 0.0
        paper_wins = paper_row[0][2] if paper_row else 0
        paper_wr = (paper_wins / paper_trades * 100) if paper_trades > 0 else 0

        # ═══ Shadow/Live PnL (live_trades table) ═══
        shadow_row = await db.conn.execute_fetchall(
            """SELECT COUNT(*), COALESCE(SUM(paper_pnl), 0),
                      COALESCE(SUM(CASE WHEN paper_pnl > 0 THEN 1 ELSE 0 END), 0)
               FROM live_trades
               WHERE paper_pnl IS NOT NULL AND settled_at >= ?""",
            (cutoff,))

        shadow_trades = shadow_row[0][0] if shadow_row else 0
        shadow_pnl = shadow_row[0][1] if shadow_row else 0.0
        shadow_wins = shadow_row[0][2] if shadow_row else 0
        shadow_wr = (shadow_wins / shadow_trades * 100) if shadow_trades > 0 else 0

        # ═══ Divergence Calculation ═══
        has_enough = paper_trades >= min_trades and shadow_trades >= min_trades
        pnl_delta = abs(shadow_pnl - paper_pnl)
        base_pnl = max(abs(paper_pnl), abs(shadow_pnl), 1.0)  # avoid div by zero
        divergence_pct = (pnl_delta / base_pnl) * 100

        wr_delta = abs(shadow_wr - paper_wr)

        # ═══ Build Report ═══
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        if has_enough and (divergence_pct >= alert_pct or wr_delta >= 10):
            # Alert: significant divergence
            status_emoji = "🔴" if divergence_pct >= 10 else "🟡"
            lines = [
                f"{status_emoji} <b>PnL Divergence Alert</b> ({int(window_h)}h)",
                f"━━━━━━━━━━━━━━━━━━━━━",
                f"",
                f"📄 <b>Paper:</b> {paper_trades}t | WR {paper_wr:.1f}% | PnL ${paper_pnl:+.2f}",
                f"🔴 <b>Shadow:</b> {shadow_trades}t | WR {shadow_wr:.1f}% | PnL ${shadow_pnl:+.2f}",
                f"",
                f"📊 <b>Divergence: {divergence_pct:.1f}%</b> (threshold: {alert_pct}%)",
                f"📊 WR Delta: {wr_delta:.1f}pp",
                f"",
            ]
            if divergence_pct >= 10:
                lines.append("⚠️ Paper sonuclari guvenilir DEGIL! Live scaling durdurun.")
            elif divergence_pct >= 5:
                lines.append("⚠️ Divergence yuksek. Paper-live farkini inceleyin.")
            lines.append(f"\n🕐 {now_str}")
        elif has_enough:
            # Green: low divergence — daily summary
            lines = [
                f"✅ <b>PnL Divergence OK</b> ({int(window_h)}h)",
                f"📄 Paper: {paper_trades}t WR{paper_wr:.1f}% ${paper_pnl:+.2f}",
                f"🔴 Shadow: {shadow_trades}t WR{shadow_wr:.1f}% ${shadow_pnl:+.2f}",
                f"📊 Divergence: {divergence_pct:.1f}% (threshold: {alert_pct}%)",
                f"🕐 {now_str}",
            ]
        else:
            # Not enough data — brief summary only
            logger.debug(
                f"pnl_divergence: insufficient data (paper={paper_trades}, "
                f"shadow={shadow_trades}, min={min_trades})")
            # Still send daily summary if paper has trades
            if paper_trades > 0:
                lines = [
                    f"📊 <b>Daily Paper Summary</b> ({int(window_h)}h)",
                    f"📄 Paper: {paper_trades}t | WR {paper_wr:.1f}% | PnL ${paper_pnl:+.2f}",
                    f"🔴 Shadow: {shadow_trades}t (min {min_trades} icin yetersiz)",
                    f"🕐 {now_str}",
                ]
            else:
                return  # nothing to report

        admin = _resolve_admin()
        if not admin:
            logger.warning("pnl_divergence: no admin chat id")
            return

        msg = "\n".join(lines)
        try:
            await context.bot.send_message(
                chat_id=admin, text=msg, parse_mode="HTML")
            logger.info(f"pnl_divergence: sent daily report "
                        f"(div={divergence_pct:.1f}%, paper={paper_trades}t, "
                        f"shadow={shadow_trades}t)")
        except (TelegramError, asyncio.TimeoutError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. send_message
            # raises TelegramError (NetworkError/BadRequest/Unauthorized) +
            # asyncio.TimeoutError on transport timeout. Other exceptions
            # propagate to outer job wrapper for visibility.
            logger.error(f"pnl_divergence send failed: "
                         f"{type(e).__name__}: {e}")

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. Job callbacks are scheduled by telegram.ext.JobQueue; an
        # unhandled exception would stop the scheduler thread and silently
        # kill future runs. Wide catch + exc_info=True preserves full trace
        # in logs while keeping the queue alive. This is the T7.6 job-safety
        # exemption pattern.
        logger.error(f"pnl_divergence_job failed: {e}", exc_info=True)
