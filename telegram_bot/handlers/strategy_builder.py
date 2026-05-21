"""
PolyPaper Bot - Strategy Builder (Phase 2)
Full interactive Strategy Builder matching Polyscout's UI exactly.
Uses ConversationHandler for multi-step flow with inline buttons.
"""

import logging
import warnings

# Suppress PTB's per_message=False warning. We mix CallbackQueryHandler with
# MessageHandler in this conversation, so per_message=True is impossible —
# the warning is informational only and clutters logs on every restart.
try:
    from telegram.warnings import PTBUserWarning  # type: ignore

    warnings.filterwarnings(
        "ignore",
        message=r".*per_message=False.*",
        category=PTBUserWarning,
    )
except ImportError:
    # T11.8-B (2026-04-24): narrow from bare Exception. Older python-telegram-
    # bot versions (<21.x) don't export PTBUserWarning. Silent swallow
    # correct — warning is informational-only.
    pass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db.database import Database
from db.models import Asset, Direction, Strategy, StrategyStatus, Timeframe
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.strategy_builder")

# Conversation states
(
    SELECT_ASSET,
    SELECT_TIMEFRAME,
    SELECT_DIRECTION,
    ENTER_AMOUNT,
    ENTER_TRIGGER,
    ENTER_PRICE_DIFF,
    ENTER_BET_FROM,
    ENTER_BET_TO,
    ENTER_STOP_LOSS,
    ENTER_TAKE_PROFIT,
    ENTER_MAX_EXEC,
    ENTER_MAX_LOSSES,
    ENTER_SLIPPAGE,
    SELECT_EMA,
    ENTER_VOLATILITY,
    CONFIRM_STRATEGY,
    EDIT_FIELD,
) = range(17)

# ── Defaults matching Polyscout ──
DEFAULT_STRATEGY = {
    "asset": "BTC",
    "timeframe": "5m",
    "direction": "any",
    "amount": 1.0,
    "trigger": 0.50,
    "price_diff": 2.0,
    "bet_from": 0.5,
    "bet_to": 0.5,
    "stop_loss": "",
    "take_profit": "",
    "max_exec": 1,
    "max_losses": 3,
    "slippage": None,
    "ema": False,
    "volatility": 0,
    "stype": "fusion",
}


def _build_summary(data: dict) -> str:
    """Build strategy summary text like Polyscout's Strategy Builder."""
    sl_text = "Disabled"
    if data.get("stop_loss"):
        sl = data["stop_loss"]
        try:
            val = float(sl)
            sl_text = f"@ {val} odds" if val < 1 else f"{val}% drawdown"
        except (ValueError, TypeError):
            sl_text = str(sl)

    tp_text = "Disabled"
    if data.get("take_profit"):
        tp = data["take_profit"]
        try:
            val = float(tp)
            tp_text = f"Default @ {val} odds" if val < 2 else f"{val}% gain"
        except (ValueError, TypeError):
            tp_text = str(tp)

    max_exec = data.get("max_exec")
    max_exec_text = str(max_exec) if max_exec else "Unlimited"
    max_losses = data.get("max_losses")
    max_losses_text = str(max_losses) if max_losses else "Unlimited"
    slippage = data.get("slippage")
    slippage_text = f"{slippage}" if slippage else "Unlimited"
    ema_text = "On" if data.get("ema") else "Off"
    vol = data.get("volatility", 0)
    vol_text = f"{vol}%" if vol and vol > 0 else "Off (0%)"

    return (
        f"🛠 <b>Strategy Builder</b>\n\n"
        f"• Asset: <b>{data.get('asset', 'BTC')}</b>\n"
        f"• Timeframe: <b>{data.get('timeframe', '5m')}</b>\n"
        f"• Direction: <b>{data.get('direction', 'Any').capitalize()}</b>\n"
        f"• Trade amount: <b>${data.get('amount', 1)}</b>\n"
        f"• Trigger: <b>Odds = {data.get('trigger', 0.92)}</b>\n"
        f"• Price difference: <b>{data.get('price_diff', 0.03)}%</b>\n"
        f"• Bet window: <b>{data.get('bet_from', 0)}m after start → "
        f"{data.get('bet_to', 0.25)}m before end</b>\n"
        f"• Stop loss: <b>{sl_text}</b>\n"
        f"• Take profit: <b>{tp_text}</b>\n"
        f"• Max executions per event: <b>{max_exec_text}</b>\n"
        f"• Max losses per event: <b>{max_losses_text}</b>\n"
        f"• Max entry slippage: <b>{slippage_text}</b>\n"
        f"• Trend filter (EMA): <b>{ema_text}</b>\n"
        f"• Min volatility: <b>{vol_text}</b>\n"
        f"• Strategy type: <b>{data.get('stype', 'fusion')}</b>\n"
        f"• Runs until you stop it\n"
    )


