"""
PolyPaper Bot - /strategies Handler (v7)
ALL buttons work including Edit with full detail view.
"""

import logging
import os

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from db.database import Database
from db.models import Asset, Direction, Strategy, StrategyStatus, Timeframe
from telegram_bot.banners import banner_strategies
from telegram_bot.templates.safe_html import esc  # Phase 51 P51-03 Faz-2 Cluster E

logger = logging.getLogger("polypaper.handlers.strategies")


def _is_admin_call(update: Update) -> bool:
    """Epic 10 T10.2 (C3): admin gate for state-mutating callbacks.

    Returns True if ADMIN_TELEGRAM_ID / ADMIN_CHAT_ID is unset (dev
    mode) or if the caller matches. Returns False for non-admin user
    in single-admin production deployment.
    """
    admin_id = os.getenv("ADMIN_TELEGRAM_ID") or os.getenv("ADMIN_CHAT_ID")
    if not admin_id:
        return True  # dev mode — no admin configured, allow
    user = update.effective_user
    if user is None:
        return False
    return str(user.id) == str(admin_id)


async def _deny_callback(update: Update) -> None:
    """Respond to non-admin callback with a standard refusal popup."""
    q = update.callback_query
    if q is not None:
        await q.answer("⛔ Admin only", show_alert=True)


async def strategies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        await update.message.reply_text("Önce /start komutunu kullanın.")
        return
    await _send(update.message, db, user)


async def strategies_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user)


# Phase 82a hotfix: Telegram inline keyboard has a 100-button cap; with 3
# buttons per strategy that means 33 strategies max before the last card's
# Start/Delete buttons get silently dropped (observed with 34 active strats).
# Paginate so each page stays well under the cap and all control rows fit.
STRATS_PER_PAGE = 12


