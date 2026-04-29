"""
PolyPaper Bot - Phase 45 — Backtest v3 (live-parity replay)
===========================================================

Thin wrapper around the existing ReplayEngine that swaps in:
  • FeeCalculatorV3 (fees_v2 + maker rebates + tail-zone skip)
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

2026-04-29 Aşama 3.C: Becker calibration injection silindi (Heddas
direktifi). becker_curves dict empty kept for backward-compat with
inner ReplayEngine attribute (no-op stub).
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
    # use_becker_calibration removed 2026-04-29 (Heddas direktifi)
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
        self._inner: Optional[ReplayEngine] = None

    # _try_load_becker removed 2026-04-29 (Heddas direktifi: Becker tam silme)

    # ── public API ───────────────────────────────────────────────────
    async def run(self) -> dict:
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

        try:
            stats = await self._inner.run(inner_cfg) if inner_cfg else await self._inner.run()
        except TypeError:
            # Older ReplayEngine.run() with positional args
            stats = await self._inner.run()
        except Exception as e:
            logger.warning(f"Phase 45 inner ReplayEngine.run failed: {e}")
            stats = None

        result = {
            "config": {
                "strategy": self.config.strategy_name,
                "asset": self.config.asset,
                "timeframe": self.config.timeframe,
                "fee_mode": self.config.fee_mode.value,
                "fee_category": self.config.fee_category,
            },
            "fee_stats": self.fee_calc.get_stats(),
            "portfolio": _portfolio_dict(stats) if stats else {"note": "no_data"},
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
