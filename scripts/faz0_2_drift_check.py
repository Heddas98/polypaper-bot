"""FAZ 0.2 — Live/Paper Drift Check.

3-AI yol haritasının (uploads/YOL_HARITASI_3AI_SYNTHESIS.md) FAZ 0 üçüncü
ayağı. Son N gün shadow live trade'lerinin gerçek PnL'ini paper benzerleriyle
karşılaştırır.

Kabul kriteri (yol haritası §FAZ 0.2):
    drift_pnl_pct < %20  → simülatör güvenilir, mainnet'e ilerle
    drift_pnl_pct >= %20 → simülatör kalibre edilmeli (FILL_SPREAD_COST,
                            IMPACT, LATENCY_DRIFT)

Read-only — sadece DB'den SELECT. Hiçbir veri değişmez.

Usage:
    py -3.11 scripts\\faz0_2_drift_check.py
    py -3.11 scripts\\faz0_2_drift_check.py 30   # son 30 gün
    DRIFT_DAYS=7 py -3.11 scripts\\faz0_2_drift_check.py

Çıktı:
    backtest/calibration/live_paper_drift_YYYY_MM_DD.json (machine-readable)
    docs/audits/live_paper_drift_YYYY_MM_DD.md (human-readable, eğer fail)
    stdout: özet tablo + verdict
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data_store" / "polypaper.db"
OUT_DIR = REPO_ROOT / "backtest" / "calibration"
DOCS_DIR = REPO_ROOT / "docs" / "audits"

# Drift threshold from YOL_HARITASI_3AI_SYNTHESIS.md FAZ 0.2
DRIFT_THRESHOLD_PCT = 20.0


def _parse_days() -> int:
    if len(sys.argv) > 1:
        try:
            return int(sys.argv[1])
        except ValueError:
            pass
    try:
        return int(os.getenv("DRIFT_DAYS", "14"))
    except ValueError:
        return 14


def main() -> int:
    days = _parse_days()
    if not DB_PATH.exists():
        print(f"❌ DB not found: {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT id, strategy_label, slug, direction, pnl, paper_pnl,
               fee_entry, fee_exit, entry_price, exit_price, amount,
               result, created_at, settled_at
        FROM live_trades
        WHERE settled_at IS NOT NULL
          AND pnl IS NOT NULL
          AND paper_pnl IS NOT NULL
          AND created_at >= ?
        ORDER BY created_at ASC
        """,
        (cutoff,),
    ).fetchall()

    if not rows:
        report = {
            "audit": "FAZ 0.2 — live/paper drift",
            "date": datetime.now(timezone.utc).isoformat(),
            "window_days": days,
            "trade_count": 0,
            "verdict": "INSUFFICIENT_DATA",
            "note": (
                "Son {0} gün içinde settled live trade yok. Bot mainnet'e "
                "geçmeden önce en az 10-20 gerçek trade ile drift ölçülmeli."
            ).format(days),
        }
        _write_json(report)
        print(f"⚠ {report['verdict']}: settled live trade yok ({days} gün penceresi)")
        return 1

    # Per-trade drift series
    drifts_usd = []
    drifts_pct = []
    fee_drifts = []
    fill_drifts_pct = []  # entry price drift if available

    for r in rows:
        live_pnl = float(r["pnl"] or 0)
        paper_pnl = float(r["paper_pnl"] or 0)
        delta = live_pnl - paper_pnl
        drifts_usd.append(delta)
        if abs(paper_pnl) > 0.001:
            drifts_pct.append((delta / paper_pnl) * 100)

        fee_total = float(r["fee_entry"] or 0) + float(r["fee_exit"] or 0)
        fee_drifts.append(fee_total)

    # Aggregate sums
    n = len(rows)
    total_live = sum(float(r["pnl"] or 0) for r in rows)
    total_paper = sum(float(r["paper_pnl"] or 0) for r in rows)
    total_drift_usd = total_live - total_paper
    total_drift_pct = (
        (total_drift_usd / total_paper * 100) if abs(total_paper) > 0.001 else 0.0
    )

    mean_drift_usd = statistics.mean(drifts_usd) if drifts_usd else 0.0
    median_drift_usd = statistics.median(drifts_usd) if drifts_usd else 0.0
    stdev_drift_usd = (
        statistics.stdev(drifts_usd) if len(drifts_usd) > 1 else 0.0
    )

    median_drift_pct = (
        statistics.median(drifts_pct) if drifts_pct else 0.0
    )
    p95_drift_pct = (
        sorted(drifts_pct)[int(len(drifts_pct) * 0.95)]
        if len(drifts_pct) >= 20
        else max(drifts_pct, default=0.0)
    )

    # Per-strategy breakdown
    by_strat: dict = {}
    for r in rows:
        s = r["strategy_label"]
        by_strat.setdefault(
            s, {"trades": 0, "live_pnl": 0.0, "paper_pnl": 0.0, "fee_total": 0.0}
        )
        by_strat[s]["trades"] += 1
        by_strat[s]["live_pnl"] += float(r["pnl"] or 0)
        by_strat[s]["paper_pnl"] += float(r["paper_pnl"] or 0)
        by_strat[s]["fee_total"] += (
            float(r["fee_entry"] or 0) + float(r["fee_exit"] or 0)
        )
    for s, d in by_strat.items():
        d["drift_usd"] = round(d["live_pnl"] - d["paper_pnl"], 4)
        d["drift_pct"] = round(
            ((d["live_pnl"] - d["paper_pnl"]) / d["paper_pnl"] * 100)
            if abs(d["paper_pnl"]) > 0.001
            else 0.0,
            2,
        )

    # Verdict
    abs_drift_pct = abs(total_drift_pct)
    if abs_drift_pct < DRIFT_THRESHOLD_PCT:
        verdict = "PASS"
        recommendation = (
            f"Simülatör güvenilir (drift {abs_drift_pct:.1f}% < {DRIFT_THRESHOLD_PCT}%). "
            "Mainnet ölçeğine ilerlenebilir."
        )
    else:
        verdict = "FAIL"
        sign = "+" if total_drift_pct > 0 else "-"
        recommendation = (
            f"Simülatör drift {sign}{abs_drift_pct:.1f}% > {DRIFT_THRESHOLD_PCT}% — "
            "kalibrasyon gerekli. Önerilen knob'lar: FILL_SPREAD_COST, "
            "IMPACT, LATENCY_DRIFT (config/settings.py + T4.6-B sweep raporu)."
        )

    result = {
        "audit": "FAZ 0.2 — live/paper drift",
        "date": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "cutoff_iso": cutoff,
        "trade_count": n,
        "totals": {
            "live_pnl_usd": round(total_live, 4),
            "paper_pnl_usd": round(total_paper, 4),
            "drift_usd": round(total_drift_usd, 4),
            "drift_pct": round(total_drift_pct, 2),
            "total_fees_usd": round(sum(fee_drifts), 4),
        },
        "per_trade_stats": {
            "mean_drift_usd": round(mean_drift_usd, 4),
            "median_drift_usd": round(median_drift_usd, 4),
            "stdev_drift_usd": round(stdev_drift_usd, 4),
            "median_drift_pct": round(median_drift_pct, 2),
            "p95_drift_pct": round(p95_drift_pct, 2),
        },
        "by_strategy": by_strat,
        "verdict": verdict,
        "criterion_pct": DRIFT_THRESHOLD_PCT,
        "recommendation": recommendation,
    }

    _write_json(result)
    _write_md(result, days)

    print("=" * 70)
    print(f"FAZ 0.2 Live/Paper Drift Check ({days} gün)")
    print("=" * 70)
    print(f"  Trade count:       {n}")
    print(f"  Live PnL:          ${total_live:+.2f}")
    print(f"  Paper PnL:         ${total_paper:+.2f}")
    print(f"  Drift USD:         ${total_drift_usd:+.2f}")
    print(f"  Drift %:           {total_drift_pct:+.2f}%")
    print(f"  Median per-trade:  ${median_drift_usd:+.4f}")
    print(f"  StdDev per-trade:  ${stdev_drift_usd:.4f}")
    print()
    print("  Per-strategy:")
    for s, d in sorted(
        by_strat.items(), key=lambda kv: -kv[1]["trades"]
    ):
        print(
            f"    {s:<35} n={d['trades']:>3}  "
            f"live=${d['live_pnl']:+.2f}  paper=${d['paper_pnl']:+.2f}  "
            f"drift={d['drift_pct']:+.1f}%"
        )
    print()
    print(f"  Verdict: {verdict}")
    print(f"  → {recommendation}")
    print("=" * 70)
    return 0 if verdict == "PASS" else 1


