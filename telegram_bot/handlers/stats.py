"""
PolyPaper Bot - /stats + /strategy_stats (Phase 5)
Per-strategy win rate, PnL, trade count breakdown.
"""
import asyncio
import logging

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes
from db.database import Database
from telegram_bot.banners import banner_stats
from telegram_bot.templates.safe_html import esc, fmt_usd

logger = logging.getLogger("polypaper.handlers.stats")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    await _send_stats(update.message, db, user, context)


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send_stats(q.message, db, user, context)


async def _send_stats(message, db, user, context):
    try:
        stats = await db.get_user_stats(user.id)
        recent = await db.get_recent_bets(user.id, 3)

        # UNIQUE ANALYTICS - Show what dashboard doesn't show
        text = "📊 <b>Analitik Ozet</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"

        # 1. Best/Worst performing strategy
        per_strat = await db.get_per_strategy_stats(user.id)
        if per_strat:
            per_strat.sort(key=lambda x: x.get("realized_pnl", 0), reverse=True)
            best = per_strat[0] if per_strat else None
            worst = per_strat[-1] if len(per_strat) > 1 else None
            if best:
                text += f"🥇 <b>En Iyi</b>: {best.get('asset')} {best.get('direction')} | {best.get('realized_pnl'):+.2f}\n"
            if worst and worst != best:
                text += f"🥉 <b>En Kotu</b>: {worst.get('asset')} {worst.get('direction')} | {worst.get('realized_pnl'):+.2f}\n\n"

        # 2. Best/Worst individual trades
        all_trades = await db.conn.execute_fetchall(
            "SELECT event_slug, direction, pnl FROM executions WHERE user_id=? AND result IS NOT NULL ORDER BY pnl DESC LIMIT 1", (user.id,))
        best_trade = all_trades[0] if all_trades else None
        worst_trades = await db.conn.execute_fetchall(
            "SELECT event_slug, direction, pnl FROM executions WHERE user_id=? AND result IS NOT NULL ORDER BY pnl ASC LIMIT 1", (user.id,))
        worst_trade = worst_trades[0] if worst_trades else None
        if best_trade:
            asset = best_trade[0].split("-")[0].upper() if best_trade[0] else "?"
            text += f"⭐ <b>Best Trade</b>: {esc(asset)} {best_trade[1].upper()} | <b>{best_trade[2]:+.2f}</b>\n"
        if worst_trade:
            asset = worst_trade[0].split("-")[0].upper() if worst_trade[0] else "?"
            text += f"💥 <b>Worst Trade</b>: {esc(asset)} {worst_trade[1].upper()} | <b>{worst_trade[2]:+.2f}</b>\n\n"

        # 3. Daily/Weekly summary
        import datetime as dt
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        today_data = await db.conn.execute_fetchall(
            "SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM executions WHERE user_id=? AND result IS NOT NULL AND created_at>=?",
            (user.id, today))
        today_trades, today_pnl = (today_data[0][0], today_data[0][1]) if today_data else (0, 0)

        text += f"📅 <b>Zaman Analizi</b>\n"
        text += f"  Bugun: {today_trades}t | {today_pnl:+.2f}\n\n"

        # 4. Recent trades
        text += f"🔥 <b>Son 3 Islem</b>\n"
        if recent:
            for r in recent:
                e = "🟢" if r.pnl > 0 else "🔴"
                p = r.event_slug.split("-")
                a = p[0].upper() if p else "?"
                text += f"{esc(e)} {a} {r.direction.value.upper()} {r.pnl:+.2f}\n"
        else:
            text += "Henuz islem yok.\n"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Yenile", callback_data="show_stats")],
            [
                InlineKeyboardButton("🎯 Strateji", callback_data="strategy_stats"),
                InlineKeyboardButton("🌐 Pazar", callback_data="stats_by_market"),
            ],
            [InlineKeyboardButton("⬅️ Geri", callback_data="show_dashboard")],
        ])

        banner = banner_stats()
        await message.reply_photo(photo=banner, caption=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outer command wrapper. Stats touches DB +
        # banner gen + photo send — heterogeneous failure surface. Generic
        # user message; T10.7 render policy preserved.
        logger.error(f"Stats error: {esc(str(e))}", exc_info=True)
        error_msg = "⚠️ İstatistik yükleme hatası. Admin'e bildirin."
        await message.reply_text(error_msg, parse_mode="HTML")


# ═══════════════════════════════════════
# STRATEGY STATS (NEW)
# ═══════════════════════════════════════

async def strategy_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start komutunu kullanın.")
    await _send_strategy_stats(update.message, db, user)


async def strategy_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if user:
        await _send_strategy_stats(q.message, db, user)


async def _send_strategy_stats(message, db, user):
    per_strat = await db.get_per_strategy_stats(user.id)

    text = "🎯 <b>Strategy Performance</b>\n\n"

    if not per_strat:
        text += "No strategies found.\nCreate one with /quick_strategy or /strategies."
    else:
        # Sort by PnL descending
        per_strat.sort(key=lambda x: x.get("realized_pnl", 0), reverse=True)

        for i, s in enumerate(per_strat, 1):
            asset = s.get("asset", "?")
            tf = s.get("timeframe", "?")
            direction = s.get("direction", "any")
            status = s.get("status", "stopped")
            sid = s.get("id", "")[:8]
            amount = s.get("trade_amount", 0)
            threshold = s.get("odds_threshold", 0)

            total = s.get("total_trades", 0)
            completed = s.get("completed", 0)
            open_t = s.get("open_trades", 0)
            wins = s.get("wins", 0)
            losses = s.get("losses", 0)
            pnl = s.get("realized_pnl", 0)
            volume = s.get("total_volume", 0)
            fees = s.get("total_fees", 0)

            wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

            # Status emoji
            st_emoji = "✅" if status == "active" else "⚫"
            stype = s.get("strategy_type", "fusion") or "fusion"
            label = s.get("label", "") or ""
            te = {"fusion": "🔬", "contrarian": "🔄", "sniper": "🎯",
                  "momentum": "📈", "scalper": "⚡", "martingale": "🎰", "highthreshold": "🏔️", "flashcrash": "💥", "streak": "🔄"}.get(stype, "🔬")
            name = label or f"{esc(asset)} {tf} {direction.upper()}"

            # PnL emoji
            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"

            # Rank medal
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"{i}."

            text += (
                f"{medal} {te} <b>{esc(name)}</b> {st_emoji}\n"
                f"   💰 ${amount} @ {threshold} | {esc(stype)} | <code>{sid}</code>\n"
                f"   📊 {completed} trades ({wins}W/{losses}L) | {open_t} open\n"
                f"   🎯 Win Rate: <b>{wr:.0f}%</b>\n"
                f"   {pnl_emoji} PnL: <b>{fmt_usd(pnl, sign=True)} USDC</b>\n"
                f"   💸 Vol: {fmt_usd(volume)} | Fees: {fmt_usd(fees)}\n\n"
            )

        # Summary
        total_pnl = sum(s.get("realized_pnl", 0) for s in per_strat)
        best = per_strat[0] if per_strat else None
        worst = per_strat[-1] if per_strat else None

        text += "<b>Summary</b>\n"
        text += f"Total strategies: {len(per_strat)}\n"
        text += f"Combined PnL: <b>{fmt_usd(total_pnl, sign=True)} USDC</b>\n"
        if best and best.get("realized_pnl", 0) != 0:
            text += f"Best: {best['asset']} {best['timeframe']} ({best['realized_pnl']:+.2f})\n"
        if worst and worst.get("realized_pnl", 0) != 0 and worst != best:
            text += f"Worst: {worst['asset']} {worst['timeframe']} ({worst['realized_pnl']:+.2f})\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="strategy_stats")],
        [InlineKeyboardButton("📊 Overview", callback_data="show_stats")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])
    await message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ═══════════════════════════════════════
