"""
Phase 69: Champion Tracker — Best Config History per Strategy
=============================================================
Source: TradeSight (champion config concept)

Tracks the best-performing parameter configuration for each strategy.
Enables:
  - Regression detection: "This week's config is worse than last week's champion"
  - Rollback: Can restore champion params if current config degrades
  - Tournament integration: Stores tournament winners

Usage:
    from utils.champion_tracker import ChampionTracker

    tracker = ChampionTracker(db)
    await tracker.record(strategy_id, params, score, metric)
    champion = await tracker.get_champion(strategy_id)
    history = await tracker.get_history(strategy_id, limit=10)

ENV:
    CHAMPION_TRACKER_ENABLED=true
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.utils.champion")


@dataclass
class ChampionRecord:
    """A champion configuration snapshot."""

    strategy_id: str = ""
    strategy_label: str = ""
    params: dict = field(default_factory=dict)
    score: float = 0.0
    metric: str = "sharpe_ratio"
    win_rate: float = 0.0
    total_trades: int = 0
    total_pnl: float = 0.0
    recorded_at: str = ""
    source: str = ""  # "tournament", "manual", "auto_optimizer"


class ChampionTracker:
    """Track and manage best-performing strategy configurations."""

    def __init__(self, db):
        self.db = db
        self._initialized = False

    async def _ensure_table(self):
        """Create champion_configs table if not exists."""
        if self._initialized:
            return
        await self.db.conn.execute("""
            CREATE TABLE IF NOT EXISTS champion_configs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id TEXT NOT NULL,
                strategy_label TEXT,
                params_json TEXT,
                score REAL,
                metric TEXT DEFAULT 'sharpe_ratio',
                win_rate REAL,
                total_trades INTEGER,
                total_pnl REAL,
                source TEXT DEFAULT 'manual',
                recorded_at TEXT DEFAULT (datetime('now')),
                is_current_champion INTEGER DEFAULT 0
            )
        """)
        await self.db.conn.commit()
        self._initialized = True

    async def record(
        self,
        strategy_id: str,
        params: dict,
        score: float,
        metric: str = "sharpe_ratio",
        label: str = "",
        win_rate: float = 0.0,
        total_trades: int = 0,
        total_pnl: float = 0.0,
        source: str = "manual",
    ) -> bool:
        """Record a new champion config. Returns True if it's a new champion."""
        await self._ensure_table()

        # Check if this beats current champion
        current = await self.get_champion(strategy_id, metric)
        is_new_champion = current is None or score > current.score

        if is_new_champion:
            # Demote old champion
            await self.db.conn.execute(
                "UPDATE champion_configs SET is_current_champion = 0 "
                "WHERE strategy_id = ? AND metric = ?",
                (strategy_id, metric),
            )

        await self.db.conn.execute(
            """INSERT INTO champion_configs
               (strategy_id, strategy_label, params_json, score, metric,
                win_rate, total_trades, total_pnl, source, is_current_champion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy_id,
                label,
                json.dumps(params),
                score,
                metric,
                win_rate,
                total_trades,
                total_pnl,
                source,
                1 if is_new_champion else 0,
            ),
        )
        await self.db.conn.commit()

        if is_new_champion:
            logger.info(
                "🏆 New champion for %s: score=%.4f (%s) params=%s",
                strategy_id,
                score,
                metric,
                json.dumps(params),
            )

        return is_new_champion

    async def get_champion(
        self, strategy_id: str, metric: str = "sharpe_ratio"
    ) -> Optional[ChampionRecord]:
        """Get current champion config for a strategy."""
        await self._ensure_table()

        row = await self.db.conn.execute_fetchall(
            """SELECT strategy_id, strategy_label, params_json, score, metric,
                      win_rate, total_trades, total_pnl, recorded_at, source
               FROM champion_configs
               WHERE strategy_id = ? AND metric = ? AND is_current_champion = 1
               LIMIT 1""",
            (strategy_id, metric),
        )

        if not row:
            return None

        r = row[0]
        return ChampionRecord(
            strategy_id=r[0],
            strategy_label=r[1] or "",
            params=json.loads(r[2]) if r[2] else {},
            score=r[3] or 0.0,
            metric=r[4] or metric,
            win_rate=r[5] or 0.0,
            total_trades=r[6] or 0,
            total_pnl=r[7] or 0.0,
            recorded_at=r[8] or "",
            source=r[9] or "",
        )

    async def get_history(
        self, strategy_id: str, metric: str = "sharpe_ratio", limit: int = 10
    ) -> list[ChampionRecord]:
        """Get champion config history for a strategy."""
        await self._ensure_table()

        rows = await self.db.conn.execute_fetchall(
            """SELECT strategy_id, strategy_label, params_json, score, metric,
                      win_rate, total_trades, total_pnl, recorded_at, source
               FROM champion_configs
               WHERE strategy_id = ? AND metric = ?
               ORDER BY recorded_at DESC
               LIMIT ?""",
            (strategy_id, metric, limit),
        )

        return [
            ChampionRecord(
                strategy_id=r[0],
                strategy_label=r[1] or "",
                params=json.loads(r[2]) if r[2] else {},
                score=r[3] or 0.0,
                metric=r[4] or metric,
                win_rate=r[5] or 0.0,
                total_trades=r[6] or 0,
                total_pnl=r[7] or 0.0,
                recorded_at=r[8] or "",
                source=r[9] or "",
            )
            for r in rows
        ]

    async def check_regression(
        self, strategy_id: str, current_score: float, metric: str = "sharpe_ratio"
    ) -> dict:
        """Check if current performance has regressed vs champion."""
        champion = await self.get_champion(strategy_id, metric)
        if not champion:
            return {"regressed": False, "message": "No champion yet"}

        regression_pct = ((champion.score - current_score) / max(abs(champion.score), 0.01)) * 100

        if regression_pct > 20:
            return {
                "regressed": True,
                "regression_pct": round(regression_pct, 1),
                "champion_score": champion.score,
                "current_score": current_score,
                "champion_params": champion.params,
                "message": f"⚠️ {regression_pct:.0f}% regression vs champion "
                f"(champion: {champion.score:.4f}, current: {current_score:.4f})",
            }
        return {
            "regressed": False,
            "regression_pct": round(regression_pct, 1),
            "message": f"OK ({regression_pct:.0f}% from champion)",
        }

    def format_telegram(self, champion: ChampionRecord) -> str:
        """Format champion record for Telegram."""
        label = esc(champion.strategy_label or champion.strategy_id)
        lines = [
            f"🏆 <b>Champion: {label}</b>",
            f"Score: <b>{champion.score:.4f}</b> ({esc(champion.metric)})",
            f"WR: {champion.win_rate*100:.1f}% | Trades: {champion.total_trades} | "
            f"PnL: ${champion.total_pnl:+.2f}",
            f"Source: {esc(champion.source)} | {champion.recorded_at}",
        ]
        if champion.params:
            lines.append(f"Params: <code>{esc(json.dumps(champion.params))}</code>")
        return "\n".join(lines)
