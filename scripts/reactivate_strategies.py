"""
Phase 79b: Reactivate strategies after signal fusion cleanup.

Run with: py -3.11 scripts/reactivate_strategies.py

This script:
1. Reactivates fusion, sniper, momentum, contrarian strategies
2. Resets adaptive threshold inflation (scalper 0.90 -> 0.60)
3. Leaves flashcrash and highthreshold as-is (niche, low frequency)
"""
import asyncio
import aiosqlite
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data_store", "polypaper.db")


async def main():
    db = await aiosqlite.connect(DB_PATH, timeout=30)
    db.row_factory = aiosqlite.Row

    print("=== MEVCUT STRATEJI DURUMU ===")
    async with db.execute(
        "SELECT id, label, strategy_type, status, odds_threshold FROM strategies ORDER BY status, label"
    ) as cur:
        rows = await cur.fetchall()
        for r in rows:
            print(f"  {r['status']:>7} | {r['id'][:8]} | {(r['label'] or ''):30} | {(r['strategy_type'] or ''):12} | thr={r['odds_threshold']}")

    print()

    # 1. Reactivate fusion, sniper, momentum, contrarian (if stopped)
    reactivate_types = ("fusion", "sniper", "momentum", "contrarian")
    result = await db.execute(
        f"UPDATE strategies SET status='active' WHERE status='stopped' AND strategy_type IN ({','.join('?' for _ in reactivate_types)})",
        reactivate_types
    )
    print(f"[1] Reactivated {result.rowcount} strategies (types: {', '.join(reactivate_types)})")

    # 2. Reset inflated thresholds (> 0.80 back to reasonable defaults)
    # Different defaults per type
    threshold_resets = {
        "fusion": 0.55,
        "scalper": 0.60,
        "momentum": 0.50,
        "contrarian": 0.45,
        "sniper": 0.50,
        "martingale": 0.40,
    }
    for stype, default_thr in threshold_resets.items():
        result2 = await db.execute(
            "UPDATE strategies SET odds_threshold=? WHERE strategy_type=? AND odds_threshold > 0.80",
            (default_thr, stype)
        )
        if result2.rowcount > 0:
            print(f"[2] Reset {result2.rowcount} '{stype}' thresholds (>0.80 -> {default_thr})")

    await db.commit()

    print()
    print("=== GUNCELLENMIS STRATEJI DURUMU ===")
    async with db.execute(
        "SELECT id, label, strategy_type, status, odds_threshold FROM strategies ORDER BY status, label"
    ) as cur:
        rows = await cur.fetchall()
        active_count = 0
        for r in rows:
            if r['status'] == 'active':
                active_count += 1
            print(f"  {r['status']:>7} | {r['id'][:8]} | {(r['label'] or ''):30} | {(r['strategy_type'] or ''):12} | thr={r['odds_threshold']}")
        print(f"\n  TOPLAM AKTIF: {active_count}")

    await db.close()
    print("\nDone. Bot'u restart et: deploy_sprint5.bat")


if __name__ == "__main__":
    asyncio.run(main())
