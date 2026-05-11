"""
PolyPaper Bot - /menu Hub Handler (Phase 38b rewrite)
Central menu with inline-rendered content for every button.

Phase 38b changes:
- All "hint-only" callbacks replaced with real inline rendering
- Backtest button opens a dedicated sub-menu (replay / v2 / compare)
- Uses a lightweight Update shim to reuse each handler's command function
  without duplicating rendering logic. Each handler already writes to
  `update.message.reply_text` / `reply_photo`; the shim maps that to the
  callback query's message so we get a fresh response per tap.

T11.8-B (2026-04-24): every `menu_X` callback below is a thin route
dispatcher that invokes a sub-command (markets, stats, risk, brain, etc.)
with its own exception surface. Wide `except Exception as e:` + generic
"⚠️ X yüklenemedi. /Y dene." fallback is intentional — the user gets a
clear retry hint and the full traceback goes to the operator log via
`exc_info=True`. Each catch is annotated `# noqa: BLE001` (T11.8-B
router-dispatch exemption).
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from db.database import Database
from telegram_bot.hub_keyboard import build_main_hub_keyboard
from telegram_bot.templates.safe_html import esc, fmt_usd
from telegram_bot.version import BOT_VERSION

logger = logging.getLogger("polypaper.handlers.menu")


# ═══════════════════════════════════════════════════════════════════
# Update shim — lets callback handlers reuse command-handler code that
# writes to update.message.reply_text() without any duplication.
# ═══════════════════════════════════════════════════════════════════
class _UpdateShim:
    """Proxies a real Update but remaps .message to a specific target message.

    This is necessary because menu button taps arrive as CallbackQuery updates,
    where `update.message` is None. The underlying command handlers (brain,
    candles, risk, markets, etc.) call `update.message.reply_text(...)` —
    so we swap the .message attribute to the callback query's message and
    delegate everything else to the real Update.
    """

    __slots__ = ("_real", "message")

    def __init__(self, real_update: Update, target_message):
        object.__setattr__(self, "_real", real_update)
        object.__setattr__(self, "message", target_message)

    def __getattr__(self, name):
        return getattr(self._real, name)


async def _invoke_command(command_fn, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Call a command handler from a callback context by shimming update.message."""
    q = update.callback_query
    shim = _UpdateShim(update, q.message)
    await command_fn(shim, context)


# ═══════════════════════════════════════════════════════════════════
# Main menu
# ═══════════════════════════════════════════════════════════════════
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display the hub menu with all main features as buttons."""
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user or not user.accepted_terms:
        return await update.message.reply_text("Once /start kullanin.")
    await _send(update.message, db, user, context.bot_data.get("engine"))


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh menu from callback."""
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user, context.bot_data.get("engine"))


async def _send(message, db, user, engine=None):
    """Build and send the hub menu."""
    try:
        wallet = await db.get_active_wallet(user.id)
        balance = wallet.balance if wallet else 0

        at = await db.conn.execute_fetchall(
            "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM executions WHERE result IS NOT NULL AND user_id=?",
            (user.id,),
        )
        alltime_pnl, total_trades = (at[0][0], at[0][1]) if at else (0, 0)

        wins = await db.conn.execute_fetchall(
            "SELECT COUNT(*) FROM executions WHERE result IS NOT NULL AND pnl>0 AND user_id=?",
            (user.id,),
        )
        wr = (wins[0][0] / total_trades * 100) if wins and total_trades > 0 else 0

        pe = "📈" if alltime_pnl >= 0 else "📉"
        text = (
            f"🏠 <b>PolyPaper Bot {BOT_VERSION}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Bakiye: {fmt_usd(balance, decimals=0)} | {pe} PnL: {fmt_usd(alltime_pnl, sign=True, decimals=0)}\n"
            f"📊 {total_trades:,} trade | {wr:.0f}% WR\n\n"
            f"<i>Ana menü — bas ve aç 👇</i>"
        )

        # Phase 52 ÖNERİ #1 — shared hub keyboard across /dashboard & /menu
        kb = build_main_hub_keyboard(refresh_callback="menu_refresh")

        await message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Menu build error: {esc(str(e))}", exc_info=True)
        await message.reply_text("⚠️ Menu yukleme hatasi. /start dene.")


