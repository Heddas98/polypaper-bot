#!/usr/bin/env python3
"""
Phase 79: Strategy Cleanup — Stop Losing Strategies, Keep 4 Profitable
========================================================================
From Telegram test data (Phase 79 mega diagnosis), identified clear winners and losers.
This script stops the 11 losing strategies and ensures the 4 profitable ones remain active.

PROFITABLE STRATS (KEEP ACTIVE):
  1. BTC Martingale DCA: 81 trades, +$8.34
  2. BTC Contrarian Dip: 80 trades, +$6.05
  3. SOL Contrarian Dip: 19 trades, +$2.32
  4. ETH Contrarian Dip: 22 trades, +$1.70

LOSING STRATS (STOP):
  • BTC High-Threshold Pure: -$4.23
  • BTC Streak Reversal: -$2.86
  • XRP Contrarian Dip: -$2.51
  • DOWN Bias Fusion: -$2.10
  • Sweet Spot Fusion: -$2.02
  • ETH Martingale DCA: -$1.18
  • ETH Momentum Trend: -$0.63
  • BTC Scalper Quick: -$0.48
  • BTC Flash Crash Hunter: -$0.14
  • BTC Momentum Trend: -$0.10
  • Conservative Sniper: $0.00 (0 trades)

USAGE:  py -3.11 scripts/strategy_cleanup_phase79.py
        (Bot can be running; cleanup is low-impact database UPDATE)

Exit codes:
  0 = Success
  1 = Database error
  2 = Validation failed
"""

import asyncio
import sys
import os
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

DB_PATH = os.getenv("DATABASE_PATH", "data_store/polypaper.db")

# Strategies to keep active (by label — case-sensitive)
PROFITABLE_STRATS = {
    "BTC Martingale DCA",
    "BTC Contrarian Dip",
    "SOL Contrarian Dip",
    "ETH Contrarian Dip",
}

# Strategies to stop (by label)
LOSING_STRATS = {
    "BTC High-Threshold Pure",
    "BTC Streak Reversal",
    "XRP Contrarian Dip",
    "DOWN Bias Fusion",
    "Sweet Spot Fusion",
    "ETH Martingale DCA",
    "ETH Momentum Trend",
    "BTC Scalper Quick",
    "BTC Flash Crash Hunter",
    "BTC Momentum Trend",
    "Conservative Sniper",
}


async def main():
    """Main cleanup routine."""
    try:
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row

        print("=" * 70)
        print("Phase 79: Strategy Cleanup — Profitability Optimization")
        print("=" * 70)
        print()

        # Fetch all strategies
        cursor = await conn.execute("SELECT id, label, status FROM strategies ORDER BY label")
        all_strats = await cursor.fetchall()

        if not all_strats:
            print("WARNING: No strategies found in database.")
            await conn.close()
            return 2

        print(f"Found {len(all_strats)} total strategies in database.\n")

        # Categorize
        to_stop = []
        to_activate = []
        others = []

        for row in all_strats:
            strat_id, label, status = row['id'], row['label'], row['status']

            if label in LOSING_STRATS:
                to_stop.append((strat_id, label, status))
            elif label in PROFITABLE_STRATS:
                to_activate.append((strat_id, label, status))
            else:
                others.append((strat_id, label, status))

        # Report and validate
        print("PROFITABLE (will set to 'active'):")
        for strat_id, label, status in to_activate:
            print(f"  [{status:8}] {label}")
        print()

        if len(to_activate) != len(PROFITABLE_STRATS):
            missing = PROFITABLE_STRATS - {label for _, label, _ in to_activate}
            if missing:
                print(f"WARNING: Expected {len(PROFITABLE_STRATS)} profitable strats, found {len(to_activate)}")
                print(f"  Missing: {missing}")
                print()

        print("LOSING (will set to 'stopped'):")
        for strat_id, label, status in to_stop:
            print(f"  [{status:8}] {label}")
        print()

        if len(to_stop) != len(LOSING_STRATS):
            missing = LOSING_STRATS - {label for _, label, _ in to_stop}
            if missing:
                print(f"WARNING: Expected {len(LOSING_STRATS)} losing strats, found {len(to_stop)}")
                print(f"  Missing: {missing}")
                print()

        if others:
            print(f"OTHER ({len(others)} - not touched):")
            for strat_id, label, status in others:
                print(f"  [{status:8}] {label}")
            print()

        # Confirm before proceeding
        print("=" * 70)
        response = input("Proceed with cleanup? (yes/no): ").strip().lower()
        if response != "yes":
            print("Cleanup cancelled.")
            await conn.close()
            return 0

        print()
        print("Executing updates...")
        print()

        now_iso = datetime.now(timezone.utc).isoformat()

        # Update profitable strats to 'active'
        if to_activate:
            placeholders = ", ".join("?" * len(to_activate))
            ids_activate = [strat_id for strat_id, _, _ in to_activate]
            await conn.execute(
                f"UPDATE strategies SET status = 'active', updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (now_iso, *ids_activate)
            )
            await conn.commit()
            print(f"✓ Activated {len(to_activate)} profitable strategies:")
            for strat_id, label, old_status in to_activate:
                print(f"    {label} ({old_status} → active)")
            print()

        # Update losing strats to 'stopped'
        if to_stop:
            placeholders = ", ".join("?" * len(to_stop))
            ids_stop = [strat_id for strat_id, _, _ in to_stop]
            await conn.execute(
                f"UPDATE strategies SET status = 'stopped', updated_at = ? "
                f"WHERE id IN ({placeholders})",
                (now_iso, *ids_stop)
            )
            await conn.commit()
            print(f"✓ Stopped {len(to_stop)} losing strategies:")
            for strat_id, label, old_status in to_stop:
                print(f"    {label} ({old_status} → stopped)")
            print()

        print("=" * 70)
        print("Cleanup complete!")
        print("=" * 70)

        await conn.close()
        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