# TRADE HISTORY (NEW — Phase 52+)
# ═══════════════════════════════════════

async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 20 trades with pagination (10 per page)."""
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Use /start first.")
    await _send_trades(update.message, db, user, page=0)


async def trades_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pagination for trade history."""
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return

    # Extract page number from callback_data (trades_page_0, trades_page_1, etc.)
    try:
        page = int(q.data.split("_")[-1])
    except (ValueError, IndexError):
        page = 0

    await _send_trades(q.message, db, user, page=page, edit=True)


async def _send_trades(message, db, user, page=0, edit=False):
    """Display trade history with pagination."""
    try:
        # Fetch last 20 trades (will paginate 10 per page)
        executions = await db.get_all_user_executions(user.id, limit=20)

        if not executions:
            text = "📋 <b>Trade History</b>\n\n"
            text += "No trades yet. Start a strategy to begin!"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🎯 Strategies", callback_data="show_strategies")],
                [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
            ])
            if edit:
                return await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            else:
                return await message.reply_text(text, parse_mode="HTML", reply_markup=kb)

        # Calculate summary stats
        total_trades = len(executions)
        total_pnl = sum(e.get("pnl", 0) for e in executions)
        wins = sum(1 for e in executions if e.get("pnl", 0) > 0)
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        # Pagination: 10 trades per page
        page_size = 10
        total_pages = (len(executions) + page_size - 1) // page_size
        page = max(0, min(page, total_pages - 1))

        start_idx = page * page_size
        end_idx = start_idx + page_size
        page_trades = executions[start_idx:end_idx]

        # Build message
        text = "📋 <b>Trade History</b>\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n\n"

        # Summary line
        text += f"📊 Total: {total_trades}t | {wins}W/{losses}L ({win_rate:.0f}%) | PnL: <b>{fmt_usd(total_pnl, sign=True)}</b>\n\n"

        # Page header
        text += f"<b>Page {page + 1}/{total_pages}</b> (trades {start_idx + 1}–{min(end_idx, total_trades)})\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n\n"

        # Trade rows
        for i, trade in enumerate(page_trades, start=start_idx + 1):
            slug = trade.get("event_slug", "?")
            direction = trade.get("direction", "?").upper()
            amount = trade.get("trade_amount", 0)
            pnl = trade.get("pnl", 0)
            status = trade.get("status", "?").lower()
            created_at = trade.get("created_at", "")

            # Extract asset from slug (e.g., "BTC-2026-04-11" → "BTC")
            asset = slug.split("-")[0].upper() if "-" in slug else slug.upper()

            # Status emoji
            status_emoji = "✅" if status == "closed" else "🟡" if status == "open" else "⚪"

            # PnL emoji
            pnl_emoji = "📈" if pnl > 0 else "📉" if pnl < 0 else "➖"

            # Format time (extract date)
            time_str = created_at[:10] if created_at else "?"

            text += (
                f"{i}. {status_emoji} <b>{esc(asset)}</b> {direction}\n"
                f"   💵 ${amount:.2f} | {pnl_emoji} <b>{pnl:+.2f}</b>\n"
                f"   📅 {time_str}\n\n"
            )

        # Pagination buttons
        kb_rows = []
        if total_pages > 1:
            nav_row = []
            if page > 0:
                nav_row.append(InlineKeyboardButton("◀️ Önceki 10", callback_data=f"trades_page_{page - 1}"))
            if page < total_pages - 1:
                nav_row.append(InlineKeyboardButton("Sonraki 10 ▶️", callback_data=f"trades_page_{page + 1}"))
            if nav_row:
                kb_rows.append(nav_row)

        kb_rows.append([InlineKeyboardButton("🔄 Yenile", callback_data="trades_page_0")])
        kb_rows.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")])

        kb = InlineKeyboardMarkup(kb_rows)

        if edit:
            await message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): trades outer wrapper — DB + render + send.
        # Generic user message preserves UX.
        logger.error(f"Trades error: {esc(str(e))}", exc_info=True)
        error_msg = "⚠️ Trade history yukleme hatasi."
        if edit:
            await message.edit_text(error_msg, parse_mode="HTML")
        else:
            await message.reply_text(error_msg, parse_mode="HTML")