# ═══════════════════════════════════════════════════════════════════
# Menu button callbacks — all render inline content now
# ═══════════════════════════════════════════════════════════════════
async def menu_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dashboard button — render dashboard inline."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.banners import banner_dashboard
        from telegram_bot.handlers.dashboard import DASHBOARD_BUTTONS, _build

        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if not user:
            return await q.message.reply_text("Önce /start kullanın.")
        text = await _build(db, user, context.bot_data.get("engine"))
        banner = banner_dashboard()
        await q.message.reply_photo(
            photo=banner, caption=text, parse_mode="HTML", reply_markup=DASHBOARD_BUTTONS
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_dashboard error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Dashboard yüklenemedi. /dashboard dene.")


async def menu_strategies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Strategies — render full interactive strategies panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.strategies import _send

        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _send(q.message, db, user)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_strategies error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Stratejiler yüklenemedi. /strategies dene.")


async def menu_brain_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Brain — render full AI Brain control panel with toggle buttons."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.ai_handler import brain_command

        await _invoke_command(brain_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_brain error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Brain yüklenemedi. /brain dene.")


async def menu_backtest_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Backtest — consolidated sub-menu with inline buttons."""
    q = update.callback_query
    await q.answer()
    text = (
        "🧪 <b>Backtest Merkezi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Bir motor seç — hepsi inline panelde açılır:\n\n"
        "🔄 <b>Replay</b>: Gerçek kaydedilmiş L2 orderbook (en gerçekçi)\n"
        "⚡ <b>Quick v2</b>: Hızlı config paneli, 11 strateji\n"
        "🏆 <b>Karşılaştır</b>: Tüm strateji PnL sıralaması"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Replay (Gerçek L2)", callback_data="menu_bt_replay")],
            [InlineKeyboardButton("⚡ Quick v2", callback_data="menu_bt_v2")],
            [InlineKeyboardButton("🏆 Karşılaştır", callback_data="menu_bt_compare")],
            [InlineKeyboardButton("⬅️ Ana Menü", callback_data="menu_refresh")],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def menu_bt_replay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submenu → open the replay panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.backtest_v2 import backtest_replay_command

        await _invoke_command(backtest_replay_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_bt_replay error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Replay yüklenemedi. /backtest_replay dene.")


async def menu_bt_v2_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submenu → open backtest v2 panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.backtest_v2 import backtest_v2_cmd

        await _invoke_command(backtest_v2_cmd, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_bt_v2 error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Backtest v2 yüklenemedi. /backtest_v2 dene.")


async def menu_bt_compare_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submenu → open compare panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.backtest_v2 import compare_cmd

        await _invoke_command(compare_cmd, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_bt_compare error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Karşılaştır yüklenemedi. /compare dene.")


async def menu_positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Positions — render full positions panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.positions import _show

        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _show(q.message, db, user, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_positions error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Pozisyonlar yüklenemedi. /positions dene.")


async def menu_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stats — render full stats panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.stats import _send_stats

        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _send_stats(q.message, db, user, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_stats error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ İstatistik yüklenemedi. /stats dene.")


async def menu_risk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Risk — render full risk panel via command handler shim."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.risk_handler import risk_command

        await _invoke_command(risk_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_risk error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Risk yüklenemedi. /risk dene.")


async def menu_market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Market — render full markets panel via command handler shim."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.markets import markets_command

        await _invoke_command(markets_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_market error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Piyasa yüklenemedi. /markets dene.")


async def menu_candles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Candles — render full candle collector panel via command handler shim."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.markets import candles_command

        await _invoke_command(candles_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_candles error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Mum verisi yüklenemedi. /candles dene.")


async def menu_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Settings — render full settings panel."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.settings_handler import _send_settings

        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _send_settings(q.message, db, user)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_settings error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Ayarlar yüklenemedi. /settings dene.")


async def menu_live_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Live mode — render full live trader panel via command handler shim."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.live_handler import live_command

        await _invoke_command(live_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_live error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Live yüklenemedi. /live dene.")


async def menu_learning_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Learning sub-menu — Phase 77 learning brain features."""
    q = update.callback_query
    await q.answer()
    text = (
        "🎓 <b>Öğrenme Merkezi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Bot hatalarından öğrenir, pattern keşfeder.\n\n"
        "📋 <b>Komutlar:</b>\n"
        "/why — Son trade kararlarını açıkla\n"
        "/mistakes — Overconfident kayıplar (yüksek sinyal, kayıp)\n"
        "/patterns — En iyi/kötü trading pattern'leri\n"
        # T1.3 Commit 4: /markov + /capital satırları kaldırıldı (phase76 ghost).
        "/lifecycle — Strateji yaşam döngüsü (/lc)\n"
        "/brier — Brier Score kalibrasyon raporu\n"
        "/ev_stats — EV gate istatistikleri\n"
        "/metrics — Sharpe / Sortino / MDD metrikleri"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 /why", callback_data="why_refresh"),
                InlineKeyboardButton("💀 /mistakes", callback_data="menu_cmd_mistakes"),
                InlineKeyboardButton("📊 /patterns", callback_data="patterns_refresh"),
            ],
            # T1.3 Commit 4 (2026-04-20): /markov + /capital butonları kaldırıldı —
            # phase76_handler.py ghost modüllere bağlıydı, silindi.
            [InlineKeyboardButton("⬅️ Ana Menü", callback_data="menu_refresh")],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def menu_experiment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Experiment sub-menu — safe parameter testing."""
    q = update.callback_query
    await q.answer()
    text = (
        "🔬 <b>Deney Merkezi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Güvenli parametre testi — sandbox ortamında.\n\n"
        "📋 <b>Kullanım:</b>\n"
        "/experiment KEY=VALUE — Parametre testi başlat\n"
        "/experiment_apply — Sonucu uygula (ENV'ye yaz)\n"
        "/experiment_discard — İptal et\n\n"
        "<b>📋 Örnek:</b>\n"
        "<code>/experiment MIN_COMPOSITE=0.30</code>\n"
        "<code>/experiment KELLY_FRACTION=0.15</code>\n"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Ana Menü", callback_data="menu_refresh")],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def menu_health_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Health sub-menu — module health dashboard."""
    q = update.callback_query
    await q.answer()
    try:
        from telegram_bot.handlers.phase77_handler import health_command

        await _invoke_command(health_command, update, context)
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_health error: {esc(e)}", exc_info=True)
        await q.message.reply_text("⚠️ Sağlık yüklenemedi. /health dene.")


async def menu_advanced_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Advanced sub-menu — evolutionary, swarm, cross-platform features."""
    q = update.callback_query
    await q.answer()
    # T1.3 Commit 5-6 (2026-04-20): Ghost komutlar (breed, vote, drift_check,
    # market_quality, correlation_check, whale) menüden çıkarıldı —
    # roadmap_handler.py'deki komutlar ve callback'ler silindi.
    text = (
        "🚀 <b>Gelişmiş Araçlar</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Piyasa analiz ve kalibrasyon araçları.\n\n"
        "📈 <b>Piyasa Analiz:</b>\n"
        "/surface — 2D kalibrasyon yüzeyi C(K,τ)\n"
        "/latency — WebSocket bağlantı durumu\n\n"
        "🔧 <b>Kalibrasyon:</b>\n"
        "/becker_recal_status — Becker recal durumu\n"
        "/becker_recal_manual — Becker manuel recal"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬅️ Ana Menü", callback_data="menu_refresh")],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _menu_cmd_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, cmd_name: str):
    """Generic callback → invoke a command handler by name."""
    q = update.callback_query
    await q.answer()
    try:
        import importlib

        # Map callback names to (module, function) pairs
        # T1.3 Commit 4 (2026-04-20): markov + capital entries removed —
        # phase76_handler.py ghost modüllere (core.markov_estimator,
        # core.capital_allocator) bağlıydı, silindi.
        # T1.3 Commit 6 (2026-04-20): breed/vote/whale entries removed —
        # roadmap_handler.py içindeki bu komutlar Commit 5'te silindi
        # (ghost modül bağımlılıkları: core.evolutionary, core.majority_voting,
        # data_feeds.whale_tracker).
        CMD_MAP = {
            "mistakes": ("telegram_bot.handlers.phase77_handler", "mistakes_command"),
        }
        if cmd_name in CMD_MAP:
            mod_path, fn_name = CMD_MAP[cmd_name]
            mod = importlib.import_module(mod_path)
            fn = getattr(mod, fn_name)
            await _invoke_command(fn, update, context)
        else:
            await q.message.reply_text(f"⚠️ /{cmd_name} bulunamadı.")
    except Exception as e:  # noqa: BLE001
        logger.error(f"menu_cmd_{cmd_name} error: {esc(e)}", exc_info=True)
        await q.message.reply_text(f"⚠️ /{cmd_name} yüklenemedi. Komutu doğrudan dene.")


async def menu_cmd_mistakes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _menu_cmd_callback(update, context, "mistakes")


# T1.3 Commit 4 (2026-04-20): menu_cmd_markov_callback + menu_cmd_capital_callback
# silindi — phase76_handler.py ghost modüllere bağlıydı.
# T1.3 Commit 6 (2026-04-20): menu_cmd_breed_callback + menu_cmd_vote_callback +
# menu_cmd_whale_callback silindi — roadmap_handler.py'deki komutlar Commit 5'te
# ghost olarak temizlendi.


async def menu_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help button — show quick help with all command sections."""
    q = update.callback_query
    await q.answer()
    text = (
        "❓ <b>Yardım & Komutlar</b>\n\n"
        "<b>🔥 Kısa Yollar</b>\n"
        "/d — Dashboard | /pos — Pozisyonlar | /s — Stratejiler\n"
        "/h — Sağlık | /cap — Sermaye | /lc — Yaşam döngüsü\n\n"
        "<b>🧠 AI</b>\n"
        "/brain — AI Brain kontrol | /ai — Doğal dil komut\n"
        "/analyze — AI analiz | /autopilot — AI önerileri\n\n"
        "<b>📊 Veri</b>\n"
        "/stats — İstatistik | /performance — Performans\n"
        "/kelly — Position sizing | /metrics — Sharpe/Sortino\n\n"
        "<b>🎓 Öğrenme</b>\n"
        "/why — Neden aldı? | /mistakes — Overconfident kayıplar\n"
        "/patterns — En iyi/kötü pattern'ler\n"
        "/markov — Markov Chain | /capital — Sermaye dağılımı\n\n"
        "<b>🧪 Backtest & Deney</b>\n"
        "/backtest_v2 — Hızlı v2\n"
        "/experiment KEY=VAL — Parametre testi\n\n"
        "<b>🚀 Gelişmiş</b>\n"
        "/breed — Genetik | /vote — Swarm | /whale — Whale akış\n"
        "/surface — 2D kalibrasyon | /latency — API latency\n\n"
        "<b>⚙️ Yönetim</b>\n"
        "/risk — Risk yönetimi | /kill — Acil durdur\n"
        "/health — Modül sağlığı | /help — Tam liste"
    )
    await q.message.reply_text(text, parse_mode="HTML")


async def menu_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh button — rebuild menu."""
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user, context.bot_data.get("engine"))
