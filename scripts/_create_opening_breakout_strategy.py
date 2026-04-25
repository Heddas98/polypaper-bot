"""Create opening_breakout live strategy instance in DB.

Backtest probe (2026-04-24, last 50 markets):
  trades=11  wins=8  losses=3  pnl=+$4.26  WR=73%

Live strategy: core/strategy_plugins.py:961 OpeningBreakoutLiveStrategy
  - Triggers on first-minute BTC move >= breakout_usd (default $10)
  - Confidence 0.55-0.85 scaled by move size
  - Engine wires plugin_meta["btc_move_usd"] from external_feed (Binance spot)

Spec (conservative shadow start, $1/trade):
  asset:                BTC      (live wires only BTC spot momentum to btc_move_usd)
  timeframe:            5m       (matches probe; BTC 5m has high market frequency)
  direction:            any      (let the breakout sign drive)
  trade_amount:         1.0      ($1/trade Phase 81 doctrine for new live strategies)
  strategy_type:        opening_breakout
  odds_threshold:       0.51     (low — confidence already gated by breakout size)
  stop_loss_percent:    0.20     (Phase 80 default; Sprint S1-04 baseline)
  take_profit_percent:  0.15     (slightly tighter — first-minute moves often retrace)
  max_executions_per_event: 1
  minutes_before_end:   0.5
  minutes_after_start:  0.5      (must enter in first 30s — the "opening" window)
  status:               active   (start in shadow live mode, $1/trade safe)
  deploy_stage:         canary

Usage (Windows):
    py -3.11 scripts/_create_opening_breakout_strategy.py            # create
    py -3.11 scripts/_create_opening_breakout_strategy.py --dry-run  # preview SQL only

After create, restart bot so strategy registry picks up the new row.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DB_PATH = REPO / "data_store" / "polypaper.db"

LABEL = "BTC Opening Breakout (Phase 91 Live)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview INSERT SQL without writing.")
    parser.add_argument("--user-id", default=None,
                        help="Override user_id (default: pick first user).")
    parser.add_argument("--wallet-id", default=None,
                        help="Override wallet_id (default: active wallet of user).")
    parser.add_argument("--trade-amount", type=float, default=1.0)
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # Resolve user + wallet
        uid = args.user_id
        if not uid:
            row = cur.execute("SELECT id FROM users LIMIT 1").fetchone()
            if not row:
                print("No user — start the bot once first.")
                return 1
            uid = row[0]
        wid = args.wallet_id
        if not wid:
            row = cur.execute(
                "SELECT id FROM wallets WHERE user_id=? "
                "ORDER BY balance DESC LIMIT 1", (uid,)).fetchone()
            if not row:
                print(f"No wallet for user {uid}")
                return 1
            wid = row[0]

        # Sanity: don't double-create
        existing = cur.execute(
            "SELECT id, status FROM strategies "
            "WHERE strategy_type='opening_breakout' AND user_id=?",
            (uid,)).fetchall()
        if existing:
            print("ALREADY EXISTS:")
            for r in existing:
                print(f"  id={r['id'][:8]} status={r['status']}")
            print("Use /strategies UI to start/stop, or pass a different user.")
            return 0

        # Build INSERT
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        sql = """INSERT INTO strategies (
            id, user_id, wallet_id, label, asset, timeframe, direction,
            trade_amount, odds_threshold, price_difference,
            minutes_before_end, minutes_after_start,
            stop_loss_percent, take_profit_percent,
            max_executions_per_event, max_losses_per_event,
            max_entry_slippage, ma_filter_enabled, min_volatility,
            strategy_type, status, started_at, created_at, updated_at,
            deploy_stage, strategy_params
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )"""
        params = (
            sid, uid, wid, LABEL, "BTC", "5m", "any",
            args.trade_amount, 0.51, 0.0,
            0.5, 0.5,
            0.20, 0.15,
            1, 3,
            None, 0, 0.0,
            "opening_breakout", "active", now, now, now,
            "canary", '{"breakout_usd": 10.0}',
        )

        if args.dry_run:
            print("=== DRY RUN — SQL preview ===")
            print(sql)
            print()
            print("Params:")
            for i, p in enumerate(params):
                print(f"  [{i}] = {p!r}")
            return 0

        cur.execute(sql, params)
        con.commit()
        print(f"✅ Created opening_breakout strategy:")
        print(f"   id           = {sid}")
        print(f"   label        = {LABEL}")
        print(f"   user_id      = {uid}")
        print(f"   wallet_id    = {wid}")
        print(f"   trade_amount = ${args.trade_amount:.2f}")
        print()
        print("Restart bot to load the new strategy:")
        print("  taskkill /F /IM python.exe  &&  start_bot.bat")
        print("Or via Telegram: /restart (if configured).")
        return 0

    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
