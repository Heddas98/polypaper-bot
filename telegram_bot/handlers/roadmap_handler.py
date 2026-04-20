"""
PolyPaper Bot — Phase 70-73 Telegram Command Handlers
=====================================================
Exposes orphaned roadmap modules as Telegram commands:
  /ev_stats    — EV threshold statistics
  /metrics     — Performance metrics (Sharpe/Sortino/MaxDD)
  /breed       — Run evolutionary parameter breeding
  /vote        — Majority voting consensus status
  /drift_check — Paper vs live PnL drift check
  /whale       — Whale flow signal analysis
  /surface     — 2D calibration surface status
  /latency     — Cross-market latency stats
  /spread_info — Spread analysis for active markets
  /market_quality — EventWaves market quality score
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
# /breed — Evolutionary Parameter Breeding
# ═══════════════════════════════════════════════════
async def breed_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Run evolutionary breeding on top strategies."""
    if not _is_admin(update):
        return
    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    await update.message.reply_text("🧬 Evolutionary breeding başlatılıyor...", parse_mode="HTML")
    try:
        from core.evolutionary import EvolutionaryBreeder
        breeder = EvolutionaryBreeder(db)
        result = await breeder.breed()
        text = breeder.format_telegram(result)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Breed hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# /vote — Majority Voting Consensus
# ═══════════════════════════════════════════════════
async def vote_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show current strategy consensus (majority voting)."""
    if not _is_admin(update):
        return
    db = ctx.bot_data.get("db")
    engine = ctx.bot_data.get("engine")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    try:
        # Collect votes from active strategies' last signals
        rows = await db.conn.execute_fetchall(
            """SELECT s.id, s.strategy_type,
                      (SELECT COUNT(*) FROM executions e WHERE e.strategy_id=s.id AND e.result IS NOT NULL) as trades,
                      (SELECT COALESCE(SUM(CASE WHEN e2.pnl>0 THEN 1.0 ELSE 0.0 END) / NULLIF(COUNT(*),0), 0.5)
                       FROM executions e2 WHERE e2.strategy_id=s.id AND e2.result IS NOT NULL) as wr,
                      s.direction
               FROM strategies s WHERE s.status='active'""")

        if not rows or len(rows) < 2:
            await update.message.reply_text("📊 Aktif strateji yeterli değil (min 2).", parse_mode="HTML")
            return

        from core.majority_voting import Vote, compute_majority_vote, format_voting_telegram
        votes = []
        for r in rows:
            sid, stype, trades, wr, direction = r
            if trades and trades >= 5:
                # Phase 78-fix: Vote dataclass has no 'trades' field; add strategy_type
                votes.append(Vote(
                    strategy_id=str(sid),
                    strategy_type=str(stype) if stype else "",
                    direction=str(direction) if direction else "UP",
                    confidence=float(wr) if wr else 0.5,
                    win_rate=float(wr) if wr else 0.5,
                ))

        if len(votes) < 2:
            await update.message.reply_text("📊 Yeterli oy yok (min 2 strateji, 5+ trade).", parse_mode="HTML")
            return

        result = compute_majority_vote(votes)
        text = format_voting_telegram(result)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Vote hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# /drift_check — Paper vs Live PnL Drift
# ═══════════════════════════════════════════════════
async def drift_check_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Compare paper vs shadow live PnL drift."""
    if not _is_admin(update):
        return
    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    try:
        from core.pnl_verification import PnLVerifier
        verifier = PnLVerifier(db)
        result = await verifier.verify()
        text = verifier.format_telegram(result)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Drift check hatası: {esc(str(e))}", parse_mode="HTML")