async def _send(message, db, user, page: int = 0, edit: bool = False):
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        await message.reply_text("Cüzdan bulunamadı. /start komutunu kullanın.")
        return
    strats = await db.get_strategies_by_user(user.id, wallet.id)
    total = len(strats)
    per = STRATS_PER_PAGE
    pages = max(1, (total + per - 1) // per)
    page = max(0, min(page, pages - 1))
    start_idx = page * per
    end_idx = min(start_idx + per, total)
    page_strats = strats[start_idx:end_idx]

    # Status summary for header
    active = sum(1 for s in strats if s.status == StrategyStatus.ACTIVE)
    stopped = total - active
    header = f"🎰 <b>{wallet.label} Strategies</b>  " f"(▶{active} / ⏸{stopped} / Σ{total})"
    page_hdr = f"  <i>[sayfa {page+1}/{pages}]</i>" if pages > 1 else ""
    text = f"{header}{page_hdr}\n\n"

    btns = []
    if total:
        for i, s in enumerate(page_strats, start=start_idx + 1):
            text += f"{s.summary_line(i)}\n"
            is_stopped = s.status == StrategyStatus.STOPPED
            btns.append(
                [
                    InlineKeyboardButton(f"{i} 📝", callback_data=f"edit_strat_{s.id}"),
                    InlineKeyboardButton(
                        "▶ Start" if is_stopped else "⏸ Stop",
                        callback_data=f"{'start' if is_stopped else 'stop'}_strat_{s.id}",
                    ),
                    InlineKeyboardButton("🗑", callback_data=f"delete_strat_{s.id}"),
                ]
            )
        text += "\nTap 📝 detay, ▶/⏸ kontrol, 🗑 sil."
        # Pagination nav row
        if pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("⬅️ Önceki", callback_data=f"strats_page_{page-1}"))
            nav.append(InlineKeyboardButton(f"{page+1}/{pages}", callback_data="strats_noop"))
            if page < pages - 1:
                nav.append(InlineKeyboardButton("Sonraki ➡️", callback_data=f"strats_page_{page+1}"))
            btns.append(nav)
        # Bulk actions — always available
        btns.append(
            [
                InlineKeyboardButton("▶ Start all", callback_data="start_all_strats"),
                InlineKeyboardButton("⏸ Stop all", callback_data="stop_all_strats"),
            ]
        )
    else:
        text += "Henüz strateji yok.\n"
    btns.append([InlineKeyboardButton("➕ Add Strategy", callback_data="add_strategy")])
    btns.append([InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")])

    banner = banner_strategies()
    # Phase 50 hotfix: Telegram photo caption limit is 1024 chars. If we
    # have many strategies the summary_line list easily overflows → send
    # the banner as a plain photo and follow up with the list as a regular
    # HTML message (4096 char limit).
    if len(text) > 1000:
        await message.reply_photo(
            photo=banner,
            caption="🎰 <b>Strategies</b>",
            parse_mode="HTML",
        )
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(btns),
        )
    else:
        await message.reply_photo(
            photo=banner,
            caption=text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(btns),
        )


async def strategies_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 82a hotfix: pagination callback for /strategies list."""
    q = update.callback_query
    data = q.data or ""
    if data == "strats_noop":
        await q.answer()
        return
    try:
        page = int(data.replace("strats_page_", ""))
    except (ValueError, TypeError, AttributeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. int() coercion
        # of callback_data suffix; fallback to page 0 on malformed.
        page = 0
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user, page=page)


async def start_strategy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Epic 10 T10.2 (C3): admin gate — sid comes verbatim from
    # callback_data; without this, any caller can flip any strategy.
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    sid = q.data.replace("start_strat_", "")
    db: Database = context.bot_data["db"]
    await db.update_strategy_status(sid, StrategyStatus.ACTIVE)
    await q.answer("Started ▶")
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user)


async def stop_strategy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Epic 10 T10.2 (C3): admin gate.
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    sid = q.data.replace("stop_strat_", "")
    db: Database = context.bot_data["db"]
    await db.update_strategy_status(sid, StrategyStatus.STOPPED)
    await q.answer("Stopped ⏸")
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send(q.message, db, user)


async def delete_strategy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 52 BUG #5 — first tap shows confirm dialog; second tap deletes.

    Callback data format:
      delete_strat_<sid>           → first tap: show confirm dialog
      delete_strat_confirm_<sid>   → user confirms: actually delete
      delete_strat_cancel_<sid>    → user cancels: dismiss dialog

    Epic 10 T10.2 (C3): admin gate — without it, any Telegram user who
    reaches a delete_strat_* callback can delete any strategy by id.
    """
    # Gate before reading any callback_data.
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    raw = q.data.replace("delete_strat_", "")
    db: Database = context.bot_data["db"]

    if raw.startswith("confirm_"):
        sid = raw[len("confirm_") :]
        await db.update_strategy_status(sid, StrategyStatus.STOPPED)
        await db.delete_strategy(sid)
        await q.answer("Silindi 🗑")
        try:
            await q.edit_message_text("🗑 Strateji silindi.", parse_mode="HTML")
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): narrow from bare Exception. edit_message
            # may BadRequest on no-op or message gone — silent swallow OK.
            pass
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if user:
            await _send(q.message, db, user)
        return

    if raw.startswith("cancel_"):
        await q.answer("İptal edildi")
        try:
            await q.edit_message_text("❌ Silme iptal edildi.", parse_mode="HTML")
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): edit_message no-op tolerated.
            pass
        return

    # First tap — show confirmation dialog
    sid = raw
    s = await db.get_strategy(sid)
    if not s:
        return await q.answer("Strateji bulunamadı", show_alert=True)
    await q.answer()
    name = s.label or s.auto_label()
    text = (
        f"⚠️ <b>Stratejiyi sil?</b>\n\n"
        f"<b>{esc(name)}</b>\n"
        f"ID: <code>{esc(s.id[:12])}</code>\n"
        f"Durum: <b>{esc(s.status.value)}</b>\n\n"
        f"Bu işlem geri alınamaz. Trade geçmişi log'da kalır."
    )
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Evet, sil", callback_data=f"delete_strat_confirm_{s.id}"),
                InlineKeyboardButton("❌ İptal", callback_data=f"delete_strat_cancel_{s.id}"),
            ],
        ]
    )
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def start_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Epic 10 T10.2 (C3): admin gate.
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await q.answer("Hata oluştu. Tekrar deneyin.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await q.answer("Cüzdan bulunamadı")
    n = 0
    for s in await db.get_strategies_by_user(user.id, wallet.id):
        if s.status == StrategyStatus.STOPPED:
            await db.update_strategy_status(s.id, StrategyStatus.ACTIVE)
            n += 1
    await q.answer(f"Started {n} strategies ▶")
    await _send(q.message, db, user)


async def stop_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Epic 10 T10.2 (C3): admin gate.
    if not _is_admin_call(update):
        return await _deny_callback(update)
    q = update.callback_query
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await q.answer("Hata oluştu. Tekrar deneyin.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await q.answer("Cüzdan bulunamadı")
    n = 0
    for s in await db.get_strategies_by_user(user.id, wallet.id):
        if s.status == StrategyStatus.ACTIVE:
            await db.update_strategy_status(s.id, StrategyStatus.STOPPED)
            n += 1
    await q.answer(f"Stopped {n} strategies ⏸")
    await _send(q.message, db, user)


# ══════════════════════════════════════════════════════════════════
# Phase 82a hotfix: /start_all and /stop_all slash commands
# ──────────────────────────────────────────────────────────────────
# Previously only the callback buttons existed, but with 34 strategies
# the Telegram 100-button cap truncated those buttons off the list.
# Slash commands are a reliable bulk-control escape hatch.
# ══════════════════════════════════════════════════════════════════
async def start_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await update.message.reply_text("Cüzdan bulunamadı.")
    n = 0
    for s in await db.get_strategies_by_user(user.id, wallet.id):
        if s.status == StrategyStatus.STOPPED:
            await db.update_strategy_status(s.id, StrategyStatus.ACTIVE)
            n += 1
    await update.message.reply_text(f"▶ <b>{n} strateji başlatıldı.</b>", parse_mode="HTML")


async def stop_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await update.message.reply_text("Cüzdan bulunamadı.")
    n = 0
    for s in await db.get_strategies_by_user(user.id, wallet.id):
        if s.status == StrategyStatus.ACTIVE:
            await db.update_strategy_status(s.id, StrategyStatus.STOPPED)
            n += 1
    await update.message.reply_text(f"⏸ <b>{n} strateji durduruldu.</b>", parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════
# Phase 52 BUG #2/#3 + ÖNERİ #3/#4 — /quick_strategy
#   • Real inline wizard when called with no args
#   • type → direction + default trigger odds mapping (BUG #3)
#   • Readable name format: momentum_ETH_15m_up_0.55 (ÖNERİ #4)
#   • Explanatory Usage text for power users (ÖNERİ #3)
# ═══════════════════════════════════════════════════════════════════════

QUICK_STRATEGY_TYPES = ("fusion", "classic", "momentum", "contrarian", "scalper", "sniper")

# Phase 52 BUG #3 — strategy type now drives direction and sensible defaults.
# momentum: follow the trend, prefer YES (UP) entries.
# contrarian: fade the move, prefer NO (DOWN) entries.
# scalper/sniper/fusion/classic: bi-directional (engine picks side).
# classic (Phase 82e Sprint 4.6): no-algorithm — sadece trigger/TP/SL
_TYPE_DEFAULTS = {
    "fusion": {"direction": Direction.ANY, "odds_default": 0.55},
    "classic": {"direction": Direction.ANY, "odds_default": 0.55},
    "momentum": {"direction": Direction.UP, "odds_default": 0.55},
    "contrarian": {"direction": Direction.DOWN, "odds_default": 0.55},
    "scalper": {"direction": Direction.ANY, "odds_default": 0.52},
    "sniper": {"direction": Direction.ANY, "odds_default": 0.65},
}


def _apply_quick_strategy_type(stype: str) -> tuple[Direction, float]:
    """Return (direction, odds_default) for a quick-strategy type."""
    spec = _TYPE_DEFAULTS.get(stype, _TYPE_DEFAULTS["fusion"])
    return spec["direction"], spec["odds_default"]


def _quick_strategy_label(
    stype: str, asset: Asset, tf: Timeframe, direction: Direction, odds: float
) -> str:
    """Phase 52 ÖNERİ #4 — human-readable auto label.

    Format: ``<type>_<ASSET>_<tf>_<direction>_<odds>``
    Example: ``momentum_ETH_15m_up_0.55``
    """
    return f"{stype}_{asset.value}_{tf.value}_{direction.value}_{odds}"


def _quick_strategy_usage_text() -> str:
    return (
        "⚡ <b>Quick Strategy</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Argümansız çağırırsan inline wizard açılır.\n"
        "Güç kullanıcıları tek satırla direkt oluşturabilir:\n\n"
        "<code>/quick_strategy &lt;asset&gt; &lt;tf&gt; &lt;amount$&gt; &lt;odds&gt; [type]</code>\n\n"
        "<b>Parametreler</b>\n"
        "  • <b>asset</b>   : BTC | ETH | SOL | XRP\n"
        "  • <b>tf</b>      : 1m | 5m | 15m | 1h\n"
        "  • <b>amount</b>  : 0.5 — 100 ($)\n"
        "  • <b>odds</b>    : 0.50 — 0.95\n"
        "  • <b>type</b>    : fusion (default) | classic | momentum | contrarian | scalper | sniper\n\n"
        "<b>Tip etkisi</b> (Phase 52 BUG #3)\n"
        "  momentum   → direction=UP, follow trend\n"
        "  contrarian → direction=DOWN, fade reversals\n"
        "  scalper    → direction=ANY, tight odds 0.52\n"
        "  sniper     → direction=ANY, high threshold 0.65\n"
        "  fusion     → direction=ANY, default 0.55\n"
        "  classic    → direction=ANY, no-algo trigger/TP/SL\n\n"
        "<b>Örnek</b>\n"
        "  <code>/quick_strategy ETH 15m 1 0.60 momentum</code>\n"
        "  → <code>momentum_ETH_15m_up_0.60</code>"
    )


async def quick_strategy_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Use /start first.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await update.message.reply_text("No wallet.")
    args = context.args or []

    # Phase 52 BUG #2 — no args ⇒ inline wizard
    if len(args) == 0:
        return await _quick_wizard_start(update, context)

    if len(args) < 4:
        # Partial args → show Usage rather than guessing
        return await update.message.reply_text(_quick_strategy_usage_text(), parse_mode="HTML")

    try:
        asset = Asset(args[0].upper())
        tf = Timeframe(args[1].lower())
        amt = float(args[2])
        odds = float(args[3])
        stype = args[4].lower() if len(args) > 4 else "fusion"
        if stype not in QUICK_STRATEGY_TYPES:
            stype = "fusion"
    except (ValueError, KeyError) as e:
        return await update.message.reply_text(f"Invalid: {esc(e)}")

    direction, _odds_default = _apply_quick_strategy_type(stype)
    auto_lbl = _quick_strategy_label(stype, asset, tf, direction, odds)
    s = Strategy(
        user_id=user.id,
        wallet_id=wallet.id,
        label=auto_lbl,
        asset=asset,
        timeframe=tf,
        trade_amount=amt,
        odds_threshold=odds,
        direction=direction,
        strategy_type=stype,
    )
    s = await db.create_strategy(s)
    await update.message.reply_text(
        f"✅ Created!\n{s.summary_line(1)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start", callback_data=f"start_strat_{s.id}")],
                [InlineKeyboardButton("🎰 Strategies", callback_data="show_strategies")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 52 BUG #2 — /quick_strategy inline wizard
#
# State machine stored in context.user_data["qs_wizard"]:
#   {asset: "BTC", tf: "5m", amount: 1.0, odds: 0.55, stype: "fusion"}
#
# Callback prefix: "qs_"
#   qs_a_<ASSET>     → asset picked, advance to timeframe
#   qs_t_<TF>        → timeframe picked, advance to amount
#   qs_m_<AMT>       → amount picked, advance to odds
#   qs_o_<ODDS>      → odds picked, advance to type
#   qs_k_<TYPE>      → type picked, show confirm
#   qs_go            → confirm, create strategy
#   qs_cancel        → abort wizard
# ═══════════════════════════════════════════════════════════════════════

_ASSETS = ("BTC", "ETH", "SOL", "XRP")
_TIMEFRAMES = ("1m", "5m", "15m", "1h")
_AMOUNTS = (0.5, 1.0, 2.0, 5.0, 10.0)
_ODDS = (0.50, 0.55, 0.60, 0.65, 0.70)


async def _quick_wizard_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["qs_wizard"] = {}
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(a, callback_data=f"qs_a_{a}") for a in _ASSETS],
            [InlineKeyboardButton("❌ İptal", callback_data="qs_cancel")],
        ]
    )
    text = (
        "⚡ <b>Quick Strategy Wizard</b> — Adım 1/5\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 <b>Asset seç:</b>\n"
        "<i>(veya direkt: /quick_strategy BTC 5m 1 0.55 momentum)</i>"
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def quick_strategy_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch for all qs_* callback data."""
    q = update.callback_query
    data = q.data or ""
    state = context.user_data.setdefault("qs_wizard", {})

    if data == "qs_cancel":
        context.user_data.pop("qs_wizard", None)
        await q.answer("İptal")
        try:
            await q.edit_message_text("❌ Wizard iptal edildi.", parse_mode="HTML")
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): edit_message no-op tolerated.
            pass
        return

    # Step 1 → asset picked, show timeframe picker
    if data.startswith("qs_a_"):
        state["asset"] = data[len("qs_a_") :]
        await q.answer()
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(tf, callback_data=f"qs_t_{tf}") for tf in _TIMEFRAMES],
                [InlineKeyboardButton("❌ İptal", callback_data="qs_cancel")],
            ]
        )
        return await _edit_or_reply(
            q,
            f"⚡ <b>Quick Strategy Wizard</b> — Adım 2/5\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Asset: <b>{esc(state['asset'])}</b>\n\n"
            f"⏱ <b>Timeframe seç:</b>",
            kb,
        )

    # Step 2 → tf picked, show amount picker
    if data.startswith("qs_t_"):
        state["tf"] = data[len("qs_t_") :]
        await q.answer()
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"${a}", callback_data=f"qs_m_{a}") for a in _AMOUNTS],
                [InlineKeyboardButton("❌ İptal", callback_data="qs_cancel")],
            ]
        )
        return await _edit_or_reply(
            q,
            f"⚡ <b>Quick Strategy Wizard</b> — Adım 3/5\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Asset: <b>{esc(state.get('asset',''))}</b>\n"
            f"TF: <b>{esc(state['tf'])}</b>\n\n"
            f"💰 <b>Trade amount seç ($):</b>",
            kb,
        )

    # Step 3 → amount picked, show odds picker
    if data.startswith("qs_m_"):
        try:
            state["amount"] = float(data[len("qs_m_") :])
        except ValueError:
            return await q.answer("Geçersiz", show_alert=True)
        await q.answer()
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"{o:.2f}", callback_data=f"qs_o_{o}") for o in _ODDS],
                [InlineKeyboardButton("❌ İptal", callback_data="qs_cancel")],
            ]
        )
        return await _edit_or_reply(
            q,
            f"⚡ <b>Quick Strategy Wizard</b> — Adım 4/5\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Asset: <b>{esc(state.get('asset',''))}</b>\n"
            f"TF: <b>{esc(state.get('tf',''))}</b>\n"
            f"Amount: <b>${state['amount']}</b>\n\n"
            f"🎯 <b>Trigger odds seç:</b>",
            kb,
        )

    # Step 4 → odds picked, show type picker
    if data.startswith("qs_o_"):
        try:
            state["odds"] = float(data[len("qs_o_") :])
        except ValueError:
            return await q.answer("Geçersiz", show_alert=True)
        await q.answer()
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🔬 fusion", callback_data="qs_k_fusion"),
                    InlineKeyboardButton("📈 momentum", callback_data="qs_k_momentum"),
                ],
                [
                    InlineKeyboardButton("🔄 contrarian", callback_data="qs_k_contrarian"),
                    InlineKeyboardButton("⚡ scalper", callback_data="qs_k_scalper"),
                ],
                [InlineKeyboardButton("🎯 sniper", callback_data="qs_k_sniper")],
                [InlineKeyboardButton("❌ İptal", callback_data="qs_cancel")],
            ]
        )
        return await _edit_or_reply(
            q,
            f"⚡ <b>Quick Strategy Wizard</b> — Adım 5/5\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Asset: <b>{esc(state.get('asset',''))}</b> | "
            f"TF: <b>{esc(state.get('tf',''))}</b> | "
            f"${state.get('amount','')} @ {state.get('odds','')}\n\n"
            f"📊 <b>Type seç:</b>\n"
            f"<i>momentum→UP, contrarian→DOWN, diğerleri→ANY</i>",
            kb,
        )

    # Step 5 → type picked, show confirm
    if data.startswith("qs_k_"):
        stype = data[len("qs_k_") :]
        if stype not in QUICK_STRATEGY_TYPES:
            stype = "fusion"
        state["stype"] = stype
        await q.answer()
        try:
            asset = Asset(state["asset"])
            tf = Timeframe(state["tf"])
        except (ValueError, KeyError):
            return await q.message.reply_text("❌ Geçersiz state. /quick_strategy ile tekrar dene.")
        direction, _ = _apply_quick_strategy_type(stype)
        label = _quick_strategy_label(stype, asset, tf, direction, state["odds"])
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Oluştur", callback_data="qs_go"),
                    InlineKeyboardButton("❌ İptal", callback_data="qs_cancel"),
                ],
            ]
        )
        return await _edit_or_reply(
            q,
            f"⚡ <b>Quick Strategy Wizard</b> — Onay\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📛 Label: <code>{esc(label)}</code>\n"
            f"📊 Asset: <b>{esc(asset.value)}</b>\n"
            f"⏱ TF: <b>{esc(tf.value)}</b>\n"
            f"💰 Amount: <b>${state['amount']}</b>\n"
            f"🎯 Odds: <b>{state['odds']}</b>\n"
            f"📈 Type: <b>{esc(stype)}</b>\n"
            f"↕️ Direction: <b>{esc(direction.value)}</b>\n",
            kb,
        )

    # Confirm → create
    if data == "qs_go":
        db: Database = context.bot_data["db"]
        user = await db.get_user_by_telegram_id(update.effective_user.id)
        if not user:
            return await q.answer("User not found", show_alert=True)
        wallet = await db.get_active_wallet(user.id)
        if not wallet:
            return await q.answer("No wallet", show_alert=True)
        try:
            asset = Asset(state["asset"])
            tf = Timeframe(state["tf"])
            amt = float(state["amount"])
            odds = float(state["odds"])
            stype = state.get("stype", "fusion")
        except (KeyError, ValueError):
            return await q.answer("Incomplete wizard state", show_alert=True)
        direction, _ = _apply_quick_strategy_type(stype)
        label = _quick_strategy_label(stype, asset, tf, direction, odds)
        s = Strategy(
            user_id=user.id,
            wallet_id=wallet.id,
            label=label,
            asset=asset,
            timeframe=tf,
            trade_amount=amt,
            odds_threshold=odds,
            direction=direction,
            strategy_type=stype,
        )
        s = await db.create_strategy(s)
        context.user_data.pop("qs_wizard", None)
        await q.answer("✅ Oluşturuldu")
        try:
            await q.edit_message_text(
                f"✅ <b>Strateji oluşturuldu!</b>\n\n{s.summary_line(1)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("▶ Start", callback_data=f"start_strat_{s.id}")],
                        [InlineKeyboardButton("🎰 Strategies", callback_data="show_strategies")],
                    ]
                ),
            )
        except (TimeoutError, BadRequest, TelegramError):
            # T11.8-B (2026-04-24): edit_message fallback to fresh reply.
            await q.message.reply_text(f"✅ Created!\n{s.summary_line(1)}", parse_mode="HTML")
        return

    # Unknown qs_ → silently dismiss
    await q.answer()


async def _edit_or_reply(q, text: str, kb):
    """Try to edit the wizard message in place; fall back to a new reply."""
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): edit_message fallback to fresh reply.
        await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def edit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 19: /edit <id_prefix> <field> <value> — Edit any strategy parameter."""
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Use /start first.")

    args = context.args or []
    if len(args) < 3:
        return await update.message.reply_text(
            "📝 <b>Strateji Düzenle</b>\n\n"
            "Kullanım: <code>/edit ID alan değer</code>\n\n"
            "Düzenlenebilir alanlar:\n"
            "• <code>label</code> — İsim\n"
            "• <code>trade_amount</code> — İşlem tutarı ($)\n"
            "• <code>odds_threshold</code> — Tetikleme eşiği\n"
            "• <code>direction</code> — Yön (up/down/any)\n"
            "• <code>price_difference</code> — Fiyat farkı %\n"
            "• <code>stop_loss_odds</code> — Stop loss\n"
            "• <code>take_profit_odds</code> — Take profit\n"
            "• <code>max_executions_per_event</code> — Max işlem\n"
            "• <code>ma_filter_enabled</code> — EMA (1/0)\n"
            "• <code>strategy_type</code> — Tip\n\n"
            "Örnek:\n"
            "<code>/edit abc12345 label Agresif_BTC</code>\n"
            "<code>/edit abc12345 trade_amount 2.5</code>\n"
            "<code>/edit abc12345 odds_threshold 0.60</code>",
            parse_mode="HTML",
        )

    prefix = args[0]
    field = args[1]
    value_str = " ".join(args[2:])

    # Find strategy by prefix
    strats = await db.get_strategies_by_user(user.id)
    matched = [s for s in strats if s.id.startswith(prefix)]
    if not matched:
        return await update.message.reply_text(f"❌ '{prefix}' ile başlayan strateji bulunamadı.")
    if len(matched) > 1:
        return await update.message.reply_text(
            f"⚠️ '{prefix}' birden fazla eşleşiyor. Daha uzun ID kullanın."
        )

    s = matched[0]

    # Type conversion
    float_fields = {
        "trade_amount",
        "odds_threshold",
        "price_difference",
        "stop_loss_percent",
        "stop_loss_odds",
        "take_profit_percent",
        "take_profit_odds",
        "min_volatility",
        "max_entry_slippage",
        "minutes_before_end",
        "minutes_after_start",
    }
    int_fields = {"max_executions_per_event", "max_losses_per_event", "ma_filter_enabled"}
    str_fields = {"label", "direction", "strategy_type"}

    try:
        if field in float_fields:
            value = float(value_str) if value_str.lower() not in ("none", "off", "0") else None
        elif field in int_fields:
            value = int(value_str)
        elif field in str_fields:
            value = value_str
        else:
            return await update.message.reply_text(
                f"❌ '{field}' düzenlenebilir değil.\n/edit yazarak alanları görün."
            )
    except ValueError:
        return await update.message.reply_text(f"❌ Geçersiz değer: '{value_str}'")

    ok = await db.update_strategy_field(s.id, field, value)
    if ok:
        await update.message.reply_text(
            f"✅ <b>Güncellendi!</b>\n"
            f"Strateji: <code>{s.id[:8]}</code>\n"
            f"<b>{field}</b> → <code>{esc(value)}</code>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(f"❌ '{field}' güncellenemedi.")


async def clone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 24: /clone <id_prefix> — Clone an existing strategy."""
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start kullanın.")

    args = context.args or []
    if not args:
        return await update.message.reply_text(
            "📋 <b>Strateji Klonla</b>\n\n"
            "<code>/clone ID</code>\n\n"
            "Mevcut stratejinin aynısını kopyalar.\n"
            "Sonra /edit ile parametreleri değiştirebilirsiniz.",
            parse_mode="HTML",
        )

    prefix = args[0]
    strats = await db.get_strategies_by_user(user.id)
    matched = [s for s in strats if s.id.startswith(prefix)]
    if not matched:
        return await update.message.reply_text(f"❌ '{prefix}' bulunamadı.")
    if len(matched) > 1:
        return await update.message.reply_text("⚠️ Birden fazla eşleşme. Daha uzun ID kullanın.")

    s = matched[0]
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await update.message.reply_text("Cüzdan bulunamadı.")

    from db.models import Strategy, StrategyStatus

    clone = Strategy(
        user_id=user.id,
        wallet_id=wallet.id,
        label=f"KLON_{s.label or s.auto_label()}",
        asset=s.asset,
        timeframe=s.timeframe,
        direction=s.direction,
        trade_amount=s.trade_amount,
        odds_threshold=s.odds_threshold,
        price_difference=s.price_difference,
        minutes_after_start=s.minutes_after_start,
        minutes_before_end=s.minutes_before_end,
        stop_loss_percent=s.stop_loss_percent,
        stop_loss_odds=s.stop_loss_odds,
        take_profit_percent=s.take_profit_percent,
        take_profit_odds=s.take_profit_odds,
        max_executions_per_event=s.max_executions_per_event,
        max_losses_per_event=s.max_losses_per_event,
        max_entry_slippage=s.max_entry_slippage,
        ma_filter_enabled=s.ma_filter_enabled,
        min_volatility=s.min_volatility,
        strategy_type=s.strategy_type,
        status=StrategyStatus.STOPPED,
    )
    clone = await db.create_strategy(clone)

    await update.message.reply_text(
        f"📋 <b>Klonlandı!</b>\n\n"
        f"Kaynak: {s.label or s.id[:8]}\n"
        f"Klon: {clone.label}\n"
        f"ID: <code>{clone.id[:12]}</code>\n\n"
        f"/edit {clone.id[:8]} ile parametreleri değiştirin.\n"
        f"/strategies ile başlatın.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Başlat", callback_data=f"start_strat_{clone.id}")],
                [InlineKeyboardButton("🎰 Stratejiler", callback_data="show_strategies")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster E — merged from autopilot_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def autopilot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run AutoPilot analysis and show proposed actions with approve/reject buttons."""
    engine = context.bot_data.get("engine")
    if not engine or not engine.autopilot:
        return await update.message.reply_text("AutoPilot aktif degil.")

    await update.message.reply_text("🤖 AutoPilot analiz ediliyor...")

    actions = await engine.autopilot.generate_actions()

    if not actions:
        return await update.message.reply_text(
            "🤖 <b>AutoPilot</b>\n\n"
            "✅ Tum stratejiler normal parametrelerde.\n"
            "Oneri uretmek icin en az 8 trade'li strateji gerekli.",
            parse_mode="HTML",
        )

    for action in actions:
        aid = await engine.autopilot.store_pending(action)

        # Phase 50 hotfix A-08: action fields can contain '<' (e.g. "<1s")
        # which Telegram's HTML parser then interprets as a tag. Escape all
        # string interpolation to avoid "Can't parse entities: unsupported
        # start tag" crashes.
        text = (
            f"{esc(action.get('emoji', '🤖'))} <b>AutoPilot Onerisi</b>\n\n"
            f"<b>{esc(str(action.get('desc', '')))}</b>\n"
            f"Neden: {esc(str(action.get('reason', '')))}\n"
            f"Tip: {esc(str(action.get('stype', '')))}"
        )

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Onayla", callback_data=f"ap_yes_{aid}"),
                    InlineKeyboardButton("❌ Reddet", callback_data=f"ap_no_{aid}"),
                ]
            ]
        )

        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    await update.message.reply_text(
        f"🤖 {len(actions)} aksiyon onerisi yukarida.\n"
        f"Butonlara basarak onayla veya reddet.\n"
        f"<i>Bot restart olsa bile butonlar calisir.</i>",
        parse_mode="HTML",
    )


