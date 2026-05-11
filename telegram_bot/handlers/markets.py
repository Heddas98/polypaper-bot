"""
PolyPaper Bot - /markets Handler (v4 - FIXED)
Shows only markets with real odds. Marks no-liquidity as "No liquidity".
"""

import asyncio
import logging
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

from data.market_scanner import MarketScanner
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.markets")


def _time_remaining(end_str) -> str:
    try:
        end = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
        secs = int((end - datetime.now(UTC)).total_seconds())
        if secs < 0:
            return "ENDED"
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m {secs % 60}s"
        return f"{secs // 3600}h {(secs % 3600) // 60}m"
    except (ValueError, TypeError, AttributeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. fromisoformat
        # ValueError, str() TypeError, .replace() AttributeError on None.
        return "?"


async def markets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    scanner: MarketScanner = context.bot_data.get("scanner")
    if not scanner:
        await update.message.reply_text("Scanner hazır değil.")
        return

    text = _build_markets_text(scanner)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_markets")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def refresh_markets_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 23: Instant render from cache, then background fresh scan."""
    query = update.callback_query
    await query.answer()
    scanner: MarketScanner = context.bot_data.get("scanner")
    if not scanner:
        return

    # Step 1: INSTANT render from current cache (no wait)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Yenile", callback_data="refresh_markets")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    try:
        text = _build_markets_text(scanner) + "\n⏳ <i>Güncel fiyatlar yükleniyor...</i>"
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Initial render
        # may BadRequest "not modified" if user double-clicks; tolerated.
        pass

    # Step 2: Background scan → re-render with fresh data
    asyncio.create_task(_refresh_and_update(query, scanner, kb))


async def _refresh_and_update(query, scanner, kb):
    """Background: scan then edit message with fresh data."""
    try:
        await asyncio.wait_for(scanner._do_scan(), timeout=8.0)
    except (TimeoutError, asyncio.CancelledError, AttributeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. wait_for
        # TimeoutError is the expected miss case; AttributeError if scanner
        # not yet initialized. Continue with stale snapshot anyway.
        pass
    text = _build_markets_text(scanner)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except (TimeoutError, BadRequest, TelegramError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Same edit_message
        # no-op pattern.
        pass


def _build_markets_text(scanner: MarketScanner) -> str:
    text = "📈 <b>Live Crypto Up/Down Markets</b>\n\n"

    if not scanner.active_markets:
        text += "🔍 No active markets found.\nMarkets refresh every 30s.\n\n"
    else:
        count = 0
        for key in sorted(scanner.active_markets.keys()):
            markets = scanner.active_markets[key]
            if not markets:
                continue
            asset, tf = key.split("_", 1)
            m = markets[0]
            slug = m.get("slug", "")
            odds = scanner.get_current_odds(slug)
            end = m.get("endDate") or m.get("end_date_iso", "")
            tl = _time_remaining(end) if end else "?"

            if odds and odds.get("up_odds") is not None:
                up = f"{odds['up_odds']:.2f}"
                dn = f"{odds['down_odds']:.2f}" if odds.get("down_odds") is not None else "—"
                sp = f"{odds['spread']:.3f}" if odds.get("spread") is not None else "—"
                text += f"🪙 <b>{esc(asset)} {tf}</b> ⏱{tl}\n   ⬆<b>{up}</b> | ⬇<b>{dn}</b> | Spread: {sp}\n\n"
            else:
                text += f"🪙 <b>{esc(asset)} {tf}</b> ⏱{tl}\n   ⚠️ No liquidity yet\n\n"
            count += 1

        text += f"<i>{count} market(s) found</i>\n\n"

    text += f"🔄 <i>{scanner.get_status_summary()}</i>"
    return text


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from candle_handler.py
# ═══════════════════════════════════════════════════════════════════════
from data.polymarket_client import safe_float  # noqa: E402
from db.database import Database  # noqa: E402
from telegram_bot.templates.callback_proxy import CallbackUpdateProxy  # noqa: E402


async def candles_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 9: Show last 5 candles with EMA + odds for each active strategy market."""
    db: Database = context.bot_data["db"]
    scanner = context.bot_data.get("scanner")
    odds_feed = context.bot_data.get("odds_feed")

    if not scanner:
        return await update.message.reply_text("Scanner çalışmıyor.")

    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")

    strats = await db.get_strategies_by_user(user.id)
    active = [s for s in strats if s.status.value == "active"]

    if not active:
        return await update.message.reply_text(
            "Aktif strateji yok. /strategies ile bir tane oluşturun."
        )

    seen = set()
    text = "🕯 <b>Live Candles + EMA</b>\n\n"

    for s in active:
        asset, tf = s.asset.value, s.timeframe.value
        key = f"{asset}_{tf}"
        if key in seen:
            continue
        seen.add(key)

        market = scanner.get_current_market(asset, tf)
        if not market:
            text += f"⚫ <b>{esc(asset)} {tf}</b>: No market\n\n"
            continue

        slug = market.get("slug", "")
        cached = scanner.get_current_odds(slug)
        if not cached:
            text += f"⚫ <b>{esc(asset)} {tf}</b>: No odds\n\n"
            continue

        candles = scanner.get_candles(asset, tf, limit=5) if hasattr(scanner, "get_candles") else []
        ema = None
        if odds_feed and hasattr(odds_feed, "get_ema"):
            ema = odds_feed.get_ema(slug, "up", period=5)

        up = safe_float(cached.get("up_odds")) or 0.5
        text += f"🪙 <b>{esc(asset)} {tf}</b>\n"
        text += f"   ⬆ <b>{up:.3f}</b>"
        if ema is not None:
            diff = up - ema
            arrow = "↗" if diff > 0 else ("↘" if diff < 0 else "→")
            text += f"  EMA5: {ema:.3f} {arrow}\n"
        else:
            text += "\n"

        if candles:
            text += "   Last 5: "
            text += " | ".join(
                f"{safe_float(c.get('close', c.get('up_odds'))) or 0:.2f}" for c in candles[-5:]
            )
            text += "\n"
        text += "\n"

    text += "<i>Refresh for live update</i>"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="candles_refresh")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def candle_refresh_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for candles_refresh button — re-runs candles_command via proxy."""
    q = update.callback_query
    await q.answer()
    proxy = CallbackUpdateProxy.from_update(update)
    await candles_command(proxy, context)


# ═══════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from signals_handler.py
# ═══════════════════════════════════════════════════════════════════════


async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 10: Live signal fusion evaluation for all active strategies."""
    db: Database = context.bot_data["db"]
    engine = context.bot_data.get("engine")
    scanner = context.bot_data.get("scanner")
    odds_feed = context.bot_data.get("odds_feed")

    if not engine or not scanner:
        return await update.message.reply_text("Engine çalışmıyor.")

    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")

    strats = await db.get_strategies_by_user(user.id)
    active = [s for s in strats if s.status.value == "active"]

    if not active:
        return await update.message.reply_text("Aktif strateji yok. Önce birini oluşturun.")

    text = "📡 <b>Live Signal Fusion</b>\n\n"

    for s in active:
        asset, tf = s.asset.value, s.timeframe.value
        market = scanner.get_current_market(asset, tf)
        if not market:
            text += f"⚫ {s.id[:8]} {esc(asset)}/{tf}: No market\n\n"
            continue

        slug = market.get("slug", "")
        cached = scanner.get_current_odds(slug)
        if not cached:
            text += f"⚫ {s.id[:8]} {esc(asset)}/{tf}: No odds\n\n"
            continue

        up = safe_float(cached.get("up_odds")) or 0.5
        down = safe_float(cached.get("down_odds")) or 0.5
        threshold = s.odds_threshold or 0.50
        odds_series = odds_feed.get_odds_series(slug, "up") if odds_feed else []

        # Phase 79: Diagnostic for empty odds_series
        if not odds_series:
            logger.debug(f"[SIGNALS] empty odds_series for {s.id[:8]} {asset}/{tf} slug={slug}")

        from data.polymarket_client import INTERVAL_SECS as _SIG_INTERVAL_SECS

        minutes_remaining = None
        total_minutes = _SIG_INTERVAL_SECS.get(tf, 300) / 60
        end_str = market.get("endDate")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(str(end_str).replace("Z", "+00:00"))
                minutes_remaining = (end_dt - datetime.now(UTC)).total_seconds() / 60
            except (ValueError, TypeError, AttributeError):
                # T11.8-B (2026-04-24): narrow from bare Exception. Same
                # ISO-parse surface as _time_remaining above.
                pass

        sig = engine.signals.evaluate(
            up,
            down,
            threshold,
            s.direction.value,
            odds_series,
            minutes_remaining,
            total_minutes,
        )

        score = sig.composite_score
        if score >= 0.3:
            bar = "🟢🟢🟢"
        elif score >= 0.2:
            bar = "🟢🟢⚪"
        elif score >= 0.1:
            bar = "🟢⚪⚪"
        elif score >= 0:
            bar = "🟡⚪⚪"
        else:
            bar = "🔴⚪⚪"

        trade_icon = "✅" if sig.should_trade else "❌"

        text += (
            f"{trade_icon} <b>{s.id[:8]} {esc(asset)}/{tf} {s.direction.value}</b>\n"
            f"  {bar} Score: <b>{score:+.3f}</b>\n"
            f"  📊 Odds: {sig.signals.get('odds', 0):+.2f} | "
            f"📈 EMA: {sig.signals.get('ema', 0):+.2f}\n"
            f"  🚀 Mom: {sig.signals.get('momentum', 0):+.2f} | "
            f"📉 Vol: {sig.signals.get('volatility', 0):+.2f} | "
            f"⏰ Time: {sig.signals.get('time', 0):+.2f}\n"
            f"  → {sig.direction or 'none'} | thr={threshold}\n\n"
        )

    text += "<i>Score ≥ 0.20 = trade | Refresh for live update</i>"

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="show_signals")],
            [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
        ]
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def signals_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for show_signals — re-runs signals_command via proxy."""
    q = update.callback_query
    await q.answer()
    proxy = CallbackUpdateProxy.from_update(update)
    await signals_command(proxy, context)
