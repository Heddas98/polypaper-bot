r"""
Phase 82c Task #15 — AI_F_* strategies -105% loss diagnosis
============================================================

Queries the live DB (bot-safe: separate read-only connection)
to inspect AI_F_* strategy executions:
  - Total trades / WR per strategy
  - Avg entry price, avg PnL, fee breakdown
  - Recent 10 losing trades detail
  - Win vs Loss distribution by entry price bucket

Usage:
    py -3.11 tools\diag_ai_f_loss.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data_store" / "polypaper.db"


def main() -> int:
    if not DB_PATH.exists():
        print(f"[ERR] DB not found: {DB_PATH}")
        return 1

    # Read-only URI — bot-safe
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row

    try:
        # 1. AI_F_* strategy list
        print("=" * 70)
        print(" [1] AI_F_* strategies — list")
        print("=" * 70)
        rows = conn.execute(
            "SELECT id,label,strategy_type,status,odds_threshold,trade_amount,"
            "take_profit_odds,stop_loss_odds,created_at "
            "FROM strategies WHERE label LIKE 'AI_F_%' "
            "ORDER BY created_at DESC"
        ).fetchall()
        print(f"  Count: {len(rows)}")
        for r in rows:
            print(
                f"  {r['label']:30s} status={r['status']:12s} "
                f"thr={r['odds_threshold']} amt=${r['trade_amount']:.2f} "
                f"tp={r['take_profit_odds']} sl={r['stop_loss_odds']} "
                f"created={r['created_at'][:19] if r['created_at'] else 'NULL'}"
            )

        # 2. Executions summary per AI_F strategy
        print()
        print("=" * 70)
        print(" [2] AI_F_* executions summary")
        print("=" * 70)
        rows = conn.execute(
            """SELECT s.label,
                      COUNT(e.id) AS total,
                      SUM(CASE WHEN e.result='won' THEN 1 ELSE 0 END) AS wins,
                      SUM(CASE WHEN e.result='lost' THEN 1 ELSE 0 END) AS losses,
                      ROUND(SUM(e.pnl),2) AS total_pnl,
                      ROUND(AVG(e.pnl),4) AS avg_pnl,
                      ROUND(AVG(e.execution_price),4) AS avg_entry,
                      ROUND(AVG(e.fee_amount),4) AS avg_fee,
                      ROUND(AVG(e.signal_score),3) AS avg_sig
               FROM strategies s
               JOIN executions e ON e.strategy_id=s.id
               WHERE s.label LIKE 'AI_F_%'
                 AND e.status IN ('claimed','closed','settled')
               GROUP BY s.label
               ORDER BY total DESC"""
        ).fetchall()
        if not rows:
            print("  (no settled AI_F_* executions)")
        else:
            hdr = (
                f"  {'label':30s} {'N':>4s} {'W':>3s} {'L':>3s} "
                f"{'PnL':>8s} {'avg':>7s} {'entry':>6s} {'fee':>6s} {'sig':>5s}"
            )
            print(hdr)
            for r in rows:
                print(
                    f"  {r['label']:30s} {r['total']:>4d} "
                    f"{r['wins'] or 0:>3d} {r['losses'] or 0:>3d} "
                    f"{r['total_pnl']:>+8.2f} {r['avg_pnl']:>+7.4f} "
                    f"{r['avg_entry']:>6.4f} {r['avg_fee']:>6.4f} "
                    f"{r['avg_sig'] or 0:>5.3f}"
                )

        # 3. Recent 15 AI_F trades full detail
        print()
        print("=" * 70)
        print(" [3] Recent 15 AI_F_* trades — full detail")
        print("=" * 70)
        rows = conn.execute(
            """SELECT s.label, e.direction, e.execution_price, e.trade_amount,
                      e.fee_amount, e.pnl, e.payout, e.result, e.signal_score,
                      e.event_slug, e.created_at, e.closed_at,
                      e.duration_sec, e.max_favorable_move, e.max_adverse_move
               FROM executions e
               JOIN strategies s ON s.id=e.strategy_id
               WHERE s.label LIKE 'AI_F_%'
                 AND e.status IN ('claimed','closed','settled')
               ORDER BY e.created_at DESC LIMIT 15"""
        ).fetchall()
        if not rows:
            print("  (no recent AI_F_* trades)")
        else:
            for r in rows:
                print(
                    f"  {r['label'][:25]:25s} {r['direction']:4s} "
                    f"@{r['execution_price']:.4f} ${r['trade_amount']:.2f} "
                    f"fee={r['fee_amount']:.4f} pnl={r['pnl']:+.2f} "
                    f"payout={r['payout']:.2f} {r['result']:4s} "
                    f"sig={(r['signal_score'] or 0):.2f} "
                    f"dur={r['duration_sec'] or 0}s "
                    f"{r['event_slug'][:30]}"
                )

        # 4. Entry price distribution (won vs lost)
        print()
        print("=" * 70)
        print(" [4] Entry price distribution — won vs lost")
        print("=" * 70)
        rows = conn.execute(
            """SELECT
                  CASE
                    WHEN execution_price < 0.3 THEN '<30c'
                    WHEN execution_price < 0.4 THEN '30-40c'
                    WHEN execution_price < 0.5 THEN '40-50c'
                    WHEN execution_price < 0.6 THEN '50-60c'
                    WHEN execution_price < 0.7 THEN '60-70c'
                    WHEN execution_price < 0.8 THEN '70-80c'
                    ELSE '80c+'
                  END AS bucket,
                  result,
                  COUNT(*) AS n,
                  ROUND(SUM(pnl),2) AS pnl_sum
               FROM executions e
               JOIN strategies s ON s.id=e.strategy_id
               WHERE s.label LIKE 'AI_F_%'
                 AND e.status IN ('claimed','closed','settled')
               GROUP BY bucket, result
               ORDER BY bucket, result"""
        ).fetchall()
        if not rows:
            print("  (no data)")
        else:
            print(f"  {'bucket':>8s} {'result':>8s} {'N':>4s} {'PnL':>8s}")
            for r in rows:
                print(
                    f"  {r['bucket']:>8s} {(r['result'] or '?'):>8s} "
                    f"{r['n']:>4d} {r['pnl_sum']:>+8.2f}"
                )

        # 5. Reason analysis — why did they lose?
        print()
        print("=" * 70)
        print(" [5] Reason field analysis (if populated)")
        print("=" * 70)
        # check if 'reason' / 'exit_reason' column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info('executions')").fetchall()]
        reason_col = None
        for cand in ("exit_reason", "reason", "close_reason"):
            if cand in cols:
                reason_col = cand
                break
        if reason_col:
            rows = conn.execute(
                f"""SELECT {reason_col} AS r, COUNT(*) AS n,
                           ROUND(SUM(pnl),2) AS pnl_sum
                    FROM executions e
                    JOIN strategies s ON s.id=e.strategy_id
                    WHERE s.label LIKE 'AI_F_%' AND e.pnl IS NOT NULL
                    GROUP BY {reason_col}
                    ORDER BY n DESC"""
            ).fetchall()
            print(f"  reason column: {reason_col}")
            for r in rows:
                print(f"    {(r['r'] or '(null)'):30s} n={r['n']:>4d} pnl={r['pnl_sum']:>+8.2f}")
        else:
            print(f"  (no reason column in executions; cols: {cols})")

        # 6. Settlement path ratio (orphan vs normal)
        print()
        print("=" * 70)
        print(" [6] Settlement status distribution")
        print("=" * 70)
        rows = conn.execute(
            """SELECT e.status, COUNT(*) AS n, ROUND(SUM(pnl),2) AS pnl_sum
               FROM executions e
               JOIN strategies s ON s.id=e.strategy_id
               WHERE s.label LIKE 'AI_F_%'
               GROUP BY e.status
               ORDER BY n DESC"""
        ).fetchall()
        for r in rows:
            print(
                f"    status={(r['status'] or '?'):15s} n={r['n']:>4d} pnl={r['pnl_sum'] or 0:>+8.2f}"
            )

        print()
        print("=" * 70)
        print(" [DONE]")
        print("=" * 70)

    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