def _build_confirm_keyboard(data: dict, edit_mode: bool = False) -> InlineKeyboardMarkup:
    """Build the parameter buttons grid like Polyscout."""
    sl_val = data.get("stop_loss", "Disabled")
    tp_val = data.get("take_profit", "Disabled")
    try:
        sl_f = float(sl_val)
        sl_display = f"@ {sl_f} odds" if sl_f < 1 else f"{sl_f}%"
    except (ValueError, TypeError):
        sl_display = "Disabled"
    try:
        tp_f = float(tp_val)
        tp_display = f"Default @ {tp_f} odds" if tp_f < 2 else f"{tp_f}%"
    except (ValueError, TypeError):
        tp_display = "Disabled"

    confirm_text = "💾 Kaydet" if edit_mode else "✅ Confirm"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Asset: {data.get('asset', 'BTC')}", callback_data="sb_edit_asset"
                ),
                InlineKeyboardButton(
                    f"Timeframe: {data.get('timeframe', '5m')}", callback_data="sb_edit_timeframe"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Amount: ${data.get('amount', 1)}", callback_data="sb_edit_amount"
                ),
                InlineKeyboardButton(
                    f"Trigger: Odds = {data.get('trigger', 0.92)}", callback_data="sb_edit_trigger"
                ),
            ],
            [
                InlineKeyboardButton(
                    f"Direction: {data.get('direction', 'Any').capitalize()}",
                    callback_data="sb_edit_direction",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Price difference: {data.get('price_diff', 0.03)}%",
                    callback_data="sb_edit_price_diff",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Bet from: {data.get('bet_from', 0)}m after start",
                    callback_data="sb_edit_bet_from",
                ),
                InlineKeyboardButton(
                    f"Bet to: {data.get('bet_to', 0.25)}m before end",
                    callback_data="sb_edit_bet_to",
                ),
            ],
            [InlineKeyboardButton(f"Stop loss: {sl_display}", callback_data="sb_edit_stop_loss")],
            [
                InlineKeyboardButton(
                    f"Take profit: {tp_display}", callback_data="sb_edit_take_profit"
                )
            ],
            [
                InlineKeyboardButton(
                    f"Max executions/event: {data.get('max_exec') or 'Unlimited'}",
                    callback_data="sb_edit_max_exec",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Max losses/event: {data.get('max_losses') or 'Unlimited'}",
                    callback_data="sb_edit_max_losses",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Max entry slippage: {data.get('slippage') or 'Unlimited'}",
                    callback_data="sb_edit_slippage",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Trend filter (EMA): {'On' if data.get('ema') else 'Off'}",
                    callback_data="sb_edit_ema",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Min volatility: {'Off (0%)' if not data.get('volatility') else str(data['volatility'])+'%'}",
                    callback_data="sb_edit_volatility",
                )
            ],
            [
                InlineKeyboardButton(
                    f"Strategy type: {data.get('stype', 'fusion')}", callback_data="sb_edit_stype"
                )
            ],
            [
                InlineKeyboardButton(confirm_text, callback_data="sb_confirm"),
                InlineKeyboardButton("⬅️ Back", callback_data="sb_cancel"),
            ],
        ]
    )


# ═══════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════