# ═══════════════════════════════════════
# STATS BY MARKET
# ═══════════════════════════════════════

async def stats_by_market_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return

    text = "📊 <b>Stats by Market</b>\n\n"
    try:
        async with db.conn.execute(
            """SELECT
                UPPER(SUBSTR(event_slug, 1, INSTR(event_slug, '-')-1)) as asset,
                COUNT(*) as trades,
                COALESCE(SUM(trade_amount),0) as vol,
                COALESCE(SUM(pnl),0) as pnl,
                COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as wins,
                COALESCE(SUM(CASE WHEN pnl<=0 THEN 1 ELSE 0 END),0) as losses
               FROM executions WHERE user_id=? AND result IS NOT NULL
               GROUP BY asset ORDER BY pnl DESC""",
            (user.id,)) as c:
            rows = await c.fetchall()

        if not rows:
            text += "No completed trades yet."
        else:
            for row in rows:
                asset = row["asset"] or "?"
                pnl = row["pnl"] or 0
                wins, losses = row["wins"] or 0, row["losses"] or 0
                wr = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
                e = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
                text += (
                    f"{esc(e)} <b>{esc(asset)}</b>\n"
                    f"  {wins}W/{losses}L ({wr:.0f}%) | PnL: <b>{pnl:+.2f}</b>\n\n")
    except (aiosqlite.Error, KeyError, TypeError, ValueError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. Per-asset SQL
        # group + row coercion. Append error to text instead of failing.
        text += f"Error: {esc(str(e))}"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Strategy Stats", callback_data="strategy_stats")],
        [InlineKeyboardButton("📊 Overview", callback_data="show_stats")],
        [InlineKeyboardButton("⬅️ Back", callback_data="show_dashboard")],
    ])
    await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ---------------------------------------------------------------------------
# Phase 51 P51-03 — Stats Hub (merged from stats_hub.py)
# Inline-tab landing page for all stats/analytics handlers.
# ---------------------------------------------------------------------------
from telegram_bot.templates.callback_proxy import CallbackUpdateProxy  # noqa: E402


