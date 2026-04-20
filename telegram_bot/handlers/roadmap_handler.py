"""
PolyPaper Bot — Phase 70-73 Telegram Command Handlers
=====================================================
Exposes orphaned roadmap modules as Telegram commands:
  /ev_stats    — EV threshold statistics (core.ev_tracker)
  /metrics     — Performance metrics (Sharpe/Sortino/MaxDD) (backtest.metrics)
  /surface     — 2D calibration surface status (calibration.surface_2d)
  /latency     — WebSocket connection stats (Phase 79 S2-06, engine.scanner.ws)

T1.3 Commit 5 (2026-04-20): Ghost modüllere bağlı 6 komut silindi:
  /breed, /vote, /drift_check, /whale, /market_quality, /correlation_check
  Bağımlı arşivler: core.evolutionary, core.majority_voting, core.pnl_verification,
  data_feeds.whale_tracker, data_feeds.event_waves, core.strategy_correlation
"""
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.roadmap")

ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))


def _is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ADMIN_ID


# ═══════════════════════════════════════════════════
# /ev_stats — EV Threshold Statistics + Edge Realization Ratio (Phase 75+)
# ═══════════════════════════════════════════════════
async def ev_stats_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Show EV statistics: threshold filtering + edge realization analysis.

    Edge Realization Ratio = realized_pnl / expected_ev
    ✅ 0.9-1.0 = excellent model
    ✅ 0.75-0.9 = good model
    ⚠️ 0.6-0.75 = acceptable (monitor)
    ❌ <0.6 = bad (overfitting/broken)
    """
    if not _is_admin(update):
        return

    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    try:
        from core.ev_tracker import EVTracker
        ev = EVTracker(db)

        # Get summary for all active strategies
        summary = await ev.get_all_strategies_ev_summary()

        lines = ["<b>📊 EV & Edge Realization Stats</b>\n\n"]

        if not summary:
            lines.append("Henüz trade yok.\n")
        else:
            lines.append("<b>Top Strategies by Edge Realization:</b>\n")
            for label, stats in summary[:10]:
                trades = stats['trades']
                avg_pnl = stats['avg_pnl']
                edge_real = stats['edge_real']
                wr = stats['wr']

                # Color emoji based on quality
                if edge_real >= 0.9:
                    emoji = "✅✅"
                    quality = "excellent"
                elif edge_real >= 0.75:
                    emoji = "✅"
                    quality = "good"
                elif edge_real >= 0.6:
                    emoji = "⚠️"
                    quality = "acceptable"
                else:
                    emoji = "❌"
                    quality = "bad"

                lines.append(
                    f"{emoji} <code>{label:25s}</code> "
                    f"ratio={edge_real:5.2f} | "
                    f"PnL={avg_pnl:+6.2f} | WR={wr:5.1f}% | n={trades:3d}\n"
                )

        text = "".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n..."

        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ EV stats hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# /metrics [strategy_id] — Performance Metrics
# ═══════════════════════════════════════════════════
async def metrics_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Sharpe, Sortino, MaxDD, Profit Factor etc."""
    if not _is_admin(update):
        return
    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    args = ctx.args
    strategy_filter = args[0] if args else None

    try:
        query = "SELECT pnl FROM executions WHERE result IS NOT NULL"
        params = ()
        if strategy_filter:
            query += " AND strategy_id = ?"
            params = (strategy_filter,)
        query += " ORDER BY closed_at ASC"

        rows = await db.conn.execute_fetchall(query, params)
        if not rows or len(rows) < 5:
            await update.message.reply_text(
                "📊 Yeterli veri yok (min 5 trade gerekli).", parse_mode="HTML")
            return

        pnl_series = [float(r[0]) for r in rows if r[0] is not None]

        from backtest.metrics import compute_metrics, format_metrics_telegram
        m = compute_metrics(pnl_series)
        title = f"📊 <b>Performance Metrics</b>"
        if strategy_filter:
            title += f" — <code>{esc(strategy_filter)}</code>"
        text = title + "\n\n" + format_metrics_telegram(m)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Metrics hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# T1.3 Commit 5 (2026-04-20): /breed, /vote, /drift_check, /whale silindi
# Bağımlı arşivler: core.evolutionary, core.majority_voting, core.pnl_verification,
# data_feeds.whale_tracker — hepsi _archive/sprint4_modules/ altında. Komutlar
# ghost modüller üzerinden except Exception yakalıyordu, sessiz broken durumdaydı.
# ═══════════════════════════════════════════════════


# ═══════════════════════════════════════════════════
# /surface — 2D Calibration Surface Status
# ═══════════════════════════════════════════════════
async def surface_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show 2D calibration surface C(K,τ) status."""
    if not _is_admin(update):
        return
    engine = ctx.bot_data.get("engine")
    surface = getattr(engine, "_calib_surface_2d", None) if engine else None

    if not surface:
        await update.message.reply_text(
            "⚠️ 2D Surface aktif değil (SURFACE_2D_ENABLED=false veya veri yok).",
            parse_mode="HTML")
        return

    try:
        from calibration.surface_2d import format_surface_telegram
        text = format_surface_telegram(surface)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Surface hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# /latency — Cross-Market Latency Stats
# ═══════════════════════════════════════════════════
async def latency_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Phase 79 S2-06: Show WebSocket connection stats (replacement for latency data)."""
    if not _is_admin(update):
        return

    try:
        engine = ctx.bot_data.get("engine")
        lines = ["⏱ <b>WebSocket Connection Status</b>"]

        if engine and hasattr(engine, "scanner") and engine.scanner:
            scanner = engine.scanner
            ws = getattr(scanner, "ws", None)

            if ws:
                # Extract WS stats
                connected = "🟢 Connected" if ws.is_connected else "⚫ Disconnected"
                lines.append(f"Status: {connected}")

                msg_count = getattr(ws, "_msg_count", 0)
                reconnects = getattr(ws, "_reconnects", 0)
                errors = getattr(ws, "_errors", 0)
                last_msg_ts = getattr(ws, "_last_msg_ts", 0)

                lines.append(f"Messages received: {msg_count:,}")
                lines.append(f"Reconnections: {reconnects}")
                lines.append(f"Errors: {errors}")

                if last_msg_ts > 0:
                    import time
                    age_sec = time.time() - last_msg_ts
                    lines.append(f"Last message: {age_sec:.1f}s ago")
                else:
                    lines.append("Last message: never")

                subscribed = getattr(ws, "_subscribed", set())
                if subscribed:
                    lines.append(f"Subscribed tokens: {len(subscribed)}")
            else:
                lines.append("WebSocket: not initialized")
        else:
            lines.append("Engine or scanner: not available")

        text = "\n".join(lines)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ WS stats hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# T1.3 Commit 5 (2026-04-20): /market_quality + /correlation_check silindi
# Bağımlı arşivler: data_feeds.event_waves, core.strategy_correlation —
# _archive/sprint4_modules/ altında.
# ═══════════════════════════════════════════════════
