#!/usr/bin/env python3
"""
Phase 61: Complete Strategy Reset — 20 Optimized Strategies
============================================================
Deletes ALL existing strategies and creates 20 new ones covering:
- All 4 assets (BTC, ETH, SOL, XRP)
- All strategy types (momentum, contrarian, scalper, sniper, highthreshold,
  late_convergence, flashcrash, streak, martingale)
- Multiple timeframes (5m, 15m)
- Direction diversity (up, down, any)
- Trade amounts: $1 base (safe paper), proven ones get $2

USAGE:  py -3.11 scripts/reset_strategies_20.py
        (Bot must be STOPPED first — run kill_all_start.bat after)
"""

import asyncio
import uuid
import sys
import os
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

DB_PATH = os.getenv("DATABASE_PATH", "data_store/polypaper.db")

def uid():
    return str(uuid.uuid4())

def now_iso():
    return datetime.now(timezone.utc).isoformat()

# ══════════════════════════════════════════════════════════════════════
# 20 STRATEGY PORTFOLIO — Optimized for Polymarket crypto 5m/15m
# ══════════════════════════════════════════════════════════════════════
#
# Design principles:
# 1. Cover ALL 9 strategy types so every bot function gets exercised
# 2. Diversify across assets — no single-asset concentration
# 3. Use 5m timeframe primarily (most liquid, most opportunities)
# 4. Lower thresholds where safe (0.52-0.65) to catch more trades
# 5. Use 'any' direction mostly — let the strategy decide
# 6. $1 per trade (safe), $2 for historically strong types
# 7. Regime-aware: include strategies for trending AND ranging
#
# Strategy fitness by regime:
#   TRENDING:  momentum ✅, sniper ✅
#   RANGING:   scalper ✅, contrarian ✅, streak ✅
#   VOLATILE:  flashcrash ✅, highthreshold ✅, sniper ✅
#   ALL:       late_convergence ✅ (timing-based, regime-agnostic)

STRATEGIES = [
    # ═══ CORE MONEY MAKERS — Late Convergence (regime-agnostic, 80%+ elapsed) ═══
    {
        "label": "LC-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 2.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.3, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "late_convergence",
    },
    {
        "label": "LC-ETH-5m",
        "asset": "ETH", "timeframe": "5m", "direction": "any",
        "trade_amount": 2.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.3, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "late_convergence",
    },
    {
        "label": "LC-SOL-5m",
        "asset": "SOL", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.3, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "late_convergence",
    },

    # ═══ HIGH THRESHOLD — Ultra safe 80c+ zone, high WR ═══
    {
        "label": "HT-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 2.0, "odds_threshold": 0.78,
        "minutes_before_end": 0.3, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.05,
        "strategy_type": "highthreshold",
    },
    {
        "label": "HT-ETH-5m",
        "asset": "ETH", "timeframe": "5m", "direction": "any",
        "trade_amount": 2.0, "odds_threshold": 0.78,
        "minutes_before_end": 0.3, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.05,
        "strategy_type": "highthreshold",
    },

    # ═══ MOMENTUM — Trend following, best in trending markets ═══
    {
        "label": "MOM-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.58,
        "minutes_before_end": 0.5, "minutes_after_start": 1.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "momentum",
    },
    {
        "label": "MOM-ETH-15m",
        "asset": "ETH", "timeframe": "15m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.58,
        "minutes_before_end": 1.0, "minutes_after_start": 2.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "momentum",
    },

    # ═══ CONTRARIAN — Mean reversion, best in ranging/volatile ═══
    {
        "label": "CTR-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.52,
        "minutes_before_end": 0.5, "minutes_after_start": 1.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "contrarian",
    },
    {
        "label": "CTR-SOL-5m",
        "asset": "SOL", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.52,
        "minutes_before_end": 0.5, "minutes_after_start": 1.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "contrarian",
    },

    # ═══ SCALPER — Quick in/out, best in ranging with tight spreads ═══
    {
        "label": "SCA-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.4, "minutes_after_start": 0.5,
        "max_executions_per_event": 2,  # scalper can fire twice
        "max_entry_slippage": 0.06,
        "strategy_type": "scalper",
    },
    {
        "label": "SCA-ETH-5m",
        "asset": "ETH", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.4, "minutes_after_start": 0.5,
        "max_executions_per_event": 2,
        "max_entry_slippage": 0.06,
        "strategy_type": "scalper",
    },

    # ═══ SNIPER — Multi-check high confidence, all regimes ═══
    {
        "label": "SNI-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 2.0, "odds_threshold": 0.60,
        "minutes_before_end": 0.5, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.06,
        "strategy_type": "sniper",
    },
    {
        "label": "SNI-XRP-5m",
        "asset": "XRP", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.60,
        "minutes_before_end": 0.5, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.06,
        "strategy_type": "sniper",
    },

    # ═══ FLASH CRASH — Volatility spike catcher ═══
    {
        "label": "FC-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.52,
        "minutes_before_end": 0.3, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "flashcrash",
    },
    {
        "label": "FC-ETH-5m",
        "asset": "ETH", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.52,
        "minutes_before_end": 0.3, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "flashcrash",
    },

    # ═══ STREAK REVERSAL — After consecutive losses, bet reversal ═══
    {
        "label": "STR-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.5, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "streak",
    },
    {
        "label": "STR-SOL-5m",
        "asset": "SOL", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 0.5, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "streak",
    },

    # ═══ MARTINGALE — Double down after loss (controlled) ═══
    {
        "label": "MG-BTC-5m",
        "asset": "BTC", "timeframe": "5m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.60,
        "minutes_before_end": 0.5, "minutes_after_start": 0.5,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "max_losses_per_event": 2,  # cap martingale depth
        "strategy_type": "martingale",
    },

    # ═══ 15m DIVERSIFICATION — Longer timeframe for different patterns ═══
    {
        "label": "LC-BTC-15m",
        "asset": "BTC", "timeframe": "15m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.55,
        "minutes_before_end": 1.0, "minutes_after_start": 0.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.08,
        "strategy_type": "late_convergence",
    },
    {
        "label": "SNI-ETH-15m",
        "asset": "ETH", "timeframe": "15m", "direction": "any",
        "trade_amount": 1.0, "odds_threshold": 0.58,
        "minutes_before_end": 1.0, "minutes_after_start": 1.0,
        "max_executions_per_event": 1, "max_entry_slippage": 0.06,
        "strategy_type": "sniper",
    },
]

