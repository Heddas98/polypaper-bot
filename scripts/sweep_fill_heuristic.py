"""T4.6 -- Backtest sweep parity: heuristic vs empirical fill_model values.

Runs the SAME ReplayEngine backtest twice with different ENV overrides
on `backtest/simulation/fill_model.py` constants. Compares PnL, WR,
trade count, mean PnL/trade. Acceptance criterion (T4.6 spec):
  |delta_pnl_pct| < 5% AND direction consistent.

Why: T4.5 calibration showed empirical p90 slippage = +2.30% (vs the
heuristic FILL_SPREAD_COST=0.005). This script quantifies how much
the sim diverges from live and whether the heuristic is too pessimistic
or optimistic for backtest realism.

Usage (Windows):
    py -3.11 scripts/sweep_fill_heuristic.py
    py -3.11 scripts/sweep_fill_heuristic.py --strategy classic_btc_5m
    py -3.11 scripts/sweep_fill_heuristic.py --markets 50

Output:
    Console: side-by-side stats table + verdict (PASS/FAIL/INVESTIGATE)
    JSON:    backtest/calibration/sweep_fill_heuristic_<TS>.json

Read-only DB. Bot-safe.

NOTE: Module reload trick -- fill_model.py reads ENV at top-level
constant init time. Setting ENV mid-process needs `importlib.reload`
to re-evaluate constants. We do this between runs.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data_store" / "polypaper.db"
DEFAULT_OUT = REPO_ROOT / "backtest" / "calibration"

# Ensure repo root in sys.path so `import backtest.*` / `import db.*` works
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ENV pairs:
#   HEURISTIC  = current code defaults (T4.2 Faz A baseline)
#   EMPIRICAL  = T4.5 calibration findings (1082 trade analysis)
HEURISTIC_ENV = {
    "FILL_SPREAD_COST": "0.005",  # 0.5% (current default)
    "FILL_IMPACT_SCALE": "0.01",  # 1% (current default)
    "FILL_LATENCY_DRIFT_BPS_PER_MS": "0.08",
}
EMPIRICAL_ENV = {
    "FILL_SPREAD_COST": "0.023",  # T4.5 weighted p90 ~= 2.3%
    "FILL_IMPACT_SCALE": "0.025",  # ~1 stdev above mean
    "FILL_LATENCY_DRIFT_BPS_PER_MS": "0.04",  # T4.7 telemetry: half heuristic
}


async def run_backtest(
    env_overrides: Dict[str, str], label: str, strategy_name: str, markets: int, trade_amount: float
) -> Dict[str, Any]:
    """Apply ENV overrides + reload fill_model + run a backtest."""
    print(f"\n[{label}] Setting ENV: {env_overrides}")
    saved = {}
    for k, v in env_overrides.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v

    try:
        # Reload fill_model so module-top constants pick up the new ENV
        import backtest.simulation.fill_model as _fm

        importlib.reload(_fm)
        print(
            f"[{label}] fill_model reloaded -- "
            f"SPREAD_COST={_fm.FillSimulator.SPREAD_COST}, "
            f"IMPACT_SCALE={getattr(_fm.FillSimulator, 'IMPACT_SCALE', 'N/A')}"
        )

        # Lazy import to avoid loading deps until needed
        from backtest.replay_engine import ReplayConfig, ReplayEngine
        from db.database import Database

        db = Database(str(DEFAULT_DB))
        await db.initialize()
        try:
            config = ReplayConfig(
                strategy_name=strategy_name,
                initial_balance=10000.0,
                trade_amount=trade_amount,
                fill_mode="real_orderbook",
                last_n=markets,
            )
            engine = ReplayEngine(db, config)
            print(f"[{label}] Running backtest " f"(strategy={strategy_name}, last_n={markets})...")
            stats = await engine.run()

            return {
                "label": label,
                "env": env_overrides,
                "total_trades": getattr(stats, "total_trades", 0),
                "wins": getattr(stats, "wins", 0),
                "losses": getattr(stats, "losses", 0),
                "wr_pct": (
                    getattr(stats, "wins", 0) / max(1, getattr(stats, "total_trades", 1)) * 100
                ),
                "total_pnl": round(getattr(stats, "total_pnl", 0.0), 4),
                "mean_pnl": round(
                    getattr(stats, "total_pnl", 0.0) / max(1, getattr(stats, "total_trades", 1)), 4
                ),
                "final_balance": round(getattr(stats, "final_balance", 10000.0), 2),
            }
        finally:
            try:
                await db.close()
            except (AttributeError, RuntimeError):
                pass

    finally:
        # Restore ENV (so a second run doesn't inherit)
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Compute deltas between two runs (B - A normalized)."""
    if a["total_pnl"] == 0:
        delta_pct = float("inf") if b["total_pnl"] != 0 else 0.0
    else:
        delta_pct = (b["total_pnl"] - a["total_pnl"]) / abs(a["total_pnl"]) * 100

    direction_consistent = (a["total_pnl"] >= 0 and b["total_pnl"] >= 0) or (
        a["total_pnl"] < 0 and b["total_pnl"] < 0
    )

    abs_delta_pct = abs(delta_pct) if delta_pct != float("inf") else 999

    if abs_delta_pct < 5.0 and direction_consistent:
        verdict = "PASS"
    elif abs_delta_pct < 15.0 and direction_consistent:
        verdict = "INVESTIGATE"
    else:
        verdict = "FAIL"

    return {
        "delta_pnl": round(b["total_pnl"] - a["total_pnl"], 4),
        "delta_pnl_pct": round(delta_pct, 2),
        "delta_trades": b["total_trades"] - a["total_trades"],
        "delta_wr_pp": round(b["wr_pct"] - a["wr_pct"], 2),
        "direction_consistent": direction_consistent,
        "verdict": verdict,
        "criterion": "|delta_pnl_pct| < 5% AND direction consistent",
    }


