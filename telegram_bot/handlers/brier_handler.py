"""
Phase 66: /brier command — Brier Score report on demand.
Shows prediction calibration quality, Murphy decomposition,
and calibration curve analysis.

Usage:
    /brier           → 7-day report (all sources)
    /brier 24        → 24-hour report
    /brier ai_brain  → filter by AI Brain decisions only
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.handlers._exc_render import render_user_exception

logger = logging.getLogger("polypaper.handler.brier")


async def brier_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /brier command."""
    try:
        db = context.application.bot_data.get("db")
        if not db:
            await update.message.reply_text("DB unavailable")
            return

        # Parse args
        hours = 168  # default 7 days
        source = None
        for arg in context.args or []:
            try:
                hours = int(arg)
            except ValueError:
                source = arg

        from utils.brier_tracker import BrierTracker

        tracker = BrierTracker(db)
        report = await tracker.get_report(source=source, hours=hours)
        text = tracker.format_report(report)

        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost handler wrapper intentionally wide.
        # Brier report touches DB + tracker format + telegram send — wide
        # catch + render_user_exception (T11.6 policy). exc_info=True to
        # preserve full trace for operator.
        logger.error(f"/brier failed: {e}", exc_info=True)
        await update.message.reply_text(
            render_user_exception(e, "❌ Brier error"),
            parse_mode="HTML",
        )
