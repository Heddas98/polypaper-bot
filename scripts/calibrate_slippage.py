"""T4.5 -- Empirical slippage calibration from production DB.

Reads `data_store/polypaper.db` executions table (settled trades only)
and computes per-bucket percentiles of `realized_slippage`. Output:

  1. Console: human-readable markdown tables
  2. `backtest/calibration/slippage_2026q2.json` -- machine-readable
     per-bucket p10/p50/p90/p99/n for:
       - maker vs taker
       - direction (UP/DOWN)
       - strategy_type (classic vs AI)
       - trade_amount bracket ($0-2, $2-5, $5-15, $15+)
       - regime_at_entry (if populated)
  3. `.env.example` override suggestions printed at the end

This DOES NOT mutate the DB (read-only connection). Safe to run while
bot is up; locks only during the SELECT.

Usage:
    py -3.11 scripts/calibrate_slippage.py
    py -3.11 scripts/calibrate_slippage.py --db PATH
    py -3.11 scripts/calibrate_slippage.py --quiet   # JSON only

Exit code:
    0: success (JSON written + console tables)
    1: DB not readable
    2: no settled trades (insufficient data)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data_store" / "polypaper.db"
DEFAULT_OUT = REPO_ROOT / "backtest" / "calibration" / "slippage_2026q2.json"


def _pct(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (p / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _bucket_amount(amt: Optional[float]) -> str:
    if amt is None:
        return "unknown"
    if amt < 2.0:
        return "$0-2"
    if amt < 5.0:
        return "$2-5"
    if amt < 15.0:
        return "$5-15"
    return "$15+"


def _stats(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {
        "n": len(s),
        "mean": round(statistics.mean(s), 4),
        "stdev": round(statistics.pstdev(s), 4) if len(s) > 1 else 0.0,
        "min": round(s[0], 4),
        "p10": round(_pct(s, 10), 4),
        "p50": round(_pct(s, 50), 4),
        "p90": round(_pct(s, 90), 4),
        "p99": round(_pct(s, 99), 4),
        "max": round(s[-1], 4),
    }


def load_executions(db_path: Path) -> List[Dict[str, Any]]:
    """Read settled executions with realized_slippage populated."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    except sqlite3.Error as e:
        print(f"ERROR: DB open failed: {e}", file=sys.stderr)
        return []

    try:
        # Detect which columns actually exist (schema may vary)
        cols_info = conn.execute("PRAGMA table_info(executions)").fetchall()
        col_names = {row[1] for row in cols_info}
        # Core columns needed
        select_cols = [
            "id",
            "direction",
            "trade_amount",
            "result",
            "realized_slippage",
            "created_at",
            "closed_at",
        ]
        optional = [
            "is_maker",
            "strategy_id",
            "regime_at_entry",
            "execution_price",
            "odds_threshold",
        ]
        for c in optional:
            if c in col_names:
                select_cols.append(c)

        # Only settled (closed_at NOT NULL) and slippage non-NULL
        sql = (
            f"SELECT {', '.join(select_cols)} FROM executions "
            "WHERE closed_at IS NOT NULL "
            "  AND realized_slippage IS NOT NULL "
            "  AND status != 'pending'"
        )
        cur = conn.execute(sql)
        rows = [dict(zip(select_cols, r, strict=False)) for r in cur.fetchall()]
        return rows
    finally:
        conn.close()


