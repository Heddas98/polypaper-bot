"""
PolyPaper Bot - /dashboard (v9 — Phase 33 Adaptive Intelligence)
Rich dashboard: balance, PnL, top strategies, regime, AI, TS ranking.
"""

import datetime
import logging

import aiosqlite
from telegram import Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from db.database import Database
from telegram_bot.banners import banner_dashboard
from telegram_bot.hub_keyboard import build_main_hub_keyboard
from telegram_bot.templates.safe_html import esc, esc_code, fmt_usd
from telegram_bot.version import BOT_VERSION

logger = logging.getLogger("polypaper.handlers.dashboard")

# Phase 52 ÖNERİ #1 — unified hub keyboard shared with /menu. Dashboard
# keeps its own refresh callback so the banner/caption edit path still
# works (menu uses menu_refresh which rebuilds the whole message).
DASHBOARD_BUTTONS = build_main_hub_keyboard(refresh_callback="refresh_dashboard")


async def _build(db, user, engine=None):
    try:
        wallet = await db.get_active_wallet(user.id)
        balance = wallet.balance if wallet else 0
        at = await db.conn.execute_fetchall(
            "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM executions WHERE result IS NOT NULL AND user_id=?",
            (user.id,),
        )
        alltime_pnl, total_trades = (at[0][0], at[0][1]) if at else (0, 0)
        op = await db.conn.execute_fetchall(
            "SELECT COUNT(*), COALESCE(SUM(trade_amount),0) FROM executions WHERE status='bet_placed' AND user_id=?",
            (user.id,),
        )
        open_count, open_exp = (op[0][0], op[0][1]) if op else (0, 0)
        strats = await db.conn.execute_fetchall(
            "SELECT COUNT(*) FROM strategies WHERE status='active' AND user_id=?", (user.id,)
        )
        active = strats[0][0] if strats else 0
        today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        tp = await db.conn.execute_fetchall(
            "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM executions WHERE result IS NOT NULL AND user_id=? AND created_at>=?",
            (user.id, today),
        )
        t_pnl, t_count = (tp[0][0], tp[0][1]) if tp else (0, 0)
        wins = await db.conn.execute_fetchall(
            "SELECT COUNT(*) FROM executions WHERE result IS NOT NULL AND pnl>0 AND user_id=?",
            (user.id,),
        )
        wr = (wins[0][0] / total_trades * 100) if wins and total_trades > 0 else 0

        # COMPACT LAYOUT: Key metrics only
        pe = "📈" if alltime_pnl >= 0 else "📉"
        te = "🟢" if t_pnl >= 0 else "🔴"
        text = (
            f"🏦 <b>PolyPaper Dashboard</b> <code>{BOT_VERSION}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 <b>{fmt_usd(balance)}</b> | {pe} {fmt_usd(alltime_pnl, sign=True)} | {te} {fmt_usd(t_pnl, sign=True)}\n"
            f"🎯 Win: <b>{wr:.0f}%</b> | 📊 Aktif: {active} | 📍 Acik: {open_count}\n"
        )

        # Engine status (if available)
        if engine:
            ws = "🟢" if engine._is_ws_fresh() else "⚫"
            regime_emoji = {"trending": "📈", "ranging": "↔️", "volatile": "🌪"}.get(
                engine.regime.regime, "❓"
            )
            text += f"\n{regime_emoji} <b>{engine.regime.regime.upper()}</b> | WS={ws}\n"

            # Phase 47f.9: risk snapshot directly on dashboard — no more
            # bouncing to /rs for the four numbers that decide whether to
            # keep the bot running vs kill it.
            try:
                rs = getattr(engine.risk, "state", None)
                lim = getattr(engine.risk, "limits", None)
                if rs and lim:
                    dpnl = float(getattr(rs, "daily_pnl", 0.0) or 0.0)
                    max_dl = float(getattr(lim, "max_daily_loss", 50.0) or 50.0)
                    streak = int(getattr(rs, "loss_streak", 0) or 0)
                    max_ls = int(getattr(lim, "max_loss_streak", 10) or 10)
                    exp_now = float(getattr(rs, "total_exposure", 0.0) or 0.0)
                    max_exp = float(getattr(lim, "max_total_exposure", 100.0) or 100.0)
                    halted = bool(getattr(rs, "halted", False))
                    halt_emoji = "🛑" if halted else ("⚠️" if dpnl <= -0.8 * max_dl else "✅")
                    pnl_pct = (dpnl / max_dl * 100) if max_dl > 0 else 0.0
                    exp_pct = (exp_now / max_exp * 100) if max_exp > 0 else 0.0
                    text += (
                        f"{halt_emoji} dPnL: <b>{dpnl:+.2f}</b>/"
                        f"{max_dl:.0f} ({pnl_pct:+.0f}%) | "
                        f"streak: <b>{streak}</b>/{max_ls} | "
                        f"exp: ${exp_now:.0f}/{max_exp:.0f} ({exp_pct:.0f}%)\n"
                    )
            except (AttributeError, KeyError, TypeError) as _re:
                # T11.8-B (2026-04-24): narrow from bare Exception. Risk
                # snapshot deep attribute access; missing engine.risk attr
                # is the common failure. Skip block, dashboard renders rest.
                logger.debug(f"dashboard risk snapshot: " f"{type(_re).__name__}: {_re}")

        text += "\n<i>Detaylar icin butonlari tıkla ➡️</i>"
        return text
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): _build outer wrapper intentionally wide.
        # Dashboard touches DB + wallet + strategies + risk + AI brain —
        # heterogeneous failure surface. Generic user message preserves UX.
        logger.error(f"Dashboard build error: {esc(str(e))}", exc_info=True)
        return "⚠️ Dashboard yukleme hatasi. Lütfen /start yaziniz."


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Eski `/dashboard` komutu — 2026-05-19 tek-kapı redesign'dan beri
    HİÇBİR komuta bağlı DEĞİL (`/dashboard` `/d` → mode-seçim ekranı).

    Detaylı dashboard içeriği artık PAPER MODE altında gösteriliyor
    (`main_dashboard.paper_dashboard` → `_build` reuse). Bu fonksiyon
    geriye-dönük uyumluluk için duruyor (banner'lı foto varyantı).
    """
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user or not user.accepted_terms:
        return await update.message.reply_text("Once /start kullanin.")
    try:
        text = await _build(db, user, context.bot_data.get("engine"))
        banner = banner_dashboard()
        await update.message.reply_photo(
            photo=banner, caption=text, parse_mode="HTML", reply_markup=DASHBOARD_BUTTONS
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outer command wrapper. Banner generation +
        # photo send may surface FileNotFoundError + TelegramError. Fall
        # back to plain reply.
        logger.error(f"Dashboard command error: {esc(str(e))}", exc_info=True)
        error_msg = "⚠️ Dashboard yukleme hatasi. Lütfen /start yaziniz."
        await update.message.reply_text(error_msg, parse_mode="HTML")


async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return
    text = await _build(db, user, context.bot_data.get("engine"))
    await update.callback_query.message.reply_text(
        text, parse_mode="HTML", reply_markup=DASHBOARD_BUTTONS
    )


async def refresh_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Yenileniyor...")
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return
    text = await _build(db, user, context.bot_data.get("engine"))
    try:
        await update.callback_query.edit_message_caption(
            caption=text, parse_mode="HTML", reply_markup=DASHBOARD_BUTTONS
        )
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): narrow from bare Exception. edit_message_
        # caption BadRequest "not modified" or original sent without photo
        # (no caption to edit). Fall back to fresh reply.
        await update.callback_query.message.reply_text(
            text, parse_mode="HTML", reply_markup=DASHBOARD_BUTTONS
        )


async def add_funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Once /start kullanin.")
    amount = 1000.0
    if context.args:
        try:
            amount = float(context.args[0])
        except ValueError:
            return await update.message.reply_text("Gecersiz miktar.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await update.message.reply_text("Cuzdan bulunamadi.")
    await db.conn.execute("UPDATE wallets SET balance=balance+? WHERE id=?", (amount, wallet.id))
    await db.conn.commit()
    await update.message.reply_text(
        f"✅ ${amount:.2f} eklendi! Yeni: ${wallet.balance+amount:.2f}", parse_mode="HTML"
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster J — merged from journal.py
# ═══════════════════════════════════════════════════════════════════════


async def journal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent trade log entries from DB.
    /journal          → son 10 entry
    /journal 20       → son 20 entry
    /journal wins     → sadece kazanclar
    /journal losses   → sadece kayiplar
    /journal rejects  → reddedilen tradeler
    """
    db: Database = context.bot_data["db"]

    args = context.args or []
    limit = 10
    event_filter = None

    for arg in args:
        if arg.isdigit():
            limit = min(int(arg), 30)
        elif arg.lower() in ("win", "wins", "won"):
            event_filter = "won"
        elif arg.lower() in ("loss", "losses", "lost"):
            event_filter = "lost"
        elif arg.lower() in ("reject", "rejects", "rejection"):
            event_filter = "REJECTION"
        elif arg.lower() in ("entry", "entries"):
            event_filter = "ENTRY"
        elif arg.lower() in ("exit", "exits"):
            event_filter = "EXIT"

    try:
        if event_filter == "REJECTION":
            rows = await db.conn.execute_fetchall(
                """SELECT tl.event, tl.slug, tl.reason, tl.ts, COALESCE(s.label, 'Unknown')
                FROM trade_log tl LEFT JOIN strategies s ON tl.strategy_id = s.id
                WHERE tl.event='REJECTION' ORDER BY tl.ts DESC LIMIT ?""",
                (limit,),
            )
        elif event_filter in ("won", "lost"):
            rows = await db.conn.execute_fetchall(
                """SELECT tl.event, tl.slug, tl.direction, tl.price, tl.pnl, tl.reason, tl.ts, COALESCE(s.label, 'Unknown')
                FROM trade_log tl LEFT JOIN strategies s ON tl.strategy_id = s.id
                WHERE tl.event='SETTLEMENT' AND tl.reason LIKE ?
                ORDER BY tl.ts DESC LIMIT ?""",
                (f"%{event_filter}%", limit),
            )
        elif event_filter:
            rows = await db.conn.execute_fetchall(
                """SELECT tl.event, tl.slug, tl.direction, tl.price, tl.amount, tl.pnl, tl.reason, tl.ts, COALESCE(s.label, 'Unknown')
                FROM trade_log tl LEFT JOIN strategies s ON tl.strategy_id = s.id
                WHERE tl.event=? ORDER BY tl.ts DESC LIMIT ?""",
                (event_filter, limit),
            )
        else:
            rows = await db.conn.execute_fetchall(
                """SELECT tl.event, tl.slug, tl.direction, tl.price, tl.amount, tl.pnl, tl.reason, tl.ts, COALESCE(s.label, 'Unknown')
                FROM trade_log tl LEFT JOIN strategies s ON tl.strategy_id = s.id
                ORDER BY tl.ts DESC LIMIT ?""",
                (limit,),
            )
    except aiosqlite.Error:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT raises
        # aiosqlite.OperationalError when trade_log table missing.
        # Table might not exist yet
        return await update.message.reply_text(
            "📓 <b>Trade Journal</b>\n\n"
            "DB tablosu henuz olusturulmadi.\n"
            "Bot yeniden baslatildiginda otomatik olusacak.\n\n"
            "<i>Yedek: data_store/trade_journal.jsonl</i>",
            parse_mode="HTML",
        )

    if not rows:
        return await update.message.reply_text(
            f"📓 Kayit bulunamadi. Filtre: {event_filter or 'tumu'}"
        )

    text = f"📓 <b>Trade Journal</b> (son {len(rows)})\n"
    text += f"Filtre: <b>{event_filter or 'tumu'}</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n\n"

    for r in rows:
        ts = str(r[-2])[5:16] if r[-2] else "?"
        event = r[0]
        slug = (r[1] or "")[:25]
        strat_label = (r[-1] or "Unknown")[:20]

        if event == "ENTRY":
            text += f"📥 {ts} {esc(slug)} <i>({strat_label})</i>\n"
            text += f"  {r[2] or '?'} @{r[3]:.3f} ${r[4]:.2f}\n" if r[3] else f"  {r[2] or '?'}\n"
        elif event == "SETTLEMENT":
            pnl = r[4] if len(r) > 4 and r[4] else 0
            emoji = "🟢" if pnl and pnl > 0 else "🔴"
            text += f"{emoji} {ts} {esc(slug)} <i>({strat_label})</i>\n"
            text += f"  pnl={pnl:+.2f} {r[5] or ''}\n" if pnl else f"  {r[5] or ''}\n"
        elif event == "EXIT":
            pnl = r[5] if len(r) > 5 else 0
            emoji = "💰" if pnl and pnl > 0 else "🛑"
            text += f"{emoji} {ts} {esc(slug)} <i>({strat_label})</i>\n"
            text += f"  {r[6] or ''} pnl={pnl:+.2f}\n" if pnl else ""
        elif event == "REJECTION":
            text += f"❌ {ts} {esc(slug)} <i>({strat_label})</i>\n"
            text += f"  {r[2] or '?'}\n"
        else:
            text += f"📋 {ts} {event} {esc(slug)} <i>({strat_label})</i>\n"

    text += "\n<i>/journal 20 | /journal wins | /journal losses | /journal rejects</i>"

    # Truncate for Telegram
    if len(text) > 4000:
        text = text[:3950] + "\n\n<i>... truncated</i>"

    await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster J — merged from info_handler.py
# ═══════════════════════════════════════════════════════════════════════


# Strategy parameter descriptions (Turkish)
PARAM_DESCRIPTIONS = {
    "odds_threshold": (
        "📊 Odds Threshold (Fiyat Eşiği)",
        "Minimum fiyat seviyesi. Örneğin 0.45 ayarlanırsa, "
        "sadece Up fiyatı 0.45'in üstündeyken trade açılır. "
        "Düşük eşik = daha fazla trade, yüksek eşik = daha seçici.",
    ),
    "trade_amount": (
        "💰 Trade Amount (İşlem Tutarı)",
        "Her trade'de kullanılacak USDC miktarı. "
        "AI stratejileri $1 ile başlar, 20+ trade sonrası scale edilir.",
    ),
    "direction": (
        "📈 Yön (Direction)",
        "UP = Fiyat yukarı gidecek bahsi. DOWN = aşağı. " "ANY = her iki yönde de trade açar.",
    ),
    "strategy_type": (
        "🎯 Strateji Tipi",
        "momentum: Trend takip. contrarian: Trend karşıtı. "
        "fusion: 6 sinyal birleşimi. scalper: Hızlı al-sat. "
        "sniper: Yüksek güvenli tek atış.",
    ),
    "tp_percent": (
        "🎯 Take Profit (%)",
        "Kâr al seviyesi. %10 ayarlanırsa, " "pozisyon %10 kârda otomatik kapatılır.",
    ),
    "sl_percent": (
        "🛑 Stop Loss (%)",
        "Zarar durdur. %5 ayarlanırsa, " "pozisyon %5 zararda otomatik kapatılır.",
    ),
    "min_odds": (
        "📉 Minimum Fiyat",
        "Bu fiyatın altında trade açılmaz. " "Örn: 0.10 = sadece 10c üstünde işlem.",
    ),
    "max_odds": (
        "📈 Maksimum Fiyat",
        "Bu fiyatın üstünde trade açılmaz. " "Örn: 0.90 = sadece 90c altında işlem.",
    ),
    "take_profit_odds": (
        "📈 Take Profit (Odds)",
        "Kâr al için fiyat deltası (odds cinsinden). "
        "Örn: 0.05 = pozisyon 0.05 fiyat deltasında otomatik kapatılır.",
    ),
    "stop_loss_odds": (
        "🛑 Stop Loss (Odds)",
        "Zarar durdur için fiyat deltası (odds cinsinden). "
        "Örn: 0.03 = pozisyon 0.03 fiyat kaybında otomatik kapatılır.",
    ),
    "price_difference": (
        "📐 Price Difference (%)",
        "Fiyat değişimi filtresi. Örn: %2 = sadece son 2 dakikada "
        "fiyat %2'den fazla değiştiğinde trade aç.",
    ),
    "ma_filter_enabled": (
        "📏 EMA Filtresi",
        "Exponential Moving Average (Üstel Hareketli Ortalama) filtresi. "
        "Açık (✅) = trend ile uyumlu trades. Kapalı (❌) = tüm trades.",
    ),
    "max_executions_per_event": (
        "🔢 Max Executions",
        "Aynı event'te maksimum trade sayısı. "
        "Örn: 2 = bir piyasa olayında en fazla 2 trade. "
        "0 veya boş = sınırsız.",
    ),
    "minutes_after_start": (
        "⏱ Başlangıç Gecikme (dakika)",
        "Event'ten kaç dakika sonra trade açılmaya başlansın. "
        "Örn: 5 = event'ten 5 dakika sonra trade aç.",
    ),
    "minutes_before_end": (
        "⏱ Bitiş Kaymasi (dakika)",
        "Event'ten kaç dakika kadar trade açılsın. "
        "Örn: 2 = event'ten 2 dakika kala trades kapat.",
    ),
    "label": (
        "📛 Strateji İsmi",
        "Stratejiniz için özel bir ad. Ayarlanmazsa otomatik ad verilir. "
        "Örn: 'High Risk Momentum' veya 'Contrarian BTC'",
    ),
}


async def info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle info_PARAMNAME callbacks with alert popups."""
    q = update.callback_query
    data = q.data

    # Extract parameter name: info_PARAMNAME
    if not data.startswith("info_"):
        return

    param_name = data[5:]  # Remove "info_" prefix

    if param_name not in PARAM_DESCRIPTIONS:
        await q.answer(f"Bilgi bulunamadi: {param_name}", show_alert=False)
        return

    title, desc = PARAM_DESCRIPTIONS[param_name]
    alert_text = f"<b>{title}</b>\n\n{desc}"

    # Show as alert popup
    await q.answer(alert_text, show_alert=True)


def get_param_info_button(param_name: str, button_label: str) -> dict:
    """
    Helper to create an info button for a parameter.
    Usage: InlineKeyboardButton(get_param_info_button("odds_threshold", "🎯 Tetik")["label"],
                                callback_data=get_param_info_button("odds_threshold", "🎯 Tetik")["data"])
    Or simpler: InlineKeyboardButton("🎯 Tetik", callback_data="info_odds_threshold")
    """
    return {"label": f"{button_label} ❓", "data": f"info_{param_name}"}


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster J — merged from price_alert_handler.py
# ═══════════════════════════════════════════════════════════════════════
from typing import Any  # noqa: E402  # Phase 51 P51-03 Faz-2 fix: was aliased but used as Any

VALID_OPS = {">", ">=", "<", "<=", "=="}


def _alerts_store(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, Any]]:
    bd = context.application.bot_data
    if "price_alerts" not in bd:
        bd["price_alerts"] = []
    return bd["price_alerts"]


async def alert_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Kullanım: <code>/alert SLUG OP PRICE</code>\n"
            "OP: &gt;, &gt;=, &lt;, &lt;=, ==\n"
            "Örn: <code>/alert btc-up-20260410 &gt;= 0.65</code>",
            parse_mode="HTML",
        )
        return
    slug = args[0].strip()
    op = args[1].strip()
    if op not in VALID_OPS:
        await update.effective_message.reply_text(
            f"Geçersiz OP: <code>{esc_code(op)}</code>. Valid: {', '.join(VALID_OPS)}",
            parse_mode="HTML",
        )
        return
    try:
        price = float(args[2])
    except ValueError:
        await update.effective_message.reply_text("Fiyat sayı olmalı.")
        return
    if not (0.0 < price < 1.0):
        await update.effective_message.reply_text("Fiyat 0<p<1 aralığında olmalı.")
        return
    alerts = _alerts_store(context)
    new_id = max((a["id"] for a in alerts), default=0) + 1
    alerts.append(
        {
            "id": new_id,
            "chat_id": update.effective_chat.id if update.effective_chat else None,
            "slug": slug,
            "op": op,
            "price": price,
            "fired": False,
        }
    )
    await update.effective_message.reply_text(
        f"✅ Alert #{new_id} eklendi: <code>{esc_code(slug)} {esc(op)} {price:.4f}</code>",
        parse_mode="HTML",
    )


async def alerts_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    alerts = _alerts_store(context)
    if not alerts:
        await update.effective_message.reply_text("📭 Aktif alert yok.")
        return
    lines = ["<b>🔔 Price Alerts</b>"]
    for a in alerts:
        flag = "✅" if not a.get("fired") else "🔕"
        lines.append(
            f"{flag} #{a['id']}: <code>{esc_code(a['slug'])} "
            f"{esc(a['op'])} {a['price']:.4f}</code>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def alert_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Kullanım: /alert_del ID")
        return
    try:
        aid = int(args[0])
    except ValueError:
        await update.effective_message.reply_text("ID sayı olmalı.")
        return
    alerts = _alerts_store(context)
    before = len(alerts)
    alerts[:] = [a for a in alerts if a["id"] != aid]
    if len(alerts) < before:
        await update.effective_message.reply_text(f"🗑 Alert #{aid} silindi.")
    else:
        await update.effective_message.reply_text(f"Alert #{aid} bulunamadı.")


def _check_op(current: float, op: str, target: float) -> bool:
    if op == ">":
        return current > target
    if op == ">=":
        return current >= target
    if op == "<":
        return current < target
    if op == "<=":
        return current <= target
    if op == "==":
        return abs(current - target) < 1e-6
    return False


async def price_alert_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback (run every 30s). Checks live prices against alerts.

    Wired in bot.py:
        app.job_queue.run_repeating(price_alert_job, interval=30, first=30)
    """
    app = context.application
    odds_feed = app.bot_data.get("odds_feed")
    if odds_feed is None:
        return
    alerts = _alerts_store(context)
    if not alerts:
        return
    for alert in alerts:
        if alert.get("fired"):
            continue
        slug = alert["slug"]
        try:
            current = odds_feed.get_price(slug) if hasattr(odds_feed, "get_price") else None
        except (AttributeError, KeyError, TypeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. odds_feed
            # cache lookup; missing slug surfaces as KeyError/TypeError.
            current = None
        if current is None:
            continue
        if _check_op(float(current), alert["op"], float(alert["price"])):
            alert["fired"] = True
            chat_id = alert.get("chat_id")
            if chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            f"🔔 <b>Price Alert #{alert['id']}</b>\n"
                            f"<code>{esc_code(slug)}</code>\n"
                            f"Current: {current:.4f} {esc(alert['op'])} "
                            f"{alert['price']:.4f}"
                        ),
                        parse_mode="HTML",
                    )
                except (TimeoutError, TelegramError) as e:
                    # T11.8-B (2026-04-24): narrow from bare Exception. Alert
                    # send transport — best effort, alert still recorded.
                    logger.warning(f"price_alert DM failed: " f"{type(e).__name__}: {e}")
