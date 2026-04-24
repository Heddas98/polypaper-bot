"""T4.5-C -- Per-strategy PnL + slippage audit.

Reads `data_store/polypaper.db` and produces a per-strategy report:
  - n trades, win rate, total PnL, mean PnL/trade
  - mean realized_slippage, p50, p10, p90
  - **Net edge after slippage** (mean_pnl + slippage_cost_estimate)

Goal: identify which specific strategy_id underperforms after slippage
is accounted for. T4.5 calibration showed contrarian aggregate
mean=-5.69%; this script breaks that down per strategy_id to find:
  - Which contrarian strategies are still profitable despite slippage?
  - Which should be paused?
  - Which assets are problematic per strategy?

Usage:
    py -3.11 scripts/audit_strategy_pnl.py
    py -3.11 scripts/audit_strategy_pnl.py --type contrarian
    py -3.11 scripts/audit_strategy_pnl.py --asset SOL
    py -3.11 scripts/audit_strategy_pnl.py --top 20

Read-only DB access. Bot-safe.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "data_store" / "polypaper.db"


def load_executions_with_strategies(db_path: Path) -> List[Dict[str, Any]]:
    """Join executions with strategies for full per-strategy view."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30.0)
    try:
        sql = """
        SELECT
          e.id, e.strategy_id, e.direction, e.trade_amount,
          e.pnl, e.payout, e.result, e.realized_slippage,
          e.created_at, e.closed_at,
          s.label, s.asset, s.timeframe, s.strategy_type, s.status
        FROM executions e
        LEFT JOIN strategies s ON e.strategy_id = s.id
        WHERE e.closed_at IS NOT NULL
          AND e.realized_slippage IS NOT NULL
          AND e.status != 'pending'
        """
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def aggregate_per_strategy(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_strat: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sid = r.get("strategy_id") or "<no_strategy_id>"
        by_strat.setdefault(sid, []).append(r)

    out: Dict[str, Dict[str, Any]] = {}
    for sid, group in by_strat.items():
        first = group[0]
        pnls = [float(r.get("pnl") or 0) for r in group]
        slips = [float(r.get("realized_slippage") or 0) for r in group]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)

        slip_sorted = sorted(slips)
        n = len(group)

        out[sid] = {
            "strategy_id": sid,
            "label": first.get("label") or "",
            "asset": first.get("asset") or "?",
            "timeframe": first.get("timeframe") or "?",
            "strategy_type": first.get("strategy_type") or "?",
            "status": first.get("status") or "?",
            "n": n,
            "wins": wins,
            "losses": losses,
            "wr_pct": round(wins / n * 100, 1) if n else 0,
            "total_pnl": round(sum(pnls), 4),
            "mean_pnl": round(statistics.mean(pnls), 4) if pnls else 0,
            "median_pnl": round(statistics.median(pnls), 4) if pnls else 0,
            "mean_slip_pct": round(statistics.mean(slips), 3) if slips else 0,
            "p10_slip": round(slip_sorted[int(0.1 * (n - 1))], 3) if n > 1 else 0,
            "p50_slip": round(slip_sorted[n // 2], 3) if n else 0,
            "p90_slip": round(slip_sorted[int(0.9 * (n - 1))], 3) if n > 1 else 0,
        }
    return out


def filter_and_sort(
    aggregates: Dict[str, Dict[str, Any]],
    type_filter: Optional[str],
    asset_filter: Optional[str],
    top: int,
) -> List[Dict[str, Any]]:
    items = list(aggregates.values())
    if type_filter:
        items = [a for a in items
                 if (a["strategy_type"] or "").lower() == type_filter.lower()]
    if asset_filter:
        items = [a for a in items
                 if (a["asset"] or "").upper() == asset_filter.upper()]
    # Sort by total_pnl asc (worst first --> make pause candidates obvious)
    items.sort(key=lambda a: a["total_pnl"])
    return items[:top] if top > 0 else items


def render_table(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "(no rows match filter)"
    lines = []
    lines.append(
        f"{'sid':<10} {'label':<22} {'asset':<5} {'tf':<4} {'type':<12} "
        f"{'st':<8} {'n':>4} {'wr%':>5} {'totPnL':>9} {'mPnL':>7} "
        f"{'mSlp%':>7} {'p10S':>6} {'p90S':>6}"
    )
    lines.append("-" * 130)
    for a in items:
        sid_short = a["strategy_id"][:8] if a["strategy_id"] else "?"
        label = (a["label"] or "")[:22]
        lines.append(
            f"{sid_short:<10} {label:<22} {a['asset']:<5} "
            f"{a['timeframe']:<4} {a['strategy_type']:<12} "
            f"{a['status']:<8} {a['n']:>4} {a['wr_pct']:>5.1f} "
            f"{a['total_pnl']:>9.2f} {a['mean_pnl']:>7.4f} "
            f"{a['mean_slip_pct']:>7.2f} {a['p10_slip']:>6.2f} "
            f"{a['p90_slip']:>6.2f}"
        )
    return "\n".join(lines)


def render_summary_buckets(items: List[Dict[str, Any]]) -> str:
    """Roll-up: total PnL & avg slippage by strategy_type & asset."""
    by_type: Dict[str, Dict[str, float]] = {}
    by_asset: Dict[str, Dict[str, float]] = {}

    def _accum(buf: Dict[str, Dict[str, float]], key: str, a: Dict[str, Any]):
        slot = buf.setdefault(key, {"n": 0, "total_pnl": 0.0, "slips": []})
        slot["n"] += a["n"]
        slot["total_pnl"] += a["total_pnl"]
        slot["slips"].append((a["mean_slip_pct"], a["n"]))

    for a in items:
        _accum(by_type, a["strategy_type"], a)
        _accum(by_asset, a["asset"], a)

    def _fmt(buf, title):
        lines = [f"\n## {title}",
                 f"{'bucket':<14} {'n':>5} {'totPnL':>10} {'avg_slip%':>10}"]
        lines.append("-" * 50)
        for k, v in sorted(buf.items(), key=lambda kv: -kv[1]["total_pnl"]):
            # Weighted avg slippage
            tot_n = sum(n for _, n in v["slips"])
            wavg = (sum(s * n for s, n in v["slips"]) / tot_n) if tot_n else 0
            lines.append(
                f"{str(k)[:14]:<14} {v['n']:>5} "
                f"{v['total_pnl']:>10.2f} {wavg:>10.2f}"
            )
        return "\n".join(lines)

    return _fmt(by_type, "Roll-up by strategy_type") + "\n" + \
           _fmt(by_asset, "Roll-up by asset")


def main() -> int:
    parser = argparse.ArgumentParser(description="Per-strategy PnL audit")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--type", default=None,
                        help="filter by strategy_type (e.g. contrarian)")
    parser.add_argument("--asset", default=None,
                        help="filter by asset (e.g. SOL, ETH, BTC)")
    parser.add_argument("--top", type=int, default=20,
                        help="top N rows (sorted worst PnL first)")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"ERROR: DB not found: {db}", file=sys.stderr)
        return 1

    print(f"[audit] Loading from {db}...")
    rows = load_executions_with_strategies(db)
    print(f"[audit] Loaded {len(rows)} executions across "
          f"{len({r.get('strategy_id') for r in rows})} strategies")

    agg = aggregate_per_strategy(rows)
    items = filter_and_sort(agg, args.type, args.asset, args.top)

    filt = []
    if args.type:
        filt.append(f"type={args.type}")
    if args.asset:
        filt.append(f"asset={args.asset}")
    if args.top:
        filt.append(f"top={args.top}")
    print()
    print("=" * 130)
    print(f"Per-strategy detail (sorted by total_pnl ASC -- worst first)"
          f"  {'(' + ', '.join(filt) + ')' if filt else ''}")
    print("=" * 130)
    print(render_table(items))

    # Always show roll-up across the FULL aggregate (not filtered top)
    print()
    print("=" * 130)
    print("Roll-up summary (across ALL strategies, not filter-limited)")
    print("=" * 130)
    print(render_summary_buckets(list(agg.values())))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
