"""T4.7-C regression guard — fill_model.py EMPIRICAL defaults.

Locks the three slippage heuristic defaults to their post-T4.7-C values
so an accidental revert back to the pre-empirical heuristic is caught.

Source of truth:
  backtest/simulation/fill_model.py
    SPREAD_COST     = 0.023  (was 0.005 pre-T4.7-C)
    LATENCY_DRIFT   = 0.04   (was 0.08 pre-T4.7-C), read at call-time
    IMPACT_SCALE    = 0.025  (was 0.01  pre-T4.7-C)

Motivation:
  T4.6-B sweep (classic strategy, 199 trades) proved HEURISTIC vs EMPIRICAL
  fill params yielded delta_pnl_pct = -33.68% (backtest was 4.6x too
  optimistic on realized slippage). T4.7-C elevates the defaults to
  EMPIRICAL so backtest PnL matches live PnL without per-run ENV overrides.

  If someone lowers SPREAD_COST back to 0.005 (or similar) without updating
  this test, we want a visible regression signal.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_fill_model():
    """Fresh import so class-body os.getenv() re-reads current env."""
    import backtest.simulation.fill_model as fm
    importlib.reload(fm)
    return fm


def test_spread_cost_default_is_empirical(monkeypatch):
    """FILL_SPREAD_COST default must be 0.023 (T4.7-C empirical)."""
    monkeypatch.delenv("FILL_SPREAD_COST", raising=False)
    fm = _reload_fill_model()
    assert fm.FillSimulator.SPREAD_COST == 0.023, (
        f"SPREAD_COST default regressed to {fm.FillSimulator.SPREAD_COST}. "
        "T4.7-C locked this at 0.023 based on T4.5 p90 empirical slippage. "
        "Reverting without updating this test risks 4.6x optimistic backtest."
    )


def test_impact_scale_default_is_empirical(monkeypatch):
    """FILL_IMPACT_SCALE default must be 0.025 (T4.7-C empirical)."""
    monkeypatch.delenv("FILL_IMPACT_SCALE", raising=False)
    fm = _reload_fill_model()
    assert fm.FillSimulator.IMPACT_SCALE == 0.025, (
        f"IMPACT_SCALE default regressed to {fm.FillSimulator.IMPACT_SCALE}. "
        "T4.7-C locked this at 0.025 (mean+1σ of T4.5 realized slippage). "
        "Pre-T4.7-C value 0.01 landed SOL/ETH ~2-3x optimistic."
    )


def test_latency_drift_default_is_empirical(monkeypatch):
    """FILL_LATENCY_DRIFT_BPS_PER_MS default must be 0.04 (T4.7-C empirical).

    This constant is read at call-time inside `simulate_fill`, so we probe
    it indirectly: set env unset, inspect the literal in source via the
    fall-through behavior.
    """
    monkeypatch.delenv("FILL_LATENCY_DRIFT_BPS_PER_MS", raising=False)
    _reload_fill_model()
    # Read back what os.getenv with that default returns
    default_str = os.getenv("FILL_LATENCY_DRIFT_BPS_PER_MS", "__UNSET__")
    assert default_str == "__UNSET__", (
        "test env pollution: FILL_LATENCY_DRIFT_BPS_PER_MS should be absent"
    )
    # Now force a call path that hits the default
    source = (REPO_ROOT / "backtest" / "simulation" / "fill_model.py").read_text(
        encoding="utf-8"
    )
    assert 'os.getenv("FILL_LATENCY_DRIFT_BPS_PER_MS", "0.04")' in source, (
        "LATENCY_DRIFT default regressed. T4.7-C locked at 0.04 bps/ms "
        "(half-heuristic) — see T4.6-B sweep + T4.5 calibration."
    )


def test_env_override_still_works(monkeypatch):
    """Legacy reproducibility — setting ENV=0.005 reverts to pre-T4.7-C."""
    monkeypatch.setenv("FILL_SPREAD_COST", "0.005")
    fm = _reload_fill_model()
    assert fm.FillSimulator.SPREAD_COST == 0.005, (
        "ENV override path broken — sweep script relies on this for "
        "HEURISTIC vs EMPIRICAL comparisons."
    )
    # Cleanup happens automatically via monkeypatch teardown
