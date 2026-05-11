"""
PolyPaper Bot - /lifecycle Handler (Phase 74b)
===============================================
Shows per-strategy adaptive lifecycle parameters.
Displays phase (exploration/evaluation/proven), filter overrides,
size multipliers, and last adjustment reason for each strategy.

ADMIN ONLY — shows engine internals.
"""

import logging

import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Settings

logger = logging.getLogger("polypaper.handlers.lifecycle")


def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


async def lifecycle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lifecycle — Per-strategy adaptive parameter overview.

    Shows each strategy's lifecycle phase, filter overrides, and
    recent adjustments. Helps understand why some strategies get
    looser/tighter filters.

    ADMIN ONLY.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine or not hasattr(engine, "lifecycle"):
        return await update.message.reply_text("Engine veya lifecycle çalışmıyor.")

    lc = engine.lifecycle

    # If cache is empty, try to load from active strategies
    if not lc._cache:
        try:
            strategies = await engine.db.get_active_strategies()
            for s in strategies:
                await lc.get_params(s.id)
        except (aiosqlite.Error, AttributeError, KeyError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. get_active_
            # strategies DB query + lc.get_params cache miss path. Debug-log
            # only; missing cache entry surfaces downstream as empty table.
            logger.debug(f"lifecycle load: {type(e).__name__}: {e}")

    if not lc._cache:
        return await update.message.reply_text(
            "<i>Henüz lifecycle verisi yok. Stratejiler trade yapmaya başladığında dolacak.</i>",
            parse_mode="HTML",
        )

    # Build display
    lines = ["<b>📊 Strategy Lifecycle Manager</b>\n"]

    phase_counts = {"exploration": 0, "evaluation": 0, "proven": 0}
    for sid, p in sorted(lc._cache.items()):
        phase_counts[p.phase] = phase_counts.get(p.phase, 0) + 1

        emoji = {"exploration": "🔬", "evaluation": "📊", "proven": "✅"}.get(p.phase, "❓")

        # Get strategy label
        label = sid[:8]
        try:
            rows = await engine.db.conn.execute_fetchall(
                "SELECT label FROM strategies WHERE id=?", (sid,)
            )
            if rows and rows[0][0]:
                label = rows[0][0]
        except (aiosqlite.Error, IndexError):
            # T11.8-B (2026-04-24): narrow from bare Exception. Label lookup
            # is ornamental — if missing we fall back to sid prefix.
            pass

        lines.append(
            f"{emoji} <b>{label}</b> [{p.phase}]\n"
            f"   comp={p.min_composite:.2f} conv={p.conviction_min:.2f} "
            f"edge={p.edge_gate_mult:.2f} size={p.trade_amount_mult:.1f}x"
        )
        if p.adjustment_reason:
            lines.append(f"   └ <i>{p.adjustment_reason}</i>")

    # Summary
    lines.insert(
        1,
        f"🔬 Exploration: {phase_counts.get('exploration', 0)} | "
        f"📊 Evaluation: {phase_counts.get('evaluation', 0)} | "
        f"✅ Proven: {phase_counts.get('proven', 0)}\n",
    )

    text = "\n".join(lines)
    # Telegram max message length
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    await update.message.reply_text(text, parse_mode="HTML")