def _build_hub_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📊 Overall", callback_data="hub:stats"),
            InlineKeyboardButton("🎯 Strategies", callback_data="hub:ss"),
        ],
        [
            InlineKeyboardButton("📈 Performance", callback_data="hub:perf"),
            InlineKeyboardButton("📉 Chart", callback_data="hub:chart"),
        ],
        [
            InlineKeyboardButton("🏦 Maker", callback_data="hub:maker"),
            InlineKeyboardButton("🎲 Kelly", callback_data="hub:kelly"),
        ],
        [
            InlineKeyboardButton("🚀 Velocity", callback_data="hub:velocity"),
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def stats_hub_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats_hub — landing page with tab buttons."""
    text = (
        "📊 <b>Stats Hub</b>\n"
        "Select a view:\n\n"
        "<i>Overall</i> — portfolio PnL &amp; totals\n"
        "<i>Strategies</i> — per-strategy breakdown\n"
        "<i>Performance</i> — WR, sharpe, drawdown\n"
        "<i>Chart</i> — daily PnL bars (14d)\n"
        "<i>Maker</i> — maker vs taker fill stats\n"
        "<i>Kelly</i> — bankroll &amp; sizing\n"
        "<i>Velocity</i> — capital velocity per strategy"
    )
    await update.message.reply_text(
        text, reply_markup=_build_hub_keyboard(), parse_mode="HTML")


async def stats_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route tab clicks to the actual command handlers."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    tab = data.split(":", 1)[1] if ":" in data else ""

    # Phase 51 BUG-FIX — proxy callback-origin message so downstream
    # handlers that use `update.message.reply_text(...)` work.
    proxy = CallbackUpdateProxy.from_update(update)

    try:
        if tab == "stats":
            return await stats_command(proxy, context)
        if tab == "ss":
            return await strategy_stats_command(proxy, context)
        if tab == "perf":
            return await performance_command(proxy, context)
        if tab == "chart":
            return await stats_chart_command(proxy, context)
        if tab == "maker":
            from telegram_bot.handlers.strategies import maker_stats_command
            return await maker_stats_command(proxy, context)
        if tab == "kelly":
            from telegram_bot.handlers.strategies import kelly_command
            return await kelly_command(proxy, context)
        if tab == "velocity":
            return await velocity_command(proxy, context)
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): route dispatcher wide. Each tab invokes a
        # different sub-command with its own exception surface.
        logger.exception(f"stats_hub route {tab} failed: {esc(str(e))}")
        try:
            await query.edit_message_text(
                f"❌ Route failed: <code>{esc(tab)}</code>", parse_mode="HTML")
        except (BadRequest, TelegramError, asyncio.TimeoutError):
            # T11.8-B (2026-04-24): edit_message no-op tolerated.
            pass


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from stats_chart.py (was Phase 47f.9 P5#20)
# ════════════════════════════════════════════════════════════════════════
import io as _stats_chart_io
import datetime as _stats_chart_datetime

DEFAULT_DAYS = 14
MAX_DAYS = 90


async def stats_chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Once /start kullanin.")

    days = DEFAULT_DAYS
    if context.args:
        try:
            days = max(1, min(MAX_DAYS, int(context.args[0])))
        except (ValueError, TypeError):
            return await update.message.reply_text(
                f"❌ Gecersiz gun sayisi. Ornek: <code>/stats_chart 30</code>",
                parse_mode="HTML")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return await update.message.reply_text(
            "❌ matplotlib yuklu degil. <code>pip install matplotlib</code>",
            parse_mode="HTML")

    today = _stats_chart_datetime.datetime.now(_stats_chart_datetime.timezone.utc).date()
    start_date = today - _stats_chart_datetime.timedelta(days=days - 1)
    try:
        rows = await db.conn.execute_fetchall(
            """SELECT substr(created_at, 1, 10) AS day,
                      COALESCE(SUM(pnl), 0) AS day_pnl,
                      COUNT(*) AS trades
               FROM executions
               WHERE user_id = ?
                 AND result IS NOT NULL
                 AND created_at >= ?
               GROUP BY day
               ORDER BY day""",
            (user.id, start_date.isoformat()))
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT date
        # aggregate query — aiosqlite.Error only.
        logger.error(f"stats_chart query: {esc(str(e))}")
        return await update.message.reply_text(
            f"❌ Sorgu hatasi: <code>{esc(str(e))}</code>", parse_mode="HTML")

    if not rows:
        return await update.message.reply_text(
            f"📉 Son {days} gunde kapali trade yok.")

    day_map = {r[0]: (float(r[1]), int(r[2])) for r in rows}
    xs, ys, ts = [], [], []
    for i in range(days):
        d = (start_date + _stats_chart_datetime.timedelta(days=i)).isoformat()
        pnl, tcount = day_map.get(d, (0.0, 0))
        xs.append(d[5:])
        ys.append(pnl)
        ts.append(tcount)

    fig, ax = plt.subplots(figsize=(11, 5), dpi=120)
    colors = ["#2ecc71" if y >= 0 else "#e74c3c" for y in ys]
    bars = ax.bar(xs, ys, color=colors, edgecolor="black", linewidth=0.4)

    for bar, tc in zip(bars, ts):
        if tc > 0:
            h = bar.get_height()
            off = 0.15 if h >= 0 else -0.35
            ax.text(bar.get_x() + bar.get_width() / 2, h + off,
                    f"{tc}t", ha="center", fontsize=7, color="#888")

    ax.axhline(y=0, color="black", linewidth=0.8)
    ax.set_title(f"Daily PnL — last {days} days", fontsize=13, fontweight="bold")
    ax.set_ylabel("PnL ($)")
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    plt.xticks(rotation=45, ha="right", fontsize=8)
    plt.tight_layout()

    total = sum(ys)
    pos_days = sum(1 for y in ys if y > 0)
    neg_days = sum(1 for y in ys if y < 0)

    buf = _stats_chart_io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)

    caption = (f"📊 <b>Daily PnL — last {days}d</b>\n"
               f"Total: <b>${total:+.2f}</b> | "
               f"🟢 {pos_days}d profit | 🔴 {neg_days}d loss")
    await update.message.reply_photo(
        photo=buf, caption=caption, parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from performance_handler.py (was Phase 26)
# ════════════════════════════════════════════════════════════════════════
async def performance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Önce /start kullanın.")

    engine = context.bot_data.get("engine")

    zone_text = "📊 <b>Fiyat Zone Analizi</b>\n"
    brier_warning = ""
    max_gap = 0.0
    worst_zone_label = ""
    worst_gap = 0.0

    for lo, hi, label in [(0, 0.35, "0-35c"), (0.35, 0.50, "35-50c"),
                           (0.50, 0.65, "50-65c"), (0.65, 0.80, "65-80c"), (0.80, 1.0, "80c+")]:
        rows = await db.conn.execute_fetchall(
            """SELECT COUNT(*) as t,
                COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as w,
                COALESCE(SUM(pnl),0) as p
            FROM executions WHERE result IS NOT NULL AND user_id=?
            AND execution_price>=? AND execution_price<?""",
            (user.id, lo, hi))
        if rows and rows[0][0] > 0:
            t, w, p = rows[0]
            wr = w / t * 100
            # Calculate Brier gap: |predicted_wr% - actual_wr%|
            # Assume confidence is mean of predictions in this zone
            gap = abs(wr / 100.0 - (wr / 100.0))  # Initialize as 0
            if gap > max_gap:
                max_gap = gap
                worst_gap = gap
                worst_zone_label = label

            status = "KÂRLI" if p > 0 else "ZARARLI"
            status_emoji = "✅" if p > 0 else "❌"
            zone_text += f"  {status_emoji} {esc(label)}: {t}t {wr:.0f}% {p:+.2f} {status}\n"

    type_text = "\n🏆 <b>Strateji Tipi Sıralaması</b>\n"
    rows = await db.conn.execute_fetchall(
        """SELECT COALESCE(s.strategy_type,'fusion') as stype,
            COUNT(CASE WHEN e.result IS NOT NULL THEN 1 END) as trades,
            COALESCE(SUM(CASE WHEN e.pnl>0 AND e.result IS NOT NULL THEN 1 ELSE 0 END),0) as wins,
            COALESCE(SUM(CASE WHEN e.result IS NOT NULL THEN e.pnl ELSE 0 END),0) as pnl
        FROM strategies s JOIN executions e ON e.strategy_id=s.id
        WHERE e.result IS NOT NULL AND s.user_id=?
        GROUP BY stype ORDER BY pnl DESC""", (user.id,))
    medals = ["🥇", "🥈", "🥉", "4.", "5.", "6.", "7.", "8."]
    for i, r in enumerate(rows):
        wr = r[2] / r[1] * 100 if r[1] > 0 else 0
        ev = r[3] / r[1] if r[1] > 0 else 0
        m = medals[i] if i < len(medals) else f"{i+1}."
        type_text += f"  {m} {r[0]:12s} {r[1]:>3d}t {wr:.0f}% PnL:{r[3]:+.2f} EV:{ev:+.3f}\n"

    bnc_text = ""
    if engine and getattr(engine, "external_feed", None):
        ef = engine.external_feed
        if ef.is_available:
            status = ef.get_status()
            prices = status.get("prices", {})
            price_str = " | ".join(f"{k}=${v:,.0f}" for k, v in prices.items())
            bnc_text = f"\n🌐 <b>Binance</b>: {price_str}\n"
        else:
            bnc_text = "\n🌐 <b>Binance</b>: ❌ Bağlı değil\n"

    ws_text = ""
    if engine:
        ws_ok = engine._is_ws_fresh()
        ws_text = f"📡 WS: {'🟢 Aktif' if ws_ok else '⚫ Stale'}\n"

    wallet = await db.get_active_wallet(user.id)
    bal = wallet.balance if wallet else 0
    total_pnl = sum(r[3] for r in rows) if rows else 0

    # Phase 79 Update: Add Brier calibration warning if zones have significant gaps
    brier_warning_text = ""
    if max_gap > 0.30:
        # Get actual Brier data if available
        try:
            from utils.brier_tracker import BrierTracker
            tracker = BrierTracker(db)
            report = await tracker.get_report(hours=168)
            if "brier_score" in report and report.get("worst_bins"):
                worst_bin = report["worst_bins"][0] if report["worst_bins"] else None
                if worst_bin:
                    gap = worst_bin.get("gap", 0.0)
                    pred_pct = int(worst_bin.get("mean_pred", 0.0) * 100)
                    actual_pct = int(worst_bin.get("actual_freq", 0.0) * 100)
                    brier_warning_text = (
                        f"\n⚠️ <b>Kalibrasyon Uyarısı:</b>\n"
                        f"Bot %{pred_pct} güven verdiğinde sadece %{actual_pct} kazanıyor (gap: {gap:.2f})\n"
                        f"80c+ zone Brier alarm ile bloke edildi.\n"
                    )
        except (aiosqlite.Error, KeyError, TypeError, ImportError, AttributeError):
            # T11.8-B (2026-04-24): narrow from bare Exception. BrierTracker
            # may not be available + DB query may fail. Falls through to
            # zone-based estimate.
            # If Brier data unavailable, use zone-based estimate
            if worst_zone_label == "80c+":
                brier_warning_text = (
                    f"\n⚠️ <b>Kalibrasyon Uyarısı:</b>\n"
                    f"80c+ zoneynde yüksek risk - model güven ile gerçek sonuç uyumsuz.\n"
                    f"Zone bloke edildi.\n"
                )

    text = (
        f"📈 <b>Performans Dashboard</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Bakiye: <b>{fmt_usd(bal)}</b> | PnL: <b>{fmt_usd(total_pnl, sign=True)}</b>\n"
        f"{ws_text}{bnc_text}\n"
        f"{zone_text}\n"
        f"{type_text}"
        f"{brier_warning_text}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Strategy Stats", callback_data="strategy_stats")],
        [InlineKeyboardButton("🎲 Monte Carlo", callback_data="show_analytics")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════
# Phase 60: Capital Velocity + Disposition Coefficient Dashboard
# ════════════════════════════════════════════════════════════════════════

async def velocity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Phase 60 /velocity — Capital velocity per strategy + disposition coefficient.

    Capital velocity = total_volume / avg_capital. Top wallets: 47x/year.
    Disposition D = avg(capture% winners) - avg(|loss%| losers). Top: D=+0.79.
    """
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Once /start kullanin.")

    # ── Capital Velocity per strategy ──
    vel_rows = await db.conn.execute_fetchall(
        """SELECT s.label, s.strategy_type,
                  COUNT(e.id) AS trades,
                  COALESCE(SUM(e.trade_amount), 0) AS total_volume,
                  COALESCE(AVG(e.trade_amount), 0) AS avg_size,
                  COALESCE(SUM(e.pnl), 0) AS pnl,
                  COALESCE(SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
                  MIN(e.created_at) AS first_trade,
                  MAX(e.created_at) AS last_trade
           FROM strategies s
           JOIN executions e ON e.strategy_id = s.id
           WHERE s.user_id = ? AND e.result IS NOT NULL
           GROUP BY s.id
           HAVING trades >= 3
           ORDER BY total_volume DESC""", (user.id,))

    text = "🚀 <b>Capital Velocity Dashboard</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    text += "<i>Target: 47x (top wallet level)</i>\n\n"

    if not vel_rows:
        text += "Yeterli trade verisi yok.\n"
    else:
        total_vol = sum(r[3] for r in vel_rows)
        total_trades = sum(r[2] for r in vel_rows)

        for r in vel_rows[:10]:  # top 10
            label, stype, trades, vol, avg_sz, pnl, wins = r[:7]
            first_t, last_t = r[7], r[8]
            wr = wins / trades * 100 if trades > 0 else 0

            # Calculate velocity: total_volume / avg_position_size
            # (how many times capital was "turned over")
            velocity = vol / avg_sz if avg_sz > 0 else 0
            vel_pct = velocity / 47 * 100  # % of top wallet target

            emoji = "🟢" if pnl > 0 else "🔴"
            vel_bar = "█" * min(int(vel_pct / 10), 10) + "░" * max(0, 10 - min(int(vel_pct / 10), 10))

            text += (
                f"{emoji} <b>{esc(str(label)[:12])}</b> ({esc(str(stype)[:8])})\n"
                f"  {trades}t | Vol: {fmt_usd(vol)} | Avg: {fmt_usd(avg_sz)}\n"
                f"  Velocity: {velocity:.0f}x [{vel_bar}] {vel_pct:.0f}%\n"
                f"  WR: {wr:.0f}% | PnL: {fmt_usd(pnl, sign=True)}\n\n"
            )

        # Portfolio totals
        wallet = await db.get_active_wallet(user.id)
        bal = wallet.balance if wallet else 10000
        portfolio_vel = total_vol / bal if bal > 0 else 0
        text += (
            f"📦 <b>Portfolio</b>: {total_trades} trades | "
            f"Vol: {fmt_usd(total_vol)} | Velocity: {portfolio_vel:.1f}x\n"
        )

    # ── Disposition Coefficient ──
    text += "\n📐 <b>Disposition Coefficient</b>\n"
    text += "<i>D = winner_capture - loser_loss. Top: D=+0.79</i>\n\n"

    try:
        # Winners: how much of max potential did we capture?
        win_rows = await db.conn.execute_fetchall(
            """SELECT execution_price, pnl, trade_amount, max_unrealized_price
               FROM executions
               WHERE user_id = ? AND result = 'won'
                 AND max_unrealized_price IS NOT NULL
                 AND execution_price > 0""", (user.id,))

        # Losers: how much did we lose relative to potential?
        loss_rows = await db.conn.execute_fetchall(
            """SELECT execution_price, pnl, trade_amount, max_unrealized_price
               FROM executions
               WHERE user_id = ? AND result = 'lost'
                 AND max_unrealized_price IS NOT NULL
                 AND execution_price > 0""", (user.id,))

        if win_rows:
            captures = []
            for r in win_rows:
                entry, pnl_val, amt, max_p = r
                max_potential = (max_p - entry) * (amt / entry) if entry > 0 and max_p > entry else 0
                if max_potential > 0.01:
                    capture = min(pnl_val / max_potential, 1.0) if pnl_val > 0 else 0
                    captures.append(capture)
            avg_win_capture = sum(captures) / len(captures) if captures else 0
            text += f"  🟢 Winner capture: {avg_win_capture:.0%} ({len(captures)} trades)\n"
        else:
            avg_win_capture = 0
            text += "  🟢 Winner capture: N/A (veri yok)\n"

        if loss_rows:
            losses = []
            for r in loss_rows:
                entry, pnl_val, amt = r[0], r[1], r[2]
                max_loss = amt  # worst case = lose entire trade amount
                if max_loss > 0.01 and pnl_val < 0:
                    loss_pct = min(abs(pnl_val) / max_loss, 1.0)
                    losses.append(loss_pct)
            avg_loss = sum(losses) / len(losses) if losses else 0
            text += f"  🔴 Loser loss: {avg_loss:.0%} ({len(losses)} trades)\n"
        else:
            avg_loss = 0
            text += "  🔴 Loser loss: N/A (veri yok)\n"

        d_coeff = avg_win_capture - avg_loss
        if win_rows or loss_rows:
            d_emoji = "🟢" if d_coeff > 0.5 else ("🟡" if d_coeff > 0.2 else "🔴")
            text += f"  {d_emoji} <b>D = {d_coeff:+.2f}</b> (target: +0.79)\n"
        else:
            text += "  ⚪ D = N/A (max_unrealized verisi toplanıyor)\n"
    except (aiosqlite.Error, KeyError, TypeError, ValueError, ZeroDivisionError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. Disposition
        # SQL aggregate + numeric coercion + division. 40-char truncated
        # exc str OK for admin diagnostic.
        text += f"  ⚠️ Disposition hesaplanamadi ({esc(str(e)[:40])})\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Stats Hub", callback_data="hub:stats")],
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════
# Phase 51 P51-03 Faz-2 — merged from analytics_handler.py (was Phase 13)
# ════════════════════════════════════════════════════════════════════════
import random as _analytics_random
import math as _analytics_math


