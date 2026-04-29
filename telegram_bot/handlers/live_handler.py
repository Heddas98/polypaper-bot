"""
PolyPaper Bot - /live command (Phase 34: Shadow Mode)
Button UI — toggle live, paper vs real side by side, trade history.
Real data feeds back into paper model calibration.
"""
import asyncio
import logging

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.live")


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main live dashboard with toggle buttons."""
    engine = context.bot_data.get("engine")
    if not engine or not hasattr(engine, 'live'):
        return await update.message.reply_text("Live trader bulunamadı.")
    text, kb = await _build_main(engine, context.bot_data.get("db"))
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def live_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all live_ button callbacks."""
    q = update.callback_query
    await q.answer()
    data = q.data
    engine = context.bot_data.get("engine")
    db = context.bot_data.get("db")
    if not engine or not hasattr(engine, 'live'):
        return

    if data == "live_toggle":
        # Phase 52 ÖNERİ #6 — enabling live mode now requires explicit
        # confirmation (real USDC is on the line). Disabling stays one
        # tap — users should always be able to kill live flow instantly.
        st = engine.live.get_status()
        currently_on = bool(st.get("enabled", False))
        if not currently_on:
            confirm_text = (
                "⚠️ <b>Live Mode'u etkinleştir?</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Bu adım gerçek <b>pUSD</b> kullanacaktır.\n\n"
                f"🤖 Bot risk limit: <b>${st.get('budget', 1.49):.2f}</b> (LIVE_BUDGET)\n"
                f"📌 Trade başı: <b>$1.00</b>\n"
                f"🎯 Strateji sayısı: <b>3 (whitelist)</b>\n\n"
                "<i>Polymarket gerçek bakiyeni /portfolio'dan kontrol et.</i>\n\n"
                "Emin misin?"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Evet, aç", callback_data="live_toggle_confirm"),
                 InlineKeyboardButton("❌ İptal", callback_data="live_toggle_cancel")],
            ])
            try:
                await q.edit_message_text(confirm_text, parse_mode="HTML", reply_markup=kb)
            except (BadRequest, TelegramError, asyncio.TimeoutError):
                # T11.8-B (2026-04-24): narrow from bare Exception. edit
                # BadRequest "not modified" or original message gone — fall
                # back to fresh reply.
                await q.message.reply_text(confirm_text, parse_mode="HTML", reply_markup=kb)
            return
        # Already on → toggle OFF immediately (no confirm)
        new_state = engine.live.toggle()
        logger.info(f"💰 Live trader → {'AKTIF 🟢' if new_state else 'KAPALI 🔴'}")
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_toggle_confirm":
        new_state = engine.live.toggle()
        logger.info(f"💰 Live trader → {'AKTIF 🟢' if new_state else 'KAPALI 🔴'} (confirmed)")
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_toggle_cancel":
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_compare":
        text = await _build_compare(engine)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")]])
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_history":
        text = await _build_history(engine)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Ana Panel", callback_data="live_main")]])
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    elif data == "live_main":
        text, kb = await _build_main(engine, db)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): same edit fallback pattern as confirm above.
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def _build_main(engine, db):
    """Main dashboard — paper + live side by side.

    2026-04-29 Aşama 3.A: cüzdan tutarlılığı fix. Eski 'Bütçe $1.49' satırı
    LIVE_BUDGET env (bot risk limit) idi — Heddas bunu 'gerçek bakiye'
    gibi görüyordu. Yeni format ayrım yapar:
      - 💰 Polymarket Bakiye (gerçek pUSD, portfolio cache'ten)
      - 🤖 Bot Risk Limit (LIVE_BUDGET env, harcama limiti)
    """
    st = engine.live.get_status()
    active = st.get("active", False)  # is_enabled() = _enabled and not _paused
    on = st["enabled"]
    paused = st.get("paused", False)

    if active:
        status_text = "✅ AKTIF"
    elif on and paused:
        status_text = "⏸ PAUSED"
    else:
        status_text = "🔴 KAPALI"

    # Paper stats
    p_pnl, p_trades, p_wr = 0, 0, 0
    if db:
        try:
            r = await db.conn.execute_fetchall(
                "SELECT COALESCE(SUM(pnl),0), COUNT(*), "
                "COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) "
                "FROM executions WHERE result IS NOT NULL")
            if r:
                p_pnl, p_trades = r[0][0], r[0][1]
                p_wr = r[0][2] / r[0][1] * 100 if r[0][1] > 0 else 0
        except (aiosqlite.Error, IndexError, TypeError, ZeroDivisionError):
            # T11.8-B (2026-04-24): narrow from bare Exception. Aggregate
            # SELECT + row[0][n] indexing + division by zero (defensive).
            pass

    # 2026-04-29 Aşama 3.A: Polymarket gerçek cüzdan bilgileri ortak
    # cache'ten oku. Eğer cache yok ise placeholder göster, tüm UI'lar
    # aynı kaynaktan beslenir (live + dashboard + start + portfolio).
    pm_balance = "N/A"
    pm_allowance = "N/A"
    pm_nav = "N/A"
    pm_age = ""
    try:
        from data.polymarket_portfolio import read_cached_snapshot, cache_age_seconds
        pm_snap = await read_cached_snapshot(db)
        if pm_snap:
            pm_balance = f"${float(pm_snap.get('pusd_balance', 0)):.2f}"
            pm_allowance = f"${float(pm_snap.get('pusd_allowance', 0)):.2f}"
            pm_nav = f"${float(pm_snap.get('portfolio_value_usd', 0)):.2f}"
            age_s = cache_age_seconds(pm_snap)
            pm_age = f" (veri {age_s}s önce)" if age_s < 999 else " (stale)"
    except Exception as _pe:  # noqa: BLE001
        logger.debug(f"pm cache read in live_handler: {_pe}")

    # 2026-04-29 Aşama 3.B: top-level mode banner
    from telegram_bot.templates.mode_banner import format_banner
    text = (
        format_banner()
        + f"💰 <b>PolyPaper — Live Trader</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 <b>PAPER</b> (simülasyon)\n"
        f"  PnL: {p_pnl:+.2f} | {p_trades}t | WR: {p_wr:.0f}%\n\n"
        f"💰 <b>POLYMARKET CÜZDAN</b> (gerçek){pm_age}\n"
        f"  pUSD Bakiye:     <b>{pm_balance}</b>\n"
        f"  Allowance:       {pm_allowance}\n"
        f"  Açık Pozisyon NAV: {pm_nav}\n\n"
        f"🤖 <b>BOT LIVE TRADER</b>\n"
        f"  Durum: {status_text}\n"
        f"  Cüzdan: {st['wallet']}\n"
        f"  Risk Limit: ${st.get('remaining', 0):.2f} / ${st.get('budget', 1.49):.2f}\n"
        f"  Trade başı:    $1.00 (LIVE_MAX_TRADE)\n"
        f"  Bot PnL: <b>${st['total_pnl']:+.4f}</b> | Bugün ${st['daily_pnl']:+.4f} ({st['daily_trades']}t)\n"
        f"  Pozisyon: {'📌 AÇIK' if st.get('open') else '—'}\n\n"
        f"<i>'Risk Limit' = bot toplam harcama tavanı (LIVE_BUDGET env). "
        f"'Bakiye' = Polymarket'taki gerçek pUSD. Detay → /portfolio</i>"
    )

    toggle_btn = "⏸ Duraklat" if active else "▶️ Devam Et"
    if not on:
        toggle_btn = "✅ Live Aç"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(toggle_btn, callback_data="live_toggle")],
        [InlineKeyboardButton("📊 Paper vs Real", callback_data="live_compare"),
         InlineKeyboardButton("📋 Live Geçmiş", callback_data="live_history")],
        [InlineKeyboardButton("🔄 Yenile", callback_data="live_main")],
    ])
    return text, kb


