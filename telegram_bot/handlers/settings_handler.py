"""
PolyPaper Bot - /settings Handler
Notification preferences matching Polyscout's settings view.
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from db.database import Database
from telegram_bot.banners import banner_settings
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.settings")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command."""
    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)

    if not user:
        await update.message.reply_text("Önce /start komutunu kullanın.")
        return

    await _send_settings(update.message, db, user)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings button callback."""
    query = update.callback_query
    await query.answer()

    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)

    if not user:
        return

    await _send_settings(query.message, db, user)


async def _send_settings(message, db, user):
    """Render settings view like Polyscout."""
    on = lambda v: "ON" if v else "OFF"
    check = lambda v: "✅" if v else "❌"

    text = (
        f"⚙️ <b>Notification Settings</b>\n\n"
        f"Toggle which alerts you want to receive:\n\n"
        f"• Buy notifications: <b>{on(user.notify_buy)}</b>\n"
        f"• Stop-loss alerts: <b>{on(user.notify_stop_loss)}</b>\n"
        f"• Take-profit alerts: <b>{on(user.notify_take_profit)}</b>\n"
        f"• Claim results: <b>{on(user.notify_claim)}</b>\n"
        f"• No-buy alerts: <b>{on(user.notify_no_buy)}</b>\n"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Buy notifications: {check(user.notify_buy)}", callback_data="toggle_notify_buy")],
        [InlineKeyboardButton(f"Stop-loss alerts: {check(user.notify_stop_loss)}", callback_data="toggle_notify_stop_loss")],
        [InlineKeyboardButton(f"Take-profit alerts: {check(user.notify_take_profit)}", callback_data="toggle_notify_take_profit")],
        [InlineKeyboardButton(f"Claim results: {check(user.notify_claim)}", callback_data="toggle_notify_claim")],
        [InlineKeyboardButton(f"No-buy alerts: {check(user.notify_no_buy)}", callback_data="toggle_notify_no_buy")],
        [InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")],
    ])

    banner = banner_settings()
    await message.reply_photo(
        photo=banner,
        caption=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def toggle_notification_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle a notification preference."""
    query = update.callback_query
    await query.answer()

    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)

    if not user:
        return

    field = query.data.replace("toggle_", "")
    current = getattr(user, field, None)
    if current is not None:
        setattr(user, field, not current)
        await db.update_user(user)

    # Refresh settings view
    on = lambda v: "ON" if v else "OFF"
    check = lambda v: "✅" if v else "❌"

    text = (
        f"⚙️ <b>Notification Settings</b>\n\n"
        f"Toggle which alerts you want to receive:\n\n"
        f"• Buy notifications: <b>{on(user.notify_buy)}</b>\n"
        f"• Stop-loss alerts: <b>{on(user.notify_stop_loss)}</b>\n"
        f"• Take-profit alerts: <b>{on(user.notify_take_profit)}</b>\n"
        f"• Claim results: <b>{on(user.notify_claim)}</b>\n"
        f"• No-buy alerts: <b>{on(user.notify_no_buy)}</b>\n"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Buy notifications: {check(user.notify_buy)}", callback_data="toggle_notify_buy")],
        [InlineKeyboardButton(f"Stop-loss alerts: {check(user.notify_stop_loss)}", callback_data="toggle_notify_stop_loss")],
        [InlineKeyboardButton(f"Take-profit alerts: {check(user.notify_take_profit)}", callback_data="toggle_notify_take_profit")],
        [InlineKeyboardButton(f"Claim results: {check(user.notify_claim)}", callback_data="toggle_notify_claim")],
        [InlineKeyboardButton(f"No-buy alerts: {check(user.notify_no_buy)}", callback_data="toggle_notify_no_buy")],
        [InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")],
    ])

    try:
        await query.edit_message_caption(
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    except Exception:
        pass  # Ignore if message hasn't changed


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster G — merged from plugins_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def plugins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19.5: List strategy plugins with their configurable params."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    plugins = engine.plugins.list_all()
    text = "🧩 <b>Strateji Eklentileri</b>\n\n"
    for p in plugins:
        cfg = engine.plugins.get_config(p['name'])
        cfg_str = ""
        if cfg:
            cfg_str = "\n    " + " | ".join(f"{k}={v}" for k, v in cfg.items())
        text += f"  {p['description']}\n    Tip: <code>{p['name']}</code>{cfg_str}\n\n"

    text += (
        "  🔬 <b>fusion</b> (varsayılan)\n"
        "    Çoklu sinyal bileşik skor\n\n"
        "<b>Kullanım:</b>\n"
        "<code>/quick_strategy BTC 5m 1 0.50 contrarian</code>\n"
        "<code>/quick_strategy BTC 5m 1 0.50 martingale</code>\n\n"
        "<b>Parametre düzenle:</b>\n"
        "<code>/plugin_set martingale MULTIPLIER 1.5</code>\n"
        "<code>/plugin_set contrarian min_deviation 0.10</code>\n"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def plugin_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19.5: /plugin_set <plugin> <param> <value> — Edit plugin parameters."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    args = context.args or []
    if len(args) < 1:
        text = "⚙️ <b>Plugin Parametreleri</b>\n\n"
        for pname, params in engine.plugins.CONFIGURABLE.items():
            cfg = engine.plugins.get_config(pname)
            text += f"<b>{pname}</b>:\n"
            for k, v in cfg.items():
                text += f"  • <code>{k}</code> = {v}\n"
            text += "\n"
        text += "Düzenle: <code>/plugin_set plugin param değer</code>"
        return await update.message.reply_text(text, parse_mode="HTML")

    if len(args) < 3:
        plugin = args[0]
        cfg = engine.plugins.get_config(plugin)
        if cfg:
            text = f"⚙️ <b>{plugin} parametreleri:</b>\n"
            for k, v in cfg.items():
                text += f"  • <code>{k}</code> = {v}\n"
            return await update.message.reply_text(text, parse_mode="HTML")
        return await update.message.reply_text(f"❌ Plugin bulunamadı: {args[0]}")

    plugin, param, value = args[0], args[1], " ".join(args[2:])
    ok = engine.plugins.set_config(plugin, param, value)
    if ok:
        new_val = engine.plugins.get_config(plugin).get(param, value)
        await update.message.reply_text(
            f"✅ <b>{plugin}.{esc(param)}</b> → <code>{new_val}</code>",
            parse_mode="HTML")
    else:
        await update.message.reply_text(
            f"❌ Geçersiz: {plugin}.{esc(param)}\n/plugin_set yazarak geçerli parametreleri görün.")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster G — merged from promote.py
# ═══════════════════════════════════════════════════════════════════════
import os as _promote_os  # noqa: E402
from telegram_bot.templates import ERR as _PROMOTE_ERR  # noqa: E402
from telegram_bot.templates.safe_html import esc_code as _promote_esc_code  # noqa: E402

PROMOTE_MIN_TRADES = int(_promote_os.getenv("PROMOTE_MIN_TRADES", "30"))
PROMOTE_MIN_PNL = float(_promote_os.getenv("PROMOTE_MIN_PNL", "0.0"))


async def _promote_get_db(context):
    return context.application.bot_data.get("db")


async def canary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await _promote_get_db(context)
    if not db:
        await update.message.reply_text(_PROMOTE_ERR["DB_UNAVAILABLE"], parse_mode="HTML")
        return

    cur = await db.conn.execute(
        """SELECT s.id, s.label, s.status, s.deploy_stage,
                  (SELECT COUNT(*) FROM executions e
                     WHERE e.strategy_id=s.id AND e.result IS NOT NULL) AS trades,
                  (SELECT COALESCE(SUM(pnl),0) FROM executions e
                     WHERE e.strategy_id=s.id AND e.result IS NOT NULL) AS pnl
           FROM strategies s
           WHERE s.deploy_stage='canary'
           ORDER BY trades DESC"""
    )
    rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text(
            "🕯 <b>No canary strategies.</b>\nAll strategies are promoted.",
            parse_mode="HTML")
        return

    lines = [f"🕯 <b>Canary Strategies ({len(rows)})</b>\n"]
    for r in rows:
        sid = r["id"][:8]
        label = r["label"] or "—"
        trades = r["trades"] or 0
        pnl = r["pnl"] or 0.0
        ready = "✅" if (trades >= PROMOTE_MIN_TRADES and pnl > PROMOTE_MIN_PNL) else "⏳"
        lines.append(
            f"{ready} <code>{_promote_esc_code(sid)}</code> <b>{esc(label)}</b>\n"
            f"   {trades}t PnL${pnl:+.2f} status={esc(r['status'])}"
        )
    lines.append(
        f"\n<i>Promote gate: {PROMOTE_MIN_TRADES}+ trades, PnL > ${PROMOTE_MIN_PNL}</i>\n"
        f"Use /promote &lt;id_prefix&gt; to graduate.")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def promote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/promote &lt;strategy_id_prefix&gt;</code>", parse_mode="HTML")
        return

    prefix = context.args[0]
    db = await _promote_get_db(context)
    if not db:
        await update.message.reply_text(_PROMOTE_ERR["DB_UNAVAILABLE"], parse_mode="HTML")
        return

    cur = await db.conn.execute(
        "SELECT id, label, deploy_stage FROM strategies WHERE id LIKE ?", (f"{prefix}%",))
    matches = await cur.fetchall()
    if not matches:
        await update.message.reply_text(
            f"❌ No strategy matches <code>{_promote_esc_code(prefix)}</code>", parse_mode="HTML")
        return
    if len(matches) > 1:
        ids = ", ".join(f"<code>{_promote_esc_code(m['id'][:8])}</code>" for m in matches)
        await update.message.reply_text(
            f"⚠️ Ambiguous prefix — matches: {ids}", parse_mode="HTML")
        return

    row = matches[0]
    sid = row["id"]
    label = row["label"] or "—"
    if row["deploy_stage"] == "promoted":
        await update.message.reply_text(
            f"ℹ️ <b>{esc(label)}</b> already promoted.", parse_mode="HTML")
        return

    cur = await db.conn.execute(
        """SELECT COUNT(*) AS trades, COALESCE(SUM(pnl), 0) AS pnl
           FROM executions WHERE strategy_id=? AND result IS NOT NULL""",
        (sid,))
    stat_row = await cur.fetchone()
    trades = stat_row["trades"] or 0
    pnl = stat_row["pnl"] or 0.0

    if trades < PROMOTE_MIN_TRADES:
        await update.message.reply_text(
            f"⛔ <b>Gate fail:</b> need ≥{PROMOTE_MIN_TRADES} trades, got {trades}",
            parse_mode="HTML")
        return
    if pnl <= PROMOTE_MIN_PNL:
        await update.message.reply_text(
            f"⛔ <b>Gate fail:</b> PnL ${pnl:.2f} ≤ ${PROMOTE_MIN_PNL:.2f}",
            parse_mode="HTML")
        return

    await db.conn.execute(
        "UPDATE strategies SET deploy_stage='promoted', updated_at=datetime('now') WHERE id=?",
        (sid,))
    await db.conn.commit()
    logger.info(f"PROMOTE: {sid[:8]} {esc(label)} canary → promoted ({trades}t PnL${pnl:.2f})")
    await update.message.reply_text(
        f"✅ <b>Promoted:</b> {esc(label)}\n"
        f"   trades={trades} PnL=${pnl:+.2f}\n"
        f"   Full-size sizing now active.",
        parse_mode="HTML")


async def demote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: <code>/demote &lt;strategy_id_prefix&gt;</code>", parse_mode="HTML")
        return
    prefix = context.args[0]
    db = await _promote_get_db(context)
    if not db:
        await update.message.reply_text(_PROMOTE_ERR["DB_UNAVAILABLE"], parse_mode="HTML")
        return
    cur = await db.conn.execute(
        "SELECT id, label, deploy_stage FROM strategies WHERE id LIKE ?", (f"{prefix}%",))
    matches = await cur.fetchall()
    if not matches:
        await update.message.reply_text(
            f"❌ No strategy matches <code>{_promote_esc_code(prefix)}</code>", parse_mode="HTML")
        return
    if len(matches) > 1:
        await update.message.reply_text("⚠️ Ambiguous prefix", parse_mode="HTML")
        return
    row = matches[0]
    await db.conn.execute(
        "UPDATE strategies SET deploy_stage='canary', updated_at=datetime('now') WHERE id=?",
        (row["id"],))
    await db.conn.commit()
    logger.warning(f"DEMOTE: {row['id'][:8]} {row['label']} promoted → canary")
    await update.message.reply_text(
        f"⚠️ <b>Demoted to canary:</b> {esc(row['label'] or '—')}\n"
        f"   Reduced sizing re-enabled.",
        parse_mode="HTML")
