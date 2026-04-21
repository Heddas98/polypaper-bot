"""
PolyPaper Bot - Thompson Sampling Strategy Selector (Phase 33)
Multi-armed bandit for adaptive strategy capital allocation.

Each strategy = one arm of the bandit.
Beta(alpha, beta) distribution per strategy.
Win → alpha+1, Loss → beta+1.
Decay factor 0.995 per trade → adapts to shifting markets.

Result: bot automatically routes capital to winning strategies.
"""
import logging
import os
import random
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger("polypaper.core.selector")

DECAY = 0.995  # Slowly forget old data (adapts to regime changes)
MIN_ALPHA = 1.0
MIN_BETA = 1.0
# Phase 58: Tightened from 0.60 to 0.40 — only top 40% of strategies trade.
# Audit showed 60% was too loose: weak strategies kept bleeding money.
THOMPSON_TOP_PCT = float(os.getenv("THOMPSON_TOP_PCT", "0.40"))


@dataclass
class ArmState:
    """Thompson Sampling state for one strategy."""
    alpha: float = 2.0   # Prior wins (start optimistic)
    beta: float = 2.0    # Prior losses
    total_trades: int = 0
    recent_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.alpha / (self.alpha + self.beta) if (self.alpha + self.beta) > 0 else 0.5

    def sample(self) -> float:
        """Sample from Beta distribution."""
        try:
            return random.betavariate(max(self.alpha, 0.01), max(self.beta, 0.01))
        except ValueError:
            return 0.5

    def update(self, won: bool, pnl: float = 0):
        """Update after trade result with decay."""
        # Decay old observations
        self.alpha = max(MIN_ALPHA, self.alpha * DECAY)
        self.beta = max(MIN_BETA, self.beta * DECAY)
        # Add new observation
        if won:
            self.alpha += 1.0
        else:
            self.beta += 1.0
        self.total_trades += 1
        self.recent_pnl += pnl


class StrategySelector:
    """Thompson Sampling multi-armed bandit for strategy selection."""

    def __init__(self):
        self._arms: dict[str, ArmState] = {}  # strategy_id → ArmState

    def get_or_create(self, strategy_id: str) -> ArmState:
        if strategy_id not in self._arms:
            self._arms[strategy_id] = ArmState()
        return self._arms[strategy_id]

    def should_trade(self, strategy_id: str, num_active: int = 5, engine=None) -> bool:
        """Should this strategy take the next trade?

        Samples all active arms, returns True if this arm is in top-N.
        For paper trading with multiple strategies, we allow top-60% to trade.

        If brain_flags['thompson_sampling'] is disabled, uses equal-weight fallback.
        """
        arm = self.get_or_create(strategy_id)

        # Check if Thompson Sampling is disabled via brain_flags
        ts_enabled = True
        if engine:
            ts_enabled = getattr(engine, 'brain_flags', {}).get('thompson_sampling', True)

        if not ts_enabled:
            # Fallback: equal weights, allow all to trade during exploration
            if arm.total_trades < 10:
                return True
            # During normal operation, use equal distribution (60% threshold still applies)
            num_all = len(self._arms)
            threshold_count = max(1, int(num_all * 0.6))
            return num_all <= threshold_count

        # Thompson Sampling enabled (normal path)
        if arm.total_trades < 10:
            return True  # Always trade during exploration phase

        # Sample all arms
        samples = {}
        for sid, a in self._arms.items():
            samples[sid] = a.sample()

        # Phase 58: Allow only top THOMPSON_TOP_PCT of strategies to trade
        # (was 60%, now 40% by default — env THOMPSON_TOP_PCT overrides)
        if strategy_id not in samples:
            return True
        bottom_pct = 1.0 - THOMPSON_TOP_PCT  # Block this fraction
        threshold_rank = max(1, int(len(samples) * bottom_pct))
        sorted_samples = sorted(samples.values(), reverse=True)
        if len(sorted_samples) <= threshold_rank:
            return True
        cutoff = sorted_samples[min(threshold_rank, len(sorted_samples)-1)]
        return samples[strategy_id] >= cutoff

    def record_result(self, strategy_id: str, won: bool, pnl: float = 0):
        """Record trade result for Thompson Sampling update."""
        arm = self.get_or_create(strategy_id)
        arm.update(won, pnl)
        logger.debug(f"TS update: {strategy_id[:8]} {'W' if won else 'L'} "
                     f"α={arm.alpha:.1f} β={arm.beta:.1f} WR={arm.win_rate:.0%}")

    def get_rankings(self) -> list[dict]:
        """Get all strategies ranked by Thompson Sampling score."""
        rankings = []
        for sid, arm in self._arms.items():
            rankings.append({
                "id": sid,
                "alpha": round(arm.alpha, 1),
                "beta": round(arm.beta, 1),
                "win_rate": round(arm.win_rate * 100, 1),
                "sample": round(arm.sample() * 100, 1),
                "trades": arm.total_trades,
                "pnl": round(arm.recent_pnl, 2),
            })
        return sorted(rankings, key=lambda x: x["sample"], reverse=True)

    def get_status(self) -> dict:
        rankings = self.get_rankings()
        return {
            "total_arms": len(self._arms),
            "rankings": rankings[:10],
        }

    async def load_from_db(self, db):
        """Initialize arms from historical trade data."""
        try:
            rows = await db.conn.execute_fetchall(
                """SELECT e.strategy_id,
                    COUNT(*) as t,
                    SUM(CASE WHEN e.pnl > 0 THEN 1 ELSE 0 END) as w,
                    COALESCE(SUM(e.pnl), 0) as pnl
                FROM executions e WHERE e.result IS NOT NULL
                GROUP BY e.strategy_id""")
            for r in (rows or []):
                arm = self.get_or_create(r[0])
                wins, losses = r[2], r[1] - r[2]
                # Apply decay for historical data (older = less weight)
                arm.alpha = max(MIN_ALPHA, 1.0 + wins * 0.5)  # Half weight for history
                arm.beta = max(MIN_BETA, 1.0 + losses * 0.5)
                arm.total_trades = r[1]
                arm.recent_pnl = r[3]
            logger.info(f"🎰 Thompson Sampling: loaded {len(self._arms)} strategies from history")
        except (aiosqlite.Error, ValueError, TypeError, IndexError,
                AttributeError) as e:
            # T1.4 Faz 3: DB fetch + row unpack + Beta prior update. DB
            # failure is non-fatal (arms keep prior defaults), but the
            # failure class should surface loudly rather than silently.
            logger.warning(f"TS load: {e}")
