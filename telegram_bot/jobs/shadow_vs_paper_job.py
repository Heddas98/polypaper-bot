"""
Phase 47f.10 P5#22 — Shadow vs Paper Parallel Compare Job
==========================================================
Runs hourly. For each active strategy with both shadow (live_trades)
and paper (executions) fills in the last SHADOW_COMPARE_WINDOW_H hours,
computes PnL delta (shadow - paper) and WR delta. Fires a Telegram
alert ONLY when the deltas exceed thresholds — no noise on normal days.

Env:
    SHADOW_COMPARE_WINDOW_H=24
    SHADOW_COMPARE_PNL_ALERT=5.0     # USD delta to alert
    SHADOW_COMPARE_WR_ALERT=15.0     # percentage points delta
    SHADOW_COMPARE_MIN_TRADES=10     # per strategy, per bucket
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.shadow_vs_paper")


def _bucket_stats(rows):
    if not rows:
        return {"trades": 0, "wins": 0, "wr": 0.0, "pnl": 0.0}
    trades = len(rows)
    wins = sum(1 for r in rows if (r["pnl"] or r[0] or 0) > 0)
    pnl = sum((r["pnl"] or r[0] or 0) for r in rows)
    return {
        "trades": trades,
        "wins": wins,
        "wr": (wins / trades * 100.0) if trades else 0.0,
        "pnl": pnl,
    }


async def _query_paper_stats(db, strategy_id: str, cutoff: datetime) -> dict:
    """executions table: paper trades."""
    cutoff_iso = cutoff.isoformat()
    cur = await db.conn.execute(
        """SELECT pnl FROM executions
           WHERE strategy_id=? AND result IS NOT NULL
             AND closed_at >= ?""",
        (strategy_id, cutoff_iso),
    )
    rows = await cur.fetchall()
    # rows are Row objects — normalize to dict-like
    bucket = []
    for r in rows:
        try:
            bucket.append({"pnl": r["pnl"]})
        except (KeyError, IndexError, TypeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. Row access
            # by column name raises KeyError/IndexError; positional fallback
            # handles raw-tuple rows. TypeError covers None-subscript.
            bucket.append({"pnl": r[0] if len(r) else 0.0})
    return _bucket_stats(bucket)


async def _query_shadow_stats(db, strategy_label: str, cutoff: datetime) -> dict:
    """live_trades table: shadow live trades."""
    cutoff_iso = cutoff.isoformat()
    cur = await db.conn.execute(
        """SELECT paper_pnl FROM live_trades
           WHERE strategy_label=? AND paper_pnl IS NOT NULL
             AND settled_at >= ?""",
        (strategy_label, cutoff_iso),
    )
    rows = await cur.fetchall()
    bucket = [{"pnl": (r["paper_pnl"] if "paper_pnl" in r.keys() else r[0])} for r in rows]
    return _bucket_stats(bucket)


async def shadow_vs_paper_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback — hourly shadow-vs-paper delta check with alert-only output."""
    try:
        app = context.application
        db = app.bot_data.get("db")
        if db is None:
            logger.debug("shadow_vs_paper: no db")
            return

        window_h = float(os.getenv("SHADOW_COMPARE_WINDOW_H", "24"))
        pnl_alert = float(os.getenv("SHADOW_COMPARE_PNL_ALERT", "5.0"))
        wr_alert = float(os.getenv("SHADOW_COMPARE_WR_ALERT", "15.0"))
        min_trades = int(os.getenv("SHADOW_COMPARE_MIN_TRADES", "10"))

        cutoff = datetime.now(UTC) - timedelta(hours=window_h)

        # Fetch active strategies with labels
        cur = await db.conn.execute("SELECT id, label FROM strategies WHERE status='active'")
        strategies = await cur.fetchall()

        anomalies = []
        for row in strategies:
            sid = row["id"]
            label = row["label"] or ""
            if not label:
                continue

            paper = await _query_paper_stats(db, sid, cutoff)
            shadow = await _query_shadow_stats(db, label, cutoff)

            if paper["trades"] < min_trades or shadow["trades"] < min_trades:
                continue

            pnl_delta = shadow["pnl"] - paper["pnl"]
            wr_delta = shadow["wr"] - paper["wr"]

            if abs(pnl_delta) >= pnl_alert or abs(wr_delta) >= wr_alert:
                anomalies.append(
                    {
                        "label": label,
                        "paper": paper,
                        "shadow": shadow,
                        "pnl_delta": pnl_delta,
                        "wr_delta": wr_delta,
                    }
                )

        if not anomalies:
            logger.debug("shadow_vs_paper: no anomalies")
            return

        admin = resolve_admin_chat_id()
        if not admin:
            logger.warning("shadow_vs_paper: admin chat id unresolved")
            return

        lines = [f"⚠️ <b>Shadow vs Paper Delta ({int(window_h)}h)</b>\n"]
        for a in anomalies:
            lines.append(
                f"<b>{a['label']}</b>\n"
                f"  paper: {a['paper']['trades']}t WR{a['paper']['wr']:.1f}% PnL${a['paper']['pnl']:.2f}\n"
                f"  shadow: {a['shadow']['trades']}t WR{a['shadow']['wr']:.1f}% PnL${a['shadow']['pnl']:.2f}\n"
                f"  Δ: PnL${a['pnl_delta']:+.2f}  WR{a['wr_delta']:+.1f}pp\n"
            )
        msg = "\n".join(lines)
        try:
            await context.bot.send_message(chat_id=admin, text=msg, parse_mode="HTML")
            logger.info(f"shadow_vs_paper: sent {len(anomalies)} anomaly alerts")
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. send_message
            # raises TelegramError subclasses; asyncio.TimeoutError on
            # transport timeout. Unknown exceptions bubble to outer wrapper.
            logger.error(f"shadow_vs_paper send failed: " f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. JobQueue thread safety — see T7.6 job-safety exemption.
        logger.error(f"shadow_vs_paper_job failed: {e}", exc_info=True)