async def start_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the Strategy Builder from /strategies → Add Strategy."""
    # 2026-05-22 (Heddas #7): strateji sistemi devre disi — olusturma engellendi.
    from telegram_bot.handlers.strategies import _strategy_disabled_reply

    if await _strategy_disabled_reply(update):
        return ConversationHandler.END
    query = update.callback_query
    if query:
        await query.answer()

    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        if query:
            await query.message.reply_text("Önce /start komutunu kullanın.")
        return ConversationHandler.END

    # Initialize builder data with defaults (or last strategy if exists)
    data = dict(DEFAULT_STRATEGY)

    # Try to pre-fill from most recent strategy
    strategies = await db.get_strategies_by_user(user.id)
    if strategies:
        last = strategies[-1]
        data.update(
            {
                "asset": last.asset.value,
                "timeframe": last.timeframe.value,
                "direction": last.direction.value,
                "amount": last.trade_amount,
                "trigger": last.odds_threshold or 0.92,
                "price_diff": last.price_difference or 0.03,
                "bet_from": last.minutes_after_start,
                "bet_to": last.minutes_before_end,
                "stop_loss": str(last.stop_loss_odds or last.stop_loss_percent or ""),
                "take_profit": str(last.take_profit_odds or last.take_profit_percent or ""),
                "max_exec": last.max_executions_per_event,
                "max_losses": last.max_losses_per_event,
                "slippage": last.max_entry_slippage,
                "ema": last.ma_filter_enabled,
                "volatility": last.min_volatility or 0,
                "stype": getattr(last, "strategy_type", "fusion") or "fusion",
            }
        )
        prefill_note = "\nℹ Pre-filled from your most recent strategy.\n"
    else:
        prefill_note = ""

    context.user_data["sb"] = data

    text = _build_summary(data) + prefill_note + "\nTap a field to adjust it."
    keyboard = _build_confirm_keyboard(data)

    msg = query.message if query else update.message
    await msg.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    return CONFIRM_STRATEGY


# ═══════════════════════════════════════
# FIELD EDITORS
# ═══════════════════════════════════════


async def edit_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("₿ BTC", callback_data="sb_set_asset_BTC"),
                InlineKeyboardButton("Ξ ETH", callback_data="sb_set_asset_ETH"),
            ],
            [
                InlineKeyboardButton("◎ SOL", callback_data="sb_set_asset_SOL"),
                InlineKeyboardButton("✕ XRP", callback_data="sb_set_asset_XRP"),
            ],
        ]
    )
    await query.edit_message_text("Select your asset:", reply_markup=keyboard)
    return CONFIRM_STRATEGY


async def set_asset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    asset = query.data.replace("sb_set_asset_", "")
    context.user_data["sb"]["asset"] = asset
    return await _show_summary(query, context)


async def edit_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("5m", callback_data="sb_set_tf_5m"),
                InlineKeyboardButton("15m", callback_data="sb_set_tf_15m"),
                InlineKeyboardButton("1h", callback_data="sb_set_tf_1h"),
            ],
            [
                InlineKeyboardButton("4h", callback_data="sb_set_tf_4h"),
                InlineKeyboardButton("24h", callback_data="sb_set_tf_24h"),
            ],
        ]
    )
    await query.edit_message_text("Select timeframe:", reply_markup=keyboard)
    return CONFIRM_STRATEGY


async def set_timeframe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    tf = query.data.replace("sb_set_tf_", "")
    context.user_data["sb"]["timeframe"] = tf
    return await _show_summary(query, context)


async def edit_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬆ Up", callback_data="sb_set_dir_up"),
                InlineKeyboardButton("⬇ Down", callback_data="sb_set_dir_down"),
                InlineKeyboardButton("↕ Any", callback_data="sb_set_dir_any"),
            ],
        ]
    )
    await query.edit_message_text("Select direction:", reply_markup=keyboard)
    return CONFIRM_STRATEGY


async def set_direction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data.replace("sb_set_dir_", "")
    context.user_data["sb"]["direction"] = d
    return await _show_summary(query, context)


async def edit_ema(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb"]["ema"] = not context.user_data["sb"].get("ema", False)
    return await _show_summary(query, context)


async def edit_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "amount"
    await query.edit_message_text("Send a whole number in USDC.e (>= 1)")
    return EDIT_FIELD


async def edit_trigger(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "trigger"
    await query.edit_message_text(
        "Send the odds trigger value (e.g., 0.92).\n" "The bot buys when odds reach this value."
    )
    return EDIT_FIELD


async def edit_price_diff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "price_diff"
    await query.edit_message_text("Send the % move from open (e.g., 2 for 2%).")
    return EDIT_FIELD


async def edit_bet_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "bet_from"
    await query.edit_message_text("Send minutes after market start (e.g., 0).")
    return EDIT_FIELD


async def edit_bet_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "bet_to"
    await query.edit_message_text("Send minutes before market end (e.g., 0.25).")
    return EDIT_FIELD


async def edit_stop_loss(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "stop_loss"
    await query.edit_message_text(
        "Enter a stop loss:\n"
        "• Send a whole number like 25 for a 25% drawdown\n"
        "• Send a decimal like 0.45 for an odds price stop\n"
        "• Send 0 to disable all stop losses"
    )
    return EDIT_FIELD


async def edit_take_profit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "take_profit"
    await query.edit_message_text(
        "Enter a take profit:\n"
        "• Send a whole number like 25 for a 25% gain\n"
        "• Send a decimal like 0.75 for an odds price take profit\n"
        "• Send 0 to disable take profit completely\n\n"
        "TP is based on entry price. If TP is 0.2 and "
        "Entry Price is at 0.7, the position will close at "
        "0.9 (0.7 + 0.2)."
    )
    return EDIT_FIELD


async def edit_max_exec(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "max_exec"
    await query.edit_message_text("Send max executions per event (e.g., 1). Send 0 for unlimited.")
    return EDIT_FIELD


async def edit_max_losses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "max_losses"
    await query.edit_message_text("Send max losses per event. Send 0 for unlimited.")
    return EDIT_FIELD


async def edit_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "slippage"
    await query.edit_message_text(
        "Send the max entry slippage as a decimal (e.g., 0.10). "
        'Send 0 or "unlimited" to disable.'
    )
    return EDIT_FIELD


async def edit_volatility(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["sb_editing"] = "volatility"
    await query.edit_message_text(
        "Send the minimum volatility percent (e.g., 0.50). Send 0 to disable."
    )
    return EDIT_FIELD


async def edit_stype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔬 Fusion (default)", callback_data="sb_set_stype_fusion")],
            [InlineKeyboardButton("🎯 Classic (no-algo)", callback_data="sb_set_stype_classic")],
            [InlineKeyboardButton("📈 Momentum", callback_data="sb_set_stype_momentum")],
            [InlineKeyboardButton("🔄 Contrarian", callback_data="sb_set_stype_contrarian")],
            [InlineKeyboardButton("⚡ Scalper", callback_data="sb_set_stype_scalper")],
            [InlineKeyboardButton("🎯 Sniper", callback_data="sb_set_stype_sniper")],
        ]
    )
    await query.edit_message_text(
        "Select strategy type:\n"
        "🔬 Fusion  — 12 sinyal birleşimi (default)\n"
        "🎯 Classic — Sadece trigger/TP/SL, algoritma YOK\n"
        "📈 Momentum — Trend takibi\n"
        "🔄 Contrarian — Mean-reversion\n"
        "⚡ Scalper — Tight spread quick trades\n"
        "🎯 Sniper — Yalnızca yüksek confidence",
        reply_markup=kb,
    )
    return EDIT_FIELD


async def set_stype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    stype = query.data.replace("sb_set_stype_", "")
    context.user_data.setdefault("sb", {})["stype"] = stype
    return await _show_summary(query, context)


# ═══════════════════════════════════════
# TEXT INPUT HANDLER
# ═══════════════════════════════════════


async def handle_field_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for any editable field."""
    field = context.user_data.get("sb_editing")
    text = update.message.text.strip().lower()
    data = context.user_data.get("sb", {})

    try:
        if field == "amount":
            val = float(text)
            if val < 1:
                await update.message.reply_text("Minimum is $1. Try again.")
                return EDIT_FIELD
            data["amount"] = val

        elif field == "trigger":
            val = float(text)
            data["trigger"] = val

        elif field == "price_diff":
            data["price_diff"] = float(text)

        elif field == "bet_from":
            data["bet_from"] = float(text)

        elif field == "bet_to":
            data["bet_to"] = float(text)

        elif field == "stop_loss":
            if text in ("0", "off", "disable", "disabled"):
                data["stop_loss"] = ""
            else:
                data["stop_loss"] = text

        elif field == "take_profit":
            if text in ("0", "off", "disable", "disabled"):
                data["take_profit"] = ""
            else:
                data["take_profit"] = text

        elif field == "max_exec":
            val = int(float(text))
            data["max_exec"] = val if val > 0 else None

        elif field == "max_losses":
            val = int(float(text))
            data["max_losses"] = val if val > 0 else None

        elif field == "slippage":
            if text in ("0", "unlimited", "off"):
                data["slippage"] = None
            else:
                data["slippage"] = float(text)

        elif field == "volatility":
            val = float(text)
            data["volatility"] = val if val > 0 else 0

    except (ValueError, TypeError):
        await update.message.reply_text("Invalid value. Please try again.")
        return EDIT_FIELD

    context.user_data["sb"] = data
    summary = _build_summary(data) + "\nTap a field to adjust it."
    keyboard = _build_confirm_keyboard(data)
    await update.message.reply_text(summary, parse_mode="HTML", reply_markup=keyboard)
    return CONFIRM_STRATEGY