def render_console(run_a: Dict[str, Any], run_b: Dict[str, Any], delta: Dict[str, Any]) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("T4.6 Fill Heuristic Sweep -- HEURISTIC vs EMPIRICAL")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"{'metric':<20} {'HEURISTIC':>15} {'EMPIRICAL':>15} {'delta':>15}")
    lines.append("-" * 70)
    for k_pretty, k in [
        ("total_trades", "total_trades"),
        ("wins / losses", "wins"),
        ("wr_pct", "wr_pct"),
        ("total_pnl", "total_pnl"),
        ("mean_pnl", "mean_pnl"),
        ("final_balance", "final_balance"),
    ]:
        a_v = run_a.get(k, "-")
        b_v = run_b.get(k, "-")
        if isinstance(a_v, int | float) and isinstance(b_v, int | float):
            d = b_v - a_v
            lines.append(f"{k_pretty:<20} {a_v:>15.4f} {b_v:>15.4f} {d:>+15.4f}")
        else:
            lines.append(f"{k_pretty:<20} {a_v:>15} {b_v:>15} {'-':>15}")
    lines.append("")
    lines.append(f"  delta_pnl     : {delta['delta_pnl']:+.4f}")
    lines.append(f"  delta_pnl_pct : {delta['delta_pnl_pct']:+.2f}%")
    lines.append(
        f"  direction     : {'consistent' if delta['direction_consistent'] else 'FLIPPED'}"
    )
    lines.append(f"  criterion     : {delta['criterion']}")
    lines.append("")
    lines.append(f"  VERDICT       : {delta['verdict']}")
    lines.append("")
    if delta["verdict"] == "PASS":
        lines.append("  -> Sim heuristic LIVE'a yakin (delta < 5%). Backtest")
        lines.append("     karari production decision'a tasinabilir.")
    elif delta["verdict"] == "INVESTIGATE":
        lines.append("  -> Sim heuristic 5-15% sapiyor. Strategy karari'nda")
        lines.append("     edge kaybi/kazanci 1.5x faktorle ozetlenebilir.")
    else:
        lines.append("  -> Sim heuristic 15%+ sapma veya direction flip.")
        lines.append("     Backtest sonuclari yaniltici. fill_model")
        lines.append("     calibration update zorunlu (config/settings.py veya")
        lines.append("     .env override).")
    return "\n".join(lines)


async def amain() -> int:
    parser = argparse.ArgumentParser(description="T4.6 fill heuristic sweep")
    parser.add_argument("--strategy", default="hour_edge")
    parser.add_argument(
        "--markets", type=int, default=20, help="Last N markets to replay (default 20)"
    )
    parser.add_argument("--trade-amount", type=float, default=1.0)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    print(f"[t4.6] Strategy: {args.strategy}, markets: {args.markets}")
    print(f"[t4.6] HEURISTIC env: {HEURISTIC_ENV}")
    print(f"[t4.6] EMPIRICAL env: {EMPIRICAL_ENV}")

    run_a = await run_backtest(
        HEURISTIC_ENV,
        "HEURISTIC",
        args.strategy,
        args.markets,
        args.trade_amount,
    )
    run_b = await run_backtest(
        EMPIRICAL_ENV,
        "EMPIRICAL",
        args.strategy,
        args.markets,
        args.trade_amount,
    )
    delta = compare(run_a, run_b)

    print()
    print(render_console(run_a, run_b, delta))

    # Save JSON
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"sweep_fill_heuristic_{ts}.json"
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": args.strategy,
        "markets": args.markets,
        "heuristic": run_a,
        "empirical": run_b,
        "delta": delta,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n[t4.6] JSON written: {out_path.relative_to(REPO_ROOT)}")

    return 0 if delta["verdict"] != "FAIL" else 1


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
