#!/usr/bin/env python3
"""
Phase 60 P2-2: Becker Sub-25c Zone Analysis
============================================
DuckDB query against becker_calibration.db to analyze the 15-25c
mispricing zone where Becker data shows crypto events resolve at 34%
(vs 15-25% implied price) = 13.8pt average mispricing gap.

Usage:
    python scripts/becker_zone_analysis.py
    # or from Telegram: /becker_zones

Output:
    - Per-5c-bin resolution rates vs implied price
    - Kalshi vs Polymarket comparison
    - Sub-25c zone aggregate stats
    - Actionable thresholds for engine tuning

Requires: data_store/becker_calibration.db (built via /becker_build)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CALIB_DB = PROJECT_ROOT / "data_store" / "becker_calibration.db"


def run_analysis() -> dict:
    """Run the sub-25c zone analysis and return structured results."""
    if not CALIB_DB.exists():
        return {"error": "becker_calibration.db not found. Run /becker_build first."}

    try:
        import duckdb  # type: ignore
    except ImportError:
        return {"error": "DuckDB not installed. pip install duckdb"}

    con = duckdb.connect(str(CALIB_DB), read_only=True)
    results = {}

    try:
        # ── Kalshi: Fine-grained 5c bins for the full range ──
        try:
            kalshi_bins = con.execute("""
                WITH bins AS (
                    SELECT
                        FLOOR(yes_price / 5.0) * 5 AS bin_cents,
                        AVG(CASE WHEN market_result = 'yes' THEN 1.0 ELSE 0.0 END) AS actual_wr,
                        COUNT(*) AS n_trades,
                        COUNT(DISTINCT ticker) AS n_markets
                    FROM kalshi_crypto
                    WHERE market_result IN ('yes', 'no')
                      AND yes_price BETWEEN 5 AND 95
                    GROUP BY bin_cents
                )
                SELECT
                    bin_cents,
                    bin_cents / 100.0 AS implied_prob,
                    actual_wr,
                    actual_wr - bin_cents / 100.0 AS delta,
                    n_trades,
                    n_markets
                FROM bins
                ORDER BY bin_cents
            """).fetchall()
            results["kalshi_bins"] = [
                {
                    "bin_cents": r[0], "implied": r[1], "actual": r[2],
                    "delta": r[3], "n_trades": r[4], "n_markets": r[5],
                }
                for r in kalshi_bins
            ]
        except Exception as e:
            results["kalshi_error"] = str(e)

        # ── Kalshi: Sub-25c zone aggregate ──
        try:
            sub25 = con.execute("""
                SELECT
                    AVG(CASE WHEN market_result = 'yes' THEN 1.0 ELSE 0.0 END) AS actual_wr,
                    AVG(yes_price) / 100.0 AS avg_implied,
                    COUNT(*) AS n_trades,
                    COUNT(DISTINCT ticker) AS n_markets
                FROM kalshi_crypto
                WHERE market_result IN ('yes', 'no')
                  AND yes_price BETWEEN 15 AND 25
            """).fetchone()
            results["kalshi_sub25"] = {
                "actual_wr": sub25[0], "avg_implied": sub25[1],
                "n_trades": sub25[2], "n_markets": sub25[3],
                "mispricing_gap": sub25[0] - sub25[1] if sub25[0] and sub25[1] else None,
            }
        except Exception as e:
            results["kalshi_sub25_error"] = str(e)

        # ── Kalshi: YES/NO taker side breakdown in sub-25c ──
        try:
            taker_split = con.execute("""
                SELECT
                    taker_side,
                    AVG(CASE WHEN market_result = 'yes' THEN 1.0 ELSE 0.0 END) AS actual_wr,
                    COUNT(*) AS n_trades
                FROM kalshi_crypto
                WHERE market_result IN ('yes', 'no')
                  AND yes_price BETWEEN 15 AND 25
                  AND taker_side IS NOT NULL
                GROUP BY taker_side
            """).fetchall()
            results["kalshi_taker_split_sub25"] = [
                {"side": r[0], "actual_wr": r[1], "n_trades": r[2]}
                for r in taker_split
            ]
        except Exception as e:
            results["kalshi_taker_split_error"] = str(e)

        # ── Polymarket: Fine-grained bins (if poly_crypto exists) ──
        try:
            poly_bins = con.execute("""
                WITH priced AS (
                    SELECT m.side, t.outcome_prices,
                           CAST(t.taker_amount AS DOUBLE) / NULLIF(t.maker_amount, 0) AS token_price
                    FROM poly_crypto AS t
                    JOIN poly_crypto_markets AS m ON t.maker_asset_id = m.token_id
                    WHERE t.maker_amount > 0 AND t.taker_amount > 0
                      AND t.outcome_prices IS NOT NULL
                    UNION ALL
                    SELECT m.side, t.outcome_prices,
                           CAST(t.maker_amount AS DOUBLE) / NULLIF(t.taker_amount, 0) AS token_price
                    FROM poly_crypto AS t
                    JOIN poly_crypto_markets AS m ON t.taker_asset_id = m.token_id
                    WHERE t.maker_amount > 0 AND t.taker_amount > 0
                      AND t.outcome_prices IS NOT NULL
                ),
                yes_priced AS (
                    SELECT
                        CASE WHEN side = 'yes' THEN token_price ELSE 1.0 - token_price END AS yes_price,
                        CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) AS resolved_yes
                    FROM priced
                    WHERE token_price BETWEEN 0.0 AND 1.0
                ),
                bins AS (
                    SELECT
                        FLOOR(yes_price * 20) * 5 AS bin_cents,
                        AVG(resolved_yes) AS actual_wr,
                        COUNT(*) AS n_trades
                    FROM yes_priced
                    WHERE yes_price BETWEEN 0.05 AND 0.95
                      AND resolved_yes IS NOT NULL
                    GROUP BY bin_cents
                )
                SELECT
                    bin_cents,
                    bin_cents / 100.0 AS implied_prob,
                    actual_wr,
                    actual_wr - bin_cents / 100.0 AS delta,
                    n_trades
                FROM bins
                ORDER BY bin_cents
            """).fetchall()
            results["poly_bins"] = [
                {
                    "bin_cents": r[0], "implied": r[1], "actual": r[2],
                    "delta": r[3], "n_trades": r[4],
                }
                for r in poly_bins
            ]
        except Exception as e:
            if "does not exist" in str(e):
                results["poly_note"] = "poly_crypto table not built (BECKER_SKIP_POLY was set)"
            else:
                results["poly_error"] = str(e)

        # ── Polymarket: Sub-25c aggregate ──
        try:
            poly_sub25 = con.execute("""
                WITH priced AS (
                    SELECT m.side, t.outcome_prices,
                           CAST(t.taker_amount AS DOUBLE) / NULLIF(t.maker_amount, 0) AS token_price
                    FROM poly_crypto AS t
                    JOIN poly_crypto_markets AS m ON t.maker_asset_id = m.token_id
                    WHERE t.maker_amount > 0 AND t.taker_amount > 0
                      AND t.outcome_prices IS NOT NULL
                    UNION ALL
                    SELECT m.side, t.outcome_prices,
                           CAST(t.maker_amount AS DOUBLE) / NULLIF(t.taker_amount, 0) AS token_price
                    FROM poly_crypto AS t
                    JOIN poly_crypto_markets AS m ON t.taker_asset_id = m.token_id
                    WHERE t.maker_amount > 0 AND t.taker_amount > 0
                      AND t.outcome_prices IS NOT NULL
                ),
                yes_priced AS (
                    SELECT
                        CASE WHEN side = 'yes' THEN token_price ELSE 1.0 - token_price END AS yes_price,
                        CAST(json_extract_string(outcome_prices, '$[0]') AS DOUBLE) AS resolved_yes
                    FROM priced
                    WHERE token_price BETWEEN 0.0 AND 1.0
                )
                SELECT
                    AVG(resolved_yes) AS actual_wr,
                    AVG(yes_price) AS avg_implied,
                    COUNT(*) AS n_trades
                FROM yes_priced
                WHERE yes_price BETWEEN 0.15 AND 0.25
                  AND resolved_yes IS NOT NULL
            """).fetchone()
            results["poly_sub25"] = {
                "actual_wr": poly_sub25[0], "avg_implied": poly_sub25[1],
                "n_trades": poly_sub25[2],
                "mispricing_gap": poly_sub25[0] - poly_sub25[1] if poly_sub25[0] and poly_sub25[1] else None,
            }
        except Exception as e:
            if "does not exist" not in str(e):
                results["poly_sub25_error"] = str(e)

    finally:
        con.close()

    return results


def format_report(results: dict) -> str:
    """Format analysis results as a human-readable report."""
    lines = []
    lines.append("=" * 60)
    lines.append("  BECKER SUB-25c ZONE ANALYSIS — Phase 60 P2-2")
    lines.append("=" * 60)

    if "error" in results:
        lines.append(f"\n  ERROR: {results['error']}")
        return "\n".join(lines)

    # Kalshi bins
    if "kalshi_bins" in results:
        lines.append("\n── KALSHI: Full Calibration Curve (5c bins) ──")
        lines.append(f"{'Bin':>6} {'Implied':>8} {'Actual':>8} {'δ(p)':>8} {'Trades':>8} {'Mkts':>6}")
        lines.append("-" * 50)
        for b in results["kalshi_bins"]:
            marker = " ◀" if 15 <= b["bin_cents"] <= 25 else ""
            lines.append(
                f"{b['bin_cents']:>5}c {b['implied']:>7.1%} {b['actual']:>7.1%} "
                f"{b['delta']:>+7.1%} {b['n_trades']:>8,} {b['n_markets']:>6}{marker}"
            )

    # Kalshi sub-25 aggregate
    if "kalshi_sub25" in results:
        s = results["kalshi_sub25"]
        lines.append("\n── KALSHI: Sub-25c Zone Aggregate (15-25c) ──")
        if s["actual_wr"] is not None:
            lines.append(f"  Avg implied price:  {s['avg_implied']:.1%}")
            lines.append(f"  Actual resolution:  {s['actual_wr']:.1%}")
            lines.append(f"  Mispricing gap:     {s['mispricing_gap']:+.1%} ({s['mispricing_gap']*100:+.1f}pt)")
            lines.append(f"  Trades:             {s['n_trades']:,}")
            lines.append(f"  Markets:            {s['n_markets']:,}")

    # Taker side split
    if "kalshi_taker_split_sub25" in results:
        lines.append("\n── KALSHI: YES/NO Taker Split (15-25c zone) ──")
        for r in results["kalshi_taker_split_sub25"]:
            lines.append(f"  {r['side']:>4} takers: WR={r['actual_wr']:.1%} ({r['n_trades']:,} trades)")

    # Poly bins
    if "poly_bins" in results:
        lines.append("\n── POLYMARKET: Full Calibration Curve (5c bins) ──")
        lines.append(f"{'Bin':>6} {'Implied':>8} {'Actual':>8} {'δ(p)':>8} {'Trades':>8}")
        lines.append("-" * 44)
        for b in results["poly_bins"]:
            marker = " ◀" if 15 <= b["bin_cents"] <= 25 else ""
            lines.append(
                f"{b['bin_cents']:>5}c {b['implied']:>7.1%} {b['actual']:>7.1%} "
                f"{b['delta']:>+7.1%} {b['n_trades']:>8,}{marker}"
            )

    # Poly sub-25 aggregate
    if "poly_sub25" in results:
        s = results["poly_sub25"]
        lines.append("\n── POLYMARKET: Sub-25c Zone Aggregate (15-25c) ──")
        if s["actual_wr"] is not None:
            lines.append(f"  Avg implied price:  {s['avg_implied']:.1%}")
            lines.append(f"  Actual resolution:  {s['actual_wr']:.1%}")
            lines.append(f"  Mispricing gap:     {s['mispricing_gap']:+.1%} ({s['mispricing_gap']*100:+.1f}pt)")
            lines.append(f"  Trades:             {s['n_trades']:,}")

    if "poly_note" in results:
        lines.append(f"\n  Note: {results['poly_note']}")

    # Actionable recommendations
    lines.append("\n" + "=" * 60)
    lines.append("  ACTIONABLE THRESHOLDS")
    lines.append("=" * 60)
    lines.append("  1. FEE_TAIL_LOW can be lowered from 0.15 to 0.10 if")
    lines.append("     sub-25c zone shows consistent positive δ(p)")
    lines.append("  2. Becker boost weight should be HIGHER in 15-25c zone")
    lines.append("     (more mispricing = more edge to capture)")
    lines.append("  3. Kelly sizing can be more aggressive in this zone")
    lines.append("     (higher WR than implied = bigger Kelly fraction)")
    lines.append("  4. Maker orders preferred (wider spreads in low-prob zone)")

    return "\n".join(lines)


def format_telegram(results: dict) -> str:
    """Format as compact HTML for Telegram /becker_zones command."""
    if "error" in results:
        return f"❌ {results['error']}"

    lines = ["<b>📊 Becker Sub-25c Zone Analysis</b>\n"]

    # Kalshi sub-25 aggregate
    if "kalshi_sub25" in results:
        s = results["kalshi_sub25"]
        if s.get("actual_wr") is not None:
            gap = s["mispricing_gap"]
            lines.append("<b>Kalshi 15-25c Zone:</b>")
            lines.append(f"  Implied: {s['avg_implied']:.1%} → Actual: {s['actual_wr']:.1%}")
            lines.append(f"  Gap: <b>{gap:+.1%}</b> ({s['n_trades']:,} trades)")

    # Poly sub-25 aggregate
    if "poly_sub25" in results:
        s = results["poly_sub25"]
        if s.get("actual_wr") is not None:
            gap = s["mispricing_gap"]
            lines.append(f"\n<b>Polymarket 15-25c Zone:</b>")
            lines.append(f"  Implied: {s['avg_implied']:.1%} → Actual: {s['actual_wr']:.1%}")
            lines.append(f"  Gap: <b>{gap:+.1%}</b> ({s['n_trades']:,} trades)")

    # Kalshi full curve (compact)
    if "kalshi_bins" in results:
        lines.append("\n<b>Kalshi δ(p) Curve:</b>")
        lines.append("<pre>")
        for b in results["kalshi_bins"]:
            bar = "█" * max(1, int(abs(b["delta"]) * 100))
            sign = "+" if b["delta"] > 0 else ""
            marker = " ◀" if 15 <= b["bin_cents"] <= 25 else ""
            lines.append(f"{b['bin_cents']:>3}c {sign}{b['delta']:.1%} {bar}{marker}")
        lines.append("</pre>")

    if "poly_note" in results:
        lines.append(f"\n<i>{results['poly_note']}</i>")

    return "\n".join(lines)


if __name__ == "__main__":
    results = run_analysis()
    print(format_report(results))
