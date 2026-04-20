"""
Phase 47f.8 - Wide Becker decision-mode sweep across all signal-producing
strategies. Goal: identify calibration-FRIENDLY vs HOSTILE clusters so the
47f.7 whitelist can be built from data, not guessing.

47f.6 proved decision-mode wiring works on 2 strategies:
  late_convergence  flip @ 0.01 -> +$17.05 PnL (+85%)  FRIENDLY
  opening_breakout  flip @ 0.01 -> -$7.76  PnL         HOSTILE

This sweep applies the same probe to the remaining 4 signal-producing
strategies from the 47f.4 run, at the best two thresholds (0.01, 0.02)
and both modes (veto, flip). We keep the two known strategies in the
grid as sanity controls.

Strategies (from 47f.4 on BTC 5m 300m):
  late_convergence     297t   CONTROL (expected FRIENDLY)
  opening_breakout     269t   CONTROL (expected HOSTILE)
  streak_reversal       10t   test
  calibration_arb        6t   test
  fade_rip               5t   test
  orderbook_imbalance    2t   test (too thin but included for completeness)

Grid: 6 strats x 2 modes x 2 thresholds = 24 B runs + 6 baselines.
At ~5s/run => ~2.5 min wall-clock.

We bump max_markets to 400 (the 47f.4 run reported 405 windows) to squeeze
every available trade for the thin strategies.

Usage (Windows):
  py -3.11 -u scripts\\ab_sweep_phase47f8.py [asset] [timeframe] [max_markets]

Defaults: asset=BTC timeframe=5m max_markets=400
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("phase47f8_sweep")

STRATEGIES = [
    "late_convergence",      # 47f.6 control: FRIENDLY
    "opening_breakout",      # 47f.6 control: HOSTILE
    "streak_reversal",
    "calibration_arb",
    "fade_rip",
    "orderbook_imbalance",
]
MODES = ["veto", "flip"]
THRESHOLDS = [0.01, 0.02]


def p(msg: str) -> None:
    print(msg, flush=True)


async def _capped_run(db, strategy: str, asset: str, timeframe: str,
                      max_markets: int, use_becker: bool) -> Optional[dict]:
    """Run ReplayEngineV3 once with a max_markets cap on the inner engine."""
    from backtest.replay_engine_v3 import ReplayEngineV3, ReplayV3Config
    from backtest.replay_engine import ReplayEngine as _RE

    cfg = ReplayV3Config(
        strategy_name=strategy,
        asset=asset,
        timeframe=timeframe,
        start_balance=10000.0,
        use_becker_calibration=use_becker,
    )
    engine = ReplayEngineV3(db=db, config=cfg)

    orig_run = _RE.run

    async def _wrapped_run(self, config=None):
        if config is not None:
            config.max_markets = max_markets
            return await orig_run(self, config)
        try:
            self.config.max_markets = max_markets
        except Exception:
            pass
        return await orig_run(self, config)

    _RE.run = _wrapped_run
    try:
        result = await engine.run()
    except Exception as e:
        logger.warning(f"[{strategy}] run failed (becker={use_becker}): {e}")
        return None
    finally:
        _RE.run = orig_run
    return result


def _pf(res: Optional[dict]) -> dict:
    if not isinstance(res, dict):
        return {"trades": 0, "wins": 0, "losses": 0, "wr": 0.0, "pnl": 0.0}
    pf = res.get("portfolio") or {}
    return {
        "trades": int(pf.get("total_trades") or 0),
        "wins": int(pf.get("wins") or 0),
        "losses": int(pf.get("losses") or 0),
        "wr": float(pf.get("win_rate") or 0.0),
        "pnl": float(pf.get("total_pnl") or 0.0),
    }


def _bk(res: Optional[dict]) -> dict:
    if not isinstance(res, dict):
        return {"signals": 0, "vetoed": 0, "flipped": 0}
    bs = res.get("becker_stats") or {}
    return {
        "signals": int(bs.get("signals_generated") or 0),
        "vetoed": int(bs.get("veto_count") or 0),
        "flipped": int(bs.get("flip_count") or 0),
    }


def _classify(best_d_pnl: float, worst_d_pnl: float,
              baseline_trades: int) -> str:
    """Label a strategy based on the sweep's best/worst delta PnL.

    Thresholds are tuned to the 47f.6 evidence:
      late_convergence: best +17.05 worst +0.00 -> FRIENDLY
      opening_breakout: best +0.00  worst -7.76 -> HOSTILE
    """
    if baseline_trades < 5:
        return "TOO_THIN"
    if best_d_pnl >= 0.50 and worst_d_pnl >= -0.50:
        return "FRIENDLY"
    if worst_d_pnl <= -0.50 and best_d_pnl <= 0.10:
        return "HOSTILE"
    if best_d_pnl >= 0.50 and worst_d_pnl <= -0.50:
        return "MIXED"
    return "NEUTRAL"


async def main() -> int:
    argv = sys.argv[1:]
    asset = argv[0] if len(argv) > 0 else "BTC"
    timeframe = argv[1] if len(argv) > 1 else "5m"
    max_markets = int(argv[2]) if len(argv) > 2 else 400

    p("=" * 90)
    p(f"  Phase 47f.8 - wide Becker decision-mode sweep (friendly/hostile cluster)")
    p(f"  asset={asset} timeframe={timeframe} max_markets={max_markets}")
    p(f"  strategies={STRATEGIES}")
    p(f"  modes={MODES} thresholds={THRESHOLDS}")
    n_b = len(STRATEGIES) * len(MODES) * len(THRESHOLDS)
    p(f"  grid = {len(STRATEGIES)} strats * {len(MODES)*len(THRESHOLDS)} (m,t) = {n_b} B + "
      f"{len(STRATEGIES)} A")
    p("=" * 90)
    p("")

    try:
        from db.database import Database
        db_path = "data_store/polypaper.db"
        if not Path(db_path).exists():
            fallback = ROOT / "data_store" / "polypaper.db"
            if fallback.exists():
                db_path = str(fallback)
            else:
                p(f"[FAIL] DB not found at {db_path} or {fallback}")
                return 1
        db = Database(db_path)
        await db.initialize()
        p(f"[OK] DB connected: {db_path}")
        p("")
    except Exception as e:
        p(f"[FAIL] DB connect error: {e}")
        return 1

    prev_mode = os.environ.get("BECKER_DECISION_MODE")
    prev_thr = os.environ.get("BECKER_DECISION_THRESHOLD")
    prev_wl = os.environ.get("BECKER_DECISION_STRATEGY_WHITELIST")

    rows: list[dict] = []
    baselines: dict[str, dict] = {}

    try:
        for strategy in STRATEGIES:
            # Phase 47f.7 fix: replay_engine default whitelist is `late_convergence`,
            # which would dead-gate every other strategy. Override per-iteration so
            # the strategy being tested is always in the whitelist.
            os.environ["BECKER_DECISION_STRATEGY_WHITELIST"] = strategy
            os.environ["BECKER_DECISION_MODE"] = "boost"
            os.environ["BECKER_DECISION_THRESHOLD"] = "0.0"
            p(f"[baseline] {strategy} ...")
            res_a = await _capped_run(db, strategy, asset, timeframe,
                                      max_markets, use_becker=True)
            pa = _pf(res_a)
            baselines[strategy] = pa
            p(f"           A: {pa['trades']}t {pa['wins']}w "
              f"pnl={pa['pnl']:+.4f} wr={pa['wr']:.2f}%")
            if pa["trades"] == 0:
                p(f"           [skip sweep] no signals produced for {strategy}")
                continue

            for mode in MODES:
                for thr in THRESHOLDS:
                    os.environ["BECKER_DECISION_MODE"] = mode
                    os.environ["BECKER_DECISION_THRESHOLD"] = str(thr)
                    p(f"[sweep]    {strategy} {mode}@{thr:.2f} ...")
                    res_b = await _capped_run(db, strategy, asset, timeframe,
                                              max_markets, use_becker=True)
                    pb = _pf(res_b)
                    bb = _bk(res_b)
                    rows.append({
                        "strategy": strategy, "mode": mode, "thr": thr,
                        "a_trades": pa["trades"], "a_pnl": pa["pnl"], "a_wr": pa["wr"],
                        "b_trades": pb["trades"], "b_wins": pb["wins"],
                        "b_pnl": pb["pnl"], "b_wr": pb["wr"],
                        "signals": bb["signals"],
                        "vetoed": bb["vetoed"], "flipped": bb["flipped"],
                        "delta_trades": pb["trades"] - pa["trades"],
                        "delta_pnl": pb["pnl"] - pa["pnl"],
                        "delta_wr": pb["wr"] - pa["wr"],
                    })
                    p(f"           B: {pb['trades']}t {pb['wins']}w "
                      f"pnl={pb['pnl']:+.4f} wr={pb['wr']:.2f}% | "
                      f"veto={bb['vetoed']} flip={bb['flipped']} | "
                      f"dPnL={pb['pnl']-pa['pnl']:+.4f} "
                      f"dTr={pb['trades']-pa['trades']:+d}")
    finally:
        if prev_mode is None:
            os.environ.pop("BECKER_DECISION_MODE", None)
        else:
            os.environ["BECKER_DECISION_MODE"] = prev_mode
        if prev_thr is None:
            os.environ.pop("BECKER_DECISION_THRESHOLD", None)
        else:
            os.environ["BECKER_DECISION_THRESHOLD"] = prev_thr
        if prev_wl is None:
            os.environ.pop("BECKER_DECISION_STRATEGY_WHITELIST", None)
        else:
            os.environ["BECKER_DECISION_STRATEGY_WHITELIST"] = prev_wl
        await db.close()

    # Per-strategy tables
    p("")
    for strategy in STRATEGIES:
        srows = [r for r in rows if r["strategy"] == strategy]
        pa = baselines.get(strategy, {})
        if not srows or not pa:
            continue
        p("=" * 100)
        p(f"  {strategy}  (A: {pa['trades']}t pnl={pa['pnl']:+.4f} wr={pa['wr']:.2f}%)")
        p("-" * 100)
        p(f"  {'mode':>5} {'thr':>5} | {'B_tr':>5} {'B_w':>4} {'B_pnl':>10} "
          f"{'B_wr%':>7} | {'dTr':>5} {'dPnL':>10} {'dWR%':>7} | "
          f"{'veto':>5} {'flip':>5}")
        p("-" * 100)
        for r in srows:
            p(f"  {r['mode']:>5} {r['thr']:>5.2f} | "
              f"{r['b_trades']:>5} {r['b_wins']:>4} "
              f"{r['b_pnl']:>+10.4f} {r['b_wr']:>7.2f} | "
              f"{r['delta_trades']:>+5d} {r['delta_pnl']:>+10.4f} "
              f"{r['delta_wr']:>+7.2f} | "
              f"{r['vetoed']:>5} {r['flipped']:>5}")
    p("=" * 100)
    p("")

    # Cluster classification
    p("CLUSTER CLASSIFICATION (data-driven whitelist seed)")
    p("=" * 100)
    p(f"  {'strategy':<22} {'baseline':<22} {'best cfg':<26} "
      f"{'worst cfg':<26} {'label':<10}")
    p("-" * 100)
    clusters: dict[str, list[dict]] = {
        "FRIENDLY": [], "HOSTILE": [], "MIXED": [], "NEUTRAL": [], "TOO_THIN": [],
    }
    for strategy in STRATEGIES:
        srows = [r for r in rows if r["strategy"] == strategy]
        pa = baselines.get(strategy, {})
        if not pa:
            continue
        if not srows:
            label = "TOO_THIN" if pa["trades"] < 5 else "ZERO_SIGNALS"
            clusters.setdefault(label, []).append({
                "strategy": strategy, "baseline": pa,
                "best": None, "worst": None, "label": label})
            p(f"  {strategy:<22} {pa['trades']}t pnl={pa['pnl']:+.2f}      "
              f"{'--':<26} {'--':<26} {label:<10}")
            continue
        best = max(srows, key=lambda r: r["delta_pnl"])
        worst = min(srows, key=lambda r: r["delta_pnl"])
        label = _classify(best["delta_pnl"], worst["delta_pnl"], pa["trades"])
        clusters[label].append({
            "strategy": strategy, "baseline": pa,
            "best": best, "worst": worst, "label": label,
        })
        best_str = f"{best['mode']}@{best['thr']:.2f} d={best['delta_pnl']:+.2f}"
        worst_str = f"{worst['mode']}@{worst['thr']:.2f} d={worst['delta_pnl']:+.2f}"
        baseline_str = f"{pa['trades']}t pnl={pa['pnl']:+.2f}"
        p(f"  {strategy:<22} {baseline_str:<22} {best_str:<26} "
          f"{worst_str:<26} {label:<10}")
    p("=" * 100)
    p("")

    # Whitelist seed
    p("WHITELIST SEED (47f.7 input)")
    p("-" * 100)
    friendly = clusters.get("FRIENDLY", [])
    if friendly:
        p("  BECKER_STRATEGY_WHITELIST candidates (enable decision-mode):")
        for item in friendly:
            best = item["best"]
            p(f"    - {item['strategy']}: {best['mode']}@{best['thr']:.2f} "
              f"(dPnL={best['delta_pnl']:+.4f}, dWR={best['delta_wr']:+.2f}pp)")
    else:
        p("  No FRIENDLY strategies found -- do NOT wire live decision-mode yet.")
    p("")
    hostile = clusters.get("HOSTILE", [])
    if hostile:
        p("  HOSTILE strategies (keep in default boost mode, do NOT whitelist):")
        for item in hostile:
            worst = item["worst"]
            p(f"    - {item['strategy']}: worst {worst['mode']}@{worst['thr']:.2f} "
              f"dPnL={worst['delta_pnl']:+.4f}")
    mixed = clusters.get("MIXED", [])
    if mixed:
        p("")
        p("  MIXED strategies (best cfg wins, worst cfg loses -- tighter sweep needed):")
        for item in mixed:
            p(f"    - {item['strategy']}")
    neutral = clusters.get("NEUTRAL", [])
    if neutral:
        p("")
        p("  NEUTRAL (no signal moves enough to matter):")
        for item in neutral:
            p(f"    - {item['strategy']}")
    thin = clusters.get("TOO_THIN", [])
    if thin:
        p("")
        p("  TOO_THIN (baseline <5 trades, statistically unreliable):")
        for item in thin:
            p(f"    - {item['strategy']} ({item['baseline']['trades']}t)")
    p("-" * 100)
    p("")
    p("Phase 47f.8 sweep COMPLETE.")
    return 0


def _run_guarded() -> int:
    try:
        return asyncio.run(main())
    except Exception as e:
        import traceback
        p(f"[FATAL] top-level exception: {e}")
        p(traceback.format_exc())
        return 2


if __name__ == "__main__":
    sys.exit(_run_guarded())