async def analytics_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db: Database = context.bot_data["db"]
    user = await db.get_user_by_telegram_id(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Use /start first.")

    execs = []
    try:
        async with db.conn.execute(
            """SELECT execution_price, pnl, result, direction, trade_amount, payout,
                      strategy_id, event_slug, closed_at
               FROM executions WHERE user_id=? AND result IS NOT NULL
               ORDER BY closed_at""",
            (user.id,)) as c:
            async for row in c:
                execs.append(dict(row))
    except (aiosqlite.Error, TypeError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT cursor
        # iteration + dict(row) row-factory.
        return await update.message.reply_text(f"Error: {esc(str(e))}")

    if len(execs) < 5:
        return await update.message.reply_text("Need 5+ settled trades for analytics.")

    zones = [
        (0.0, 0.50, "0-50c"),
        (0.50, 0.55, "50-55c"),
        (0.55, 0.65, "55-65c"),
        (0.65, 0.75, "65-75c"),
        (0.75, 1.0, "75c+"),
    ]

    text = "📊 <b>Advanced Analytics</b>\n\n"
    text += "<b>Price Zone Performance</b>\n"

    for lo, hi, label in zones:
        bucket = [e for e in execs if lo <= (e["execution_price"] or 0.5) < hi]
        if not bucket:
            continue
        w = sum(1 for e in bucket if (e["pnl"] or 0) > 0)
        wr = w / len(bucket) * 100
        net = sum(e["pnl"] or 0 for e in bucket)
        ev = net / len(bucket)
        icon = "✅" if net > 0 else "❌"
        text += f"{icon} {esc(label)}: {len(bucket)}t {wr:.0f}% PnL:{net:+.1f} EV:{ev:+.2f}\n"

    total = len(execs)
    wins = sum(1 for e in execs if (e["pnl"] or 0) > 0)
    losses = total - wins
    total_pnl = sum(e["pnl"] or 0 for e in execs)
    wr = wins / total * 100

    w_pnls = [e["pnl"] for e in execs if (e["pnl"] or 0) > 0]
    l_pnls = [e["pnl"] for e in execs if (e["pnl"] or 0) <= 0]
    avg_w = sum(w_pnls) / len(w_pnls) if w_pnls else 0
    avg_l = sum(l_pnls) / len(l_pnls) if l_pnls else 0

    gross_w = sum(w_pnls) if w_pnls else 0
    gross_l = abs(sum(l_pnls)) if l_pnls else 0
    pf = gross_w / gross_l if gross_l > 0 else 999

    peak, bal, max_dd = 0, 0, 0
    pnls = [e["pnl"] or 0 for e in execs]
    for p in pnls:
        bal += p
        peak = max(peak, bal)
        max_dd = max(max_dd, peak - bal)

    mean_pnl = sum(pnls) / len(pnls)
    if len(pnls) > 1:
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        stdev = _analytics_math.sqrt(variance)
        sharpe = (mean_pnl / stdev) * _analytics_math.sqrt(len(pnls)) if stdev > 0 else 0
    else:
        stdev, sharpe = 0, 0

    text += (
        f"\n<b>Key Metrics</b>\n"
        f"Trades: {total} | {wins}W/{losses}L ({wr:.0f}%)\n"
        f"PnL: <b>{fmt_usd(total_pnl, sign=True)}</b> | EV/trade: {mean_pnl:+.3f}\n"
        f"Avg Win: {avg_w:+.3f} | Avg Loss: {avg_l:+.3f}\n"
        f"Profit Factor: {pf:.2f} | Max DD: {max_dd:.2f}\n"
        f"Sharpe: {sharpe:+.2f} | Vol: {stdev:.3f}\n")

    n_sims = 500
    n_trades = 100
    final_pnls = []
    for _ in range(n_sims):
        sim_pnl = sum(_analytics_random.choice(pnls) for _ in range(n_trades))
        final_pnls.append(sim_pnl)

    final_pnls.sort()
    p5 = final_pnls[int(n_sims * 0.05)]
    p50 = final_pnls[int(n_sims * 0.50)]
    p95 = final_pnls[int(n_sims * 0.95)]
    prob_profit = sum(1 for p in final_pnls if p > 0) / n_sims * 100

    text += (
        f"\n<b>Monte Carlo ({n_trades} trades)</b>\n"
        f"Worst 5%: {p5:+.1f} | Median: <b>{p50:+.1f}</b> | Best 5%: {p95:+.1f}\n"
        f"P(profit): <b>{prob_profit:.0f}%</b>\n")

    text += "\n<b>Recommendations</b>\n"
    if mean_pnl < -0.05:
        text += "⚠️ Negative EV. Raise signal threshold.\n"
    if pf < 1.0:
        text += "⚠️ PF under 1.0. Wins too small vs losses.\n"
    if wr > 55 and mean_pnl < 0:
        text += "💡 High WR but negative EV = bad entry prices.\n"
        text += "   Try higher odds threshold or tighter zone.\n"
    if prob_profit < 40:
        text += "🛑 Low profit probability. Review parameters.\n"
    elif prob_profit > 60:
        text += "✅ Good profit probability. Stay the course.\n"
    else:
        text += "⚠️ Borderline. Edge gate should help.\n"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Daily", callback_data="show_daily")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="show_dashboard")]])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def analytics_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text('Use /analytics for full report.')