# ═══════════════════════════════════════════════════
# /whale — Whale Flow Signal
# ═══════════════════════════════════════════════════
async def whale_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show whale flow analysis from recent large trades."""
    if not _is_admin(update):
        return
    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    try:
        from data_feeds.whale_tracker import WhaleTracker, WhaleTrade
        tracker = WhaleTracker(db)

        # Phase 78-fix: ensure whale_trades table exists before querying
        try:
            await db.conn.execute(
                """CREATE TABLE IF NOT EXISTS whale_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL,
                    direction TEXT DEFAULT '',
                    notional_usd REAL DEFAULT 0,
                    price REAL DEFAULT 0,
                    ts_ms INTEGER DEFAULT 0,
                    source TEXT DEFAULT ''
                )""")
        except Exception:
            pass

        rows = await db.conn.execute_fetchall(
            """SELECT slug, direction, COALESCE(notional_usd, 0), price, COALESCE(ts_ms, 0)
               FROM whale_trades
               WHERE ts_ms > ?
               ORDER BY notional_usd DESC LIMIT 30""",
            (int(__import__('time').time() * 1000 - 3600000),))

        if not rows:
            await update.message.reply_text(
                "🐋 <b>Whale Flow</b>\n\nSon 1 saatte büyük trade yok.",
                parse_mode="HTML")
            return

        whale_trades = [
            WhaleTrade(slug=r[0], direction=r[1] or "",
                       amount_usd=float(r[2] or 0), price=float(r[3] or 0),
                       timestamp=float(r[4] or 0), source="internal")
            for r in rows
        ]

        signal = tracker.compute_signal(whale_trades, orderbook=None)
        text = tracker.format_telegram(signal)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Whale hatası: {esc(str(e))}", parse_mode="HTML")


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
# /market_quality [slug] — EventWaves Market Quality
# ═══════════════════════════════════════════════════
async def market_quality_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Assess market quality using EventWaves scoring."""
    if not _is_admin(update):
        return

    args = ctx.args
    slug = args[0] if args else None

    try:
        from data_feeds.event_waves import assess_market_quality, format_quality_telegram

        db = ctx.bot_data.get("db")
        if slug and db:
            row = await db.conn.execute_fetchall(
                """SELECT COUNT(*) as trades,
                          SUM(trade_amount) as volume
                   FROM executions WHERE slug=? AND created_at > datetime('now', '-24 hours')""",
                (slug,))
            vol_24h = float(row[0][1]) if row and row[0][1] else 0
            trades_24h = int(row[0][0]) if row and row[0][0] else 0
        else:
            slug = "overall"
            row = await db.conn.execute_fetchall(
                """SELECT COUNT(*) as trades,
                          SUM(trade_amount) as volume
                   FROM executions WHERE created_at > datetime('now', '-24 hours')""")
            vol_24h = float(row[0][1]) if row and row[0][1] else 0
            trades_24h = int(row[0][0]) if row and row[0][0] else 0

        quality = assess_market_quality(
            slug=slug,
            volume_24h=vol_24h,
            spread=0.02,
            n_traders=0,
            minutes_remaining=60.0 * 24,
            total_minutes=60.0 * 24,
            up_odds=0.5,
        )
        text = format_quality_telegram(quality)
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ Market quality hatası: {esc(str(e))}", parse_mode="HTML")


# /correlation_check — Strategy Orthogonality Analysis (Phase 75+)
# ═══════════════════════════════════════════════════
async def correlation_check_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Identify redundant vs independent strategies via correlation analysis.

    Correlation > 0.7 = false diversification (same strategy, different params)
    Correlation < 0.3 = truly independent strategies
    """
    if not _is_admin(update):
        return

    db = ctx.bot_data.get("db")
    if not db:
        await update.message.reply_text("⚠️ DB bağlantısı yok.", parse_mode="HTML")
        return

    try:
        from core.strategy_correlation import StrategyCorrelationAnalyzer
        analyzer = StrategyCorrelationAnalyzer(db)

        # Get correlations
        correlations = await analyzer.get_all_correlations()
        redundant_groups = await analyzer.identify_redundant_groups(threshold=0.7)
        orthogonal = await analyzer.get_orthogonal_strategies(threshold=0.3)

        lines = ["<b>🔗 Strategy Correlation Analysis</b>\n\n"]

        # Redundant groups
        if redundant_groups:
            lines.append("<b>⚠️ Redundant Strategy Groups (corr > 0.7):</b>\n")
            for group_key, members in redundant_groups.items():
                escaped_group = esc(group_key)
                escaped_members = ", ".join(esc(m) for m in members)
                lines.append(f"  {escaped_group}: {escaped_members}\n")
            lines.append("\n")

        # Orthogonal strategies
        if orthogonal:
            lines.append(f"<b>✅ Independent Strategies (corr < 0.3):</b>\n")
            for label in orthogonal:
                lines.append(f"  • {esc(label)}\n")
            lines.append("\n")

        # Top correlations (high redundancy)
        if correlations:
            lines.append("<b>🔴 Highest Correlations (redundancy risk):</b>\n")
            for l1, l2, corr in correlations[:5]:
                lines.append(f"  {esc(l1):20s} <-> {esc(l2):20s}  corr={corr:+.2f}\n")

        text = "".join(lines)
        if len(text) > 4000:
            text = text[:3990] + "\n..."

        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ Correlation hatası: {esc(str(e))}", parse_mode="HTML")
