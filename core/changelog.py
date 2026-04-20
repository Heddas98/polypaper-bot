"""
Phase 79b: Strategy Changelog — tracks ALL changes to strategies.

Every TUNE, SCALE, DELETE, CREATE, RESTART, ADAPTIVE change is logged
so AI Brain can see full history and avoid repeating mistakes.

Sources:
  - ai_brain: AI Brain TUNE/SCALE/DELETE/CREATE/RESTART actions
  - adaptive_optimizer: Rolling WR kill, adaptive threshold, auto-resume
  - user_telegram: User commands (/stop, /restart, strategy builder)
  - hyperopt: HyperOpt parameter application

Usage:
    from core.changelog import log_change
    await log_change(db, strategy_id, "TUNE", "ai_brain",
                     old={"odds_threshold": 0.55}, new={"odds_threshold": 0.50},
                     reason="WR < 55%", wr=42.0, pnl=-1.5, trades=30)
"""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("polypaper.core.changelog")


async def log_change(db, strategy_id: str, action: str, source: str,
                     old: dict = None, new: dict = None,
                     reason: str = "", label: str = "",
                     wr: float = None, pnl: float = None, trades: int = None):
    """Log a strategy change to the changelog table.

    Args:
        db: Database instance (has db.conn)
        strategy_id: Strategy UUID
        action: TUNE, SCALE, DELETE, CREATE, RESTART, ADAPTIVE_THRESHOLD,
                ROLLING_WR_KILL, ADAPTIVE_DEAD, LIFECYCLE_ADJUST
        source: ai_brain, adaptive_optimizer, user_telegram, hyperopt
        old: Previous values as dict, e.g. {"odds_threshold": 0.55}
        new: New values as dict, e.g. {"odds_threshold": 0.50}
        reason: Human-readable reason
        label: Strategy label (optional, fetched from DB if empty)
        wr: Win rate at time of change
        pnl: PnL at time of change
        trades: Trade count at time of change
    """
    try:
        # Get label from DB if not provided
        if not label and strategy_id:
            try:
                rows = await db.conn.execute_fetchall(
                    "SELECT label FROM strategies WHERE id LIKE ?",
                    (f"{strategy_id}%",))
                if rows:
                    label = rows[0][0] or strategy_id[:8]
            except Exception:
                label = strategy_id[:8] if strategy_id else "?"

        now = datetime.now(timezone.utc).isoformat()
        await db.conn.execute(
            """INSERT INTO strategy_changelog
               (strategy_id, strategy_label, action, source,
                old_value, new_value, reason,
                wr_at_time, pnl_at_time, trades_at_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_id[:36] if strategy_id else "",
             label or "?",
             action,
             source,
             json.dumps(old, default=str) if old else None,
             json.dumps(new, default=str) if new else None,
             reason or "",
             wr, pnl, trades,
             now))
        await db.conn.commit()
    except Exception as e:
        # Table might not exist yet (pre-migration) — silently skip
        logger.debug(f"changelog write: {e}")


async def get_changelog_for_ai(db, max_active_per_strat: int = 0,
                                max_stopped_summary: int = 20) -> list[str]:
    """Get formatted changelog for AI Brain _gather_data().

    Active strategies: ALL changelog entries (no limit per strategy)
    Stopped strategies: 1-line summary per strategy
    30+ day old stopped: excluded entirely

    Returns list of formatted strings ready to append to prompt.
    """
    lines = []
    try:
        # Active strategies — full history
        active_rows = await db.conn.execute_fetchall(
            """SELECT c.strategy_label, c.action, c.source,
                      c.old_value, c.new_value, c.reason,
                      c.wr_at_time, c.pnl_at_time, c.trades_at_time,
                      c.created_at, s.status
               FROM strategy_changelog c
               LEFT JOIN strategies s ON c.strategy_id = s.id
               WHERE s.status = 'active'
               ORDER BY c.strategy_label, c.created_at""")

        if active_rows:
            lines.append("\n═══ AKTIF STRATEJI DEGISIKLIK GECMISI (TAM) ═══")
            current_label = ""
            for r in active_rows:
                label = r[0] or "?"
                if label != current_label:
                    current_label = label
                    lines.append(f"\n  [{label}]:")
                action = r[1] or "?"
                source = r[2] or "?"
                old_v = r[3] or ""
                new_v = r[4] or ""
                reason = r[5] or ""
                wr = r[6]
                pnl = r[7]
                ts = (r[9] or "")[:16]
                wr_str = f"WR={wr:.0f}%" if wr is not None else ""
                pnl_str = f"PnL={pnl:+.2f}" if pnl is not None else ""
                context = f" ({wr_str} {pnl_str})" if wr_str or pnl_str else ""

                # Format old→new compactly
                change_str = ""
                if old_v and new_v:
                    try:
                        old_d = json.loads(old_v) if isinstance(old_v, str) else {}
                        new_d = json.loads(new_v) if isinstance(new_v, str) else {}
                        changes = []
                        for k in set(list(old_d.keys()) + list(new_d.keys())):
                            if old_d.get(k) != new_d.get(k):
                                changes.append(f"{k}:{old_d.get(k)}→{new_d.get(k)}")
                        if changes:
                            change_str = " " + ", ".join(changes)
                    except Exception:
                        pass

                lines.append(
                    f"    {ts} {action} [{source}]{change_str}"
                    f"{context}"
                    f"{' — ' + reason if reason else ''}")

        # Stopped strategies — 1-line summary per strategy
        stopped_summary = await db.conn.execute_fetchall(
            """SELECT c.strategy_label, c.strategy_id,
                      COUNT(*) as changes,
                      GROUP_CONCAT(c.action, '→') as action_chain,
                      MAX(c.wr_at_time) as best_wr,
                      MIN(c.pnl_at_time) as worst_pnl,
                      MAX(c.trades_at_time) as max_trades,
                      MAX(c.created_at) as last_change
               FROM strategy_changelog c
               LEFT JOIN strategies s ON c.strategy_id = s.id
               WHERE s.status = 'stopped'
                 AND c.created_at > datetime('now', '-30 days')
               GROUP BY c.strategy_id
               ORDER BY last_change DESC
               LIMIT ?""", (max_stopped_summary,))

        if stopped_summary:
            lines.append("\n═══ DURMUS STRATEJI OZETI (son 30 gun) ═══")
            for s in stopped_summary:
                label = s[0] or "?"
                changes = s[2] or 0
                chain = s[3] or "?"
                best_wr = s[4]
                worst_pnl = s[5]
                max_trades = s[6]
                wr_str = f"max_WR={best_wr:.0f}%" if best_wr is not None else ""
                pnl_str = f"min_PnL={worst_pnl:+.2f}" if worst_pnl is not None else ""
                lines.append(
                    f"  {label}: {changes} degisiklik, {max_trades or 0}t, "
                    f"{wr_str} {pnl_str} | {chain[:60]}")

    except Exception as e:
        logger.debug(f"changelog read: {e}")

    return lines
