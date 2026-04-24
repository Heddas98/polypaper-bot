"""
PolyPaper Bot - Phase 77 Handlers
===================================
/why        — Son trade kararlarını açıkla
/mistakes   — Tekrarlayan hatalar (overconfident kayıplar)
/patterns   — En iyi/kötü pattern'ler
/health     — Tüm modüllerin durumu + bağlantı haritası
/experiment — Güvenli parametre testi
/experiment_apply — Experiment sonucunu uygula
/experiment_discard — Experiment'i iptal et

ADMIN ONLY.
"""
import asyncio
import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from config.settings import Settings

logger = logging.getLogger("polypaper.handlers.phase77")


def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


# ═══════════════════════════════════════
# /why — Decision Explainer
# ═══════════════════════════════════════

async def why_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /why [slug] — Son trade kararlarını açıkla.
    Slug verilirse o market'in kararını gösterir.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    explainer = getattr(engine, "_explainer", None) if engine else None

    if explainer is None:
        return await update.message.reply_text(
            "🔍 Decision Explainer aktif değil.\n"
            "<code>DECISION_EXPLAINER_ENABLED=true</code>",
            parse_mode="HTML")

    slug = " ".join(context.args) if context.args else None

    if slug:
        chain = explainer.get_by_slug(slug)
        if chain:
            text = chain.format_telegram_full()
        else:
            text = f"<i>'{slug[:30]}' için karar bulunamadı.</i>"
    else:
        text = explainer.format_recent_telegram(n=8)

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data="why_refresh"),
        InlineKeyboardButton("📊 Dashboard", callback_data="show_dashboard"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def why_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh /why display."""
    q = update.callback_query
    await q.answer()

    engine = context.bot_data.get("engine")
    explainer = getattr(engine, "_explainer", None) if engine else None

    if explainer is None:
        return await q.edit_message_text("Decision Explainer aktif değil.")

    text = explainer.format_recent_telegram(n=8)
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data="why_refresh"),
        InlineKeyboardButton("📊 Dashboard", callback_data="show_dashboard"),
    ]])
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError, asyncio.TimeoutError):
        # T11.8-B (2026-04-24): narrow from bare Exception. edit_message_text
        # BadRequest "not modified" + transport failures. Refresh btn tolerates
        # no-op.
        pass


# ═══════════════════════════════════════
# /mistakes — Overconfident hata geçmişi
# ═══════════════════════════════════════

async def mistakes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mistakes — Tekrarlayan hatalar: yüksek sinyal ama kayıp olan trade'ler."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    memory = getattr(engine, "_trade_memory", None) if engine else None

    if memory is None:
        return await update.message.reply_text(
            "🧠 Trade Memory aktif değil.\n"
            "<code>TRADE_MEMORY_ENABLED=true</code>",
            parse_mode="HTML")

    text = memory.format_mistakes_telegram()
    await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════
# /patterns — En iyi/kötü pattern'ler
# ═══════════════════════════════════════

async def patterns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/patterns — En iyi ve kötü trade pattern'leri."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    memory = getattr(engine, "_trade_memory", None) if engine else None

    if memory is None:
        return await update.message.reply_text(
            "🧠 Trade Memory aktif değil.", parse_mode="HTML")

    best = await memory.get_best_patterns(5)
    worst = await memory.get_worst_patterns(5)

    text_best = memory.format_telegram(best, "En İyi Pattern'ler 🏆")
    text_worst = memory.format_telegram(worst, "En Kötü Pattern'ler ⚠️")

    text = text_best + "\n\n" + text_worst

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data="patterns_refresh"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def patterns_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh patterns."""
    q = update.callback_query
    await q.answer()

    engine = context.bot_data.get("engine")
    memory = getattr(engine, "_trade_memory", None) if engine else None
    if not memory:
        return

    best = await memory.get_best_patterns(5)
    worst = await memory.get_worst_patterns(5)
    text = memory.format_telegram(best, "En İyi 🏆") + "\n\n" + memory.format_telegram(worst, "En Kötü ⚠️")

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data="patterns_refresh"),
    ]])
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError, asyncio.TimeoutError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Same edit_message
        # no-op-tolerant pattern as why_callback above.
        pass