def load_strategy_map(db_path: Path) -> Dict[str, Dict[str, str]]:
    """Map strategy_id -> {asset, timeframe, strategy_type}."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error:
        return {}
    try:
        cur = conn.execute("SELECT id, asset, timeframe, strategy_type FROM strategies")
        return {
            r[0]: {"asset": r[1], "timeframe": r[2], "strategy_type": r[3]} for r in cur.fetchall()
        }
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def bucket_and_stats(
    rows: List[Dict[str, Any]],
    strat_map: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """Compute percentiles across bucket dimensions."""
    overall: List[float] = []
    by_maker: Dict[str, List[float]] = {"maker": [], "taker": [], "unknown": []}
    by_direction: Dict[str, List[float]] = {}
    by_amount: Dict[str, List[float]] = {}
    by_strategy_type: Dict[str, List[float]] = {}
    by_asset: Dict[str, List[float]] = {}
    by_regime: Dict[str, List[float]] = {}

    for r in rows:
        slip = r.get("realized_slippage")
        if slip is None:
            continue
        try:
            slip_f = float(slip)
        except (TypeError, ValueError):
            continue
        overall.append(slip_f)

        # Maker/taker
        im = r.get("is_maker")
        if im == 1 or im == "1" or im is True:
            by_maker["maker"].append(slip_f)
        elif im == 0 or im == "0" or im is False:
            by_maker["taker"].append(slip_f)
        else:
            by_maker["unknown"].append(slip_f)

        # Direction
        d = (r.get("direction") or "UNK").upper()
        by_direction.setdefault(d, []).append(slip_f)

        # Amount bucket
        amt_bucket = _bucket_amount(r.get("trade_amount"))
        by_amount.setdefault(amt_bucket, []).append(slip_f)

        # Strategy metadata
        sid = r.get("strategy_id")
        meta = strat_map.get(sid or "", {})
        stype = meta.get("strategy_type") or "unknown"
        asset = meta.get("asset") or "unknown"
        by_strategy_type.setdefault(stype, []).append(slip_f)
        by_asset.setdefault(asset, []).append(slip_f)

        # Regime (Phase 79+)
        reg = r.get("regime_at_entry") or "unknown"
        by_regime.setdefault(reg, []).append(slip_f)

    return {
        "overall": _stats(overall),
        "by_maker": {k: _stats(v) for k, v in by_maker.items()},
        "by_direction": {k: _stats(v) for k, v in by_direction.items()},
        "by_amount": {k: _stats(v) for k, v in by_amount.items()},
        "by_strategy_type": {k: _stats(v) for k, v in by_strategy_type.items()},
        "by_asset": {k: _stats(v) for k, v in by_asset.items()},
        "by_regime": {k: _stats(v) for k, v in by_regime.items()},
    }


def format_console(result: Dict[str, Any], total_rows: int) -> str:
    lines = []
    lines.append("=" * 70)
    lines.append("T4.5 Empirical Slippage Calibration")
    lines.append("=" * 70)
    lines.append(f"Total settled trades with slippage: {total_rows}")
    lines.append("")

    def _fmt_table(title: str, d: Dict[str, Any]) -> List[str]:
        out = [f"## {title}"]
        out.append(
            f"{'bucket':<20} {'n':>6} {'mean':>8} {'p10':>8} " f"{'p50':>8} {'p90':>8} {'p99':>8}"
        )
        out.append("-" * 70)
        for k, s in sorted(d.items(), key=lambda kv: -(kv[1].get("n") or 0)):
            n = s.get("n", 0)
            if n == 0:
                continue
            out.append(
                f"{str(k)[:20]:<20} {n:>6} "
                f"{s['mean']:>8.4f} {s['p10']:>8.4f} "
                f"{s['p50']:>8.4f} {s['p90']:>8.4f} {s['p99']:>8.4f}"
            )
        return out

    # Overall
    ov = result["overall"]
    lines.append("## Overall")
    lines.append(
        f"  n={ov.get('n', 0)} mean={ov.get('mean', 0):.4f} " f"stdev={ov.get('stdev', 0):.4f}"
    )
    lines.append(
        f"  p10={ov.get('p10', 0):.4f} p50={ov.get('p50', 0):.4f} "
        f"p90={ov.get('p90', 0):.4f} p99={ov.get('p99', 0):.4f}"
    )
    lines.append("")

    for title, key in [
        ("By Maker/Taker", "by_maker"),
        ("By Direction", "by_direction"),
        ("By Amount Bucket", "by_amount"),
        ("By Strategy Type", "by_strategy_type"),
        ("By Asset", "by_asset"),
        ("By Regime at Entry", "by_regime"),
    ]:
        lines.extend(_fmt_table(title, result[key]))
        lines.append("")

    # .env.example override suggestions
    lines.append("=" * 70)
    lines.append("SUGGESTED .env OVERRIDES (based on data)")
    lines.append("=" * 70)
    taker_p50 = result["by_maker"].get("taker", {}).get("p50")
    taker_p90 = result["by_maker"].get("taker", {}).get("p90")
    maker_p50 = result["by_maker"].get("maker", {}).get("p50")
    if taker_p50 is not None:
        lines.append(
            f"FILL_SPREAD_COST={max(0.001, abs(taker_p50)):.4f}"
            f"    # taker p50 |slip| (was 0.005 heuristic)"
        )
    if taker_p90 is not None:
        lines.append(
            f"FILL_IMPACT_SCALE={max(0.001, abs(taker_p90)):.4f}"
            f"    # taker p90 |slip| (Almgren-Chriss scale)"
        )
    if maker_p50 is not None:
        lines.append(
            f"# maker p50: {maker_p50:.4f} "
            f"(reference -- maker fills are better; adjust fee_rebate if needed)"
        )
    lines.append("")
    lines.append(
        "Review fill_model.py heuristics vs above and update "
        "config/settings.py or .env overrides accordingly."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="T4.5 slippage calibration")
    parser.add_argument("--db", default=str(DEFAULT_DB), help=f"DB path (default: {DEFAULT_DB})")
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help=f"JSON output (default: {DEFAULT_OUT})"
    )
    parser.add_argument("--quiet", action="store_true", help="JSON-only mode (no console tables)")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1

    if not args.quiet:
        print(f"[calibrate] Loading executions from {db}...")
    rows = load_executions(db)
    if not rows:
        print(f"ERROR: no settled trades with realized_slippage in {db}", file=sys.stderr)
        return 2

    if not args.quiet:
        print(f"[calibrate] Loaded {len(rows)} rows. " f"Loading strategy map...")
    strat_map = load_strategy_map(db)
    if not args.quiet:
        print(f"[calibrate] Strategy map: {len(strat_map)} entries")

    result = bucket_and_stats(rows, strat_map)

    if not args.quiet:
        print(format_console(result, len(rows)))

    # Write JSON output
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "db_path": str(db),
        "total_rows": len(rows),
        "buckets": result,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[calibrate] JSON written: {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
