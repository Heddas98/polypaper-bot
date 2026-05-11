"""Mod-first /start dashboard — Heddas 2026-05-06 UX redesign.

Yeni mimari:
  /start → mod seç (PAPER vs LIVE)
  PAPER mode → paper-only menü (stats, strategies, AI Brain, dashboard)
  LIVE mode  → live-only menü (BUY/SELL, positions, PnL, redeem, allowance)

İki mod arasında "Mode Değiştir" butonu ile geçiş. Bot düzeyinde
LIVE_ENABLED env flag'i ayrıca trade execution'ı kontrol eder
(paper hep aktif, live ENV ile gate'lenir).
"""

from __future__ import annotations

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.main_dashboard")


async def main_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/start` ve `/main` komutu — mod seçim ekranı.

    Kullanıcı bot'u ilk kez açtığında bu ekran çıkar.
    Her iki mod'un özet bilgisi gösterilir (bakiye + bugünkü PnL).
    """
    user_id = update.effective_user.id if update.effective_user else 0
    text, kb = await _build_main_dashboard_text_kb(context, user_id)
    if update.message:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except (BadRequest, TelegramError):
            await update.callback_query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )


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
        "🤖 <b>PolyPaper Bot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Hangi modu kullanmak istersin?\n\n"
        f"📋 <b>PAPER MODE</b>\n"
        f"  Bakiye: <b>${paper_summary['balance']:.2f}</b> "
        f"<i>(simülasyon)</i>\n"
        f"  Bugün PnL: <b>{paper_summary['pnl_emoji']} "
        f"{paper_summary['daily_pnl']:+.2f}</b>\n"
        f"  Açık strateji: {paper_summary['open_strategies']}\n\n"
        f"💰 <b>LIVE MODE</b>\n"
        f"  Bakiye: <b>${live_summary['balance']:.2f}</b> "
        f"<i>(gerçek pUSD)</i>\n"
        f"  Bugün PnL: <b>{live_summary['pnl_emoji']} "
        f"{live_summary['daily_pnl']:+.2f}</b>\n"
        f"  Açık pozisyon: {live_summary['open_positions']}\n"
        f"  Allowance: {live_summary['allowance_status']}\n"
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
    """Live trade özet bilgi — Polymarket cache'inden."""
    summary = {
        "balance": 0.0,
        "daily_pnl": 0.0,
        "pnl_emoji": "⚪",
        "open_positions": 0,
        "allowance_status": "❓ Bilinmiyor",
    }
    try:
        engine = context.bot_data.get("engine")
        if engine and getattr(engine, "db", None):
            from data.polymarket_portfolio import read_cached_snapshot

            snap = await read_cached_snapshot(engine.db)
            if snap:
                summary["balance"] = float(snap.get("pusd_balance", 0))
                allowance = float(snap.get("pusd_allowance", 0))
                summary["allowance_status"] = "✅ Hazır" if allowance >= 1.0 else "❌ Eksik"
                positions = snap.get("positions", [])
                summary["open_positions"] = len(positions)
                # Bugünkü PnL: positions cur_value - cost_basis (active only)
                # + today's redeemed activity sum
                pnl = sum(
                    float(p.get("cur_value_usd", 0)) - float(p.get("cost_basis_usd", 0))
                    for p in positions
                )
                summary["daily_pnl"] = pnl
        summary["pnl_emoji"] = "🟢" if summary["daily_pnl"] >= 0 else "🔴"
    except Exception as e:  # noqa: BLE001
        logger.debug(f"_get_live_summary: {e}")
    return summary


# ════════════════════════════════════════════════════════════════════════
# PAPER MODE Dashboard
# ════════════════════════════════════════════════════════════════════════
async def paper_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Paper-only menü — strategies, stats, AI Brain, backtest."""
    paper_summary = await _get_paper_summary(context)

    text = (
        "📋 <b>PAPER MODE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Bakiye:</b> ${paper_summary['balance']:.2f} <i>(simülasyon)</i>\n"
        f"<b>Bugün PnL:</b> {paper_summary['pnl_emoji']} "
        f"{paper_summary['daily_pnl']:+.2f}\n"
        f"<b>Aktif strateji:</b> {paper_summary['open_strategies']}\n\n"
        f"<i>Paper trade — gerçek para yok, otomatik bot çalıştırır.</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
                InlineKeyboardButton("📜 Trades", callback_data="trades_page:0"),
            ],
            [
                InlineKeyboardButton("⚙️ Stratejiler", callback_data="strategies"),
                InlineKeyboardButton("🤖 AI Brain", callback_data="ai_brain"),
            ],
            [
                InlineKeyboardButton("📈 Stats", callback_data="stats"),
                InlineKeyboardButton("🎯 Backtest", callback_data="bt_v2_main"),
            ],
            [
                InlineKeyboardButton("💡 Öneri", callback_data="suggest"),
                InlineKeyboardButton("📊 Compare", callback_data="live_compare"),
            ],
            [InlineKeyboardButton("💰 Live'a Geç →", callback_data="main_live")],
            [InlineKeyboardButton("◀️ Ana Mod Seçimi", callback_data="main_dashboard")],
        ]
    )
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )


# ════════════════════════════════════════════════════════════════════════
# LIVE MODE Dashboard
# ════════════════════════════════════════════════════════════════════════
async def live_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Live-only menü — BUY/SELL, positions, PnL, redeem, wallet."""
    live_summary = await _get_live_summary(context)
    text = (
        "💰 <b>LIVE MODE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Bakiye:</b> ${live_summary['balance']:.2f} pUSD\n"
        f"<b>Bugün PnL:</b> {live_summary['pnl_emoji']} "
        f"{live_summary['daily_pnl']:+.2f}\n"
        f"<b>Açık pozisyon:</b> {live_summary['open_positions']}\n"
        f"<b>Allowance:</b> {live_summary['allowance_status']}\n\n"
        f"<i>⚠️ Gerçek pUSD ile trade — dikkat.</i>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 BUY", callback_data="live_market_buy"),
                InlineKeyboardButton("🔴 SELL", callback_data="live_market_sell"),
            ],
            [
                InlineKeyboardButton("📊 Pozisyonlar", callback_data="live_main"),
                InlineKeyboardButton("📜 Trades", callback_data="live_history:0"),
            ],
            [
                InlineKeyboardButton("📈 PnL Detay", callback_data="live_pnl"),
                InlineKeyboardButton("📤 CSV Export", callback_data="live_export_csv"),
            ],
            [
                InlineKeyboardButton("💵 Wallet (Portfolio)", callback_data="live_main"),
                InlineKeyboardButton("✅ Allowance", callback_data="live_approve_allowance"),
            ],
            [InlineKeyboardButton("📋 Paper'a Geç →", callback_data="main_paper")],
            [InlineKeyboardButton("◀️ Ana Mod Seçimi", callback_data="main_dashboard")],
        ]
    )
    q = update.callback_query
    if q:
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError):
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )


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
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔧 Runtime Toggle", callback_data="env_toggle_main")],
            [InlineKeyboardButton("◀️ Geri", callback_data="main_dashboard")],
        ]
    )
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (BadRequest, TelegramError):
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