# ═══════════════════════════════════════
# /health — Module Health Dashboard
# ═══════════════════════════════════════

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/health — Tüm modül durumları ve bağlantı haritası."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    lines = ["🏥 <b>Modül Sağlık Raporu</b>\n"]

    # Core modules
    modules = [
        ("Engine", engine, "cycle"),
        ("Risk Manager", getattr(engine, "risk", None), "state"),
        ("Strategy Registry", getattr(engine, "registry", None), "_strategies"),
        ("Becker Calibrator", getattr(engine, "becker", None), "calibrated"),
        ("Thompson Sampler", getattr(engine, "thompson", None), "_alpha"),
        ("Odds Feed", getattr(engine, "odds_feed", None), "_cache"),
        ("WebSocket", getattr(engine, "ws", None), "connected"),
    ]

    # Phase 66+ modules
    modules += [
        ("Confluence Gate", getattr(engine, "_confluence", None), None),
        ("Technical Indicators", getattr(engine, "_tech_indicators", None), None),
        ("AI Brain", getattr(engine, "ai_brain", None), None),
        ("EV Tracker", getattr(engine, "_ev_tracker", None), None),
        ("Markov Estimator", getattr(engine, "_markov", None), None),
        ("Capital Allocator", getattr(engine, "_capital_allocator", None), None),
        ("Trade Memory", getattr(engine, "_trade_memory", None), None),
        ("Decision Explainer", getattr(engine, "_explainer", None), None),
        ("Experiment Runner", getattr(engine, "_experiment", None), None),
        ("Lifecycle Manager", getattr(engine, "lifecycle", None), None),
        ("Auto Optimizer", getattr(engine, "auto_optimizer", None), None),
    ]

    active = 0
    total = len(modules)

    for name, mod, attr in modules:
        if mod is not None:
            active += 1
            # Try to get a meaningful status
            status = "✅"
            detail = ""
            if attr:
                val = getattr(mod, attr, None)
                if isinstance(val, bool):
                    detail = "on" if val else "off"
                elif isinstance(val, (int, float)):
                    detail = str(val)
                elif isinstance(val, dict):
                    detail = f"{len(val)} items"
            lines.append(f"  {status} <b>{name}</b> {detail}")
        else:
            lines.append(f"  ❌ <b>{name}</b> <i>devre dışı</i>")

    lines.insert(1, f"Aktif: {active}/{total} modül\n")

    # Module interconnections
    lines.append("\n<b>🔗 Bağlantı Haritası:</b>")
    connections = [
        "Signal → Confluence → Markov → Memory → Sizing",
        "Fill → Capital.reserve → Journal → Memory.record",
        "Settle → Capital.release → Memory.record → Optimizer",
        "Optimizer → Lifecycle → Strategy.pause/resume",
        "Explainer → Fill.notify → Telegram",
    ]
    for c in connections:
        lines.append(f"  <code>{c}</code>")

    # ENV-controlled features
    lines.append("\n<b>⚙️ Kritik ENV'ler:</b>")
    envs = [
        ("TRADE_MEMORY_ENABLED", "true"),
        ("DECISION_EXPLAINER_ENABLED", "true"),
        ("EXPERIMENT_ENABLED", "true"),
        ("MARKOV_ENABLED", "true"),
        ("CAPITAL_ALLOCATOR_ENABLED", "true"),
        ("CONFLUENCE_ENABLED", "true"),
        ("AUTO_RESUME_ON_STARTUP", "true"),
    ]
    for key, default in envs:
        val = os.getenv(key, default)
        emoji = "🟢" if val.lower() == "true" else "🔴"
        lines.append(f"  {emoji} <code>{key}={val}</code>")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Yenile", callback_data="health_refresh"),
        InlineKeyboardButton("🔧 Diagnose", callback_data="show_diagnose"),
    ]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def health_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh health."""
    q = update.callback_query
    await q.answer("Yenileniyor...")
    # Re-run the command logic
    # Use a simple refresh message since full re-run needs update.message
    try:
        await q.edit_message_text(
            "🏥 /health komutunu tekrar çalıştırın.", parse_mode="HTML")
    except (BadRequest, TelegramError, asyncio.TimeoutError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Refresh hint msg.
        pass


# ═══════════════════════════════════════
# /experiment — Parametre Testi
# ═══════════════════════════════════════

async def experiment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /experiment KEY=VALUE KEY2=VALUE2 ...
    Güvenli parametre testi. Mevcut parametrelerle karşılaştırır.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    runner = getattr(engine, "_experiment", None) if engine else None

    if runner is None:
        return await update.message.reply_text(
            "🧪 Experiment Runner aktif değil.\n"
            "<code>EXPERIMENT_ENABLED=true</code>",
            parse_mode="HTML")

    if not context.args:
        # Show usage
        text = (
            "🧪 <b>Experiment Runner</b>\n\n"
            "<b>Kullanım:</b>\n"
            "<code>/experiment MIN_COMPOSITE=0.30 EDGE_GATE=0.40</code>\n\n"
            "Mevcut parametrelerle karşılaştırma yapar.\n"
            "Sonra /experiment_apply veya /experiment_discard.\n\n"
        )
        if runner.has_pending:
            text += "⚠️ Bekleyen experiment var. Önce apply/discard yapın."
        return await update.message.reply_text(text, parse_mode="HTML")

    params = runner.parse_params(context.args)
    if not params:
        return await update.message.reply_text(
            "⚠️ Format: KEY=VALUE. Örnek: <code>/experiment MIN_COMPOSITE=0.30</code>",
            parse_mode="HTML")

    await update.message.reply_text("🧪 Experiment çalışıyor...")

    result = await runner.run_experiment(params)
    text = runner.format_result_telegram(result)

    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    await update.message.reply_text(text, parse_mode="HTML")


async def experiment_apply_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/experiment_apply — Bekleyen experiment'i uygula."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    runner = getattr(engine, "_experiment", None) if engine else None

    if runner is None or not runner.has_pending:
        return await update.message.reply_text("🧪 Bekleyen experiment yok.")

    applied = runner.apply_pending()
    if applied:
        lines = ["✅ <b>Experiment Uygulandı</b>\n"]
        for key, (old_v, new_v) in applied.items():
            lines.append(f"  <code>{key}</code>: {old_v} → <b>{new_v}</b>")
        lines.append("\n⚠️ <i>Runtime ENV değişti. Kalıcı yapmak için .env dosyasını güncelleyin.</i>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Uygulama başarısız.")


async def experiment_discard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/experiment_discard — Bekleyen experiment'i iptal et."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    runner = getattr(engine, "_experiment", None) if engine else None

    if runner is None:
        return await update.message.reply_text("🧪 Runner aktif değil.")

    if runner.discard_pending():
        await update.message.reply_text("🗑️ Experiment iptal edildi.")
    else:
        await update.message.reply_text("Bekleyen experiment yok.")
