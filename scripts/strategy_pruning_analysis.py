"""
Strategy Pruning Analysis — P0.4 (5AI Yol Haritası §5.1)
=========================================================

DB'den son 90 gün strateji performansını okur, Sharpe/PF/Expectancy/MaxDD
hesaplar, prune karar matrisini uygular.

Heddas yerel Windows'ta çalıştırılır (DB sandbox'ta boş):
    py -3.11 scripts/strategy_pruning_analysis.py [--dry-run] [--days 90] [--keep 3]

Çıktı:
- stdout: tablo + sıralı liste
- `evidence/strategy_pruning_<TS>.json` — full data
- `evidence/strategy_pruning_<TS>.md` — human-readable

Karar matrisi (5AI Yol Haritası §5.1 P0.4):
- KEEP if: Sharpe ≥ 1.2 AND ProfitFactor ≥ 1.3 AND trade_count ≥ 30
- PROTECTED (always KEEP): PROTECTED_STRATEGIES (core/ai_brain.py)
- max KEEP: --keep N (default 3)

Pruning sadece reporting + ENV önerisi; otomatik dosya silme YOK.
Heddas direktifi: "her değişiklik onay sonrası implement edilir".

Mevcut DB schema (memory'den):
- live_trades: strategy_label, direction, entry_price, amount, pnl, paper_pnl, result, created_at
- shadow_trades (varsa): aynı şema
- paper trades varsa engine'in kendi tablosu
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Karar eşikleri (5AI Yol Haritası §5.1)
SHARPE_MIN = float(os.getenv("PRUNE_SHARPE_MIN", "1.2"))
PF_MIN = float(os.getenv("PRUNE_PF_MIN", "1.3"))
TRADES_MIN = int(os.getenv("PRUNE_TRADES_MIN", "30"))
DEFAULT_KEEP = 3

# Memory'den (core/ai_brain.py:106)
PROTECTED_STRATEGIES = {
    "M_BTC_5m_any_0.92": 0.92,
    "BTC High-Threshold Pure": 0.80,
}

DB_PATH = Path(os.getenv("POLYPAPER_DB", "data_store/polypaper.db"))


def fetch_strategy_trades(con: sqlite3.Connection, days: int) -> dict[str, list[dict]]:
    """DB'den strategy_label başına trade list oku.

    Try multiple table names (live_trades, paper_trades, shadow_trades, trades).
    Returns: {strategy_label: [{pnl, paper_pnl, created_at, ...}, ...]}
    """
    cutoff = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    out: dict[str, list[dict]] = {}

    # Discover tables — basitleştirilmiş (eski filter buggy idi)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cur.fetchall()]

    # Print all tables for diagnostic
    print(f"  📋 All DB tables ({len(tables)}): {tables}", file=sys.stderr)

    # Find ANY table with strategy + pnl columns (independent of "trade" in name)
    cand_tables = []
    table_schemas = {}
    for t in tables:
        try:
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            table_schemas[t] = cols
            has_strategy = any(c in cols for c in ("strategy_label", "strategy", "label"))
            has_pnl = any(c in cols for c in ("pnl", "realized_pnl", "profit", "result_pnl"))
            if has_strategy and has_pnl:
                cand_tables.append(t)
        except sqlite3.Error:
            continue

    print(f"  📋 Tables with strategy+pnl: {cand_tables}", file=sys.stderr)

    if not cand_tables:
        # Print schemas of all tables for diagnostic
        print("  ⚠️ No matching tables. Schemas:", file=sys.stderr)
        for t, cols in table_schemas.items():
            print(f"     {t}: {cols[:8]}{'...' if len(cols)>8 else ''}", file=sys.stderr)
        return out

    for table in cand_tables:
        try:
            cur.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in cur.fetchall()]
            label_col = (
                "strategy_label"
                if "strategy_label" in cols
                else ("strategy" if "strategy" in cols else ("label" if "label" in cols else None))
            )
            if not label_col:
                continue
            # Timestamp column (varying names across tables)
            ts_col = None
            for cand in ("created_at", "ts", "timestamp", "matched_at", "open_ts", "filled_at"):
                if cand in cols:
                    ts_col = cand
                    break
            pnl_col = (
                "pnl" if "pnl" in cols else ("realized_pnl" if "realized_pnl" in cols else None)
            )
            if not ts_col or not pnl_col:
                print(
                    f"  ⚠ skip {table}: missing ts ({ts_col}) or pnl ({pnl_col})", file=sys.stderr
                )
                continue

            print(
                f"  ✅ scan {table}: label={label_col} ts={ts_col} pnl={pnl_col}", file=sys.stderr
            )

            # Detect timestamp format (epoch float vs ISO string)
            cur.execute(f"SELECT {ts_col} FROM {table} WHERE {ts_col} IS NOT NULL LIMIT 1")
            sample_ts = cur.fetchone()
            ts_is_epoch = True
            if sample_ts and sample_ts[0]:
                try:
                    float(sample_ts[0])
                except (ValueError, TypeError):
                    ts_is_epoch = False

            if ts_is_epoch:
                sql = (
                    f"SELECT {label_col}, {pnl_col}, {ts_col} " f"FROM {table} WHERE {ts_col} >= ?"
                )
                bind = (cutoff,)
            else:
                # ISO string comparison
                cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
                sql = (
                    f"SELECT {label_col}, {pnl_col}, {ts_col} " f"FROM {table} WHERE {ts_col} >= ?"
                )
                bind = (cutoff_iso,)

            row_count = 0
            for label, pnl, ts in cur.execute(sql, bind):
                if label is None:
                    continue
                # Normalize ts to epoch
                try:
                    ts_epoch = (
                        float(ts)
                        if ts_is_epoch
                        else datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                    )
                except (ValueError, TypeError):
                    ts_epoch = 0
                out.setdefault(str(label), []).append(
                    {
                        "pnl": float(pnl or 0),
                        "ts": ts_epoch,
                        "table": table,
                    }
                )
                row_count += 1
            print(f"  📊 {table}: {row_count} rows", file=sys.stderr)
        except sqlite3.Error as e:
            print(f"  ⚠ skip table {table}: {e}", file=sys.stderr)
            continue

    return out


def compute_stats(trades: list[dict]) -> dict:
    """Trade list'ten Sharpe / PF / Expectancy / MaxDD hesapla."""
    if not trades:
        return {
            "n": 0,
            "win_rate": 0,
            "expectancy": 0,
            "profit_factor": 0,
            "sharpe": 0,
            "max_dd": 0,
            "total_pnl": 0,
        }

    pnls = [t["pnl"] for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)

    # Win rate
    win_rate = len(wins) / n if n > 0 else 0

    # Expectancy
    expectancy = total_pnl / n if n > 0 else 0

    # Profit Factor
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (
        (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
    )

    # Sharpe (per-trade, annualized assuming daily trading freq)
    if n >= 2:
        mean_pnl = sum(pnls) / n
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        # Per-trade Sharpe; annualize with sqrt(trades_per_year ~ 252 * trades_per_day)
        # Conservative: per-trade Sharpe (no annualization for simplicity)
        sharpe = (mean_pnl / std) if std > 0 else 0
    else:
        sharpe = 0

    # Max drawdown (cumulative pnl ile)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in sorted(trades, key=lambda x: x.get("ts", 0)):
        cum += p["pnl"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    return {
        "n": n,
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
        "sharpe": round(sharpe, 4),
        "max_dd": round(max_dd, 2),
        "total_pnl": round(total_pnl, 2),
    }


def decide_pruning(stats_by_label: dict[str, dict], keep_n: int) -> dict[str, str]:
    """Karar matrisi.

    Returns: {label: "KEEP" | "PRUNE" | "PROTECTED" | "INSUFFICIENT_DATA"}
    """
    decisions = {}

    # 1. PROTECTED stratejileri KEEP
    for label in stats_by_label:
        if label in PROTECTED_STRATEGIES:
            decisions[label] = "PROTECTED"

    # 2. Insufficient data
    for label, s in stats_by_label.items():
        if label in decisions:
            continue
        if s["n"] < TRADES_MIN:
            decisions[label] = "INSUFFICIENT_DATA"

    # 3. Eligible: Sharpe ≥ 1.2 AND PF ≥ 1.3
    eligible = []
    for label, s in stats_by_label.items():
        if label in decisions:
            continue
        if s["sharpe"] >= SHARPE_MIN and s["profit_factor"] >= PF_MIN:
            eligible.append((label, s))

    # 4. Sort eligible by composite score (Sharpe × PF × win_rate)
    def score(item):
        _, s = item
        return s["sharpe"] * s["profit_factor"] * (1 + s["win_rate"])

    eligible.sort(key=score, reverse=True)

    # 5. Top keep_n KEEP, rest eligible PRUNE
    protected_count = sum(1 for d in decisions.values() if d == "PROTECTED")
    slots_left = max(0, keep_n - protected_count)
    for i, (label, s) in enumerate(eligible):
        if i < slots_left:
            decisions[label] = "KEEP"
        else:
            decisions[label] = "PRUNE"

    # 6. Non-eligible (Sharpe<1.2 or PF<1.3) and not yet decided → PRUNE
    for label in stats_by_label:
        if label not in decisions:
            decisions[label] = "PRUNE"

    return decisions


def format_table(stats_by_label: dict, decisions: dict) -> str:
    """Pretty-print table."""
    lines = [
        "",
        f"{'Decision':<22} {'Strategy':<35} {'N':>5} {'WR':>7} {'PF':>7} {'Sharpe':>8} {'PnL':>10} {'MaxDD':>8}",
        "-" * 110,
    ]
    # Sort by decision then by sharpe
    order = {"PROTECTED": 0, "KEEP": 1, "PRUNE": 2, "INSUFFICIENT_DATA": 3}
    sorted_items = sorted(
        stats_by_label.items(),
        key=lambda x: (order.get(decisions.get(x[0], "PRUNE"), 9), -x[1]["sharpe"]),
    )
    for label, s in sorted_items:
        d = decisions.get(label, "?")
        emoji = {"PROTECTED": "🛡️", "KEEP": "✅", "PRUNE": "❌", "INSUFFICIENT_DATA": "❓"}.get(
            d, "?"
        )
        lines.append(
            f"{emoji} {d:<19} {label[:34]:<35} "
            f"{s['n']:>5} {s['win_rate']*100:>6.1f}% "
            f"{s['profit_factor']:>7.2f} {s['sharpe']:>8.3f} "
            f"${s['total_pnl']:>9.2f} ${s['max_dd']:>7.2f}"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90, help="Lookback days (default 90)")
    ap.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="Max strategies to keep")
    ap.add_argument("--dry-run", action="store_true", help="Read-only, no file output")
    ap.add_argument("--output-dir", default="evidence", help="Output dir")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"📊 Strategy Pruning Analysis (last {args.days}d, keep={args.keep})")
    print(f"   DB: {DB_PATH}")
    print(f"   Thresholds: Sharpe≥{SHARPE_MIN}, PF≥{PF_MIN}, N≥{TRADES_MIN}")
    print()

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    trades_by_label = fetch_strategy_trades(con, args.days)
    if not trades_by_label:
        print("⚠️  No strategy trades found. Check DB schema.", file=sys.stderr)
        con.close()
        sys.exit(2)

    stats_by_label = {label: compute_stats(trades) for label, trades in trades_by_label.items()}
    decisions = decide_pruning(stats_by_label, args.keep)

    table = format_table(stats_by_label, decisions)
    print(table)
    print()

    keep = sum(1 for d in decisions.values() if d in ("PROTECTED", "KEEP"))
    prune = sum(1 for d in decisions.values() if d == "PRUNE")
    insuf = sum(1 for d in decisions.values() if d == "INSUFFICIENT_DATA")
    print(f"\n📈 Total: {len(stats_by_label)} strategies")
    print(f"   ✅ KEEP/PROTECTED: {keep}")
    print(f"   ❌ PRUNE:          {prune}")
    print(f"   ❓ INSUFFICIENT:   {insuf}")

    if not args.dry_run:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        # JSON
        out_json = out_dir / f"strategy_pruning_{ts}.json"
        with out_json.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "lookback_days": args.days,
                    "keep_n": args.keep,
                    "thresholds": {
                        "sharpe_min": SHARPE_MIN,
                        "pf_min": PF_MIN,
                        "trades_min": TRADES_MIN,
                    },
                    "stats": stats_by_label,
                    "decisions": decisions,
                    "summary": {"keep": keep, "prune": prune, "insufficient": insuf},
                    "generated_at": datetime.now(UTC).isoformat(),
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(f"\n💾 JSON: {out_json}")

        # MD
        out_md = out_dir / f"strategy_pruning_{ts}.md"
        with out_md.open("w", encoding="utf-8") as f:
            f.write(f"# Strategy Pruning — {ts}\n\n")
            f.write(f"Lookback: {args.days}d, Keep: {args.keep}\n\n")
            f.write(f"Thresholds: Sharpe≥{SHARPE_MIN}, PF≥{PF_MIN}, N≥{TRADES_MIN}\n\n")
            f.write("```\n" + table + "\n```\n\n")
            f.write(
                f"## Summary\n\n- KEEP/PROTECTED: {keep}\n- PRUNE: {prune}\n- INSUFFICIENT: {insuf}\n"
            )
            f.write("\n## Apply\n\n")
            f.write("1. Backup current strategies: `_archive/strategies_pre_pruning_2026_05/`\n")
            f.write("2. ENV `STRATEGY_ENABLED_<LABEL>=false` for PRUNE candidates\n")
            f.write("3. Bot restart + 1 week soak test\n")
        print(f"💾 MD:   {out_md}")

    con.close()


if __name__ == "__main__":
    main()
