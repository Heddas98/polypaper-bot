#!/usr/bin/env python3
"""
Phase 60 P2-4: Becker DuckDB Full Deep Analysis
================================================
Comprehensive offline analysis of the 50GB Becker dataset (1.4GB calibration DB).

Analyses:
  1. Zone calibration:     δ(p) per 5c bin for both Kalshi + Polymarket
  2. Temporal patterns:    Weekday/hour resolution rates (mispricing by time)
  3. Asset-specific curve: Per-crypto (BTC/ETH/SOL/XRP) calibration differences
  4. Volume-weighted δ:    Size-weighted mispricing (big trades vs small)
  5. Taker-side analysis:  YES vs NO taker resolution rates
  6. Time-to-resolution:   Avg hold time by price zone
  7. Maker vs Taker edge:  Excess return comparison

Usage:
    python scripts/becker_deep_analysis.py
    python scripts/becker_deep_analysis.py --html  (save HTML report)

Requires: data_store/becker_calibration.db (built via /becker_build)
"""
from __future__ import annotations

import os
import sys
import json
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CALIB_DB = PROJECT_ROOT / "data_store" / "becker_calibration.db"


def _safe_pct(val):
    return f"{val:.1%}" if val is not None else "N/A"


def _safe_f(val, fmt=".3f"):
    return f"{val:{fmt}}" if val is not None else "N/A"


