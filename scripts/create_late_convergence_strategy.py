"""
Phase 47f.7 — Create or activate a paper strategy bound to late_convergence.

Avoids the Telegram /create_strategy wizard by writing directly to the
`strategies` table. Uses the existing user/wallet from prior strategies so
the new row plugs into the same accounting.

Usage (Windows):
  py -3.11 -u scripts\\create_late_convergence_strategy.py [--asset BTC]
                                                            [--timeframe 5m]
                                                            [--label "Phase 47f.7 shadow"]
                                                            [--amount 1.0]
                                                            [--dry-run]

Idempotent: if a strategy with strategy_type='late_convergence' AND the
same asset/timeframe already exists, prints its id and exits 0 without
inserting a duplicate. Sets status='active' so the engine picks it up
on the next scan tick.

Exit:
  0 success (created or already-exists)
  1 db / no-user error
  2 dry-run preview only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data_store" / "polypaper.db"


def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--timeframe", default="5m")
    ap.add_argument("--label", default="Phase 47f.7 late_convergence shadow")
    ap.add_argument("--amount", type=float, default=1.0)
    ap.add_argument("--direction", default="any")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[FATAL] db not found: {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()

        # Reuse the most recent user+wallet (admin chat)
        cur.execute(
            "SELECT id FROM users ORDER BY created_at DESC LIMIT 1"
            if _has_col(cur, "users", "created_at")
            else "SELECT id FROM users LIMIT 1"
        )
        u_row = cur.fetchone()
        if not u_row:
            print("[FATAL] no users in db — start the bot once via Telegram first")
            return 1
        user_id = u_row["id"]

        cur.execute(
            "SELECT id FROM wallets WHERE user_id = ? LIMIT 1", (user_id,)
        )
        w_row = cur.fetchone()
        if not w_row:
            print(f"[FATAL] no wallet for user {user_id}")
            return 1
        wallet_id = w_row["id"]

        # Idempotency check
        cur.execute(
            """SELECT id, status FROM strategies
               WHERE user_id = ? AND strategy_type = ?
                 AND asset = ? AND timeframe = ?
               LIMIT 1""",
            (user_id, "late_convergence", args.asset, args.timeframe),
        )
        existing = cur.fetchone()
        if existing:
            sid = existing["id"]
            print(f"[exists] strategy already present: id={sid} status={existing['status']}")
            if existing["status"] != "active" and not args.dry_run:
                cur.execute(
                    "UPDATE strategies SET status='active', "
                    "started_at=?, updated_at=? WHERE id=?",
                    (now_iso(), now_iso(), sid),
                )
                con.commit()
                print(f"[ok] flipped status -> active")
            return 0

        sid = str(uuid.uuid4())
        ts = now_iso()
        if args.dry_run:
            print(f"[dry-run] would INSERT id={sid}")
            print(f"          user_id={user_id} wallet_id={wallet_id}")
            print(f"          asset={args.asset} timeframe={args.timeframe}")
            print(f"          strategy_type=late_convergence amount={args.amount}")
            return 2

        cur.execute(
            """INSERT INTO strategies (
                 id, user_id, wallet_id, label, asset, timeframe,
                 direction, trade_amount, odds_threshold, minutes_before_end,
                 minutes_after_start, strategy_type, status, started_at,
                 created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, user_id, wallet_id, args.label, args.asset, args.timeframe,
             args.direction, args.amount, 0.80, 2.0, 0.0,
             "late_convergence", "active", ts, ts, ts),
        )
        con.commit()
        print(f"[ok] created strategy id={sid}")
        print(f"     {args.label} | {args.asset} {args.timeframe} | "
              f"${args.amount:.2f}/trade | strategy_type=late_convergence | active")
        return 0
    finally:
        con.close()


def _has_col(cur, table: str, col: str) -> bool:
    try:
        cur.execute(f"PRAGMA table_info({table})")
        return any(r[1] == col for r in cur.fetchall())
    except sqlite3.Error:
        return False


if __name__ == "__main__":
    sys.exit(main())
