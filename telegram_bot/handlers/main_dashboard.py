"""Mod-first tek-kapı dashboard — Heddas 2026-05-06 UX redesign,
2026-05-19 tek-kapı + birleştirme.

Mimari:
  /start /main /dashboard /d → mod seç (PAPER vs LIVE) — bot'un TEK girişi
  PAPER MODE → detaylı paper dashboard (`dashboard._build`) + paper menü
  LIVE MODE  → `/live` trade istasyonu kokpiti (`live_handler._build_main`)

Mod seçimi YALNIZ navigasyondur — hangi menü dünyasını gördüğünü seçer.
Gerçek parayla trading'i açmak ayrıdır: kokpitteki 2-tık onaylı
`live_toggle` (LIVE_ENABLED env). Mod seçmek trading'i BAŞLATMAZ.
"""

from __future__ import annotations

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from telegram_bot.handlers.live_handler import _live_start_dt, _safe_edit
from telegram_bot.hub_keyboard import build_main_hub_keyboard

logger = logging.getLogger("polypaper.main_dashboard")


def _is_admin(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> bool:
    """Admin gate — mod ekranları gerçek pUSD bakiyesi + canlı durum sızdırır.

    2026-05-19 (tek-kapı redesign): /start /dashboard /d /main hepsi bu
    ekrana düşüyor; LIVE MODE özeti gerçek bakiyeyi gösterir. settings
    yoksa fail-closed (deny) — `live_handler._is_admin` ile aynı desen.
    """
    settings = context.bot_data.get("settings")
    if settings is None:
        logger.warning(
            "main_dashboard _is_admin: settings missing, denying %s", telegram_id
        )
        return False
    return bool(settings.is_admin(telegram_id))


async def main_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/start` `/main` `/dashboard` `/d` komutu — mod seçim ekranı.

    2026-05-19 "tek kapı": bot'un tek girişi. Kullanıcı her açışta
    PAPER vs LIVE seçer; seçtiği moda göre kendi menü dünyasına gider.
    Her iki mod'un özet bilgisi gösterilir (bakiye + bugünkü PnL).
    """
    user_id = update.effective_user.id if update.effective_user else 0
    if not _is_admin(context, user_id):
        if update.message:
            await update.message.reply_text("⛔ Admin only.")
        elif update.callback_query:
            await update.callback_query.answer("Admin only", show_alert=True)
        return
    text, kb = await _build_main_dashboard_text_kb(context, user_id)
    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    elif update.callback_query:
        await _safe_edit(update.callback_query, text, kb)


async def main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mod-first dashboard callback handler.

    Patterns:
      main_dashboard           → Ana ekran
      main_paper               → Paper mode menüsüne geç
      main_live                → Live mode menüsüne geç
      main_settings            → Bot genel ayarları
    """
    q = update.callback_query
    if not q:
        return
    if not _is_admin(context, q.from_user.id):
        await q.answer("Admin only", show_alert=True)
        return
    await q.answer()
    data = q.data or ""

    if data == "main_dashboard":
        await main_command(update, context)
    elif data == "main_paper":
        await paper_dashboard(update, context)
    elif data == "main_live":
        await live_dashboard(update, context)
    elif data == "main_settings":
        await _show_bot_settings(q)


async def _build_main_dashboard_text_kb(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build mod-first dashboard text + keyboard.

    Iki modun özet bilgisi:
      PAPER: bot DB'den paper bakiye + bugün PnL
      LIVE:  Polymarket cache'inden gerçek bakiye + bugün PnL + allowance
    """
    paper_summary = await _get_paper_summary(context)
    live_summary = await _get_live_summary(context)

    text = (
        "🤖 <b>PolyPaper Bot — Mod Seçimi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "İki ayrı dünya. Hangisinde çalışacaksın?\n\n"
        "📋 <b>PAPER MODE</b> — Simülasyon\n"
        "  <i>Sanal parayla strateji geliştir + backtest. Risk yok,\n"
        "  tüm stratejiler açık, bot 7/24 otomatik trade eder.</i>\n"
        f"  💵 <b>${paper_summary['balance']:,.2f}</b>  ·  "
        f"bugün {paper_summary['pnl_emoji']} {paper_summary['daily_pnl']:+.2f}  ·  "
        f"{paper_summary['open_strategies']} aktif strateji\n\n"
        "💰 <b>LIVE MODE</b> — Gerçek pUSD\n"
        "  <i>Polymarket'te gerçek parayla trade istasyonu. Manuel\n"
        "  BUY/SELL, piyasa/risk/guard panelleri, on-chain PnL.</i>\n"
        f"  💵 <b>${live_summary['balance']:,.2f}</b>  ·  "
        f"net {live_summary['pnl_emoji']} "
        f"{'+' if live_summary['net_pnl'] >= 0 else '-'}"
        f"${abs(live_summary['net_pnl']):.2f} "
        f"({live_summary['trades']} trade)  ·  "
        f"allowance {live_summary['allowance_status']}\n\n"
        "<i>Seçtiğin mod kendi menü dünyasını açar. Mod seçimi yalnız "
        "navigasyon — gerçek parayla trading'i LIVE MODE içinde ayrıca, "
        "açık onayla başlatırsın.</i>"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 PAPER MODE →", callback_data="main_paper")],
            [InlineKeyboardButton("💰 LIVE MODE →", callback_data="main_live")],
            [InlineKeyboardButton("⚙️ Bot Ayarları", callback_data="main_settings")],
        ]
    )
    return text, kb


async def _get_paper_summary(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Paper trade özet bilgi — bot DB'den."""
    summary = {
        "balance": 0.0,
        "daily_pnl": 0.0,
        "pnl_emoji": "⚪",
        "open_strategies": 0,
    }
    try:
        engine = context.bot_data.get("engine")
        if engine and getattr(engine, "db", None):
            db = engine.db
            # 2026-05-08 FIX: bot DB schema kullan — executions tablosu (trades degil),
            # column 'pnl' (pnl_usd degil), strategy status 'started' (active=1 degil).
            try:
                async with db.conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0) FROM executions "
                    "WHERE status='filled' AND date(closed_at) = date('now')"
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        summary["daily_pnl"] = float(row[0] or 0)
            except Exception:
                pass
            try:
                async with db.conn.execute(
                    "SELECT COUNT(*) FROM strategies WHERE status='started'"
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        summary["open_strategies"] = int(row[0] or 0)
            except Exception:
                pass
            # Paper balance: ENV display (gerçek paper bakiye trade journal'dan).
            # Default $10,386 (memory: 1417 trade, +$355 PnL, baseline $10,000)
            summary["balance"] = float(os.getenv("PAPER_BUDGET_DISPLAY", "10386.0"))
        summary["pnl_emoji"] = "🟢" if summary["daily_pnl"] >= 0 else "🔴"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"_get_paper_summary: {e}")
    return summary


async def _get_live_summary(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Live trade özet bilgi — Polymarket cache'inden.

    2026-05-19 (A8 audit): eski `daily_pnl` açık pozisyonların unrealized
    PnL'iydi ama "bugün" diye etiketleniyordu. Artık bot'un GERÇEK net
    PnL'i on-chain activity'den hesaplanır (`compute_live_pnl`) — `/live`
    kokpitiyle aynı kaynak.
    """
    summary = {
        "balance": 0.0,
        "net_pnl": 0.0,
        "trades": 0,
        "pnl_emoji": "⚪",
        "open_positions": 0,
        "allowance_status": "❓ Bilinmiyor",
    }
    try:
        engine = context.bot_data.get("engine")
        if engine and getattr(engine, "db", None):
            from data.polymarket_portfolio import compute_live_pnl, read_cached_snapshot

            snap = await read_cached_snapshot(engine.db)
            if snap:
                summary["balance"] = float(snap.get("pusd_balance", 0))
                allowance = float(snap.get("pusd_allowance", 0))
                summary["allowance_status"] = "✅ Hazır" if allowance >= 1.0 else "❌ Eksik"
                summary["open_positions"] = len(snap.get("positions", []) or [])
                activity = snap.get("activity") or []
                if activity:
                    lp = compute_live_pnl(
                        activity, int(_live_start_dt().timestamp())
                    )
                    summary["net_pnl"] = float(lp.get("net_pnl", 0.0))
                    summary["trades"] = int(lp.get("trades", 0))
        net = summary["net_pnl"]
        summary["pnl_emoji"] = "🟢" if net > 0 else ("🔴" if net < 0 else "⚪")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"_get_live_summary: {e}")
    return summary


# ════════════════════════════════════════════════════════════════════════
# PAPER MODE Dashboard
# ════════════════════════════════════════════════════════════════════════
async def paper_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """📋 PAPER MODE ana ekranı — detaylı paper dashboard + paper menü.

    2026-05-19 (tek-kapı redesign): mevcut `/dashboard` detaylı içeriği
    (`dashboard._build` — bakiye, all-time/bugün PnL, WR, rejim, risk
    snapshot) burada gösterilir + paper-scoped menü + LIVE MODE'a geçiş.
    PAPER MODE'un tek ana ekranı; eski ince özet menüsünün yerine geçti.
    """
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db") or getattr(engine, "db", None)
    body = "<i>Dashboard yüklenemedi — /legacy_start ile kayıt ol.</i>"
    try:
        from telegram_bot.handlers.dashboard import _build

        user = None
        if db is not None:
            uid = update.effective_user.id if update.effective_user else 0
            user = await db.get_user_by_telegram_id(uid)
        if user is not None:
            body = await _build(db, user, engine)
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"paper_dashboard build: {_e}")
    text = "📋 <b>PAPER MODE</b> · simülasyon\n\n" + body

    # 2026-05-19 audit (ölü buton fix): eski elle-yazılmış keyboard 6 ölü
    # callback içeriyordu (strategies/ai_brain/stats/bt_v2_main/
    # trades_page:0/suggest — hiçbiri bot.py'de kayıtlı DEĞİLdi). Kanıtlanmış
    # hub keyboard'a geçildi — tüm `menu_*` callback'leri kayıtlı + çalışır.
    hub = build_main_hub_keyboard(refresh_callback="main_paper")
    rows = list(hub.inline_keyboard) + [
        [
            InlineKeyboardButton("💰 LIVE MODE →", callback_data="main_live"),
            InlineKeyboardButton("◀️ Mode Seçimi", callback_data="main_dashboard"),
        ]
    ]
    kb = InlineKeyboardMarkup(rows)
    q = update.callback_query
    if q:
        await _safe_edit(q, text, kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════
# LIVE MODE Dashboard
# ════════════════════════════════════════════════════════════════════════
async def live_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💰 LIVE MODE ana ekranı — `/live` trade istasyonu kokpiti.

    2026-05-19 (tek-kapı redesign): eski ince live menüsü kaldırıldı.
    LIVE MODE artık doğrudan `/live` trade istasyonu kokpitini gösterir
    (`live_handler._build_main`) — tek live ekranı, çift bakım yok.
    Kokpit kendi keyboard'unda "◀️ Mode Seçimi" butonu taşır.
    """
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db") or getattr(engine, "db", None)
    try:
        from telegram_bot.handlers.live_handler import _build_main

        text, kb = await _build_main(engine, db)
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"live_dashboard build: {_e}")
        text = (
            "💰 <b>LIVE MODE</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            "<i>Trade istasyonu yüklenemedi — /live komutunu dene.</i>"
        )
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Mode Seçimi", callback_data="main_dashboard")]]
        )
    q = update.callback_query
    if q:
        await _safe_edit(q, text, kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _show_bot_settings(q) -> None:
    """Bot genel ayarları (her iki mod için ortak)."""
    text = (
        "⚙️ <b>Bot Ayarları</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Genel:</b>\n"
        f"  • Live: <code>{os.getenv('LIVE_ENABLED', 'false')}</code>\n"
        f"  • Mode default: <code>{os.getenv('MODE_DEFAULT', 'paper')}</code>\n"
        f"  • Auto-redeem: <code>{os.getenv('AUTO_REDEEM_ENABLED', 'false')}</code>\n\n"
        "<b>Komutlar:</b>\n"
        "  • <code>/envt</code> — runtime env toggle\n"
        "  • <code>/risk</code> — risk limits\n"
        "  • <code>/diagnose</code> — sistem health\n"
    )
    # 2026-05-19 audit: "🔧 Runtime Toggle" butonu (env_toggle_main) ölüydü
    # — bot.py'de callback handler'ı yok, /envt yalnız komut. Buton kaldırıldı;
    # metin zaten "/envt" komutunu söylüyor.
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Geri", callback_data="main_dashboard")]]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
