"""
Phase 75+: /ev_stats command — Edge realization analysis
=========================================================

/ev_stats                 — Summary of all strategies' edge realization
/ev_stats [strategy]      — Detailed stats for one strategy
/ev_watch                 — Real-time edge quality monitor

Shows:
- Expected Value (model's theoretical edge)
- Realized PnL (actual outcome)
- Edge Realization Ratio = realized / expected
  ✅ 0.9-1.0 = excellent model
  ✅ 0.75-0.9 = good model
  ⚠️ 0.6-0.75 = acceptable (monitor)
  ❌ <0.6 = bad (overfitting/broken)
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.handlers._exc_render import render_user_exception
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.ev_stats")


async def ev_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show EV statistics for all strategies."""
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("DB unavailable")
            return

        from core.ev_tracker import EVTracker

        ev = EVTracker(db)

        # Get summary for all strategies
        summary = await ev.get_all_strategies_ev_summary()

        if not summary:
            await update.message.reply_text(
                "📊 <b>Edge Realization Stats</b>\n\n" "Henüz trade yok.",
                parse_mode="HTML",
            )
            return

        lines = ["<b>📊 Edge Realization Summary</b>\n"]
        lines.append("(ratio: realized / expected)\n")

        for label, stats in summary[:15]:  # Top 15
            trades = stats["trades"]
            avg_pnl = stats["avg_pnl"]
            edge_real = stats["edge_real"]
            wr = stats["wr"]

            # Color emoji based on quality
            if edge_real >= 0.9:
                emoji = "✅"
            elif edge_real >= 0.75:
                emoji = "✅"
            elif edge_real >= 0.6:
                emoji = "⚠️"
            else:
                emoji = "❌"

            lines.append(
                f"{emoji} <code>{label:30s}</code> "
                f"ratio={edge_real:5.2f} | PnL={avg_pnl:+6.2f} | "
                f"WR={wr:5.1f}% | n={trades:3d}\n"
            )

        text = "".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n..."

        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost handler wrapper intentionally wide.
        # EVTracker touches DB + analytic math + telegram — wide catch + T11.6
        # render policy to avoid leaking internal state to user.
        logger.error(f"/ev_stats failed: {e}", exc_info=True)
        await update.message.reply_text(
            render_user_exception(e, "❌ EV stats hatası"),
            parse_mode="HTML",
        )


async def ev_stats_strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed EV stats for a single strategy."""
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("DB unavailable")
            return

        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /ev_stats_detail [strategy_name]")
            return

        strategy_label = " ".join(args)

        # Get strategy ID
        rows = await db.conn.execute_fetchall(
            "SELECT id FROM strategies WHERE label LIKE ?",
            (f"%{strategy_label}%",),
        )

        if not rows:
            await update.message.reply_text(f"❌ Strateji bulunamadı: {esc(strategy_label)}")
            return

        strategy_id = rows[0][0]

        # Get stats
        from core.ev_tracker import EVTracker

        ev = EVTracker(db)
        stats = await ev.get_strategy_ev_stats(strategy_id)

        if stats["trade_count"] == 0:
            await update.message.reply_text(f"❌ {esc(strategy_label)}: trade yok")
            return

        # Quality color
        if stats["edge_quality"] == "excellent":
            emoji = "✅✅"
        elif stats["edge_quality"] == "good":
            emoji = "✅"
        elif stats["edge_quality"] == "acceptable":
            emoji = "⚠️"
        else:
            emoji = "❌"

        text = (
            f"{emoji} <b>{esc(strategy_label)}</b>\n\n"
            f"📊 <b>EV Analysis</b>\n"
            f"  Trades: {stats['trade_count']}\n"
            f"  Win Rate: {stats['win_rate']:.1f}%\n"
            f"  Avg Expected EV: ${stats['avg_expected_ev']:+.3f}\n"
            f"  Avg Realized PnL: ${stats['avg_realized_pnl']:+.3f}\n\n"
            f"🎯 <b>Edge Realization Ratio</b>\n"
            f"  Ratio: {stats['edge_realization_avg']:.3f}\n"
            f"  Quality: {stats['edge_quality']}\n\n"
        )

        # Interpretation
        ratio = stats["edge_realization_avg"]
        if ratio >= 0.9:
            text += "✅ Model mükemmel çalışıyor, scale et.\n"
        elif ratio >= 0.75:
            text += "✅ Model iyi, işe devam et.\n"
        elif ratio >= 0.6:
            text += "⚠️ Model kabul edilir ama monit et, overfitting riski.\n"
        else:
            text += "❌ Model kırık, pause et ve analiz et.\n"

        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): same outer-wrapper doctrine as ev_stats_command.
        # Per-strategy lookup + EVTracker chain.
        logger.error(f"/ev_stats_detail failed: {e}", exc_info=True)
        await update.message.reply_text(
            render_user_exception(e, "❌ EV stats hatası"),
            parse_mode="HTML",
        )