async def autopilot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle approve/reject button presses."""
    query = update.callback_query
    await query.answer()

    engine = context.bot_data.get("engine")
    if not engine or not engine.autopilot:
        return await query.edit_message_text("AutoPilot aktif degil.")

    data = query.data
    parts = data.split("_", 2)
    if len(parts) < 3:
        return await query.edit_message_text("❌ Gecersiz buton verisi.")

    decision = parts[1]
    action_id = parts[2]

    if decision == "yes":
        result = await engine.autopilot.execute_action(action_id)
    else:
        result = await engine.autopilot.reject_action(action_id)

    # Remove buttons after action - ALWAYS use HTML
    try:
        await query.edit_message_text(f"🤖 {esc(str(result))}", parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): autopilot callback wrapper. AI brain
        # methods may surface heterogeneous exceptions. Generic finished
        # message preserves UX.
        logger.error(f"Autopilot callback error: {esc(str(e))}")
        await query.edit_message_text("🤖 Islem tamamlandi.", parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster E — merged from optimize_handler.py
# ═══════════════════════════════════════════════════════════════════════

# Phase 17: Presets — slippage=None (was killing 688 trades), data from 481 trades
OPTIMIZED_PRESETS = [
    {
        "name": "Sweet Spot Fusion",
        "desc": "50-55c zone +0.59 PnL. Slippage cap removed (was blocking 504x!)",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 2.0,
        "bet_from": 0.5,
        "bet_to": 0.25,
        "sl_odds": 0.40,
        "tp_odds": 0.15,
        "max_exec": 2,
        "max_losses": 1,
        "slippage": None,
        "ema": True,
        "volatility": 0.3,
        "stype": "fusion",
    },
    {
        "name": "ETH Contrarian Dip",
        "desc": "11t 5W +6.76 PROVEN. Best single strategy.",
        "asset": "ETH",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.30,
        "price_diff": 5.0,
        "bet_from": 1.0,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 1,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "contrarian",
    },
    {
        "name": "BTC Contrarian Dip",
        "desc": "3t 2W +0.58. 0-50c zone: +10.02 total PnL.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 1,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "contrarian",
    },
    {
        "name": "SOL Contrarian Dip",
        "desc": "SOL dip hunter. Contrarian type: +14.68 PnL overall.",
        "asset": "SOL",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 1,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "contrarian",
    },
    {
        "name": "XRP Contrarian Dip",
        "desc": "5t 3W +1.40 proven. XRP dip hunter.",
        "asset": "XRP",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 1,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "contrarian",
    },
    {
        "name": "DOWN Bias Fusion",
        "desc": "DOWN only. Needs volume to validate edge.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "down",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 2.0,
        "bet_from": 0.5,
        "bet_to": 0.25,
        "sl_odds": 0.40,
        "tp_odds": 0.15,
        "max_exec": 2,
        "max_losses": 1,
        "slippage": None,
        "ema": True,
        "volatility": 0.3,
        "stype": "fusion",
    },
    {
        "name": "Conservative Sniper",
        "desc": "High thr 0.90 + EMA + vol. sniper: 58t 60% WR.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.90,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.25,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 1,
        "slippage": None,
        "ema": True,
        "volatility": 0.5,
        "stype": "sniper",
    },
    {
        "name": "BTC Martingale DCA",
        "desc": "Kelly-filtered 1.5x DCA, 8 levels, contrarian base signal.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 8,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "martingale",
    },
    {
        "name": "ETH Martingale DCA",
        "desc": "ETH Kelly-filtered 1.5x DCA, 8 levels max.",
        "asset": "ETH",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 3.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": 0.20,
        "max_exec": 1,
        "max_losses": 8,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "martingale",
    },
    {
        "name": "BTC Momentum Trend",
        "desc": "📈 Trend-following. Backtest: %92 WR, +41 PnL. İLK LİVE TEST.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 2.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "momentum",
    },
    {
        "name": "BTC Scalper Quick",
        "desc": "⚡ Hızlı giriş-çıkış. Backtest: %89 WR, +34 PnL.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 2.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "scalper",
    },
    {
        "name": "ETH Momentum Trend",
        "desc": "📈 ETH trend-following. Yeni strateji tipi.",
        "asset": "ETH",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 2.0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "momentum",
    },
    {
        "name": "BTC High-Threshold Pure",
        "desc": "🏔️ Sadece 80c+ zone. 87t %84 WR kanıtlanmış. Ultra-güvenli.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 2.0,
        "trigger": 0.80,
        "price_diff": 0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "highthreshold",
    },
    {
        "name": "BTC Flash Crash Hunter",
        "desc": "💥 Ani düşüşlerde alır. Mean-reversion. Nadir ama kârlı.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.40,
        "price_diff": 0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "flashcrash",
    },
    {
        "name": "BTC Streak Reversal",
        "desc": "🔄 Ardışık aynı yön sonrası ters oyna. Contrarian tarzı.",
        "asset": "BTC",
        "timeframe": "5m",
        "direction": "any",
        "amount": 1.0,
        "trigger": 0.50,
        "price_diff": 0,
        "bet_from": 0.5,
        "bet_to": 0.5,
        "sl_odds": None,
        "tp_odds": None,
        "max_exec": 1,
        "max_losses": 3,
        "slippage": None,
        "ema": False,
        "volatility": 0,
        "stype": "streak",
    },
]


async def optimize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Use /start first.")

    text = (
        "🧪 <b>Optimized Strategy Presets</b>\n\n"
        "Based on 347 trade analysis:\n"
        "• Contrarian: +20.43 PnL (41% WR, big wins)\n"
        "• 0-50c zone: +16.57 PnL (dip buys)\n"
        "• DOWN bias: +10.49 vs UP: -8.31\n"
        "• ETH Dip preset: +10.32 in 3 trades\n\n"
        "Select a preset to deploy:\n"
    )

    buttons = []
    for i, p in enumerate(OPTIMIZED_PRESETS):
        text += f"\n<b>{i+1}. {p['name']}</b>\n{p['desc']}\n"
        flags = []
        if p.get("ema"):
            flags.append("EMA")
        if p.get("volatility"):
            flags.append(f"Vol={p['volatility']}%")
        if p.get("slippage"):
            flags.append(f"Slip={p['slippage']}")
        text += f"   {p['asset']}/{p['timeframe']} thr={p['trigger']} [{p['stype']}] {' '.join(flags)}\n"
        buttons.append(
            [InlineKeyboardButton(f"{i+1}. {p['name']}", callback_data=f"opt_deploy_{i}")]
        )

    buttons.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")])
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def optimize_deploy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    idx = int(q.data.replace("opt_deploy_", ""))
    if idx >= len(OPTIMIZED_PRESETS):
        return await q.message.reply_text("Invalid preset.")

    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(q.from_user.id)
    if not user:
        return await q.message.reply_text("Use /start first.")
    wallet = await db.get_active_wallet(user.id)
    if not wallet:
        return await q.message.reply_text("No wallet found.")

    p = OPTIMIZED_PRESETS[idx]

    strategy = Strategy(
        user_id=user.id,
        wallet_id=wallet.id,
        label=p["name"],
        asset=Asset(p["asset"]),
        timeframe=Timeframe(p["timeframe"]),
        direction=Direction(p["direction"]),
        trade_amount=p["amount"],
        odds_threshold=p["trigger"],
        price_difference=p.get("price_diff"),
        minutes_after_start=p.get("bet_from", 0),
        minutes_before_end=p.get("bet_to", 0.25),
        stop_loss_odds=p.get("sl_odds"),
        take_profit_odds=p.get("tp_odds"),
        max_executions_per_event=p.get("max_exec"),
        max_losses_per_event=p.get("max_losses"),
        max_entry_slippage=p.get("slippage"),
        ma_filter_enabled=p.get("ema", False),
        min_volatility=p.get("volatility"),
        strategy_type=p.get("stype", "fusion"),
        status=StrategyStatus.ACTIVE,
    )

    strategy = await db.create_strategy(strategy)

    flags = []
    if p.get("ema"):
        flags.append("✅ EMA")
    if p.get("volatility"):
        flags.append(f"✅ Vol={p['volatility']}%")
    if p.get("slippage"):
        flags.append(f"✅ Slip={p['slippage']}")

    await q.message.reply_text(
        f"🚀 <b>Deployed: {p['name']}</b>\n\n"
        f"ID: {strategy.id[:8]}\n"
        f"{p['asset']}/{p['timeframe']} [{p['stype']}]\n"
        f"Trigger: {p['trigger']} | Amount: ${p['amount']}\n"
        f"Price diff: {p.get('price_diff', 0)}% | Slippage: {p.get('slippage', 'None')}\n"
        f"{'  '.join(flags)}\n\n"
        f"Strategy is <b>ACTIVE</b> and trading now.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🎰 View Strategies", callback_data="show_strategies")],
                [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster I — merged from kelly_handler.py
# ═══════════════════════════════════════════════════════════════════════
from core.kelly import get_strategy_kelly  # noqa: E402


async def kelly_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Once /start kullanin.")
    wallet = await db.get_active_wallet(user.id)
    balance = wallet.balance if wallet else 0
    strats = await db.conn.execute_fetchall(
        """SELECT s.id, s.label, s.strategy_type, s.trade_amount, s.odds_threshold,
            COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as trades,
            COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as wins
        FROM strategies s LEFT JOIN executions e ON e.strategy_id=s.id
        WHERE s.status='active' AND s.user_id=? GROUP BY s.id ORDER BY wins DESC""",
        (user.id,),
    )
    text = (
        f"🎯 <b>Quarter Kelly Sizing</b>\n💰 Bakiye: <b>${balance:.2f}</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
    )
    for s in strats:
        k = await get_strategy_kelly(db, s[0], balance)
        wr = s[6] / s[5] * 100 if s[5] > 0 else 0
        emoji = {"high": "✅", "medium": "🟡", "low": "⚪"}.get(k["confidence"], "⚪")
        text += (
            f"\n{emoji} <b>{s[1]}</b>\n"
            f"  {s[5]}t {wr:.0f}% | ${s[3]:.0f} → Kelly: <b>${k['size']:.2f}</b>\n"
            f"  QK={k['quarter_kelly_pct']:.1f}% [{k['confidence']}]\n"
        )
    engine = context.bot_data.get("engine")
    mode = "✅ AKTIF" if engine and engine._kelly_mode else "⚫ KAPALI"
    text += f"\n📊 Kelly Modu: {mode}\n<i>/kelly_toggle ile ac/kapa</i>"
    await update.message.reply_text(text, parse_mode="HTML")


async def kelly_toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = context.bot_data.get("engine")
    if not engine:
        return await update.message.reply_text("Engine bulunamadi.")
    engine._kelly_mode = not engine._kelly_mode
    # Epic 6 T6.5: Persist to DB so Kelly state survives bot restart.
    # Paired with engine.start() boot loader read of `engine.kelly_mode`.
    # Also mirrored in AI Brain panel's kelly_sizing virtual-flag branch
    # (telegram_bot/handlers/ai_handler.py::brain_toggle_callback).
    try:
        db = context.bot_data.get("db") or getattr(engine, "db", None)
        if db is not None:
            await db.set_setting("engine.kelly_mode", "1" if engine._kelly_mode else "0")
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. set_setting
        # surfaces aiosqlite.Error only. Persist failure is non-fatal.
        logger.warning(f"Kelly mode persist failed: " f"{type(e).__name__}: {e}")
    status = "✅ AKTIF" if engine._kelly_mode else "⚫ KAPALI"
    await update.message.reply_text(f"🎯 Kelly Modu: {status}")


async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI analysis: /analyze or /analyze brain — Phase 79b: now with execute button"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    engine = context.bot_data.get("engine")
    if not engine or not engine.analyst:
        return await update.message.reply_text("🧠 AI Brain aktif degil.", parse_mode="HTML")

    brain = engine.analyst
    status = brain.get_status()
    remaining = status["remaining"]

    mode = "daily"
    if context.args:
        m = context.args[0].lower()
        if m in ("brain", "auto", "otonom"):
            mode = "brain"

    # Loading indicator BEFORE processing
    await update.message.reply_text(
        f"⏳ <b>AI Analiz Baslatiliyor</b>\n" f"Mod: {mode}\n" f"Budget: ${remaining:.2f} kaldi...",
        parse_mode="HTML",
    )

    try:
        if mode == "brain":
            result = await brain.run_brain_cycle()
            if result:
                await update.message.reply_text(
                    f"🧠 <b>Brain Cycle Sonucu</b>\n\n{result}", parse_mode="HTML"
                )
            else:
                await update.message.reply_text("⚠️ Brain cycle sonuc uretmedi.", parse_mode="HTML")
        else:
            # Phase 79b: Use parsed version to enable action execution
            response, parsed = await brain.manual_analyze_parsed(mode)
            if not response:
                return await update.message.reply_text(
                    "⚠️ Analiz sonuc uretmedi.", parse_mode="HTML"
                )

            # Send raw analysis text (truncated for Telegram)
            for i in range(0, min(len(response), 4000), 4000):
                await update.message.reply_text(response[i : i + 4000], parse_mode="HTML")

            # If we have parsed actions, show them with execute button
            if parsed and parsed.get("actions"):
                actions = parsed["actions"]
                view = parsed.get("market_view", "?")
                confidence = parsed.get("confidence", 0)

                text = (
                    f"📋 <b>Onerilen Aksiyonlar ({len(actions)} adet)</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Gorunum: {esc(str(view))} | Guven: {confidence:.0%}\n\n"
                )
                for a in actions:
                    atype = a.get("type", "?")
                    sid = a.get("id", "?")[:12]
                    reason = esc(str(a.get("reason", ""))[:80])
                    text += f"  • <b>{atype}</b> {sid} — {reason}\n"

                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("✅ Uygula", callback_data="analyze_apply"),
                            InlineKeyboardButton("❌ Atla", callback_data="analyze_skip"),
                        ]
                    ]
                )

                msg = await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
                # Store for callback
                data_summary = response[:500] if response else ""
                brain.__class__._pending_analyze[str(msg.message_id)] = {
                    "actions": actions,
                    "parsed": parsed,
                    "data": data_summary,
                }
            elif parsed:
                # Parsed OK but no actions — offer brain cycle
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🧠 Brain Cycle Calistir", callback_data="analyze_brain"
                            )
                        ]
                    ]
                )
                await update.message.reply_text(
                    "ℹ️ AI analiz tamamlandi — onerilen aksiyon yok.\n"
                    "Brain Cycle ile otomatik aksiyon almak ister misin?",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            else:
                # Parse failed — raw text only, offer brain cycle as fallback
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🧠 Brain Cycle Calistir", callback_data="analyze_brain"
                            )
                        ]
                    ]
                )
                await update.message.reply_text(
                    "⚠️ JSON parse basarisiz — analiz metin olarak gosterildi.\n"
                    "Brain Cycle aksiyonlari otomatik uygular:",
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): analyze command outer wrapper. AI brain
        # analyze touches LLM + DB + signal eval — heterogeneous surface.
        logger.error(f"Analyze command error: {esc(str(e))}", exc_info=True)
        await update.message.reply_text(
            f"❌ <b>Analiz Hatasi</b>\n\nDetay: {str(e)[:100]}", parse_mode="HTML"
        )


async def analyze_optimize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shortcut: /optimize_ai → runs brain cycle"""
    engine = context.bot_data.get("engine")
    if not engine or not engine.analyst:
        return await update.message.reply_text("🧠 AI Brain aktif degil.", parse_mode="HTML")

    # Loading indicator
    await update.message.reply_text(
        "⏳ <b>Otonom Brain Cycle Baslatiliyor</b>...", parse_mode="HTML"
    )

    try:
        result = await engine.analyst.run_brain_cycle()
        if result:
            # Phase 78-fix: truncate long results & ensure safe HTML
            safe_result = esc(str(result)[:3500])
            await update.message.reply_text(
                f"🧠 <b>Optimizasyon Tamamlandi</b>\n\n{safe_result}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text("⚠️ Brain cycle sonuc uretmedi.", parse_mode="HTML")
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): brain cycle wrapper. LLM + signal +
        # DB layered. Truncated err already T11.6-OK pattern.
        logger.error(f"Optimize AI error: {e}", exc_info=True)
        safe_err = esc(str(e)[:100])
        await update.message.reply_text(
            f"❌ <b>Optimizasyon Hatasi</b>\n\nDetay: {safe_err}", parse_mode="HTML"
        )


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster I — merged from maker_handler.py
# ═══════════════════════════════════════════════════════════════════════
from core.fees_v2 import polymarket_maker_rebate  # noqa: E402


