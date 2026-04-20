"""
PolyPaper Bot - /positions Handler (NEW)
Shows all open positions with real-time unrealized PnL.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db.database import Database
from data.polymarket_client import safe_float
from telegram_bot.templates.safe_html import esc, fmt_usd


async def positions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if not user:
            await update.message.reply_text("Önce /start komutunu kullanın.")
            return
        await _show(update.message, db, user, context)
    except Exception as e:
        import logging
        logging.getLogger("polypaper.positions").error(f"positions_command error: {e}")
        await update.message.reply_text("⚠️ Pozisyonlar yüklenirken hata oluştu. Tekrar deneyin.")


async def positions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _show(q.message, db, user, context)
    except Exception as e:
        import logging
        logging.getLogger("polypaper.positions").error(f"positions_callback error: {e}")
        await q.message.reply_text("⚠️ Pozisyonlar yüklenirken hata oluştu.")


async def _show(message, db, user, context):
    try:
        positions = await db.get_open_positions(user.id)
    except Exception as e:
        import logging
        logging.getLogger("polypaper.positions").error(f"DB get_open_positions error: {e}")
        await message.reply_text("⚠️ DB'ye erişilemedi. /db_health ile durumu kontrol edin.")
        return
    scanner = context.bot_data.get("scanner")

    text = "📈 <b>Open Positions</b>\n\n"

    if not positions:
        text += "No open positions.\nStart a strategy to begin trading!"
    else:
        total_invested = 0.0
        total_unrealized = 0.0

        for i, pos in enumerate(positions, 1):
            slug = pos["event_slug"]
            direction = pos["direction"]
            entry = safe_float(pos["execution_price"]) or 0.5
            amount = pos["trade_amount"]
            fee = pos["fee_amount"] or 0
            shares = amount / entry if entry > 0 else 0
            total_invested += amount

            parts = slug.split("-")
            asset = parts[0].upper() if parts else "?"
            tf = parts[2] if len(parts) > 2 else "?"
            strat_label = pos.get("strategy_label") or f"{esc(asset)} {tf}"
            stype = pos.get("strategy_type") or "fusion"
            te = {"fusion": "🔬", "contrarian": "🔄", "sniper": "🎯",
                  "momentum": "📈", "scalper": "⚡", "martingale": "🎰", "highthreshold": "🏔️", "flashcrash": "💥", "streak": "🔄"}.get(stype, "🔬")

            # Live price
            current = None
            if scanner:
                odds = scanner.get_current_odds(slug)
                if odds:
                    current = safe_float(
                        odds.get("up_odds") if direction == "up" else odds.get("down_odds"))

            if current:
                value = shares * current
                unrealized = value - amount - fee
                total_unrealized += unrealized
                emoji = "🟢" if unrealized > 0 else "🔴"
                text += (
                    f"{i}. {emoji}{te} <b>{strat_label}</b> {direction.upper()}\n"
                    f"   Entry: {entry:.4f} → Now: {current:.4f}\n"
                    f"   Shares: {shares:.2f} | {fmt_usd(amount)}\n"
                    f"   PnL: <b>{fmt_usd(unrealized, sign=True)}</b>\n\n")
            else:
                text += (
                    f"{i}. ⏳{te} <b>{strat_label}</b> {direction.upper()}\n"
                    f"   Entry: {entry:.4f} | {fmt_usd(amount)}\n"
                    f"   Bekleniyor...\n\n")

        text += (
            f"<b>Summary</b>\n"
            f"Positions: {len(positions)} | Invested: {fmt_usd(total_invested)}\n"
            f"Unrealized PnL: <b>{fmt_usd(total_unrealized, sign=True)} USDC</b>")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_positions")],
        [InlineKeyboardButton("📊 Stats", callback_data="show_stats")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])
    await message.reply_text(text, parse_mode="HTML", reply_markup=kb)