async def _build_compare(engine):
    """Side-by-side paper vs real from DB."""
    comp = await engine.live.get_comparison()
    if not comp or comp.get("error"):
        return (f"📊 <b>Paper vs Real</b>\n\n"
                f"<i>Henuz live trade yok veya DB hatasi.\n"
                f"Live modu ac ve trade bekle.</i>")

    lt = comp.get("total_trades", 0)
    lp = comp.get("live_pnl", 0)
    pp = comp.get("paper_pnl_equiv", 0)
    wr = comp.get("wr", 0)

    ratio = lp / max(abs(pp), 0.001) if pp != 0 else 0

    text = (
        f"📊 <b>Paper vs Real Karsilastirma</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Live trade: <b>{lt}</b> | WR: {wr:.0f}%\n\n"
        f"{'':8s} {'Paper':>8s}  {'Real':>10s}\n"
        f"{'PnL':8s} {pp:>+8.2f}  {lp:>+10.4f}\n\n"
    )

    if lt > 0:
        text += f"📈 Real/Paper oran: <b>{ratio:.1%}</b>\n"
        if ratio > 0.7:
            text += "✅ Paper sonuclar gercege yakin!\n"
        elif ratio > 0.3:
            text += "⚠️ Paper biraz iyimser — fee/slippage farki\n"
        else:
            text += "🔴 Sapma var — paper modeli kalibre edilmeli\n"

    # Recent trades
    recent = comp.get("recent", [])
    if recent:
        text += "\n<b>Son Trade'ler:</b>\n"
        for t in recent[:5]:
            e = "🟢" if (t.get("live_pnl") or 0) > 0 else "🔴"
            text += (f"  {esc(e)} {t.get('strat','')[:18]} {t.get('dir','')[:1].upper()} "
                    f"Live:{t.get('live_pnl',0):+.4f} Paper:{t.get('paper_pnl',0):+.2f}\n")

    return text