# ═══════════════════════════════════════
# CONFIRM & SAVE
# ═══════════════════════════════════════


async def confirm_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save (create or update) the strategy to database."""
    query = update.callback_query
    await query.answer()

    db: Database = context.bot_data["db"]
    tg_user = update.effective_user
    user = await db.get_user_by_telegram_id(tg_user.id)
    if not user:
        await query.message.reply_text("Please use /start first.")
        return ConversationHandler.END

    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        await query.message.reply_text("No wallet found.")
        return ConversationHandler.END

    data = context.user_data.get("sb", {})
    edit_id = context.user_data.get("sb_edit_id")  # None = create, str = update

    # Parse stop loss
    sl_percent, sl_odds = None, None
    if data.get("stop_loss"):
        try:
            val = float(data["stop_loss"])
            if val < 1:
                sl_odds = val
            else:
                sl_percent = val / 100.0
        except (ValueError, TypeError):
            pass

    # Parse take profit
    tp_percent, tp_odds = None, None
    if data.get("take_profit"):
        try:
            val = float(data["take_profit"])
            if val < 2:
                tp_odds = val
            else:
                tp_percent = val / 100.0
        except (ValueError, TypeError):
            pass

    if edit_id:
        # ══ EDIT MODE: Update existing strategy ══
        fields = {
            "label": data.get("label") or None,
            "trade_amount": data.get("amount", 1.0),
            "odds_threshold": data.get("trigger", 0.50),
            "direction": data.get("direction", "any"),
            "price_difference": data.get("price_diff") or None,
            "minutes_after_start": data.get("bet_from", 0),
            "minutes_before_end": data.get("bet_to", 0.25),
            "stop_loss_percent": sl_percent,
            "stop_loss_odds": sl_odds,
            "take_profit_percent": tp_percent,
            "take_profit_odds": tp_odds,
            "max_executions_per_event": data.get("max_exec"),
            "max_losses_per_event": data.get("max_losses"),
            "max_entry_slippage": data.get("slippage"),
            "ma_filter_enabled": 1 if data.get("ema") else 0,
            "min_volatility": data.get("volatility") or None,
            "strategy_type": data.get("stype", "fusion"),
        }
        for field, value in fields.items():
            await db.update_strategy_field(edit_id, field, value)
        s = await db.get_strategy(edit_id)
        logger.info(f"Strategy updated: {edit_id[:8]} by user {user.telegram_id}")
        await query.edit_message_text(
            f"💾 <b>Strateji Güncellendi!</b>\n\n"
            f"{s.summary_line(1) if s else edit_id[:8]}\n\n"
            f"/strategies ile görüntüleyin.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🎰 Stratejiler", callback_data="show_strategies")],
                ]
            ),
        )
    else:
        # ══ CREATE MODE: New strategy ══
        # Phase 22: Auto-generate label if not provided
        stype = data.get("stype", "fusion")
        auto_lbl = data.get("label") or None
        if not auto_lbl:
            t = {
                "fusion": "F",
                "contrarian": "C",
                "sniper": "N",
                "momentum": "M",
                "scalper": "S",
                "martingale": "MG",
                "highthreshold": "HT",
                "flashcrash": "FC",
                "streak": "SR",
            }.get(stype, "?")
            auto_lbl = f"{t}_{data.get('asset','BTC')}_{data.get('timeframe','5m')}_{data.get('direction','any')}_{data.get('trigger',0.5)}"
        strategy = Strategy(
            user_id=user.id,
            wallet_id=wallet.id,
            label=auto_lbl,
            asset=Asset(data.get("asset", "BTC")),
            timeframe=Timeframe(data.get("timeframe", "15m")),
            direction=Direction(data.get("direction", "any")),
            trade_amount=data.get("amount", 1.0),
            odds_threshold=data.get("trigger", 0.92),
            price_difference=data.get("price_diff"),
            minutes_after_start=data.get("bet_from", 0),
            minutes_before_end=data.get("bet_to", 0.25),
            stop_loss_percent=sl_percent,
            stop_loss_odds=sl_odds,
            take_profit_percent=tp_percent,
            take_profit_odds=tp_odds,
            max_executions_per_event=data.get("max_exec"),
            max_losses_per_event=data.get("max_losses"),
            max_entry_slippage=data.get("slippage"),
            ma_filter_enabled=data.get("ema", False),
            min_volatility=data.get("volatility"),
            strategy_type=data.get("stype", "fusion"),
            status=StrategyStatus.STOPPED,
        )
        strategy = await db.create_strategy(strategy)
        logger.info(f"Strategy created: {strategy.id} by user {user.telegram_id}")
        await query.edit_message_text(
            f"✅ <b>Strateji Oluşturuldu!</b>\n\n"
            f"{strategy.summary_line(1)}\n\n"
            f"/strategies ile başlatın.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("▶ Başlat", callback_data=f"start_strat_{strategy.id}")],
                    [InlineKeyboardButton("🎰 Stratejiler", callback_data="show_strategies")],
                ]
            ),
        )

    context.user_data.pop("sb", None)
    context.user_data.pop("sb_editing", None)
    context.user_data.pop("sb_edit_id", None)
    return ConversationHandler.END


async def start_edit_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Phase 19.5: Edit existing strategy using SAME builder interface."""
    # 2026-05-22 (Heddas #7): strateji sistemi devre disi — duzenleme engellendi.
    from telegram_bot.handlers.strategies import _strategy_disabled_reply

    if await _strategy_disabled_reply(update):
        return ConversationHandler.END
    query = update.callback_query
    sid = query.data.replace("edit_strat_", "")
    await query.answer()

    db: Database = context.bot_data["db"]
    s = await db.get_strategy(sid)
    if not s:
        await query.message.reply_text("Strateji bulunamadı.")
        return ConversationHandler.END

    data = {
        "asset": s.asset.value,
        "timeframe": s.timeframe.value,
        "direction": s.direction.value,
        "amount": s.trade_amount,
        "trigger": s.odds_threshold or 0.50,
        "price_diff": s.price_difference or 0,
        "bet_from": s.minutes_after_start or 0,
        "bet_to": s.minutes_before_end or 0.5,
        "stop_loss": str(s.stop_loss_odds or s.stop_loss_percent or ""),
        "take_profit": str(s.take_profit_odds or s.take_profit_percent or ""),
        "max_exec": s.max_executions_per_event,
        "max_losses": s.max_losses_per_event,
        "slippage": s.max_entry_slippage,
        "ema": s.ma_filter_enabled,
        "volatility": s.min_volatility or 0,
        "stype": getattr(s, "strategy_type", "fusion") or "fusion",
        "label": s.label or "",
    }
    context.user_data["sb"] = data
    context.user_data["sb_edit_id"] = sid

    name = s.label or s.auto_label()
    text = (
        f"📝 <b>Düzenle:</b> {esc(name)}\n\n"
        + _build_summary(data)
        + "\n\nBir alana tıklayarak değiştirin:"
    )
    keyboard = _build_confirm_keyboard(data, edit_mode=True)
    await query.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)
    return CONFIRM_STRATEGY


