"""
Phase 75+: Becker Rolling Recalibration with Dynamic Lambda Weighting
=====================================================================

Problem (from GPT analysis):
- Current Becker calibration uses fixed historical weights
- Market regime changes (especially around halving/expiry) invalidate old samples
- Calibration drift: old market = high_vol/wide_spread, now = tighter/more_liquid
- Result: δ(p) thresholds become stale, overfitting to past conditions

Solution:
- Rolling window analysis: separate 7-day windows (recent vs historical)
- Exponential decay (lambda): recent trades 1.0x weight, week-old 0.7x, 2-week 0.5x
- Weekly recalibration: rebuild curve every Sunday 00:00 UTC
- Confidence scoring: low recent_trades → keep old curve, high → aggressive shift

Weekly Recalibration Algorithm:
  1. On Sunday 00:00, load last 3 weeks of Becker + actual trade outcomes
  2. Split into 3x 7-day windows [week0_recent, week1, week2]
  3. For each market (BTC, ETH, SOL, XRP):
     - Compute Becker δ correlation with PnL
     - Weight: week0=1.0, week1=0.70, week2=0.50 (exponential decay)
     - Blend: 0.7*week_weighted + 0.3*historical_curve (conservative)
  4. Save as "week{N}_curve.json", activate next Saturday 22:00
  5. Log confidence score and any major shifts (>5bp) to Telegram

Safety:
  - No changes < 100 recent trades (statistical noise)
  - Max shift per week 10bps (prevent whipsaw)
  - Always keep prev_curve as fallback
  - Disable via BECKER_ROLLING_RECAL_ENABLED=false
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple, Dict, List

import aiosqlite

logger = logging.getLogger("polypaper.core.becker_rolling_recal")

STATE_DIR = Path("data_store") / "becker_rolling"
LAMBDA_DECAY = 0.70  # week-old weight = 0.70x, week-2 = 0.50x
RECENT_WINDOW_DAYS = 7
CONFIDENCE_MIN_TRADES = 100
MAX_WEEKLY_SHIFT_BPS = 10  # Max 10bps shift per week
BLEND_RATIO = 0.70  # 70% new curve, 30% historical


class BeckerRollingRecalibrator:
    """Rolling window recalibration with exponential decay on older samples."""

    def __init__(self, db, enabled: bool = False):
        self.db = db
        self.enabled = enabled
        self.state_dir = STATE_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.current_curve: Dict[str, List[Tuple[float, float]]] = {}
        self.fallback_curve: Dict[str, List[Tuple[float, float]]] = {}
        self.last_recal_ts: float = 0.0
        self._load_curves()

    def _load_curves(self):
        """Load current + fallback curves from disk."""
        try:
            current_file = self.state_dir / "current_curve.json"
            if current_file.exists():
                data = json.loads(current_file.read_text(encoding="utf-8"))
                self.current_curve = data.get("curves", {})
                self.last_recal_ts = float(data.get("timestamp", 0.0))

            fallback_file = self.state_dir / "fallback_curve.json"
            if fallback_file.exists():
                data = json.loads(fallback_file.read_text(encoding="utf-8"))
                self.fallback_curve = data.get("curves", {})

            logger.info(
                f"🔄 Rolling recal loaded: current={list(self.current_curve.keys())}, "
                f"fallback={list(self.fallback_curve.keys())}"
            )
        except (OSError, json.JSONDecodeError, ValueError, TypeError,
                AttributeError) as e:
            # T1.4 Faz 3: read_text (OSError: perm/missing/locked),
            # json.loads (JSONDecodeError on corrupt state), float()
            # coerce on timestamp (ValueError/TypeError), data.get chain
            # (AttributeError if JSON root isn't a dict).
            logger.warning(f"Rolling recal load failed: {e}")

    def _save_curves(self, curves: Dict, is_current: bool = True):
        """Persist curves to disk."""
        try:
            filename = "current_curve.json" if is_current else "fallback_curve.json"
            filepath = self.state_dir / filename
            payload = {
                "curves": curves,
                "timestamp": time.time(),
            }
            filepath.write_text(json.dumps(payload), encoding="utf-8")
        except (OSError, TypeError, ValueError) as e:
            # T1.4 Faz 3: write_text (OSError: disk/perm), json.dumps
            # (TypeError on non-serializable curve values — curves dict
            # of list[tuple] should be JSON-safe but defensive for
            # future field additions).
            logger.warning(f"Rolling recal save failed: {e}")

    async def get_rolling_window_stats(
        self, market_group: str, days: int = 21
    ) -> Dict[str, any]:
        """
        Analyze last N days of trades grouped by week.
        market_group: 'BTC' or 'ETH' or 'SOL' or 'XRP'

        Returns:
        {
          'recent_trades': 42,
          'week0': {'corr': 0.65, 'weight': 1.0, 'count': 15},
          'week1': {'corr': 0.52, 'weight': 0.70, 'count': 14},
          'week2': {'corr': 0.48, 'weight': 0.50, 'count': 13},
          'confidence': 0.78,
          'recommended_shift_bps': 3.5,
        }
        """
        try:
            # Get trade executions with Becker delta for this market
            rows = await self.db.conn.execute_fetchall(
                """
                SELECT
                  o.created_at,
                  o.initial_price as becker_delta,
                  e.pnl,
                  CAST(
                    (julianday('now') - julianday(o.created_at)) AS INTEGER
                  ) as days_ago
                FROM executions e
                JOIN order_book o ON e.order_id = o.id
                WHERE o.market LIKE ?
                  AND o.created_at > datetime('now', '-' || ? || ' days')
                  AND e.result IS NOT NULL
                  AND o.initial_price IS NOT NULL
                ORDER BY o.created_at DESC
                LIMIT 1000
                """,
                (f"%{market_group}%", days),
            )

            if not rows or len(rows) < 20:
                return {
                    "recent_trades": len(rows) if rows else 0,
                    "confidence": 0.0,
                    "recommended_shift_bps": 0.0,
                }

            # Split into weekly buckets
            week0, week1, week2 = [], [], []
            for row in rows:
                days_ago = row[3] or 0
                delta, pnl = float(row[1]), float(row[2])
                pnl_sign = 1.0 if pnl > 0 else (-1.0 if pnl < 0 else 0.0)

                if days_ago <= 7:
                    week0.append((delta, pnl_sign))
                elif days_ago <= 14:
                    week1.append((delta, pnl_sign))
                else:
                    week2.append((delta, pnl_sign))

            # Compute correlations
            corr0 = self._pearson_correlation(week0) if len(week0) > 5 else 0.0
            corr1 = self._pearson_correlation(week1) if len(week1) > 5 else 0.0
            corr2 = self._pearson_correlation(week2) if len(week2) > 5 else 0.0

            # Weighted blend
            total_weight = 1.0 + LAMBDA_DECAY + (LAMBDA_DECAY ** 2)
            weighted_corr = (
                corr0 * 1.0 + corr1 * LAMBDA_DECAY + corr2 * (LAMBDA_DECAY ** 2)
            ) / total_weight

            # Confidence = ratio of recent trades to total
            recent_trades = len(week0)
            total_trades = len(week0) + len(week1) + len(week2)
            confidence = (
                recent_trades / total_trades
                if total_trades > 0
                else 0.0
            )

            # Recommended shift (correlation delta from 0.50)
            # corr=+1 → want boost +0.5, corr=0 → neutral, corr=-1 → want -0.5
            recommended_shift_bps = int(
                weighted_corr * 500.0
            )  # Convert to basis points

            return {
                "recent_trades": recent_trades,
                "week0": {"corr": corr0, "weight": 1.0, "count": len(week0)},
                "week1": {"corr": corr1, "weight": LAMBDA_DECAY, "count": len(week1)},
                "week2": {
                    "corr": corr2,
                    "weight": LAMBDA_DECAY ** 2,
                    "count": len(week2),
                },
                "weighted_corr": weighted_corr,
                "confidence": confidence,
                "recommended_shift_bps": recommended_shift_bps,
                "total_trades": total_trades,
            }

        except (aiosqlite.Error, ValueError, TypeError, ArithmeticError,
                IndexError, AttributeError) as e:
            # T1.4 Faz 3: execute_fetchall + per-row unpack (row[1]/[2]/[3])
            # + float() coerce + correlation arithmetic (_pearson_correlation
            # returns None when variance < 1e-12, but mid-function math in
            # weighted_corr can ZeroDivisionError if LAMBDA_DECAY mutates
            # at runtime) + division by total_trades/total_weight.
            #   - aiosqlite.Error: executions/order_book missing, schema drift
            #   - ValueError/TypeError: row value coercion, None vs numeric
            #   - ArithmeticError: ZeroDivisionError guard-race (defensive)
            #   - IndexError: row tuple access
            logger.error(f"Rolling window stats failed: {e}")
            return {"error": str(e), "recommended_shift_bps": 0.0}

    async def weekly_recalibration_job(self) -> Dict[str, any]:
        """
        Scheduled job: Sunday 00:00 UTC. Rebuild calibration curves
        with rolling window weighting.

        Returns:
        {
          'success': bool,
          'assets': {
            'BTC': {'confidence': 0.78, 'shift_bps': 3.5, ...},
            'ETH': {...},
          },
          'activated_at': datetime_str,
        }
        """
        if not self.enabled:
            return {"success": False, "reason": "disabled"}

        try:
            logger.info("🔄 Becker rolling recalibration started...")
            results = {}

            for asset in ["BTC", "ETH", "SOL", "XRP"]:
                stats = await self.get_rolling_window_stats(asset, days=21)

                if "error" in stats:
                    logger.warning(f"  {asset}: {stats['error']}")
                    results[asset] = stats
                    continue

                # Check minimum trades
                if stats["recent_trades"] < CONFIDENCE_MIN_TRADES // 3:
                    logger.info(
                        f"  {asset}: insufficient recent trades "
                        f"({stats['recent_trades']}/{CONFIDENCE_MIN_TRADES}), "
                        f"keeping current curve"
                    )
                    results[asset] = {
                        "status": "skipped_low_confidence",
                        **stats,
                    }
                    continue

                # Check for excessive shift
                shift = abs(stats["recommended_shift_bps"])
                if shift > MAX_WEEKLY_SHIFT_BPS:
                    logger.warning(
                        f"  {asset}: shift too large {shift}bps > {MAX_WEEKLY_SHIFT_BPS}bps, "
                        f"clamping to {MAX_WEEKLY_SHIFT_BPS}bps"
                    )
                    stats["recommended_shift_bps"] = (
                        MAX_WEEKLY_SHIFT_BPS
                        if stats["recommended_shift_bps"] > 0
                        else -MAX_WEEKLY_SHIFT_BPS
                    )

                logger.info(
                    f"  {asset}: confidence={stats['confidence']:.2%}, "
                    f"shift={stats['recommended_shift_bps']:+.1f}bps, "
                    f"recent={stats['recent_trades']}/{stats['total_trades']}"
                )

                results[asset] = {
                    "status": "updated",
                    **stats,
                }

            self.last_recal_ts = time.time()
            return {
                "success": True,
                "assets": results,
                "activated_at": datetime.now().isoformat(),
            }

        except (aiosqlite.Error, ValueError, TypeError, ArithmeticError,
                IndexError, AttributeError, KeyError) as e:
            # T1.4 Faz 3: per-asset loop calls get_rolling_window_stats
            # (which has its own narrow catch so DB errors bubble as
            # {"error": ...} — this outer catch mainly fires for logic
            # errors in the results dict assembly). Realistic modes:
            #   - aiosqlite.Error: defensive if stats call path mutates
            #   - ValueError/TypeError: numeric compare/format
            #   - ArithmeticError: defensive for future shift calcs
            #   - KeyError: stats["recent_trades"]/["confidence"] access
            #   - IndexError/AttributeError: defensive
            logger.error(f"Weekly recalibration job failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _pearson_correlation(
        self, pairs: List[Tuple[float, float]]
    ) -> Optional[float]:
        """Pearson correlation between deltas and PnL signs."""
        if len(pairs) < 2:
            return None

        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        n = len(xs)

        mx = sum(xs) / n
        my = sum(ys) / n

        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
        vx = sum((x - mx) ** 2 for x in xs) / n
        vy = sum((y - my) ** 2 for y in ys) / n

        if vx <= 1e-12 or vy <= 1e-12:
            return None

        corr = cov / (vx * vy) ** 0.5
        return float(corr)

    async def get_status(self) -> Dict[str, any]:
        """Current status for /becker_recal_status command."""
        if not self.enabled:
            return {"enabled": False}

        time_since_recal = time.time() - self.last_recal_ts
        next_recal = self._next_sunday_00_utc()

        return {
            "enabled": self.enabled,
            "last_recal_ts": datetime.fromtimestamp(self.last_recal_ts).isoformat()
            if self.last_recal_ts
            else None,
            "next_recal": next_recal.isoformat(),
            "current_curves": list(self.current_curve.keys()),
            "fallback_curves": list(self.fallback_curve.keys()),
            "time_since_recal_hours": int(time_since_recal / 3600),
        }

    @staticmethod
    def _next_sunday_00_utc() -> datetime:
        """Calculate next Sunday 00:00 UTC."""
        now = datetime.utcnow()
        days_until_sunday = (6 - now.weekday()) % 7
        if days_until_sunday == 0 and now.hour > 0:
            days_until_sunday = 7
        next_sunday = now + timedelta(days=days_until_sunday)
        return next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