async def _build_history(engine):
    """Show live trade history from DB."""
    history = await engine.live.load_trade_history()

    text = (f"📋 <b>Live Trade Gecmisi</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n")

    if not history:
        text += "<i>Henuz live trade yok.</i>\n"
    else:
        for t in history[:10]:
            emoji = "🟢" if t.get("pnl", 0) > 0 else ("🔴" if t.get("result") else "⏳")
            text += (f"{emoji} {t.get('strategy','?')[:20]}\n"
                    f"  {t.get('direction','?').upper()} @{t.get('entry_odds',0):.3f} "
                    f"${t.get('amount',0):.2f} → "
                    f"Live:{t.get('pnl',0):+.4f} Paper:{t.get('pnl_paper',0):+.2f}\n")

    st = engine.live.get_status()
    text += f"\n💵 Toplam: ${st['total_pnl']:+.4f} | Kalan: ${st.get('remaining', 0):.2f}"
    return text


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster H — merged from ws_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def ws_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ws = context.bot_data.get("ws_client")
    if not ws:
        return await update.message.reply_text(
            "🔌 <b>WebSocket</b>\n\nStatus: ⚫ REST-only mode\n"
            "Install: <code>pip install websockets</code>", parse_mode="HTML")

    st = ws.get_status()
    e = "🟢" if st["connected"] else "🔴"
    age = f"{st.get('last_msg_age')}s ago" if st.get("last_msg_age") else "Never"

    text = (
        f"🔌 <b>WebSocket Status</b>\n\n"
        f"Connection: {esc(e)} {'Connected' if st['connected'] else 'Disconnected'}\n"
        f"Subscribed: {st.get('subscribed', 0)} tokens\n"
        f"Messages: {st.get('messages', 0)}\n"
        f"Errors: {st.get('errors', 0)}\n"
        f"Last message: {age}\n"
        f"Reconnects: {st.get('reconnects', 0)}\n"
        f"Cached prices: {st.get('cached_prices', 0)}\n")

    if st["connected"]:
        text += "\nReal-time data active."
    else:
        text += "\nFalling back to REST polling."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_ws")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def ws_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ws = context.bot_data.get("ws_client")
    if not ws:
        return await q.message.reply_text("🔌 REST-only mode.")

    st = ws.get_status()
    e = "🟢" if st["connected"] else "🔴"
    age = f"{st.get('last_msg_age', '?')}s" if st.get("last_msg_age") else "-"

    text = (f"🔌 {esc(e)} | Tokens: {st.get('subscribed',0)} | "
            f"Msgs: {st.get('messages',0)} | Last: {age}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_ws")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")]])
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster H — merged from daily_handler.py
# ═══════════════════════════════════════════════════════════════════════
from db.database import Database as _DailyDatabase  # noqa: E402
from core.auto_optimizer import AutoOptimizer as _DailyAutoOptimizer  # noqa: E402


async def _build_daily(db, engine, user_id):
    optimizer = _DailyAutoOptimizer(db, engine)
    text = await optimizer.generate_daily_summary(user_id)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Analytics", callback_data="show_analytics")],
        [InlineKeyboardButton("🎯 Strategy Stats", callback_data="strategy_stats")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")]])
    return text, kb


async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: _DailyDatabase = context.bot_data["db"]
    engine = context.bot_data.get("engine")
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    text, kb = await _build_daily(db, engine, user.id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: _DailyDatabase = context.bot_data["db"]
    engine = context.bot_data.get("engine")
    user = await db.get_user_by_telegram_id(q.from_user.id)
    if not user:
        return await q.message.reply_text("Önce /start komutunu kullanın.")
    text, kb = await _build_daily(db, engine, user.id)
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
