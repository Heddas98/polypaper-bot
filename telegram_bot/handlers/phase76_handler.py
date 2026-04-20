"""
PolyPaper Bot - Phase 76 Handlers (/markov, /capital)
=====================================================
Markov Chain probability estimator display & Capital Allocator overview.
ADMIN ONLY.
"""
import logging
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import Settings

logger = logging.getLogger("polypaper.handlers.phase76")


def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


async def markov_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /markov [slug] — Show Markov Chain probability estimate for a market.
    If no slug given, shows status summary.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    markov = getattr(engine, "_markov", None)
    if markov is None:
        return await update.message.reply_text(
            "🔮 Markov Chain devre dışı.\n"
            "<code>MARKOV_ENABLED=true</code> ile aktifleştirin.",
            parse_mode="HTML")

    # If slug provided, show estimate for that market
    slug = " ".join(context.args) if context.args else None

    if slug:
        # Get odds series for this slug
        odds_feed = getattr(engine, "odds_feed", None)
        if not odds_feed:
            return await update.message.reply_text("Odds feed mevcut değil.")

        series = odds_feed.get_odds_series(slug, "up") or []
        if len(series) < 5:
            return await update.message.reply_text(
                f"🔮 <b>Markov — {slug[:30]}</b>\n"
                f"Yetersiz veri: {len(series)} tick (min 5)",
                parse_mode="HTML")

        best_ask = series[-1]
        try:
            result = markov.estimate(series, best_ask)
            text = markov.format_telegram(result, slug)
        except Exception as e:
            text = f"🔮 Markov hata: <code>{e}</code>"

        return await update.message.reply_text(text, parse_mode="HTML")

    # No slug: show summary status
    from core.markov_estimator import (
        MARKOV_ENABLED, MARKOV_N_STATES, MARKOV_N_SIMS,
        MARKOV_LOOKBACK, MARKOV_HORIZON, MARKOV_WEIGHT, MARKOV_MIN_EDGE
    )

    text = (
        "🔮 <b>Markov Chain Estimator</b>\n\n"
        f"Status: {'✅ Aktif' if MARKOV_ENABLED else '❌ Devre Dışı'}\n"
        f"States: {MARKOV_N_STATES}\n"
        f"MC Simülasyon: {MARKOV_N_SIMS:,}\n"
        f"Lookback: {MARKOV_LOOKBACK}\n"
        f"Horizon: {MARKOV_HORIZON}\n"
        f"Ağırlık: {MARKOV_WEIGHT}\n"
        f"Min Edge: {MARKOV_MIN_EDGE}\n\n"
        "<i>Kullanım: /markov &lt;slug&gt;</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def capital_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /capital — Show capital allocation table per strategy.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    ca = getattr(engine, "_capital_allocator", None)
    if ca is None:
        return await update.message.reply_text(
            "💰 Capital Allocator devre dışı.\n"
            "<code>CAPITAL_ALLOCATOR_ENABLED=true</code> ile aktifleştirin.",
            parse_mode="HTML")

    try:
        text = ca.format_telegram()
    except Exception as e:
        text = f"💰 Capital Allocator hata: <code>{e}</code>"

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    await update.message.reply_text(text, parse_mode="HTML")
