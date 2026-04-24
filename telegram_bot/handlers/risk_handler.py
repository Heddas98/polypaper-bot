"""
PolyPaper Bot - /kill, /resume, /risk Handlers (Phase 17: Admin Guard)
Emergency controls and risk dashboard from Telegram.

Phase 51 P51-03 — risk_hub.py merged into this file.
"""
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from config.settings import Settings
from telegram_bot.templates.safe_html import esc, fmt_usd
from telegram_bot.templates.callback_proxy import CallbackUpdateProxy

logger = logging.getLogger("polypaper.handlers.risk")


def _is_admin(context, telegram_id: int) -> bool:
    """Phase 17: Check admin access. Phase 54: never fallback to True."""
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(f"⚠️ _is_admin: settings missing, denying user {telegram_id}")
        return False  # Phase 54 P0-07: deny if settings missing (was True → privilege escalation)
    return settings.is_admin(telegram_id)


async def kill_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Emergency stop all trading. ADMIN ONLY."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    reason = " ".join(context.args) if context.args else "Manual Telegram /kill"
    engine.kill_switch.activate(reason)

    await update.message.reply_text(
        f"🛑 <b>KILL SWITCH ACTIVATED</b>\n\n"
        f"Reason: {esc(reason)}\n"
        f"All trading halted. Pending orders cancelled.\n"
        f"Open positions will still be monitored for settlement.\n\n"
        f"Use /resume to restart trading.",
        parse_mode="HTML")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume trading after kill switch. ADMIN ONLY."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    engine.kill_switch.deactivate()
    engine.risk.reset_halt()

    await update.message.reply_text(
        "✅ <b>Trading Resumed</b>\n\n"
        "Kill switch deactivated.\nRisk halt reset.\n"
        "Engine will evaluate strategies on next cycle.",
        parse_mode="HTML")


async def streak_reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset loss streak counter. ADMIN ONLY."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin.")
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")
    old = engine.risk.reset_streak()
    await update.message.reply_text(
        f"✅ <b>Streak Reset</b>\n\nKayip serisi: {old} → 0\nTrade'ler tekrar acilabilir.",
        parse_mode="HTML")