assert len(STRATEGIES) == 20, f"Expected 20 strategies, got {len(STRATEGIES)}"


async def main():
    print("=" * 60)
    print("  Phase 61: Strategy Reset — 20 Optimized Strategies")
    print("=" * 60)

    if not os.path.exists(DB_PATH):
        print(f"❌ DB not found: {DB_PATH}")
        sys.exit(1)

    async with aiosqlite.connect(DB_PATH, timeout=30) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=10000")

        # Get user_id and wallet_id
        row = await db.execute_fetchall("SELECT id FROM users LIMIT 1")
        if not row:
            print("❌ No user found in DB")
            sys.exit(1)
        user_id = row[0][0]

        row = await db.execute_fetchall(
            "SELECT id FROM wallets WHERE user_id=? AND is_primary=1 LIMIT 1",
            (user_id,))
        if not row:
            row = await db.execute_fetchall(
                "SELECT id FROM wallets WHERE user_id=? LIMIT 1", (user_id,))
        if not row:
            print("❌ No wallet found")
            sys.exit(1)
        wallet_id = row[0][0]

        print(f"✅ User: {user_id[:8]}...")
        print(f"✅ Wallet: {wallet_id[:8]}...")

        # Count existing
        cnt = await db.execute_fetchall("SELECT COUNT(*) FROM strategies")
        old_count = cnt[0][0]
        print(f"\n🗑️  Deleting {old_count} existing strategies (ALL users)...")

        # Nuclear delete — ALL strategies for ALL users
        await db.execute("UPDATE strategies SET status='stopped'")
        await db.execute("DELETE FROM strategies")
        # Verify deletion
        cnt2 = await db.execute_fetchall("SELECT COUNT(*) FROM strategies")
        remaining = cnt2[0][0]
        if remaining > 0:
            print(f"⚠️  {remaining} strategies still remain, forcing deletion...")
            await db.execute("DELETE FROM strategies WHERE 1=1")
        await db.commit()
        print("✅ All strategies deleted")

        # Insert 20 new strategies
        print(f"\n📦 Creating {len(STRATEGIES)} new strategies...\n")
        ts = now_iso()

        for i, s in enumerate(STRATEGIES, 1):
            sid = uid()
            await db.execute(
                """INSERT INTO strategies (
                    id, user_id, wallet_id, label, asset, timeframe,
                    direction, trade_amount, odds_threshold, price_difference,
                    minutes_before_end, minutes_after_start,
                    stop_loss_percent, stop_loss_odds,
                    take_profit_percent, take_profit_odds,
                    max_executions_per_event, max_losses_per_event,
                    max_entry_slippage, ma_filter_enabled, min_volatility,
                    strategy_type, status, started_at, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    sid, user_id, wallet_id,
                    s["label"],
                    s["asset"],
                    s["timeframe"],
                    s["direction"],
                    s["trade_amount"],
                    s["odds_threshold"],
                    None,  # price_difference
                    s.get("minutes_before_end", 0.5),
                    s.get("minutes_after_start", 0.0),
                    None,  # stop_loss_percent
                    None,  # stop_loss_odds
                    None,  # take_profit_percent
                    None,  # take_profit_odds
                    s.get("max_executions_per_event", 1),
                    s.get("max_losses_per_event"),
                    s.get("max_entry_slippage"),
                    0,     # ma_filter_enabled
                    None,  # min_volatility
                    s["strategy_type"],
                    "active",   # Start immediately
                    ts,         # started_at
                    ts,         # created_at
                    ts,         # updated_at
                ))

            typ_short = s["strategy_type"][:4].upper()
            print(f"  [{i:2d}/20] {s['label']:16s} | {s['asset']:3s} {s['timeframe']:3s} | "
                  f"{typ_short:4s} | ${s['trade_amount']:.0f} | thr={s['odds_threshold']:.2f}")

        await db.commit()

        # Verify
        cnt = await db.execute_fetchall("SELECT COUNT(*) FROM strategies WHERE status='active'")
        final = cnt[0][0]
        print(f"\n{'='*60}")
        print(f"✅ {final} strategies created and ACTIVE")
        print(f"   Now restart the bot: kill_all_start.bat")
        print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
