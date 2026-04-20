"""
Phase 79 S3-09 — /report <id> — Strategy Lifecycle Report
Shows complete lifecycle of a strategy: creation, backtest, paper, zone breakdown.
"""
import logging
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import Database
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.strategy_report")


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /report <strategy_id_prefix> command."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "📊 <b>Strateji Raporu</b>\n\n"
            "Kullanim: <code>/report &lt;strateji_id&gt;</code>\n"
            "Ornek: <code>/report abc123</code>",
            parse_mode="HTML"
        )
        return

    id_prefix = args[0].lower()
    db: Database = context.bot_data.get("db")
    if not db:
        await update.message.reply_text("❌ DB baglantisi yok.")
        return

    # Find strategy by ID prefix
    try:
        rows = await db.conn.execute_fetchall(
            "SELECT id, label, asset, timeframe, strategy_type, status, "
            "odds_threshold, direction, trade_amount, created_at "
            "FROM strategies WHERE LOWER(id) LIKE ?",
            (f"{id_prefix}%",)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ DB hatasi: {esc(str(e))}", parse_mode="HTML")
        return

    if not rows:
        await update.message.reply_text(
            f"❌ <code>{esc(id_prefix)}</code> ile baslayan strateji bulunamadi.",
            parse_mode="HTML"
        )
        return

    r = rows[0]
    sid = r[0]
    label = r[1] or "?"
    asset = r[2] or "?"
    tf = r[3] or "?"
    stype = r[4] or "?"
    status = r[5] or "?"
    threshold = r[6] or 0
    direction = r[7] or "any"
    amount = r[8] or 1.0
    created = r[9] or "?"

    # Get execution stats
    try:
        stats_row = await db.conn.execute_fetchall(
            """SELECT COUNT(*),
                      SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                      SUM(pnl),
                      SUM(fee_amount)
               FROM executions WHERE strategy_id = ? AND status = 'claimed'""",
            (sid,)
        )
        total_t = int(stats_row[0][0] or 0)
        wins = int(stats_row[0][1] or 0)
        total_pnl = float(stats_row[0][2] or 0)
        total_fees = float(stats_row[0][3] or 0)
        wr = (wins / total_t * 100) if total_t > 0 else 0
        ev = total_pnl / total_t if total_t > 0 else 0
    except Exception:
        total_t, wins, total_pnl, total_fees, wr, ev = 0, 0, 0, 0, 0, 0

    # Get zone breakdown
    zones_text = ""
    try:
        zone_rows = await db.conn.execute_fetchall(
            """SELECT
                CASE
                    WHEN execution_price < 0.35 THEN '0-35c'
                    WHEN execution_price < 0.50 THEN '35-50c'
                    WHEN execution_price < 0.65 THEN '50-65c'
                    WHEN execution_price < 0.80 THEN '65-80c'
                    ELSE '80c+'
                END as zone,
                COUNT(*) as cnt,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as w,
                SUM(pnl) as zpnl
               FROM executions
               WHERE strategy_id = ? AND status = 'claimed'
               GROUP BY zone ORDER BY zone""",
            (sid,)
        )
        for zr in zone_rows:
            z_name, z_cnt, z_wins, z_pnl = zr
            z_cnt = int(z_cnt or 0)
            z_wins = int(z_wins or 0)
            z_pnl = float(z_pnl or 0)
            z_wr = (z_wins / z_cnt * 100) if z_cnt > 0 else 0
            icon = "✅" if z_pnl > 0 else "❌"
            zones_text += f"  {icon} {z_name}: {z_cnt}t {z_wr:.0f}% WR {z_pnl:+.2f}\n"
    except Exception:
        zones_text = "  (zone verisi yok)\n"

    if not zones_text:
        zones_text = "  (henuz trade yok)\n"

    # Get last 5 trades
    recent_text = ""
    try:
        recent = await db.conn.execute_fetchall(
            """SELECT direction, execution_price, pnl, created_at
               FROM executions WHERE strategy_id = ? AND status = 'claimed'
               ORDER BY created_at DESC LIMIT 5""",
            (sid,)
        )
        for rt in recent:
            d, p, pnl_val, ts = rt
            icon = "🟢" if (pnl_val or 0) > 0 else "🔴"
            recent_text += f"  {icon} {d or '?'} @{float(p or 0):.2f} → {float(pnl_val or 0):+.2f}\n"
    except Exception:
        recent_text = "  (veri yok)\n"

    if not recent_text:
        recent_text = "  (henuz trade yok)\n"

    # Status emoji
    status_emoji = {"active": "✅", "stopped": "⚫", "paused": "⏸"}.get(
        str(status).lower(), "❓"
    )

    # WR verdict
    if total_t >= 20:
        if wr >= 55:
            wr_verdict = "🏆 Güçlü"
        elif wr >= 50:
            wr_verdict = "✅ Pozitif"
        elif wr >= 45:
            wr_verdict = "⚠️ Marjinal"
        else:
            wr_verdict = "❌ Zayıf"
    else:
        wr_verdict = "📊 Yetersiz veri"

    text = (
        f"📊 <b>Strateji Raporu</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{esc(label)}</b> ({esc(sid[:8])})\n"
        f"🏷 {esc(stype)} | {esc(asset)} {esc(tf)} | {esc(direction)}\n"
        f"💰 ${float(amount):.2f} @ {float(threshold):.2f} | {status_emoji} {esc(status)}\n"
        f"📅 Olusturma: {esc(str(created)[:10])}\n\n"
        f"📈 <b>Performans</b>\n"
        f"  Trade: {total_t} | WR: {wr:.1f}% {wr_verdict}\n"
        f"  PnL: {total_pnl:+.2f} | EV/trade: {ev:+.4f}\n"
        f"  Fee: ${total_fees:.2f}\n\n"
        f"📊 <b>Zone Dagilimi</b>\n"
        f"{zones_text}\n"
        f"🔥 <b>Son 5 Trade</b>\n"
        f"{recent_text}"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧪 Test Et", callback_data=f"test_strat_{sid[:8]}"),
            InlineKeyboardButton("🔄 Yenile", callback_data=f"report_refresh_{sid[:8]}"),
        ],
    ])

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def report_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle report refresh button."""
    query = update.callback_query
    await query.answer("Yenileniyor...")
    # Extract ID prefix from callback data
    data = query.data or ""
    prefix = data.replace("report_refresh_", "")
    if prefix:
        context.args = [prefix]
        # Simulate command call
        await report_command(update, context)
