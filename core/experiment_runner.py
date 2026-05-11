"""
PolyPaper Bot - Experiment Runner (Phase 77)
=============================================
Safely test parameter changes before applying them.

Usage:
    /experiment MIN_COMPOSITE=0.30 EDGE_GATE=0.40
    → Runs a mini-backtest with modified params
    → Shows expected impact before you commit
    → /experiment_apply to apply changes
    → /experiment_discard to throw away

ENV:
    EXPERIMENT_ENABLED=true
    EXPERIMENT_MAX_PARAMS=5       # Max params per experiment
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Dict, List, Optional

import aiosqlite

logger = logging.getLogger("polypaper.core.experiment")

EXPERIMENT_ENABLED = os.getenv("EXPERIMENT_ENABLED", "true").lower() == "true"
MAX_PARAMS = int(os.getenv("EXPERIMENT_MAX_PARAMS", "5"))


@dataclass
class ExperimentResult:
    """Result of an experiment run."""

    params_changed: Dict[str, tuple] = field(default_factory=dict)  # param → (old, new)
    baseline_wr: float = 0.0
    experiment_wr: float = 0.0
    baseline_pnl: float = 0.0
    experiment_pnl: float = 0.0
    baseline_trades: int = 0
    experiment_trades: int = 0
    improvement: float = 0.0  # % change in PnL
    recommendation: str = ""  # "apply", "discard", "neutral"
    details: str = ""
    created_at: str = ""


class ExperimentRunner:
    """
    Sandbox for testing parameter changes.

    Flow:
    1. User proposes params via /experiment KEY=VALUE ...
    2. Runner snapshots current values
    3. Runs analysis on recent trades with both param sets
    4. Returns comparison
    5. User applies or discards
    """

    def __init__(self):
        self.db = None
        self._pending: Optional[ExperimentResult] = None
        self._history: List[ExperimentResult] = []

    async def initialize(self, db):
        self.db = db
        logger.info("🧪 Phase 77: Experiment Runner initialized")

    def parse_params(self, args: List[str]) -> Dict[str, str]:
        """Parse KEY=VALUE pairs from command args."""
        params = {}
        for arg in args:
            if "=" in arg:
                key, val = arg.split("=", 1)
                params[key.strip().upper()] = val.strip()
        return params

    async def run_experiment(self, params: Dict[str, str]) -> ExperimentResult:
        """Run an experiment comparing current vs proposed params."""
        if not EXPERIMENT_ENABLED:
            return ExperimentResult(recommendation="disabled")

        if len(params) > MAX_PARAMS:
            return ExperimentResult(
                recommendation="error",
                details=f"Max {MAX_PARAMS} parametre. {len(params)} verildi.",
            )

        result = ExperimentResult(created_at=datetime.now(UTC).isoformat()[:19])

        # Snapshot current values
        for key, new_val in params.items():
            old_val = os.getenv(key, "NOT_SET")
            result.params_changed[key] = (old_val, new_val)

        # Get baseline stats from recent trades
        if self.db is not None:
            try:
                # Last 100 trades as baseline
                rows = await self.db.conn.execute_fetchall(
                    """SELECT result, pnl, COALESCE(signal_score, 0), execution_price
                       FROM executions
                       WHERE status = 'claimed' AND result IS NOT NULL
                       ORDER BY closed_at DESC
                       LIMIT 100"""
                )

                if rows:
                    result.baseline_trades = len(rows)
                    wins = sum(1 for r in rows if r[0] == "won")
                    result.baseline_wr = round(wins / len(rows) * 100, 1) if rows else 0
                    result.baseline_pnl = round(sum(r[1] or 0 for r in rows), 2)

                    # Simulate impact of parameter changes
                    # This is a heuristic: we estimate based on what the params control
                    result.experiment_trades = result.baseline_trades
                    result.experiment_wr = result.baseline_wr
                    result.experiment_pnl = result.baseline_pnl

                    for key, (old_val, new_val) in result.params_changed.items():
                        impact = self._estimate_impact(key, old_val, new_val, rows)
                        result.experiment_wr += impact.get("wr_delta", 0)
                        result.experiment_pnl += impact.get("pnl_delta", 0)
                        result.experiment_trades += impact.get("trade_delta", 0)

                    # Improvement
                    if result.baseline_pnl != 0:
                        result.improvement = round(
                            (result.experiment_pnl - result.baseline_pnl)
                            / abs(result.baseline_pnl)
                            * 100,
                            1,
                        )

                    # Recommendation
                    if (
                        result.experiment_pnl > result.baseline_pnl
                        and result.experiment_wr >= result.baseline_wr - 2
                    ):
                        result.recommendation = "apply"
                        result.details = "PnL artışı bekleniyor, WR stabil."
                    elif result.experiment_pnl < result.baseline_pnl:
                        result.recommendation = "discard"
                        result.details = "PnL düşüşü bekleniyor."
                    else:
                        result.recommendation = "neutral"
                        result.details = "Belirgin fark yok. Canlıda test önerilir."

            except (
                aiosqlite.Error,
                ValueError,
                TypeError,
                ArithmeticError,
                IndexError,
                AttributeError,
            ) as e:
                # T1.4 Faz 3: DB fetch (executions) + per-row unpack (r[0]/r[1])
                # + numeric summing + baseline_pnl division at L130.
                # Realistic failure modes:
                #   - aiosqlite.Error: executions missing / locked / schema
                #   - ValueError/TypeError: row coercion (pnl=None, bad types)
                #   - ArithmeticError: zero-divide if baseline_pnl guard races
                #     with a future edit (defensive)
                #   - IndexError/AttributeError: r[0]/r[1] or db.conn missing
                # _estimate_impact has its own narrow catch for (ValueError,
                # TypeError) on float() coercion — won't bubble up here.
                result.recommendation = "error"
                result.details = f"Analiz hatası: {e}"

        self._pending = result
        self._history.append(result)
        if len(self._history) > 20:
            self._history = self._history[-20:]

        return result

    def _estimate_impact(
        self, key: str, old_val: str, new_val: str, trades: list
    ) -> Dict[str, float]:
        """Heuristic estimate of parameter change impact."""
        impact = {"wr_delta": 0.0, "pnl_delta": 0.0, "trade_delta": 0}

        try:
            old_f = float(old_val) if old_val != "NOT_SET" else 0
            new_f = float(new_val)
        except (ValueError, TypeError):
            return impact

        key_upper = key.upper()

        # Filter-tightening params: higher = fewer trades but higher WR
        if key_upper in (
            "MIN_COMPOSITE",
            "CONVICTION_MIN",
            "EDGE_GATE",
            "MIN_SIGNAL_SCORE",
            "CONFLUENCE_MIN_GATES",
        ):
            delta = new_f - old_f
            if delta > 0:  # Tightening
                # Estimate: 10% tighter → -5% trades, +2% WR
                pct_change = delta / max(old_f, 0.01)
                impact["trade_delta"] = int(-len(trades) * pct_change * 0.5)
                impact["wr_delta"] = pct_change * 20
                impact["pnl_delta"] = impact["wr_delta"] * 0.1 * len(trades)
            else:  # Loosening
                pct_change = abs(delta) / max(old_f, 0.01)
                impact["trade_delta"] = int(len(trades) * pct_change * 0.5)
                impact["wr_delta"] = -pct_change * 15
                impact["pnl_delta"] = impact["wr_delta"] * 0.1 * len(trades)

        # Size params
        elif key_upper in ("TRADE_AMOUNT", "CAPITAL_BASE_ALLOCATION"):
            ratio = new_f / max(old_f, 0.01)
            total_pnl = sum(r[1] or 0 for r in trades)
            impact["pnl_delta"] = total_pnl * (ratio - 1)

        # Risk params
        elif key_upper in ("MAX_DAILY_LOSS", "MAX_LOSS_STREAK", "MAX_TOTAL_EXPOSURE"):
            # Looser risk → potentially more trades
            if new_f > old_f:
                impact["trade_delta"] = int(len(trades) * 0.05)

        return impact

    def apply_pending(self) -> Optional[Dict[str, tuple]]:
        """Apply pending experiment's params to os.environ."""
        if self._pending is None:
            return None

        applied = {}
        for key, (old_val, new_val) in self._pending.params_changed.items():
            os.environ[key] = new_val
            applied[key] = (old_val, new_val)

        self._pending = None
        return applied

    def discard_pending(self) -> bool:
        """Discard pending experiment."""
        if self._pending is None:
            return False
        self._pending = None
        return True

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    def format_result_telegram(self, result: ExperimentResult) -> str:
        """Format experiment result for Telegram."""
        if result.recommendation == "disabled":
            return "🧪 Experiment Runner devre dışı."
        if result.recommendation == "error":
            return f"🧪 Hata: {result.details}"

        lines = ["🧪 <b>Experiment Sonuçları</b>\n"]

        # Param changes
        lines.append("<b>Değişiklikler:</b>")
        for key, (old_v, new_v) in result.params_changed.items():
            lines.append(f"  <code>{key}</code>: {old_v} → <b>{new_v}</b>")

        lines.append("")

        # Comparison
        lines.append("<b>Karşılaştırma:</b>")
        lines.append(
            f"  Baseline:   {result.baseline_trades}t | "
            f"WR {result.baseline_wr:.1f}% | PnL ${result.baseline_pnl:+.2f}"
        )
        lines.append(
            f"  Experiment: {result.experiment_trades}t | "
            f"WR {result.experiment_wr:.1f}% | PnL ${result.experiment_pnl:+.2f}"
        )

        if result.improvement != 0:
            emoji = "📈" if result.improvement > 0 else "📉"
            lines.append(f"\n{emoji} Tahmini etki: {result.improvement:+.1f}%")

        # Recommendation
        rec_emoji = {"apply": "✅", "discard": "❌", "neutral": "🟡"}.get(
            result.recommendation, "❓"
        )
        lines.append(f"\n{rec_emoji} Öneri: <b>{result.recommendation.upper()}</b>")
        lines.append(f"<i>{result.details}</i>")

        if result.recommendation != "discard":
            lines.append("\n/experiment_apply — Uygula")
        lines.append("/experiment_discard — İptal")

        return "\n".join(lines)


# ── Singleton ──
_instance: Optional[ExperimentRunner] = None


def get_experiment_runner() -> ExperimentRunner:
    global _instance
    if _instance is None:
        _instance = ExperimentRunner()
    return _instance