async def maker_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    engine = context.bot_data.get("engine")

    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start kullanın.")

    # Pull executed trades grouped by maker/taker. The schema field is
    # `is_maker` (BOOLEAN); some legacy rows may have NULL — treat as taker.
    rows_24h = await db.conn.execute_fetchall(
        """SELECT
            COALESCE(is_maker, 0) AS m,
            COUNT(*)              AS n,
            COALESCE(SUM(fee_amount), 0) AS fees,
            COALESCE(AVG(fee_amount), 0) AS avg_fee
        FROM executions
        WHERE user_id=? AND created_at >= datetime('now','-1 day')
              AND status IN ('bet_placed','settled')
        GROUP BY COALESCE(is_maker, 0)""",
        (user.id,),
    )
    rows_7d = await db.conn.execute_fetchall(
        """SELECT
            COALESCE(is_maker, 0) AS m,
            COUNT(*)              AS n,
            COALESCE(SUM(fee_amount), 0) AS fees
        FROM executions
        WHERE user_id=? AND created_at >= datetime('now','-7 day')
              AND status IN ('bet_placed','settled')
        GROUP BY COALESCE(is_maker, 0)""",
        (user.id,),
    )
    rows_all = await db.conn.execute_fetchall(
        """SELECT
            COALESCE(is_maker, 0) AS m,
            COUNT(*)              AS n,
            COALESCE(SUM(fee_amount), 0) AS fees
        FROM executions
        WHERE user_id=? AND status IN ('bet_placed','settled')
        GROUP BY COALESCE(is_maker, 0)""",
        (user.id,),
    )

    def split(rows):
        m_n = m_fee = t_n = t_fee = 0
        for r in rows:
            is_maker = bool(r[0])
            n = r[1] or 0
            fees = r[2] or 0
            if is_maker:
                m_n += n
                m_fee += fees
            else:
                t_n += n
                t_fee += fees
        return m_n, m_fee, t_n, t_fee

    m24, mf24, t24, tf24 = split(rows_24h)
    m7, mf7, t7, tf7 = split(rows_7d)
    ma, mfa, ta, tfa = split(rows_all)

    def ratio(m, t):
        total = m + t
        return (m / total * 100) if total else 0

    # Implied maker rebate from realized taker fees (crypto pool, 20%)
    rebate_24h = polymarket_maker_rebate(tf24, "crypto")
    rebate_7d = polymarket_maker_rebate(tf7, "crypto")
    rebate_all = polymarket_maker_rebate(tfa, "crypto")

    # Engine counters (Phase 41b)
    cancel_count = 0
    if engine is not None:
        cancel_count = getattr(engine, "_cancel_count", 0) or 0

    text = (
        "📊 <b>Maker / Taker Stats</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Last 24h</b>\n"
        f"  Maker: <b>{m24}</b> ({ratio(m24, t24):.0f}%)  fees=${mf24:.4f}\n"
        f"  Taker: <b>{t24}</b> ({100 - ratio(m24, t24):.0f}%)  fees=${tf24:.4f}\n"
        f"  Implied rebate: <b>${rebate_24h:.4f}</b>\n"
        "\n<b>Last 7d</b>\n"
        f"  Maker: <b>{m7}</b> ({ratio(m7, t7):.0f}%)  fees=${mf7:.4f}\n"
        f"  Taker: <b>{t7}</b> ({100 - ratio(m7, t7):.0f}%)  fees=${tf7:.4f}\n"
        f"  Implied rebate: <b>${rebate_7d:.4f}</b>\n"
        "\n<b>Lifetime</b>\n"
        f"  Maker: <b>{ma}</b> ({ratio(ma, ta):.0f}%)  fees=${mfa:.4f}\n"
        f"  Taker: <b>{ta}</b> ({100 - ratio(ma, ta):.0f}%)  fees=${tfa:.4f}\n"
        f"  Implied rebate: <b>${rebate_all:.4f}</b>\n"
        "\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"⛔ TIF cancels (since boot): <b>{cancel_count}</b>\n"
        "\n<i>Phase 43c — maker-first execution active.\n"
        "Threshold: settings.MAKER_WIDE_SPREAD\n"
        "Taker fallback: &lt;1m to close OR |sig|&gt;0.60</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster I — merged from micro_handler.py
# ═══════════════════════════════════════════════════════════════════════
ASSETS = ("BTC", "ETH", "SOL", "XRP")


def _fmt_num(v, fmt="{:.4f}"):
    if v is None:
        return "—"
    try:
        return fmt.format(float(v))
    except (ValueError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. float() coercion
        # of None/non-numeric → em-dash placeholder.
        return "—"


async def micro_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    engine = context.bot_data.get("engine")
    if engine is None:
        return await update.message.reply_text("Engine henüz hazır değil.")

    bms = getattr(engine, "binance_multistream", None)
    clo = getattr(engine, "chainlink_oracle", None)

    lines = ["<b>🔬 Microstructure Snapshot (Phase 46)</b>", ""]

    if bms is None:
        lines.append("⚠️ <code>binance_multistream</code> bağlı değil.")
    else:
        try:
            status = bms.get_status() if hasattr(bms, "get_status") else {}
        except (AttributeError, TypeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. get_status
            # may not exist on older bms versions.
            status = {}
        lines.append(
            f"📡 stream: running={status.get('running', '?')} "
            f"uptime={status.get('uptime_s', 0)}s "
            f"reconnects={status.get('reconnects', 0)}"
        )
        lines.append(
            f"   spot_msgs={status.get('spot_msgs', 0)} "
            f"fut_msgs={status.get('fut_msgs', 0)} "
            f"window={status.get('trade_window_s', '?')}s"
        )
        lines.append("")
        for asset in ASSETS:
            try:
                feat = bms.features(asset)
            except (AttributeError, KeyError, ValueError, TypeError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception. bms.
                # features call — missing asset (KeyError) or attribute drift.
                lines.append(f"<b>{esc(asset)}</b> — error: {esc(str(e))}")
                continue
            if not feat:
                lines.append(f"<b>{esc(asset)}</b> — no data yet")
                continue
            mid = feat.get("mid")
            micro = feat.get("microprice")
            spread = feat.get("spread_bps")
            imb = feat.get("ob_imbalance")
            flow = feat.get("trade_flow_60s")
            tcnt = feat.get("trade_count_60s")
            funding = feat.get("funding_rate")
            lines.append(f"<b>{esc(asset)}</b>")
            lines.append(
                f"  mid={_fmt_num(mid, '{:.2f}')} "
                f"μprice={_fmt_num(micro, '{:.2f}')} "
                f"spr={_fmt_num(spread, '{:.1f}')}bps"
            )
            lines.append(
                f"  imb={_fmt_num(imb, '{:+.3f}')} "
                f"flow60s={_fmt_num(flow, '{:+.3f}')} "
                f"trades60s={tcnt if tcnt is not None else '—'}"
            )
            if funding is not None:
                lines.append(f"  funding={_fmt_num(funding * 100.0, '{:+.4f}')}%")
            # Chainlink parity
            if clo is not None and mid:
                try:
                    cl_price = clo.get_price(asset)
                    delta = clo.parity_delta_bps(asset, float(mid))
                    if cl_price is not None:
                        lines.append(
                            f"  ⛓ chainlink={cl_price:.2f} Δ={_fmt_num(delta, '{:.1f}')}bps"
                        )
                except (AttributeError, KeyError, TypeError, ValueError):
                    # T11.8-B (2026-04-24): per-feature render fallback.
                    pass
            lines.append("")

    if clo is None:
        lines.append(
            "ℹ️ Chainlink oracle disabled " "(<code>CHAINLINK_ORACLE_ENABLED=true</code> ile aç)."
        )

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 Cluster I — merged from recorder_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def recorder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show market recorder statistics."""
    engine = context.bot_data.get("engine")
    recorder = getattr(engine, "market_recorder", None) if engine else None

    if not recorder:
        return await update.message.reply_text("⚠️ Market Recorder aktif degil.", parse_mode="HTML")

    stats = await recorder.get_stats()

    if "error" in stats:
        return await update.message.reply_text(
            f"⚠️ Recorder hata: {stats['error']}", parse_mode="HTML"
        )

    enabled = "🟢 AKTIF" if stats.get("enabled") else "🔴 PASIF"

    text = (
        f"📸 <b>Market Data Recorder</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Durum: {enabled}\n\n"
        f"📊 <b>Toplam Veri</b>\n"
        f"  Snapshot: <b>{stats.get('snapshots', 0):,}</b>\n"
        f"  Tick Trade: <b>{stats.get('trades', 0):,}</b>\n"
        f"  Real Trade: <b>{stats.get('real_trades', 0):,}</b> (P1.1)\n"
        f"  Benzersiz Market: <b>{stats.get('unique_markets', 0)}</b>\n"
        f"  Tahm. Boyut: ~{stats.get('est_size_mb', 0):.1f} MB\n\n"
        f"🕐 <b>Zaman Araligi</b>\n"
        f"  Ilk: {stats.get('oldest', 'N/A')}\n"
        f"  Son: {stats.get('newest', 'N/A')}\n\n"
        f"📈 <b>Bu Oturum</b>\n"
        f"  Snapshot: {stats.get('session_snapshots', 0):,}\n"
        f"  Tick: {stats.get('session_trades', 0):,}\n"
        f"  Hata: {stats.get('errors', 0)}\n\n"
        f"<i>Her 2 saniyede L2 orderbook + Binance spot kaydedilir.\n"
        f"Bu veri gercekci backtest icin kullanilir.</i>"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Yenile", callback_data="recorder_refresh")],
        ]
    )

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def recorder_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh recorder stats."""
    q = update.callback_query
    await q.answer("Yenileniyor...")

    engine = context.bot_data.get("engine")
    recorder = getattr(engine, "market_recorder", None) if engine else None

    if not recorder:
        return

    stats = await recorder.get_stats()
    enabled = "🟢 AKTIF" if stats.get("enabled") else "🔴 PASIF"

    text = (
        f"📸 <b>Market Data Recorder</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Durum: {enabled}\n\n"
        f"📊 <b>Toplam Veri</b>\n"
        f"  Snapshot: <b>{stats.get('snapshots', 0):,}</b>\n"
        f"  Tick Trade: <b>{stats.get('trades', 0):,}</b>\n"
        f"  Real Trade: <b>{stats.get('real_trades', 0):,}</b> (P1.1)\n"
        f"  Benzersiz Market: <b>{stats.get('unique_markets', 0)}</b>\n"
        f"  Tahm. Boyut: ~{stats.get('est_size_mb', 0):.1f} MB\n\n"
        f"📈 <b>Bu Oturum</b>\n"
        f"  Snapshot: {stats.get('session_snapshots', 0):,}\n"
        f"  Tick: {stats.get('session_trades', 0):,}\n"
        f"  Hata: {stats.get('errors', 0)}"
    )

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Yenile", callback_data="recorder_refresh")],
        ]
    )

    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): edit_message no-op tolerated.
        pass