async def cancel_builder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("İptal")
    context.user_data.pop("sb", None)
    context.user_data.pop("sb_editing", None)
    context.user_data.pop("sb_edit_id", None)
    await query.edit_message_text("İptal edildi.")
    return ConversationHandler.END


# ═══════════════════════════════════════
# HELPER
# ═══════════════════════════════════════


async def _show_summary(query, context) -> int:
    data = context.user_data.get("sb", {})
    edit_mode = "sb_edit_id" in context.user_data
    text = _build_summary(data) + "\nTap a field to adjust it."
    keyboard = _build_confirm_keyboard(data, edit_mode=edit_mode)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    return CONFIRM_STRATEGY


# ═══════════════════════════════════════
# CONVERSATION HANDLER BUILDER
# ═══════════════════════════════════════


def get_strategy_builder_handler() -> ConversationHandler:
    """Build the ConversationHandler for the Strategy Builder."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_builder, pattern="^add_strategy$"),
            CallbackQueryHandler(start_edit_builder, pattern="^edit_strat_"),
        ],
        states={
            CONFIRM_STRATEGY: [
                # Asset
                CallbackQueryHandler(edit_asset, pattern="^sb_edit_asset$"),
                CallbackQueryHandler(set_asset, pattern="^sb_set_asset_"),
                # Timeframe
                CallbackQueryHandler(edit_timeframe, pattern="^sb_edit_timeframe$"),
                CallbackQueryHandler(set_timeframe, pattern="^sb_set_tf_"),
                # Direction
                CallbackQueryHandler(edit_direction, pattern="^sb_edit_direction$"),
                CallbackQueryHandler(set_direction, pattern="^sb_set_dir_"),
                # EMA toggle
                CallbackQueryHandler(edit_ema, pattern="^sb_edit_ema$"),
                # Text input fields
                CallbackQueryHandler(edit_amount, pattern="^sb_edit_amount$"),
                CallbackQueryHandler(edit_trigger, pattern="^sb_edit_trigger$"),
                CallbackQueryHandler(edit_price_diff, pattern="^sb_edit_price_diff$"),
                CallbackQueryHandler(edit_bet_from, pattern="^sb_edit_bet_from$"),
                CallbackQueryHandler(edit_bet_to, pattern="^sb_edit_bet_to$"),
                CallbackQueryHandler(edit_stop_loss, pattern="^sb_edit_stop_loss$"),
                CallbackQueryHandler(edit_take_profit, pattern="^sb_edit_take_profit$"),
                CallbackQueryHandler(edit_max_exec, pattern="^sb_edit_max_exec$"),
                CallbackQueryHandler(edit_max_losses, pattern="^sb_edit_max_losses$"),
                CallbackQueryHandler(edit_slippage, pattern="^sb_edit_slippage$"),
                CallbackQueryHandler(edit_volatility, pattern="^sb_edit_volatility$"),
                CallbackQueryHandler(edit_stype, pattern="^sb_edit_stype$"),
                CallbackQueryHandler(set_stype, pattern="^sb_set_stype_"),
                # Confirm / Cancel
                CallbackQueryHandler(confirm_strategy, pattern="^sb_confirm$"),
                CallbackQueryHandler(cancel_builder, pattern="^sb_cancel$"),
            ],
            EDIT_FIELD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_field_input),
                CallbackQueryHandler(set_stype, pattern="^sb_set_stype_"),
                CallbackQueryHandler(cancel_builder, pattern="^sb_cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_builder, pattern="^sb_cancel$"),
            CommandHandler("cancel", lambda u, c: ConversationHandler.END),
        ],
        per_message=False,
        per_chat=True,
    )
