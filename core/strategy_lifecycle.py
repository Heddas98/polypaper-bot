"""
PolyPaper Bot — Strategy Lifecycle Manager (Phase 74b)
=====================================================
Per-strategy adaptive parameter learning.

Instead of one global MIN_COMPOSITE for all strategies, each strategy
carries its own parameter overrides that evolve based on performance.

Lifecycle phases:
  EXPLORATION (0-20 trades)  → Loose filters, $1 trades. Goal: gather data.
  EVALUATION  (20-50 trades) → Normal filters tuned by rolling WR.
  PROVEN      (50+ trades)   → Earned trust → filters loosen for winners,
                                tighten for losers.

Every N cycles (default 300 = ~5 min), the lifecycle manager:
  1. Checks each active strategy's rolling WR and PnL
  2. Adjusts that strategy's personal filter params in strategy_params JSON
  3. Engine reads these overrides at trade-time

This replaces the "all or nothing" global approach that either:
  - Blocks 80% of organic signals (tight global gates)
  - Lets garbage through (loose global gates like Phase 62b)
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.core.lifecycle")

# ═══ Phase Boundaries ═══
EXPLORATION_MAX = int(os.getenv("LIFECYCLE_EXPLORATION_MAX", "20"))
EVALUATION_MAX = int(os.getenv("LIFECYCLE_EVALUATION_MAX", "50"))

# ═══ Phase 82e HOTFIX: Protected strategy types — lifecycle must NOT
# adjust params for these (they are user-managed). Same set as
# auto_optimizer.PROTECTED_STRATEGY_TYPES.
PROTECTED_STRATEGY_TYPES = {
    t.strip().lower() for t in
    os.getenv("PROTECTED_STRATEGY_TYPES", "classic").split(",")
    if t.strip()
}

# ═══ Default overrides per phase ═══
# Exploration: loose gates, small size — we're learning
EXPLORATION_DEFAULTS = {
    "min_composite": 0.20,     # Low bar — let signals through for learning
    "conviction_min": 0.20,    # Low bar
    "edge_gate_mult": 0.70,    # 30% looser than global edge gate
    "trade_amount_mult": 1.0,  # $1 base (don't scale up yet)
    "phase": "exploration",
}

# Evaluation: normal gates, adjusted by performance
EVALUATION_DEFAULTS = {
    "min_composite": 0.30,     # Moderate
    "conviction_min": 0.25,    # Moderate
    "edge_gate_mult": 0.85,    # 15% looser than global
    "trade_amount_mult": 1.0,
    "phase": "evaluation",
}

# Proven: earned trust — starts at global defaults, adjusted by WR
PROVEN_DEFAULTS = {
    "min_composite": 0.35,     # Global default
    "conviction_min": 0.30,    # Global default
    "edge_gate_mult": 1.0,    # No discount
    "trade_amount_mult": 1.0,  # Can scale up for winners
    "phase": "proven",
}


@dataclass
class StrategyParams:
    """Per-strategy override parameters."""
    min_composite: float = 0.35
    conviction_min: float = 0.30
    edge_gate_mult: float = 1.0     # Multiplier on global edge gate thresholds
    trade_amount_mult: float = 1.0  # Multiplier on base trade amount
    phase: str = "exploration"
    last_adjusted: str = ""         # ISO timestamp of last adjustment
    adjustment_reason: str = ""     # Human-readable reason for last change
    # Phase 75: Per-strategy filter autonomy
    # Each strategy can independently enable/disable filters
    # None = use global default, True/False = override
    kelly_enabled: bool | None = None          # Kelly sizing
    confluence_enabled: bool | None = None      # Confluence gate
    bayesian_enabled: bool | None = None        # Bayesian updater
    technical_enabled: bool | None = None       # RSI/MACD/BB
    slippage_gate: bool | None = None           # Slippage gate
    # Per-strategy stats cache (updated on lifecycle check)
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    # Phase 82d: HyperOpt persisted params (survives lifecycle save roundtrip)
    # plugin_params → registry.set_config runtime params (e.g. trend_threshold)
    # engine_gates  → engine-level gates (e.g. _min_confidence)
    plugin_params: dict = field(default_factory=dict)
    engine_gates: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "min_composite": round(self.min_composite, 3),
            "conviction_min": round(self.conviction_min, 3),
            "edge_gate_mult": round(self.edge_gate_mult, 3),
            "trade_amount_mult": round(self.trade_amount_mult, 2),
            "phase": self.phase,
            "last_adjusted": self.last_adjusted,
            "adjustment_reason": self.adjustment_reason,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 1),
            "total_pnl": round(self.total_pnl, 2),
        }
        # Only store non-None filter overrides (saves DB space)
        for f in ("kelly_enabled", "confluence_enabled", "bayesian_enabled",
                   "technical_enabled", "slippage_gate"):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        # Phase 82d: only emit HyperOpt persisted dicts when non-empty
        if self.plugin_params:
            d["plugin_params"] = self.plugin_params
        if self.engine_gates:
            d["engine_gates"] = self.engine_gates
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyParams":
        if not d:
            return cls()
        return cls(
            min_composite=d.get("min_composite", 0.35),
            conviction_min=d.get("conviction_min", 0.30),
            edge_gate_mult=d.get("edge_gate_mult", 1.0),
            trade_amount_mult=d.get("trade_amount_mult", 1.0),
            phase=d.get("phase", "exploration"),
            last_adjusted=d.get("last_adjusted", ""),
            adjustment_reason=d.get("adjustment_reason", ""),
            kelly_enabled=d.get("kelly_enabled"),
            confluence_enabled=d.get("confluence_enabled"),
            bayesian_enabled=d.get("bayesian_enabled"),
            technical_enabled=d.get("technical_enabled"),
            slippage_gate=d.get("slippage_gate"),
            total_trades=d.get("total_trades", 0),
            win_rate=d.get("win_rate", 0.0),
            total_pnl=d.get("total_pnl", 0.0),
            # Phase 82d: preserve HyperOpt persisted dicts across roundtrip
            plugin_params=d.get("plugin_params") or {},
            engine_gates=d.get("engine_gates") or {},
        )

    @classmethod
    def for_phase(cls, phase: str) -> "StrategyParams":
        """Get default params for a lifecycle phase."""
        defaults = {
            "exploration": EXPLORATION_DEFAULTS,
            "evaluation": EVALUATION_DEFAULTS,
            "proven": PROVEN_DEFAULTS,
        }.get(phase, PROVEN_DEFAULTS)
        return cls(**defaults)


class StrategyLifecycle:
    """Manages per-strategy adaptive parameters."""

    def __init__(self, db):
        self.db = db
        self._cache: dict[str, StrategyParams] = {}  # strategy_id → params

    async def ensure_column(self):
        """Add strategy_params column if it doesn't exist."""
        try:
            await self.db.conn.execute(
                "ALTER TABLE strategies ADD COLUMN strategy_params TEXT DEFAULT '{}'")
            await self.db.conn.commit()
            logger.info("✅ Added strategy_params column to strategies table")
        except Exception:
            pass  # Column already exists

    async def get_params(self, strategy_id: str) -> StrategyParams:
        """Get per-strategy params (cached)."""
        if strategy_id in self._cache:
            return self._cache[strategy_id]
        # Load from DB
        try:
            async with self.db.conn.execute(
                "SELECT strategy_params FROM strategies WHERE id=?",
                (strategy_id,)
            ) as c:
                row = await c.fetchone()
                if row and row[0]:
                    d = json.loads(row[0])
                    params = StrategyParams.from_dict(d)
                else:
                    params = StrategyParams()  # Default exploration
        except Exception as e:
            logger.debug(f"get_params {strategy_id[:8]}: {e}")
            params = StrategyParams()
        self._cache[strategy_id] = params
        return params

    async def save_params(self, strategy_id: str, params: StrategyParams):
        """Persist params to DB and update cache."""
        self._cache[strategy_id] = params
        try:
            await self.db.conn.execute(
                "UPDATE strategies SET strategy_params=? WHERE id=?",
                (json.dumps(params.to_dict()), strategy_id))
            await self.db.conn.commit()
        except Exception as e:
            logger.debug(f"save_params {strategy_id[:8]}: {e}")

    async def run_lifecycle_check(self):
        """Main entry — called by auto_optimizer every N cycles.

        For each active strategy:
        1. Determine phase based on trade count
        2. Adjust params based on rolling WR
        3. Save if changed
        """
        try:
            strategies = await self.db.get_active_strategies()
        except Exception as e:
            logger.error(f"lifecycle check: {e}")
            return

        for s in strategies:
            # Phase 82e HOTFIX: skip protected types (e.g., classic)
            stype = (getattr(s, "strategy_type", "") or "").lower()
            if stype in PROTECTED_STRATEGY_TYPES:
                continue
            try:
                await self._adjust_strategy(s)
            except Exception as e:
                logger.debug(f"lifecycle adjust {s.id[:8]}: {e}")

    async def _adjust_strategy(self, s):
        """Adjust a single strategy's params based on performance."""
        stats = await self._get_stats(s.id)
        if not stats:
            return

        trades = stats["trades"]
        wr = stats["wr"]
        pnl = stats["pnl"]

        # Determine current phase
        if trades < EXPLORATION_MAX:
            target_phase = "exploration"
        elif trades < EVALUATION_MAX:
            target_phase = "evaluation"
        else:
            target_phase = "proven"

        current = await self.get_params(s.id)
        changed = False
        reason_parts = []

        # Phase transition?
        if current.phase != target_phase:
            new_defaults = StrategyParams.for_phase(target_phase)
            # Keep earned adjustments, just update phase and base
            current.phase = target_phase
            current.min_composite = new_defaults.min_composite
            current.conviction_min = new_defaults.conviction_min
            current.edge_gate_mult = new_defaults.edge_gate_mult
            changed = True
            reason_parts.append(f"phase→{target_phase}")
            logger.info(f"🔄 [{s.id[:8]}] Phase transition → {target_phase} "
                        f"({trades}t, WR={wr:.0f}%, PnL={pnl:+.2f})")

        # Performance-based adjustment (only for evaluation/proven)
        if target_phase in ("evaluation", "proven") and trades >= 10:
            rolling_wr = stats.get("rolling_wr", wr)

            # High WR → reward: loosen gates slightly
            if rolling_wr >= 60:
                new_comp = max(current.min_composite - 0.02, 0.15)
                new_conv = max(current.conviction_min - 0.02, 0.15)
                new_edge = min(current.edge_gate_mult + 0.05, 1.2)  # Can go above 1.0 = loose
                if new_comp != current.min_composite:
                    current.min_composite = new_comp
                    current.conviction_min = new_conv
                    current.edge_gate_mult = new_edge
                    changed = True
                    reason_parts.append(f"WR={rolling_wr:.0f}%→loosen")

            # Medium WR → hold steady
            elif 48 <= rolling_wr < 60:
                pass  # No change

            # Low WR → tighten gates
            elif rolling_wr < 48:
                new_comp = min(current.min_composite + 0.03, 0.50)
                new_conv = min(current.conviction_min + 0.02, 0.45)
                new_edge = max(current.edge_gate_mult - 0.05, 0.5)
                if new_comp != current.min_composite:
                    current.min_composite = new_comp
                    current.conviction_min = new_conv
                    current.edge_gate_mult = new_edge
                    changed = True
                    reason_parts.append(f"WR={rolling_wr:.0f}%→tighten")

            # Scale up for proven winners
            if target_phase == "proven" and rolling_wr >= 55 and pnl > 0:
                scale = min(1.0 + (rolling_wr - 55) * 0.02, 2.0)  # Max 2x
                if abs(scale - current.trade_amount_mult) > 0.05:
                    current.trade_amount_mult = round(scale, 2)
                    changed = True
                    reason_parts.append(f"size→{scale:.2f}x")

        # Phase 75: Always update stats cache (even if params didn't change)
        current.total_trades = trades
        current.win_rate = round(wr, 1)
        current.total_pnl = round(pnl, 2)

        if changed:
            from datetime import datetime, timezone
            current.last_adjusted = datetime.now(timezone.utc).isoformat()
            current.adjustment_reason = "; ".join(reason_parts)

        # Save if params changed OR stats updated
        if changed or current.total_trades != trades:
            await self.save_params(s.id, current)
            logger.info(f"📊 [{s.id[:8]}] Lifecycle: {current.adjustment_reason} | "
                        f"comp={current.min_composite:.2f} conv={current.conviction_min:.2f} "
                        f"edge={current.edge_gate_mult:.2f} size={current.trade_amount_mult:.1f}x")

    async def _get_stats(self, sid: str) -> Optional[dict]:
        """Get strategy stats including rolling WR."""
        try:
            # Overall stats
            async with self.db.conn.execute(
                """SELECT COUNT(*) as trades,
                   COALESCE(SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END),0) as wins,
                   COALESCE(SUM(CASE WHEN result IS NOT NULL THEN pnl ELSE 0 END),0) as pnl
                   FROM executions WHERE strategy_id=? AND result IS NOT NULL""",
                (sid,)
            ) as c:
                r = await c.fetchone()
                if not r or r["trades"] == 0:
                    return None
                t = r["trades"]
                result = {
                    "trades": t,
                    "wins": r["wins"],
                    "pnl": r["pnl"],
                    "wr": r["wins"] / t * 100,
                }

            # Rolling WR (last 20 trades)
            rows = await self.db.conn.execute_fetchall(
                """SELECT pnl FROM executions
                   WHERE strategy_id=? AND result IS NOT NULL
                   ORDER BY closed_at DESC LIMIT 20""",
                (sid,))
            if rows and len(rows) >= 5:
                rw = sum(1 for r in rows if r[0] > 0) / len(rows) * 100
                result["rolling_wr"] = rw
            else:
                result["rolling_wr"] = result["wr"]

            return result
        except Exception:
            return None

    def format_telegram(self) -> str:
        """Format all cached params for Telegram display."""
        if not self._cache:
            return "<i>No strategy params cached</i>"
        lines = ["<b>📊 Strategy Lifecycle</b>\n"]
        for sid, p in sorted(self._cache.items()):
            emoji = {"exploration": "🔬", "evaluation": "📊", "proven": "✅"}.get(p.phase, "❓")
            lines.append(
                f"{emoji} <code>{esc(sid[:8])}</code> [{esc(p.phase)}] "
                f"comp={p.min_composite:.2f} conv={p.conviction_min:.2f} "
                f"edge={p.edge_gate_mult:.2f} size={p.trade_amount_mult:.1f}x"
            )
            if p.adjustment_reason:
                lines.append(f"   └ {esc(p.adjustment_reason)}")
        return "\n".join(lines)