async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show risk dashboard."""
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine çalışmıyor.")

    rs = engine.risk.get_status()
    ks = engine.kill_switch.get_status()
    limits = rs["limits"]

    # Kill switch status
    ks_emoji = "🛑" if ks["killed"] else "✅"
    ks_text = f"ACTIVE — {ks['reason']}" if ks["killed"] else "Inactive"

    # Risk halt
    rh_emoji = "🛑" if rs["halted"] else "✅"
    rh_text = rs["halt_reason"] if rs["halted"] else "No halt"

    # Exposure bar
    exp_pct = (rs["total_exposure"] / limits["max_exposure"] * 100) if limits["max_exposure"] > 0 else 0
    exp_bar = "█" * int(exp_pct / 10) + "░" * (10 - int(exp_pct / 10))

    text = (
        f"🛡 <b>Risk Dashboard</b>\n\n"
        f"<b>Kill Switch</b>: {ks_emoji} {ks_text}\n"
        f"<b>Risk Halt</b>: {rh_emoji} {rh_text}\n\n"
        f"<b>Exposure</b>\n"
        f"  Open: {rs['open_positions']}/{limits['max_positions']} positions\n"
        f"  [{exp_bar}] {fmt_usd(rs['total_exposure'], decimals=1)}/{fmt_usd(limits['max_exposure'], decimals=0)}\n\n"
        f"<b>Daily</b>\n"
        f"  PnL: <b>{fmt_usd(rs['daily_pnl'], sign=True)}</b> / limit -{fmt_usd(limits['max_daily_loss'], decimals=0)}\n"
        f"  Trades: {rs['daily_trades']}\n"
        f"  Loss streak: {rs['loss_streak']}\n\n"
        f"<b>Limits</b>\n"
        f"  Max position: {fmt_usd(limits['max_position'], decimals=0)}\n"
        f"  Max exposure: {fmt_usd(limits['max_exposure'], decimals=0)}\n"
        f"  Balance floor: {fmt_usd(limits['balance_floor'], decimals=0)}\n\n"
        f"Pending orders: {len(engine._pending)}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="show_risk")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def risk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19.5: Risk dashboard with INLINE EDIT BUTTONS."""
    q = update.callback_query
    await q.answer()
    engine = context.bot_data.get("engine")
    if not engine:
        return

    rs = engine.risk.get_status()
    ks = engine.kill_switch.get_status()
    lm = engine.risk.limits

    ks_emoji = "🛑" if ks["killed"] else "✅"
    exp_pct = (rs["total_exposure"] / lm.max_total_exposure * 100) if lm.max_total_exposure > 0 else 0
    exp_bar = "█" * int(exp_pct / 10) + "░" * (10 - int(exp_pct / 10))

    text = (
        f"🛡 <b>Risk Dashboard</b>\n\n"
        f"Kill: {ks_emoji} | Halt: {'🛑 '+rs['halt_reason'] if rs['halted'] else '✅'}\n"
        f"Pozisyon: {rs['open_positions']}/{lm.max_open_positions}\n"
        f"[{exp_bar}] ${rs['total_exposure']:.1f}/${lm.max_total_exposure:.0f}\n"
        f"Günlük PnL: {rs['daily_pnl']:+.2f} / -${lm.max_daily_loss:.0f}\n"
        f"Kayıp Serisi: {rs['loss_streak']}/{lm.max_loss_streak}\n\n"
        f"👇 Düzenlemek için tıklayın:"
    )

    # Phase 53b: Force exit status for button
    fe_secs = _get_fe_seconds()
    fe_label = f"⚡ Force Exit: {fe_secs}s ✅" if fe_secs > 0 else "⚡ Force Exit: OFF"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💰 Max Poz: ${lm.max_position_size}", callback_data="re_pos"),
         InlineKeyboardButton(f"📊 Max Açık: {lm.max_open_positions}", callback_data="re_open")],
        [InlineKeyboardButton(f"💼 Max Exp: ${lm.max_total_exposure}", callback_data="re_exp"),
         InlineKeyboardButton(f"📉 Gün Kayıp: ${lm.max_daily_loss}", callback_data="re_loss")],
        [InlineKeyboardButton(f"🔢 Max Trade: {lm.max_daily_trades}", callback_data="re_trades"),
         InlineKeyboardButton(f"🔥 Max Streak: {lm.max_loss_streak}", callback_data="re_streak")],
        [InlineKeyboardButton(f"🏦 Min Bakiye: ${lm.min_balance_floor}", callback_data="re_floor"),
         InlineKeyboardButton(f"🎯 Market Max: ${lm.max_single_market_exposure}", callback_data="re_market")],
        [InlineKeyboardButton(fe_label, callback_data="fe_toggle"),
         InlineKeyboardButton("✏️ Ayarla", callback_data="fe_edit")],
        [InlineKeyboardButton("🔄 Yenile", callback_data="show_risk"),
         InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


RISK_FIELDS = {
    "re_pos": ("max_position_size", "💰 Max pozisyon büyüklüğü ($):", float),
    "re_open": ("max_open_positions", "📊 Max eşzamanlı pozisyon:", int),
    "re_exp": ("max_total_exposure", "💼 Max toplam maruziyet ($):", float),
    "re_loss": ("max_daily_loss", "📉 Max günlük kayıp ($):", float),
    "re_trades": ("max_daily_trades", "🔢 Max günlük trade:", int),
    "re_streak": ("max_loss_streak", "🔥 Max kayıp serisi:", int),
    "re_floor": ("min_balance_floor", "🏦 Min bakiye ($):", float),
    "re_market": ("max_single_market_exposure", "🎯 Max tek market ($):", float),
}


# ---------------------------------------------------------------------------
# Phase 53b: Force Exit — toggle + edit from Telegram
# ---------------------------------------------------------------------------

def _get_fe_seconds() -> int:
    """Read current FORCE_EXIT_SECONDS from engine_monitor module."""
    import core.engine_monitor as em
    return em.FORCE_EXIT_SECONDS


def _set_fe_seconds(val: int):
    """Write FORCE_EXIT_SECONDS into engine_monitor module (runtime)."""
    import core.engine_monitor as em
    em.FORCE_EXIT_SECONDS = val


async def force_exit_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle force exit on/off. If on → set to 0. If off → restore last value (default 15)."""
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        return await q.answer("⛔ Admin only")
    await q.answer()

    cur = _get_fe_seconds()
    if cur > 0:
        # Save current value before disabling
        context.bot_data["_fe_last"] = cur
        _set_fe_seconds(0)
        new_val = 0
    else:
        # Restore last value or default 15
        restored = context.bot_data.get("_fe_last", 15)
        _set_fe_seconds(restored)
        new_val = restored

    # Persist to DB
    db = context.bot_data.get("db")
    if db:
        await db.set_setting("risk.force_exit_seconds", str(new_val))

    status = f"OFF" if new_val == 0 else f"{new_val}s"
    await q.message.reply_text(
        f"⚡ <b>Force Exit: {status}</b>\n"
        f"{'Devre disi birakildi.' if new_val == 0 else f'Market kapanisina {new_val}s kala pozisyonlar kapatilacak.'}\n"
        f"💾 <i>Kaydedildi</i>",
        parse_mode="HTML")


async def force_exit_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter new force exit seconds value."""
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        return await q.answer("⛔ Admin only")
    await q.answer()

    cur = _get_fe_seconds()
    context.user_data["risk_editing"] = {"attr": "_force_exit_seconds", "callback": "fe_edit"}
    await q.message.reply_text(
        f"⚡ Force Exit saniye degerini girin (0 = kapali):\n"
        f"Su an: <b>{cur}s</b>\n"
        f"Onerilen: 10-15 (5dk marketler), 20-30 (15dk marketler)",
        parse_mode="HTML")


async def risk_field_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19.5: Risk parameter edit — asks for value."""
    q = update.callback_query
    if not _is_admin(context, q.from_user.id):
        return await q.answer("⛔ Admin only")

    data = q.data
    if data in RISK_FIELDS:
        attr, prompt, _ = RISK_FIELDS[data]
        engine = context.bot_data.get("engine")
        current = getattr(engine.risk.limits, attr, "?") if engine else "?"
        context.user_data["risk_editing"] = {"attr": attr, "callback": data}
        await q.answer()
        await q.message.reply_text(f"{prompt}\nŞu an: <b>{current}</b>", parse_mode="HTML")
    else:
        await q.answer("?")


async def handle_risk_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 22: Catches text when editing risk parameter. Persists to DB."""
    editing = context.user_data.get("risk_editing")
    if not editing:
        return

    attr = editing["attr"]
    text = update.message.text.strip()

    # Phase 53b: Force exit special handling
    if attr == "_force_exit_seconds":
        try:
            value = int(text)
            if value < 0:
                raise ValueError
        except (ValueError, TypeError):
            await update.message.reply_text(f"❌ Gecersiz: '{esc(text)}' (0 veya pozitif tam sayi girin)")
            return
        old = _get_fe_seconds()
        _set_fe_seconds(value)
        context.user_data.pop("risk_editing", None)
        db = context.bot_data.get("db")
        if db:
            await db.set_setting("risk.force_exit_seconds", str(value))
        status = "OFF" if value == 0 else f"{value}s"
        await update.message.reply_text(
            f"✅ <b>Force Exit guncellendi!</b>\n"
            f"<b>{old}s</b> → <b>{status}</b>\n"
            f"💾 <i>Kaydedildi (restart korunur)</i>",
            parse_mode="HTML")
        return

    engine = context.bot_data.get("engine")
    if not engine:
        context.user_data.pop("risk_editing", None)
        return

    cast = float
    for _, (a, _, t) in RISK_FIELDS.items():
        if a == attr:
            cast = t
            break

    try:
        value = cast(text)
    except (ValueError, TypeError):
        await update.message.reply_text(f"❌ Geçersiz: '{esc(text)}'")
        return

    old = getattr(engine.risk.limits, attr)
    setattr(engine.risk.limits, attr, value)
    context.user_data.pop("risk_editing", None)

    # Phase 22: Persist to DB
    db = context.bot_data.get("db")
    if db:
        await db.set_setting(f"risk.{esc(attr)}", str(value))

    await update.message.reply_text(
        f"✅ <b>Risk güncellendi!</b>\n<b>{esc(attr)}</b>: {old} → <b>{esc(value)}</b>\n💾 <i>Kaydedildi (restart korunur)</i>",
        parse_mode="HTML")


async def risk_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19: /risk_set <param> <value> — Edit risk limits from Telegram."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Admin only command.")

    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine not running.")

    args = context.args or []
    if len(args) < 2:
        limits = engine.risk.limits
        rs = engine.risk.get_status()
        tiered = rs.get("tiered_limits", {})
        per_asset = tiered.get("per_asset", {})
        per_market = tiered.get("per_market", {})

        # Build per-asset display
        asset_lines = ""
        if per_asset:
            asset_lines = "\n<b>Per-Asset Limits (Phase 36):</b>\n"
            for asset, info in per_asset.items():
                limit = info["limit"]
                current = info["current"]
                pct = (current / limit * 100) if limit > 0 else 0
                asset_lines += f"• {esc(asset)}: ${current:.1f}/${limit:.0f} ({pct:.0f}%)\n"

        # Build per-market display
        market_lines = ""
        if per_market:
            markets = per_market.get("markets", {})
            if markets:
                market_lines = f"\n<b>Per-Market Limit:</b> ${per_market.get('limit', 0):.0f} max per slug\n"

        return await update.message.reply_text(
            "⚙️ <b>Risk Parametreleri</b>\n\n"
            f"• <code>max_position</code> = ${limits.max_position_size}\n"
            f"• <code>max_positions</code> = {limits.max_open_positions}\n"
            f"• <code>max_exposure</code> = ${limits.max_total_exposure}\n"
            f"• <code>max_daily_loss</code> = ${limits.max_daily_loss}\n"
            f"• <code>max_daily_trades</code> = {limits.max_daily_trades}\n"
            f"• <code>max_streak</code> = {limits.max_loss_streak}\n"
            f"• <code>min_balance</code> = ${limits.min_balance_floor}\n"
            f"• <code>max_market</code> = ${limits.max_single_market_exposure}\n"
            f"{asset_lines}{market_lines}\n"
            "Düzenle: <code>/risk_set param değer</code>\n"
            "Örnek: <code>/risk_set max_positions 10</code>\n"
            "<code>/risk_set max_daily_loss 25</code>",
            parse_mode="HTML")

    param = args[0].lower()
    try:
        val = float(args[1])
    except ValueError:
        return await update.message.reply_text(f"❌ Geçersiz değer: {args[1]}")

    limits = engine.risk.limits
    param_map = {
        "max_position": ("max_position_size", float),
        "max_positions": ("max_open_positions", int),
        "max_exposure": ("max_total_exposure", float),
        "max_daily_loss": ("max_daily_loss", float),
        "max_daily_trades": ("max_daily_trades", int),
        "max_streak": ("max_loss_streak", int),
        "min_balance": ("min_balance_floor", float),
        "max_market": ("max_single_market_exposure", float),
        "per_market_limit": ("per_market_limit", float),
    }

    # Phase 36: Handle per-asset limit (btc, eth, sol, xrp)
    asset_param = param.upper()
    if asset_param in ("BTC", "ETH", "SOL", "XRP"):
        old_val = limits.per_asset_limits.get(asset_param, "?")
        limits.per_asset_limits[asset_param] = val
        new_val = val

        # Phase 36: Persist per-asset limit to DB
        db = context.bot_data.get("db")
        if db:
            await db.set_setting(f"risk.per_asset.{asset_param}", str(new_val))

        return await update.message.reply_text(
            f"✅ <b>Risk guncellendi!</b>\n"
            f"<b>{asset_param} asset limit</b>: ${old_val} → <b>${new_val}</b>\n"
            f"💾 <i>Kaydedildi (restart korunur)</i>",
            parse_mode="HTML")

    if param not in param_map:
        return await update.message.reply_text(
            f"❌ Bilinmeyen parametre: '{esc(param)}'\n"
            f"Düzenleme: max_position, max_positions, max_exposure, max_daily_loss,\n"
            f"max_daily_trades, max_streak, min_balance, max_market, per_market_limit,\n"
            f"BTC, ETH, SOL, XRP (asset limits)\n"
            f"/risk_set yazarak listeye bakın.")

    attr_name, cast = param_map[param]
    old_val = getattr(limits, attr_name)
    setattr(limits, attr_name, cast(val))
    new_val = getattr(limits, attr_name)

    # Phase 28: Persist to DB (survives restart)
    db = context.bot_data.get("db")
    if db:
        await db.set_setting(f"risk.{attr_name}", str(new_val))

    await update.message.reply_text(
        f"✅ <b>Risk guncellendi!</b>\n"
        f"<b>{esc(param)}</b>: {old_val} → <b>{new_val}</b>\n"
        f"💾 <i>Kaydedildi (restart korunur)</i>",
        parse_mode="HTML")


