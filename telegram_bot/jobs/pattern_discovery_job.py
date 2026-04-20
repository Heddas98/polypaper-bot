"""
Phase 59: Weekly Pattern Discovery Job
Analyzes last 7 days of trades to find recurring patterns.

Runs weekly (or on-demand via /patterns command).
Stores discovered patterns in `discovered_patterns` table.
AI Brain reads these patterns to improve future decisions.

Patterns detected:
  - Hour-of-day WR skew (e.g., "BTC UP at UTC 22:00 has 70% WR")
  - Asset+direction combos (e.g., "ETH DOWN trades are 62% WR")
  - Zone performance shifts (e.g., "35-50c zone WR dropped from 65% to 55%")
  - Strategy-specific streaks (e.g., "fusion strategy on BTC peaks Monday-Wednesday")
"""
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("polypaper.jobs.pattern_discovery")


async def _ensure_table(db):
    """Create discovered_patterns table if it doesn't exist."""
    try:
        await db.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovered_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                description TEXT NOT NULL,
                asset TEXT,
                metric_value REAL,
                sample_size INTEGER,
                confidence TEXT,
                discovered_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                expires_at TEXT,
                is_active INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_dp_type ON discovered_patterns(pattern_type);
        """)
        await db.conn.commit()
    except Exception:
        pass


async def run_pattern_discovery(db, days: int = 7) -> list[str]:
    """Analyze recent trades and discover recurring patterns.

    Returns list of discovered pattern descriptions.
    """
    await _ensure_table(db)
    findings = []

    try:
        # ── Pattern 1: Hour-of-day WR by asset ──
        hour_data = await db.conn.execute_fetchall("""
            SELECT
                CAST(strftime('%%H', e.created_at) AS INTEGER) as hour,
                CASE WHEN e.event_slug LIKE '%%btc%%' THEN 'BTC'
                     WHEN e.event_slug LIKE '%%eth%%' THEN 'ETH'
                     WHEN e.event_slug LIKE '%%sol%%' THEN 'SOL'
                     WHEN e.event_slug LIKE '%%xrp%%' THEN 'XRP'
                     ELSE 'OTHER' END as asset,
                e.direction,
                COUNT(*) as trades,
                SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(e.pnl) as total_pnl
            FROM executions e
            WHERE e.result IS NOT NULL
                AND e.created_at >= datetime('now', ? || ' days')
            GROUP BY hour, asset, e.direction
            HAVING trades >= 5
            ORDER BY total_pnl DESC
        """, (f"-{days}",))

        for row in (hour_data or []):
            hour, asset, direction, trades, wins, pnl = row
            wr = wins / trades * 100 if trades > 0 else 0
            if wr >= 65 and trades >= 5:
                desc = f"{asset} {direction.upper()} @ UTC {hour:02d}:00 → {wr:.0f}% WR ({trades}t, PnL:{pnl:+.2f})"
                findings.append(("hour_edge", desc, asset, wr, trades))
            elif wr <= 35 and trades >= 5:
                desc = f"⚠️ {asset} {direction.upper()} @ UTC {hour:02d}:00 → {wr:.0f}% WR ({trades}t) — AVOID"
                findings.append(("hour_avoid", desc, asset, wr, trades))

        # ── Pattern 2: Asset+direction performance ──
        asset_data = await db.conn.execute_fetchall("""
            SELECT
                CASE WHEN e.event_slug LIKE '%%btc%%' THEN 'BTC'
                     WHEN e.event_slug LIKE '%%eth%%' THEN 'ETH'
                     WHEN e.event_slug LIKE '%%sol%%' THEN 'SOL'
                     WHEN e.event_slug LIKE '%%xrp%%' THEN 'XRP'
                     ELSE 'OTHER' END as asset,
                e.direction,
                COUNT(*) as trades,
                SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(e.pnl) as total_pnl,
                AVG(e.pnl) as avg_pnl
            FROM executions e
            WHERE e.result IS NOT NULL
                AND e.created_at >= datetime('now', ? || ' days')
            GROUP BY asset, e.direction
            HAVING trades >= 10
            ORDER BY total_pnl DESC
        """, (f"-{days}",))

        for row in (asset_data or []):
            asset, direction, trades, wins, pnl, avg = row
            wr = wins / trades * 100 if trades > 0 else 0
            if wr >= 60:
                desc = f"{asset} {direction.upper()} → {wr:.0f}% WR, {trades}t, PnL:{pnl:+.2f} (avg:{avg:+.3f})"
                findings.append(("asset_edge", desc, asset, wr, trades))
            elif wr <= 45 and trades >= 10:
                desc = f"⚠️ {asset} {direction.upper()} → {wr:.0f}% WR, {trades}t, PnL:{pnl:+.2f} — KAYBEDIYOR"
                findings.append(("asset_avoid", desc, asset, wr, trades))

        # ── Pattern 3: Zone performance trends ──
        zone_data = await db.conn.execute_fetchall("""
            SELECT
                CASE
                    WHEN e.execution_price < 0.35 THEN '0-35c'
                    WHEN e.execution_price < 0.50 THEN '35-50c'
                    WHEN e.execution_price < 0.65 THEN '50-65c'
                    WHEN e.execution_price < 0.80 THEN '65-80c'
                    ELSE '80c+'
                END as zone,
                COUNT(*) as trades,
                SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(e.pnl) as total_pnl
            FROM executions e
            WHERE e.result IS NOT NULL
                AND e.created_at >= datetime('now', ? || ' days')
            GROUP BY zone
            HAVING trades >= 5
        """, (f"-{days}",))

        for row in (zone_data or []):
            zone, trades, wins, pnl = row
            wr = wins / trades * 100 if trades > 0 else 0
            status = "✅" if pnl > 0 else "⚠️"
            desc = f"{status} Zone {zone}: {wr:.0f}% WR, {trades}t, PnL:{pnl:+.2f}"
            findings.append(("zone_perf", desc, None, wr, trades))

        # ── Pattern 4: Strategy-type performance ──
        strat_data = await db.conn.execute_fetchall("""
            SELECT
                s.strategy_type,
                COUNT(*) as trades,
                SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(e.pnl) as total_pnl
            FROM executions e
            JOIN strategies s ON s.id = e.strategy_id
            WHERE e.result IS NOT NULL
                AND e.created_at >= datetime('now', ? || ' days')
            GROUP BY s.strategy_type
            HAVING trades >= 5
        """, (f"-{days}",))

        for row in (strat_data or []):
            stype, trades, wins, pnl = row
            wr = wins / trades * 100 if trades > 0 else 0
            desc = f"Strategy [{stype}]: {wr:.0f}% WR, {trades}t, PnL:{pnl:+.2f}"
            findings.append(("strategy_perf", desc, None, wr, trades))

        # ── Save findings to DB ──
        # Expire old patterns first
        await db.conn.execute(
            "UPDATE discovered_patterns SET is_active=0 WHERE discovered_at < datetime('now', '-7 days')")

        for ptype, desc, asset, metric, sample in findings:
            confidence = "high" if sample >= 20 else ("medium" if sample >= 10 else "low")
            await db.conn.execute(
                """INSERT INTO discovered_patterns
                (pattern_type, description, asset, metric_value, sample_size, confidence,
                 expires_at)
                VALUES (?,?,?,?,?,?, datetime('now', '+7 days'))""",
                (ptype, desc, asset, metric, sample, confidence))

        await db.conn.commit()
        logger.info(f"📊 Pattern discovery: {len(findings)} patterns found from {days} days")

    except Exception as e:
        logger.error(f"Pattern discovery error: {e}")

    return [f[1] for f in findings]


async def pattern_discovery_callback(context):
    """JobQueue callback for weekly pattern discovery."""
    try:
        app = context.application
        db = app.bot_data.get("db")
        settings = app.bot_data.get("settings")
        if not db:
            return

        findings = await run_pattern_discovery(db, days=7)
        if not findings:
            return

        admin_id = getattr(settings, "ADMIN_TELEGRAM_ID", None)
        if not admin_id:
            return

        text = "📊 <b>Haftalık Pattern Discovery</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        for f in findings[:15]:  # Max 15 patterns
            text += f"• {f}\n"
        text += f"\n<i>Toplam {len(findings)} pattern, son 7 gün</i>"

        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML")
        except Exception:
            await context.bot.send_message(chat_id=admin_id, text=text)
    except Exception as e:
        logger.error(f"Pattern discovery job error: {e}")
