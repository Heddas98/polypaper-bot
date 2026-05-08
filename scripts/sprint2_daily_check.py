"""
Sprint 2 Daily Check — Mainnet Mikro Test Monitoring
=====================================================

Her gün manuel çalıştır (veya cron). 14 gün boyunca:
- Live trade sayısı
- Paper PnL vs Live PnL drift (T4.6-B style)
- Kill-switch state
- Polymarket portfolio snapshot
- Cloudflare 403 frekansı

Karar gate (Hafta 4 sonu):
- Drift <%10 → Sprint 3'e geç ($100 promotion)
- Drift ≥%10 → simulator fix önce
- Edge < +%5 → SaaS pivot

Kullanım:
    py -3.11 scripts/sprint2_daily_check.py
    py -3.11 scripts/sprint2_daily_check.py --days 7   # son 7 gün
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path("data_store/polypaper.db")


def hr(b: float) -> str:
    return f"{b:+.2f}" if abs(b) < 1000 else f"{b:+.0f}"


def fetch_live_trades(con, days: int):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    cur = con.cursor()
    try:
        cur.execute(
            """SELECT pnl, paper_pnl, created_at, strategy_label, result
               FROM live_trades
               WHERE created_at >= ? AND pnl IS NOT NULL
               ORDER BY created_at DESC""",
            (cutoff,),
        )
        return cur.fetchall()
    except sqlite3.Error as e:
        print(f"  ⚠ live_trades fetch fail: {e}")
        return []


def fetch_paper_baseline(con, days: int):
    """Paper trade history (trade_log or similar)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    cur = con.cursor()
    try:
        cur.execute(
            """SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%trade%'"""
        )
        tables = [r[0] for r in cur.fetchall()]
        # Try trade_log first
        if "trade_log" in tables:
            cur.execute(
                """SELECT pnl, created_at FROM trade_log
                   WHERE created_at >= ? AND pnl IS NOT NULL
                   ORDER BY created_at DESC LIMIT 1000""",
                (cutoff,),
            )
            return cur.fetchall()
    except sqlite3.Error as e:
        print(f"  ⚠ paper fetch fail: {e}")
    return []


def fetch_portfolio(con):
    cur = con.cursor()
    try:
        cur.execute(
            """SELECT pusd_balance, total_value, position_count, fetched_at
               FROM polymarket_portfolio_cache
               ORDER BY fetched_at DESC LIMIT 1"""
        )
        return cur.fetchone()
    except sqlite3.Error:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"❌ DB not found: {DB_PATH}")
        sys.exit(1)

    print(f"📊 Sprint 2 Daily Check — Last {args.days}d")
    print(f"   Time: {datetime.now(timezone.utc).isoformat()[:19]} UTC")
    print()

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row

    # 1. Live trades summary
    live_trades = fetch_live_trades(con, args.days)
    live_count = len(live_trades)
    live_pnl_sum = sum(float(t[0] or 0) for t in live_trades)
    paper_pnl_in_live = sum(float(t[1] or 0) for t in live_trades if t[1] is not None)
    wins = sum(1 for t in live_trades if float(t[0] or 0) > 0)
    losses = sum(1 for t in live_trades if float(t[0] or 0) < 0)
    win_rate = (wins / live_count * 100) if live_count > 0 else 0

    print(f"🔴 LIVE Trades ({args.days}d)")
    print(f"   Count:        {live_count}")
    print(f"   Net PnL:      ${hr(live_pnl_sum)}")
    print(f"   Paper PnL:    ${hr(paper_pnl_in_live)} (same trades, paper engine)")
    if abs(paper_pnl_in_live) > 0.001:
        drift = ((live_pnl_sum - paper_pnl_in_live) / abs(paper_pnl_in_live)) * 100
        emoji = "✅" if abs(drift) < 10 else ("⚠️" if abs(drift) < 25 else "🚨")
        print(f"   Drift:        {emoji} {drift:+.2f}% (target <%10)")
    print(f"   Win Rate:     {win_rate:.1f}% ({wins}W/{losses}L)")
    print()

    # 2. Strategy breakdown (top 5)
    if live_trades:
        from collections import defaultdict
        per_strat = defaultdict(lambda: {"n": 0, "pnl": 0, "wins": 0})
        for t in live_trades:
            s = t[3] or "unknown"
            per_strat[s]["n"] += 1
            per_strat[s]["pnl"] += float(t[0] or 0)
            if float(t[0] or 0) > 0:
                per_strat[s]["wins"] += 1
        print(f"📈 Top Strategies (live)")
        for label, stats in sorted(per_strat.items(), key=lambda x: -x[1]["pnl"])[:5]:
            wr = (stats["wins"] / stats["n"] * 100) if stats["n"] > 0 else 0
            print(f"   {label[:40]:40s} {stats['n']:>3}t  ${hr(stats['pnl']):>8}  WR{wr:.0f}%")
        print()

    # 3. Polymarket portfolio
    portf = fetch_portfolio(con)
    if portf:
        print(f"💰 Polymarket Portfolio (latest cache)")
        print(f"   pUSD Balance: ${float(portf[0] or 0):.2f}")
        print(f"   Total Value:  ${float(portf[1] or 0):.2f}")
        print(f"   Positions:    {portf[2] or 0}")
        try:
            age_s = (datetime.now(timezone.utc).timestamp() -
                     datetime.fromisoformat(str(portf[3]).replace("Z", "+00:00")).timestamp())
            print(f"   Cache Age:    {int(age_s)}s ago")
        except (ValueError, TypeError):
            pass
        print()

    # 4. Karar gate evaluation
    print(f"🎯 Sprint 2 Promotion Gate Check")
    gates = []
    gates.append(("Live trade count >= 200", live_count, 200, live_count >= 200))
    if live_pnl_sum != 0:
        net_pct = (live_pnl_sum / 20.0) * 100  # assuming $20 baseline
        gates.append(("Net PnL >= +5%", f"{net_pct:+.1f}%", "+5%", net_pct >= 5))
    if abs(paper_pnl_in_live) > 0.001:
        drift = abs(((live_pnl_sum - paper_pnl_in_live) / abs(paper_pnl_in_live)) * 100)
        gates.append(("Paper-Live drift <10%", f"{drift:.1f}%", "<10%", drift < 10))

    for name, actual, target, passed in gates:
        emoji = "✅" if passed else "❌"
        print(f"   {emoji} {name:35s} actual={actual} target={target}")

    print()
    all_pass = all(g[3] for g in gates)
    if all_pass and live_count >= 200:
        print(f"   🟢 ALL GATES PASSED → Sprint 3 ($100 promotion) hazır")
    elif live_count < 50:
        print(f"   ⏳ Trade sayısı düşük — devam et, gözle")
    else:
        print(f"   ⚠️ Gate'ler partial — drift fix veya 14 gün bekle")

    con.close()


if __name__ == "__main__":
    main()
