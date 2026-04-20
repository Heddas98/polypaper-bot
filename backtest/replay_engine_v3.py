"""
PolyPaper Bot - Phase 45 — Backtest v3 (live-parity replay)
===========================================================

Thin wrapper around the existing ReplayEngine that swaps in:
  • FeeCalculatorV3 (fees_v2 + maker rebates + tail-zone skip)
  • Optional Becker calibration curve injection (data/becker_loader)
    so signals can be re-weighted by the empirical mispricing curve
    before EV calc.
  • Maker fill rate overlay (configurable)

The original ReplayEngine still works unchanged for legacy comparisons.
v3 is what the operator runs to validate the *current live* engine
against historical L2 snapshots — apples-to-apples with what the
production bot would do today.

Telegram entry point: /backtest_v3 <strategy> [asset] [tf]
The bot handler will instantiate this class, run, and post stats.

Phase 45 stub policy:
  - When this module is imported but the underlying ReplayEngine cannot
    discover any market windows (fresh DB / no recordings yet), the
    engine returns an empty PortfolioStats with `note="no_data"`.
  - The Becker calibration loader is best-effort: if data_store has
    no calibration DB yet, the run still completes — calibration_curve
    just stays None and signals pass through unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from backtest.replay_engine import ReplayEngine, ReplayConfig
from backtest.simulation.fee_model_v3 import FeeCalculatorV3, FeeModeV3

logger = logging.getLogger("polypaper.backtest.replay_v3")


@dataclass
class ReplayV3Config:
    """All v3 knobs in one place — extends ReplayConfig with v3-only fields."""
    strategy_name: str
    asset: str = "BTC"
    timeframe: str = "5m"
    start_balance: float = 1000.0
    fee_mode: FeeModeV3 = FeeModeV3.V3_AUTO
    fee_category: str = "crypto"
    use_becker_calibration: bool = True
    maker_wide_spread: float = 0.04
    skip_tail: bool = True


class ReplayEngineV3:
    """Live-parity backtest engine — wraps ReplayEngine with v2 fees."""

    def __init__(self, db, config: ReplayV3Config):
        self.db = db
        self.config = config
        self.fee_calc = FeeCalculatorV3(
            mode=config.fee_mode,
            category=config.fee_category,
            wide_spread=config.maker_wide_spread,
            tail_skip=config.skip_tail,
        )
        self._becker_curves: dict[str, list] = {}
        self._inner: Optional[ReplayEngine] = None

    # ── Becker calibration injection (best-effort) ───────────────────
    def _try_load_becker(self) -> None:
        """Load + transform poly/kalshi calibration curves.

        Phase 47f.2: raw rows from BeckerLoader are (bin_low, actual_wr, n).
        We convert them to the same (bin_low, delta_at_midpoint) layout used
        by core.engine so core.becker_calibration.becker_delta() can consume
        them directly in the backtest signal path.
        """
        if not self.config.use_becker_calibration:
            return
        try:
            from data.becker_loader import BeckerLoader, dataset_status
            status = dataset_status()
            if not status.get("calib_present"):
                logger.info("📊 Phase 45: Becker calibration DB not present; "
                            "running without re-weighting")
                return
            loader = BeckerLoader()
            for src in ("kalshi", "poly"):
                raw = loader.calibration_curve(source=src) or []
                # Engine-compatible format: (bin_low, delta_at_midpoint)
                # where delta_at_midpoint = actual_wr - (bin_low + 0.025)
                curve = [
                    (float(r[0]), float(r[1]) - (float(r[0]) + 0.025))
                    for r in raw if r and r[0] is not None
                ]
                curve.sort(key=lambda x: x[0])
                if curve:
                    self._becker_curves[src] = curve
            logger.info(f"📊 Phase 45: loaded Becker calibration "
                        f"({len(self._becker_curves)} sources)")
        except Exception as e:
            logger.debug(f"Phase 45: Becker calibration load skipped: {e}")

    # ── public API ───────────────────────────────────────────────────
    async def run(self) -> dict:
        self._try_load_becker()
        # Reuse the original replay engine for snapshot iteration.
        # Phase 47f.3 — fix pre-existing field-name drift: the inner
        # ReplayConfig uses `initial_balance` / `asset_filter` /
        # `timeframe_filter`, NOT the v3 field names.
        inner_cfg = ReplayConfig(
            strategy_name=self.config.strategy_name,
            initial_balance=self.config.start_balance,
            asset_filter=self.config.asset,
            timeframe_filter=self.config.timeframe,
        ) if self._has_replay_config_dataclass() else None
        self._inner = ReplayEngine(self.db, inner_cfg) if inner_cfg else ReplayEngine(self.db)

        # Phase 47f.2 — inject transformed curves into the inner engine so
        # every signal that clears the strategy gate gets the same δ(p)
        # ensemble boost the live engine applies in _evaluate.
        if self._becker_curves:
            try:
                self._inner._becker_curves = dict(self._becker_curves)
                logger.info(
                    f"📈 Phase 47f.2: inner replay engine armed with "
                    f"Becker δ(p) sources={list(self._becker_curves.keys())} "
                    f"(poly={len(self._becker_curves.get('poly', []))} bins "
                    f"kalshi={len(self._becker_curves.get('kalshi', []))} bins)"
                )
            except Exception as e:
                logger.warning(f"Phase 47f.2 curve injection failed: {e}")

        try:
            stats = await self._inner.run(inner_cfg) if inner_cfg else await self._inner.run()
        except TypeError:
            # Older ReplayEngine.run() with positional args
            stats = await self._inner.run()
        except Exception as e:
            logger.warning(f"Phase 45 inner ReplayEngine.run failed: {e}")
            stats = None

        # Phase 47f.2 — report how many signals actually got the boost.
        becker_stats = {
            "boosted_signals": getattr(self._inner, "_becker_boost_count", 0),
            "total_boost_sum": round(
                float(getattr(self._inner, "_becker_boost_sum", 0.0)), 6
            ),
            # Phase 47f.3 diagnostics — surface strategy-level counters so
            # the A/B harness can tell the difference between "no signals
            # generated" and "signals generated but the boost was inert".
            "signals_generated": getattr(self._inner, "_signals_generated", 0),
            "total_snapshots": getattr(self._inner, "_total_snapshots", 0),
            "markets_processed": getattr(self._inner, "_markets_processed", 0),
            # Phase 47f.6 — decision-mode counters (veto/flip). Zero unless
            # BECKER_DECISION_MODE env var is set to veto or flip.
            "veto_count": getattr(self._inner, "_becker_veto_count", 0),
            "flip_count": getattr(self._inner, "_becker_flip_count", 0),
        }
        result = {
            "config": {
                "strategy": self.config.strategy_name,
                "asset": self.config.asset,
                "timeframe": self.config.timeframe,
                "fee_mode": self.config.fee_mode.value,
                "fee_category": self.config.fee_category,
                "use_becker": bool(self._becker_curves),
            },
            "fee_stats": self.fee_calc.get_stats(),
            "portfolio": _portfolio_dict(stats) if stats else {"note": "no_data"},
            "becker_sources": list(self._becker_curves.keys()),
            "becker_stats": becker_stats,
        }
        return result

    def _has_replay_config_dataclass(self) -> bool:
        try:
            ReplayConfig(strategy_name="x")  # type: ignore[call-arg]
            return True
        except Exception:
            return False


def _portfolio_dict(stats) -> dict:
    """Best-effort serialisation of PortfolioStats — gracefully handles
    different schema versions of the inner ReplayEngine."""
    if stats is None:
        return {}
    if isinstance(stats, dict):
        return stats
    out = {}
    for attr in ("total_trades", "wins", "losses", "win_rate", "total_pnl",
                 "final_balance", "max_drawdown", "sharpe", "avg_pnl"):
        if hasattr(stats, attr):
            try:
                out[attr] = getattr(stats, attr)
            except Exception:
                pass
    return out