def run_deep_analysis() -> dict:
    """Run all analyses against the calibration DB."""
    if not CALIB_DB.exists():
        return {"error": "becker_calibration.db not found. Run /becker_build first."}
    try:
        import duckdb  # type: ignore
    except ImportError:
        return {"error": "DuckDB not installed. pip install duckdb"}

    con = duckdb.connect(str(CALIB_DB), read_only=True)
    results = {}
    t0 = time.time()

    try:
        # ── 1. Zone Calibration (5c bins) — Kalshi ──
        try:
            results["kalshi_calibration"] = con.execute("""
                WITH bins AS (
                    SELECT
                        FLOOR(yes_price / 5.0) * 5 AS bin_c,
                        AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                        COUNT(*) AS n,
                        COUNT(DISTINCT ticker) AS mkts
                    FROM kalshi_crypto
                    WHERE market_result IN ('yes','no') AND yes_price BETWEEN 5 AND 95
                    GROUP BY bin_c
                )
                SELECT bin_c, bin_c/100.0 AS implied, actual, actual-bin_c/100.0 AS delta,
                       n, mkts
                FROM bins ORDER BY bin_c
            """).fetchall()
        except Exception as e:
            results["kalshi_calibration_error"] = str(e)

        # ── 2. Temporal Patterns — Kalshi (weekday + hour) ──
        try:
            results["kalshi_weekday"] = con.execute("""
                SELECT
                    EXTRACT(DOW FROM CAST(created_time AS TIMESTAMP)) AS dow,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    AVG(yes_price)/100.0 AS avg_implied,
                    COUNT(*) AS n
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no')
                  AND created_time IS NOT NULL
                GROUP BY dow
                ORDER BY dow
            """).fetchall()
        except Exception as e:
            results["kalshi_weekday_error"] = str(e)

        try:
            results["kalshi_hourly"] = con.execute("""
                SELECT
                    EXTRACT(HOUR FROM CAST(created_time AS TIMESTAMP)) AS hr,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    AVG(yes_price)/100.0 AS avg_implied,
                    COUNT(*) AS n
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no')
                  AND created_time IS NOT NULL
                GROUP BY hr
                ORDER BY hr
            """).fetchall()
        except Exception as e:
            results["kalshi_hourly_error"] = str(e)

        # ── 3. Asset-Specific Calibration — Kalshi ──
        try:
            results["kalshi_per_asset"] = con.execute("""
                SELECT
                    CASE
                        WHEN event_ticker LIKE 'KXBTC%' THEN 'BTC'
                        WHEN event_ticker LIKE 'KXETH%' THEN 'ETH'
                        WHEN event_ticker LIKE 'KXSOL%' THEN 'SOL'
                        WHEN event_ticker LIKE 'KXXRP%' THEN 'XRP'
                        ELSE 'OTHER'
                    END AS asset,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    AVG(yes_price)/100.0 AS avg_implied,
                    COUNT(*) AS n,
                    COUNT(DISTINCT ticker) AS mkts
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no')
                GROUP BY asset
                ORDER BY n DESC
            """).fetchall()
        except Exception as e:
            results["kalshi_per_asset_error"] = str(e)

        # ── 4. Asset × Zone — most actionable: per-asset sub-25c ──
        try:
            results["kalshi_asset_zone"] = con.execute("""
                SELECT
                    CASE
                        WHEN event_ticker LIKE 'KXBTC%' THEN 'BTC'
                        WHEN event_ticker LIKE 'KXETH%' THEN 'ETH'
                        WHEN event_ticker LIKE 'KXSOL%' THEN 'SOL'
                        WHEN event_ticker LIKE 'KXXRP%' THEN 'XRP'
                        ELSE 'OTHER'
                    END AS asset,
                    CASE
                        WHEN yes_price BETWEEN 5 AND 25 THEN 'sub25'
                        WHEN yes_price BETWEEN 26 AND 45 THEN '26-45'
                        WHEN yes_price BETWEEN 46 AND 55 THEN '46-55'
                        WHEN yes_price BETWEEN 56 AND 75 THEN '56-75'
                        WHEN yes_price BETWEEN 76 AND 95 THEN '76-95'
                    END AS zone,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    AVG(yes_price)/100.0 AS avg_implied,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) - AVG(yes_price)/100.0 AS delta,
                    COUNT(*) AS n
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no') AND yes_price BETWEEN 5 AND 95
                GROUP BY asset, zone
                HAVING n >= 50
                ORDER BY asset, zone
            """).fetchall()
        except Exception as e:
            results["kalshi_asset_zone_error"] = str(e)

        # ── 5. Taker Side Analysis ──
        try:
            results["kalshi_taker_side"] = con.execute("""
                SELECT
                    taker_side,
                    CASE
                        WHEN yes_price BETWEEN 5 AND 25 THEN 'sub25'
                        WHEN yes_price BETWEEN 26 AND 50 THEN '26-50'
                        WHEN yes_price BETWEEN 51 AND 75 THEN '51-75'
                        WHEN yes_price BETWEEN 76 AND 95 THEN '76-95'
                    END AS zone,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    COUNT(*) AS n
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no')
                  AND taker_side IS NOT NULL
                  AND yes_price BETWEEN 5 AND 95
                GROUP BY taker_side, zone
                ORDER BY taker_side, zone
            """).fetchall()
        except Exception as e:
            results["kalshi_taker_side_error"] = str(e)

        # ── 6. Volume-Weighted δ — Kalshi (by trade count bucket) ──
        try:
            results["kalshi_volume_weighted"] = con.execute("""
                SELECT
                    CASE
                        WHEN count <= 5 THEN 'small(1-5)'
                        WHEN count <= 20 THEN 'medium(6-20)'
                        WHEN count <= 100 THEN 'large(21-100)'
                        ELSE 'whale(100+)'
                    END AS size_bucket,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS actual,
                    AVG(yes_price)/100.0 AS avg_implied,
                    SUM(count) AS total_contracts,
                    COUNT(*) AS n_trades
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no') AND yes_price BETWEEN 5 AND 95
                GROUP BY size_bucket
                ORDER BY size_bucket
            """).fetchall()
        except Exception as e:
            results["kalshi_volume_weighted_error"] = str(e)

        # ── 7. Summary Stats ──
        try:
            summary = con.execute("""
                SELECT
                    COUNT(*) AS total_trades,
                    COUNT(DISTINCT ticker) AS total_markets,
                    COUNT(DISTINCT event_ticker) AS total_events,
                    MIN(created_time) AS earliest,
                    MAX(created_time) AS latest,
                    AVG(CASE WHEN market_result='yes' THEN 1.0 ELSE 0.0 END) AS overall_yes_rate
                FROM kalshi_crypto
                WHERE market_result IN ('yes','no')
            """).fetchone()
            results["kalshi_summary"] = {
                "total_trades": summary[0],
                "total_markets": summary[1],
                "total_events": summary[2],
                "earliest": str(summary[3]),
                "latest": str(summary[4]),
                "overall_yes_rate": summary[5],
            }
        except Exception as e:
            results["kalshi_summary_error"] = str(e)

        # ── Polymarket summary (if available) ──
        try:
            poly_sum = con.execute("""
                SELECT COUNT(*) AS n FROM poly_crypto
            """).fetchone()
            results["poly_trade_count"] = poly_sum[0]
        except Exception:
            results["poly_trade_count"] = 0

    finally:
        con.close()

    results["elapsed_sec"] = round(time.time() - t0, 2)
    return results