def _write_json(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    out_path = OUT_DIR / f"live_paper_drift_{date_tag}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"📊 JSON: {out_path}")


def _write_md(result: dict, days: int) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now(timezone.utc).strftime("%Y_%m_%d")
    md_path = DOCS_DIR / f"live_paper_drift_{date_tag}.md"
    t = result["totals"]
    s = result["per_trade_stats"]
    lines = [
        f"# FAZ 0.2 — Live/Paper Drift Audit",
        f"",
        f"**Tarih:** {result['date']}",
        f"**Pencere:** Son {days} gün ({result['cutoff_iso']} sonrası)",
        f"**Trade sayısı:** {result['trade_count']}",
        f"**Verdict:** **{result['verdict']}**",
        f"",
        f"## Toplam",
        f"",
        f"| Metrik | Değer |",
        f"|---|---:|",
        f"| Live PnL | ${t['live_pnl_usd']:+.2f} |",
        f"| Paper PnL | ${t['paper_pnl_usd']:+.2f} |",
        f"| Drift USD | ${t['drift_usd']:+.2f} |",
        f"| **Drift %** | **{t['drift_pct']:+.2f}%** |",
        f"| Total fees | ${t['total_fees_usd']:.2f} |",
        f"",
        f"## Per-trade istatistikler",
        f"",
        f"| Metrik | Değer |",
        f"|---|---:|",
        f"| Mean drift USD | ${s['mean_drift_usd']:+.4f} |",
        f"| Median drift USD | ${s['median_drift_usd']:+.4f} |",
        f"| StdDev drift USD | ${s['stdev_drift_usd']:.4f} |",
        f"| Median drift % | {s['median_drift_pct']:+.2f}% |",
        f"| P95 drift % | {s['p95_drift_pct']:+.2f}% |",
        f"",
        f"## Per-strategy",
        f"",
        f"| Strategy | Trades | Live PnL | Paper PnL | Drift % |",
        f"|---|---:|---:|---:|---:|",
    ]
    for sname, d in sorted(
        result["by_strategy"].items(), key=lambda kv: -kv[1]["trades"]
    ):
        lines.append(
            f"| {sname} | {d['trades']} | ${d['live_pnl']:+.2f} | "
            f"${d['paper_pnl']:+.2f} | {d['drift_pct']:+.2f}% |"
        )
    lines.extend(
        [
            f"",
            f"## Verdict & Recommendation",
            f"",
            f"**Kriter:** drift_pct < {result['criterion_pct']}% → simülatör güvenilir",
            f"",
            f"**Recommendation:** {result['recommendation']}",
            f"",
            f"---",
            f"*Auto-generated by `scripts/faz0_2_drift_check.py`*",
            f"*Yol haritası: `uploads/YOL_HARITASI_3AI_SYNTHESIS.md` §FAZ 0.2*",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 MD:   {md_path}")


if __name__ == "__main__":
    sys.exit(main())