# ---------------------------------------------------------------------------
# Phase 51 P51-03 — Risk Hub (merged from risk_hub.py)
# Inline-tab landing page for all risk commands.
# ---------------------------------------------------------------------------


def _build_risk_hub_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📊 Status", callback_data="rhub:status"),
            InlineKeyboardButton("⚙️ Limits", callback_data="rhub:limits"),
        ],
        [
            InlineKeyboardButton("🕯 Canary", callback_data="rhub:canary"),
            InlineKeyboardButton("🛑 Kill", callback_data="rhub:kill"),
        ],
        [
            InlineKeyboardButton("▶️ Resume", callback_data="rhub:resume"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def risk_hub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/risk_hub — landing with tab buttons."""
    text = (
        "🛡 <b>Risk Hub</b>\n"
        "Select an action:\n\n"
        "<i>Status</i> — current PnL, streak, exposure\n"
        "<i>Limits</i> — interactive limit editor\n"
        "<i>Canary</i> — feature-flagged strategies\n"
        "<i>Kill</i> — emergency halt all trading\n"
        "<i>Resume</i> — lift halt after kill"
    )
    await update.message.reply_text(
        text, reply_markup=_build_risk_hub_keyboard(), parse_mode="HTML")


async def risk_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    tab = data.split(":", 1)[1] if ":" in data else ""

    # Phase 51 BUG-FIX — proxy callback-origin message so downstream
    # handlers that use `update.message.reply_text(...)` work.
    proxy = CallbackUpdateProxy.from_update(update)

    try:
        if tab == "status":
            return await risk_command(proxy, context)
        if tab == "limits":
            return await risk_set_command(proxy, context)
        if tab == "canary":
            from telegram_bot.handlers.settings_handler import canary_command
            return await canary_command(proxy, context)
        if tab == "kill":
            return await kill_command(proxy, context)
        if tab == "resume":
            return await resume_command(proxy, context)
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outer route dispatcher intentionally wide.
        # Each tab invokes a different sub-handler (risk/canary/kill/resume)
        # with its own exception surface. We log the trace server-side and
        # fall back to a generic inline-edit notice; user never sees raw exc.
        logger.exception(f"risk_hub route {tab} failed: {esc(str(e))}")
        try:
            await query.edit_message_text(
                f"❌ Route failed: <code>{esc(tab)}</code>", parse_mode="HTML")
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): narrow from bare Exception. edit_message
            # BadRequest "message is not modified" or transport error on
            # inline-edit. Silent swallow is correct — original error was
            # already logged above.
            pass
