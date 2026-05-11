"""
Phase 66: Brier Score Tracker + Calibration Curve Analysis
==========================================================
Tracks AI Brain prediction accuracy using Brier Score decomposition
(Murphy 1973: calibration + resolution + uncertainty).

Source: @mikita_crypto articles A2 (Superforecasting) + A7 (Game Theory)
Superforecasters: Brier 0.15-0.20. LLMs ~19pp behind.
Target: <0.30 (3 months), <0.25 (6 months).

Usage:
    tracker = BrierTracker(db)
    await tracker.record(prediction=0.70, outcome=1)  # predicted 70%, was correct
    await tracker.record(prediction=0.55, outcome=0)  # predicted 55%, was wrong
    report = await tracker.get_report()

DB Table: brier_scores (auto-created)
    id, timestamp, source, prediction, outcome, brier_score, context_json

ENV:
    BRIER_TRACKING_ENABLED=true
    BRIER_MIN_SAMPLES=20  (minimum for meaningful report)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Optional

logger = logging.getLogger("polypaper.utils.brier")

BRIER_ENABLED = os.getenv("BRIER_TRACKING_ENABLED", "true").lower() == "true"
BRIER_MIN_SAMPLES = int(os.getenv("BRIER_MIN_SAMPLES", "20"))

# Calibration bins: [0.0-0.1), [0.1-0.2), ..., [0.9-1.0]
CALIBRATION_BINS = 10


class BrierTracker:
    """Tracks prediction accuracy with Brier Score + Murphy decomposition."""

    def __init__(self, db=None):
        self.db = db
        self._initialized = False

    async def ensure_table(self):
        """Create brier_scores table if not exists."""
        if self._initialized or not self.db:
            return
        try:
            await self.db.conn.execute("""
                CREATE TABLE IF NOT EXISTS brier_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'ai_brain',
                    prediction REAL NOT NULL,
                    outcome INTEGER NOT NULL,
                    brier_score REAL NOT NULL,
                    context_json TEXT DEFAULT '{}'
                )
            """)
            await self.db.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_brier_ts
                ON brier_scores(timestamp)
            """)
            await self.db.conn.commit()
            self._initialized = True
        except Exception as e:
            logger.warning(f"brier table init: {e}")

    async def record(
        self,
        prediction: float,
        outcome: int,
        source: str = "ai_brain",
        context: Optional[dict] = None,
    ) -> Optional[float]:
        """Record a prediction-outcome pair.

        Args:
            prediction: Forecasted probability [0.0, 1.0]
            outcome: Actual result (1 = happened, 0 = didn't)
            source: Who made the prediction ('ai_brain', 'signal_fusion', 'strategy')
            context: Optional metadata (strategy label, market slug, etc.)

        Returns:
            Brier score for this single prediction, or None if disabled.
        """
        if not BRIER_ENABLED or not self.db:
            return None

        await self.ensure_table()

        # Clamp prediction to valid range
        prediction = max(0.0, min(1.0, prediction))
        outcome = 1 if outcome else 0

        # Brier Score: (prediction - outcome)^2
        brier = (prediction - outcome) ** 2

        now = datetime.now(UTC).isoformat()
        ctx_json = json.dumps(context or {})

        try:
            await self.db.conn.execute(
                """INSERT INTO brier_scores
                   (timestamp, source, prediction, outcome, brier_score, context_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (now, source, prediction, outcome, brier, ctx_json),
            )
            await self.db.conn.commit()
            logger.debug(
                f"Brier recorded: pred={prediction:.3f} out={outcome} "
                f"BS={brier:.4f} src={source}"
            )
        except Exception as e:
            logger.warning(f"brier record failed: {e}")

        return brier

    async def get_report(self, source: Optional[str] = None, hours: int = 168) -> dict:
        """Generate Brier Score report with Murphy decomposition.

        Args:
            source: Filter by source ('ai_brain', 'signal_fusion', etc.) or None for all.
            hours: Look-back window in hours (default 168 = 7 days).

        Returns:
            dict with brier_score, calibration, resolution, uncertainty,
                 sample_count, calibration_curve, per_bin stats.
        """
        if not self.db:
            return {"error": "no_db"}

        await self.ensure_table()

        cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        query = "SELECT prediction, outcome FROM brier_scores WHERE timestamp >= ?"
        params = [cutoff]
        if source:
            query += " AND source = ?"
            params.append(source)

        try:
            cur = await self.db.conn.execute(query, params)
            rows = await cur.fetchall()
        except Exception as e:
            logger.warning(f"brier report query: {e}")
            return {"error": str(e)}

        if len(rows) < BRIER_MIN_SAMPLES:
            return {
                "error": f"insufficient_samples ({len(rows)} < {BRIER_MIN_SAMPLES})",
                "sample_count": len(rows),
            }

        predictions = [r[0] for r in rows]
        outcomes = [r[1] for r in rows]
        n = len(predictions)

        # ═══ Overall Brier Score ═══
        brier_total = sum((p - o) ** 2 for p, o in zip(predictions, outcomes, strict=False)) / n

        # ═══ Murphy Decomposition ═══
        # BS = REL - RES + UNC
        # REL (Reliability/Calibration): lower = better calibrated
        # RES (Resolution): higher = better at distinguishing
        # UNC (Uncertainty): base rate uncertainty (constant)
        base_rate = sum(outcomes) / n
        unc = base_rate * (1 - base_rate)

        # Bin predictions for calibration analysis
        bins = [[] for _ in range(CALIBRATION_BINS)]
        for p, o in zip(predictions, outcomes, strict=False):
            bin_idx = min(int(p * CALIBRATION_BINS), CALIBRATION_BINS - 1)
            bins[bin_idx].append((p, o))

        # Calculate REL and RES
        rel = 0.0
        res = 0.0
        calibration_curve = []

        for i, bin_data in enumerate(bins):
            if not bin_data:
                calibration_curve.append(
                    {
                        "bin": f"{i/CALIBRATION_BINS:.1f}-{(i+1)/CALIBRATION_BINS:.1f}",
                        "count": 0,
                        "mean_pred": 0.0,
                        "actual_freq": 0.0,
                        "gap": 0.0,
                    }
                )
                continue

            nk = len(bin_data)
            mean_pred = sum(p for p, _ in bin_data) / nk
            actual_freq = sum(o for _, o in bin_data) / nk

            rel += nk * (actual_freq - mean_pred) ** 2
            res += nk * (actual_freq - base_rate) ** 2

            calibration_curve.append(
                {
                    "bin": f"{i/CALIBRATION_BINS:.1f}-{(i+1)/CALIBRATION_BINS:.1f}",
                    "count": nk,
                    "mean_pred": round(mean_pred, 4),
                    "actual_freq": round(actual_freq, 4),
                    "gap": round(abs(actual_freq - mean_pred), 4),
                }
            )

        rel /= n
        res /= n

        # ═══ Skill Score ═══
        # BSS = 1 - BS/UNC → 1.0 = perfect, 0.0 = no skill, <0 = worse than base
        skill = 1.0 - (brier_total / unc) if unc > 0 else 0.0

        # ═══ Worst bins (largest calibration gap) ═══
        non_empty = [b for b in calibration_curve if b["count"] > 0]
        worst_bins = sorted(non_empty, key=lambda x: -x["gap"])[:3]

        return {
            "brier_score": round(brier_total, 4),
            "reliability": round(rel, 4),
            "resolution": round(res, 4),
            "uncertainty": round(unc, 4),
            "skill_score": round(skill, 4),
            "base_rate": round(base_rate, 4),
            "sample_count": n,
            "calibration_curve": calibration_curve,
            "worst_bins": worst_bins,
            "hours": hours,
            "source": source or "all",
            "target": "<0.30 (3mo), <0.25 (6mo)",
        }

    def format_report(self, report: dict) -> str:
        """Format Brier report for Telegram display (HTML)."""
        if "error" in report:
            return f"📊 Brier: {report['error']} (n={report.get('sample_count', 0)})"

        bs = report["brier_score"]
        # Rating
        if bs < 0.15:
            rating = "🏆 Superforecaster"
        elif bs < 0.25:
            rating = "🎯 Excellent"
        elif bs < 0.30:
            rating = "✅ Good"
        elif bs < 0.40:
            rating = "⚠️ Mediocre"
        else:
            rating = "❌ Poor"

        lines = [
            f"📊 <b>Brier Score Report</b> ({report['source']}, {report['hours']}h)",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"🎯 Brier Score: <b>{bs:.4f}</b> {rating}",
            f"📐 Reliability: {report['reliability']:.4f} (lower=better calibrated)",
            f"🔬 Resolution: {report['resolution']:.4f} (higher=better distinction)",
            f"🎲 Uncertainty: {report['uncertainty']:.4f}",
            f"⚡ Skill Score: {report['skill_score']:.4f}",
            f"📈 Base Rate: {report['base_rate']:.3f}",
            f"📊 Samples: {report['sample_count']}",
            "",
            "<b>Calibration Curve:</b>",
        ]

        for b in report["calibration_curve"]:
            if b["count"] == 0:
                continue
            bar = "█" * max(1, int(b["count"] / max(1, report["sample_count"]) * 20))
            gap_indicator = " ⚠️" if b["gap"] > 0.10 else ""
            # Phase 78-fix: HTML-escape bin label to avoid parse errors
            bin_label = (
                str(b["bin"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            lines.append(
                f"  {bin_label}: pred={b['mean_pred']:.2f} "
                f"act={b['actual_freq']:.2f} "
                f"n={b['count']} {bar}{gap_indicator}"
            )

        if report.get("worst_bins"):
            lines.append("")
            lines.append("<b>Worst Calibration Gaps:</b>")
            for wb in report["worst_bins"]:
                wb_label = (
                    str(wb["bin"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                )
                lines.append(
                    f"  {wb_label}: gap={wb['gap']:.3f} "
                    f"(pred={wb['mean_pred']:.2f} vs actual={wb['actual_freq']:.2f})"
                )

        return "\n".join(lines)