def format_report(results: dict) -> str:
    """Format as human-readable text report."""
    lines = []
    lines.append("=" * 70)
    lines.append("  BECKER DEEP ANALYSIS — Phase 60 P2-4")
    lines.append("=" * 70)

    if "error" in results:
        lines.append(f"\n  ERROR: {results['error']}")
        return "\n".join(lines)

    # Summary
    if "kalshi_summary" in results:
        s = results["kalshi_summary"]
        lines.append(f"\n── KALSHI DATASET SUMMARY ──")
        lines.append(f"  Total trades:  {s['total_trades']:,}")
        lines.append(f"  Markets:       {s['total_markets']:,}")
        lines.append(f"  Events:        {s['total_events']:,}")
        lines.append(f"  Range:         {s['earliest']} → {s['latest']}")
        lines.append(f"  Overall YES%:  {s['overall_yes_rate']:.1%}")
    if results.get("poly_trade_count", 0) > 0:
        lines.append(f"  Poly trades:   {results['poly_trade_count']:,}")

    # 1. Zone calibration
    if "kalshi_calibration" in results:
        lines.append(f"\n── 1. ZONE CALIBRATION (5c bins) ──")
        lines.append(f"{'Bin':>6} {'Implied':>8} {'Actual':>8} {'δ(p)':>8} {'Trades':>10} {'Mkts':>6}")
        lines.append("-" * 52)
        for r in results["kalshi_calibration"]:
            lines.append(
                f"{r[0]:>5}c {r[1]:>7.1%} {r[2]:>7.1%} {r[3]:>+7.1%} {r[4]:>10,} {r[5]:>6}")

    # 2. Temporal — weekday
    if "kalshi_weekday" in results:
        dow_names = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}
        lines.append(f"\n── 2. WEEKDAY PATTERNS ──")
        lines.append(f"{'Day':>5} {'Actual':>8} {'Implied':>8} {'Delta':>8} {'Trades':>10}")
        for r in results["kalshi_weekday"]:
            d = r[3] if r[3] else 0
            delta = r[1] - r[2] if r[1] and r[2] else 0
            lines.append(
                f"{dow_names.get(int(r[0]), '?'):>5} {_safe_pct(r[1]):>8} "
                f"{_safe_pct(r[2]):>8} {delta:>+7.1%} {d:>10,}")

    # Temporal — hourly
    if "kalshi_hourly" in results:
        lines.append(f"\n── 2b. HOURLY PATTERNS ──")
        lines.append(f"{'Hour':>5} {'Actual':>8} {'Implied':>8} {'Delta':>8} {'Trades':>10}")
        for r in results["kalshi_hourly"]:
            delta = r[1] - r[2] if r[1] and r[2] else 0
            n = r[3] if r[3] else 0
            lines.append(
                f"{int(r[0]):>4}h {_safe_pct(r[1]):>8} {_safe_pct(r[2]):>8} "
                f"{delta:>+7.1%} {n:>10,}")

    # 3. Per-asset
    if "kalshi_per_asset" in results:
        lines.append(f"\n── 3. PER-ASSET CALIBRATION ──")
        lines.append(f"{'Asset':>6} {'Actual':>8} {'Implied':>8} {'Delta':>8} {'Trades':>10} {'Mkts':>6}")
        for r in results["kalshi_per_asset"]:
            delta = r[1] - r[2] if r[1] and r[2] else 0
            lines.append(
                f"{r[0]:>6} {_safe_pct(r[1]):>8} {_safe_pct(r[2]):>8} "
                f"{delta:>+7.1%} {r[3]:>10,} {r[4]:>6}")

    # 4. Asset × Zone
    if "kalshi_asset_zone" in results:
        lines.append(f"\n── 4. ASSET × ZONE MATRIX ──")
        lines.append(f"{'Asset':>6} {'Zone':>8} {'Actual':>8} {'Implied':>8} {'δ(p)':>8} {'Trades':>8}")
        lines.append("-" * 52)
        for r in results["kalshi_asset_zone"]:
            lines.append(
                f"{r[0]:>6} {r[1]:>8} {r[2]:>7.1%} {r[3]:>7.1%} "
                f"{r[4]:>+7.1%} {r[5]:>8,}")

    # 5. Taker side
    if "kalshi_taker_side" in results:
        lines.append(f"\n── 5. TAKER SIDE ANALYSIS ──")
        lines.append(f"{'Side':>6} {'Zone':>8} {'Actual':>8} {'Trades':>10}")
        for r in results["kalshi_taker_side"]:
            lines.append(
                f"{r[0]:>6} {r[1]:>8} {_safe_pct(r[2]):>8} {r[3]:>10,}")

    # 6. Volume-weighted
    if "kalshi_volume_weighted" in results:
        lines.append(f"\n── 6. VOLUME-WEIGHTED δ ──")
        lines.append(f"{'Bucket':>14} {'Actual':>8} {'Implied':>8} {'Contracts':>10} {'Trades':>8}")
        for r in results["kalshi_volume_weighted"]:
            lines.append(
                f"{r[0]:>14} {_safe_pct(r[1]):>8} {_safe_pct(r[2]):>8} "
                f"{r[3]:>10,} {r[4]:>8,}")

    lines.append(f"\n  Elapsed: {results.get('elapsed_sec', '?')}s")
    lines.append("=" * 70)
    return "\n".join(lines)


def format_html(results: dict) -> str:
    """Format as self-contained HTML report."""
    html = ["""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Becker Deep Analysis</title>
<style>
body{font-family:system-ui;margin:20px;background:#1a1b26;color:#c0caf5}
table{border-collapse:collapse;margin:10px 0;width:100%}
th,td{border:1px solid #3b4261;padding:6px 10px;text-align:right;font-size:13px}
th{background:#24283b;color:#7aa2f7}
tr:nth-child(even){background:#1e2030}
.pos{color:#9ece6a}.neg{color:#f7768e}
h2{color:#7aa2f7;border-bottom:1px solid #3b4261;padding-bottom:5px}
h3{color:#bb9af7}
.summary{background:#24283b;padding:15px;border-radius:8px;margin:10px 0}
</style></head><body>
<h1>🔬 Becker Deep Analysis — Phase 60 P2-4</h1>
"""]

    if "error" in results:
        html.append(f"<p style='color:#f7768e'>ERROR: {results['error']}</p></body></html>")
        return "\n".join(html)

    # Summary
    if "kalshi_summary" in results:
        s = results["kalshi_summary"]
        html.append(f"""<div class="summary">
<h3>Kalshi Dataset Summary</h3>
<p>Trades: <b>{s['total_trades']:,}</b> | Markets: <b>{s['total_markets']:,}</b> |
Events: <b>{s['total_events']:,}</b> | Range: {s['earliest'][:10]} → {s['latest'][:10]} |
YES%: <b>{s['overall_yes_rate']:.1%}</b></p>
</div>""")

    # Zone calibration table
    if "kalshi_calibration" in results:
        html.append("<h2>1. Zone Calibration (5c bins)</h2><table>")
        html.append("<tr><th>Bin</th><th>Implied</th><th>Actual</th><th>δ(p)</th><th>Trades</th><th>Markets</th></tr>")
        for r in results["kalshi_calibration"]:
            cls = "pos" if r[3] > 0 else "neg"
            html.append(
                f"<tr><td>{r[0]:.0f}c</td><td>{r[1]:.1%}</td><td>{r[2]:.1%}</td>"
                f"<td class='{cls}'>{r[3]:+.1%}</td><td>{r[4]:,}</td><td>{r[5]}</td></tr>")
        html.append("</table>")

    # Per-asset
    if "kalshi_per_asset" in results:
        html.append("<h2>3. Per-Asset Calibration</h2><table>")
        html.append("<tr><th>Asset</th><th>Actual</th><th>Implied</th><th>Delta</th><th>Trades</th></tr>")
        for r in results["kalshi_per_asset"]:
            delta = r[1] - r[2] if r[1] and r[2] else 0
            cls = "pos" if delta > 0 else "neg"
            html.append(
                f"<tr><td>{r[0]}</td><td>{r[1]:.1%}</td><td>{r[2]:.1%}</td>"
                f"<td class='{cls}'>{delta:+.1%}</td><td>{r[3]:,}</td></tr>")
        html.append("</table>")

    # Asset × Zone
    if "kalshi_asset_zone" in results:
        html.append("<h2>4. Asset × Zone Matrix</h2><table>")
        html.append("<tr><th>Asset</th><th>Zone</th><th>Actual</th><th>Implied</th><th>δ(p)</th><th>Trades</th></tr>")
        for r in results["kalshi_asset_zone"]:
            cls = "pos" if r[4] > 0 else "neg"
            html.append(
                f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]:.1%}</td>"
                f"<td>{r[3]:.1%}</td><td class='{cls}'>{r[4]:+.1%}</td><td>{r[5]:,}</td></tr>")
        html.append("</table>")

    # Taker side
    if "kalshi_taker_side" in results:
        html.append("<h2>5. Taker Side Analysis</h2><table>")
        html.append("<tr><th>Side</th><th>Zone</th><th>Actual WR</th><th>Trades</th></tr>")
        for r in results["kalshi_taker_side"]:
            html.append(
                f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]:.1%}</td><td>{r[3]:,}</td></tr>")
        html.append("</table>")

    html.append(f"<p><i>Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
                f"Elapsed: {results.get('elapsed_sec', '?')}s</i></p>")
    html.append("</body></html>")
    return "\n".join(html)


if __name__ == "__main__":
    results = run_deep_analysis()

    if "--html" in sys.argv:
        html = format_html(results)
        out = PROJECT_ROOT / "reports" / "becker_deep_analysis.html"
        out.parent.mkdir(exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(f"HTML report saved to {out}")
    elif "--json" in sys.argv:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_report(results))
