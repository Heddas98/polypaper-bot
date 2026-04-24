"""T4.6-B probe — hangi registered strategy last_n=50 markets'ta trade üretiyor?

T4.6 sweep `hour_edge` default ile 0 trade çıkmıştı (muhtemelen saat-tetikleyici
son 20 markets'ta tutmadı). Önce `trade üretiyor` olan 1-2 strategy bul, sonra
T4.6-B full sweep (`sweep_fill_heuristic.py`) onlarla koş.

Usage (sandbox veya Windows):
    python scripts/_probe_trade_producing_strategies.py
    python scripts/_probe_trade_producing_strategies.py --markets 100

Read-only DB. Bot-safe.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Adayları elle seç — karışık bir set (backtest-native + live adapter)
CANDIDATES = [
    "late_convergence",      # minutes-before-end tetikleyici, her market'ta tetiklenebilir
    "streak_reversal",
    "opening_breakout",
    "taker_flow",
    "orderbook_imbalance",
    "contrarian",            # live adapter (T4.5-C en çok trade)
    "classic",               # live adapter
    "fusion",                # live adapter (aktif SOL b991bc34)
    "hour_edge",             # baseline (0 trade çıktığı kanıtlı)
]


async def probe(strategy: str, markets: int) -> dict:
    """Küçük replay — 1 strategy × last_n markets. Sadece trade sayısını al."""
    from db.database import Database
    from backtest.replay_engine import ReplayEngine, ReplayConfig
    import backtest.strategies  # trigger @register

    db = Database(str(REPO_ROOT / "data_store" / "polypaper.db"))
    await db.initialize()
    try:
        cfg = ReplayConfig(
            strategy_name=strategy,
            initial_balance=10000.0,
            trade_amount=1.0,
            fill_mode="real_orderbook",
            last_n=markets,
        )
        try:
            engine = ReplayEngine(db, cfg)
            stats = await engine.run()
            return {
                "strategy": strategy,
                "trades": getattr(stats, "total_trades", 0),
                "wins": getattr(stats, "wins", 0),
                "losses": getattr(stats, "losses", 0),
                "pnl": round(getattr(stats, "total_pnl", 0.0), 4),
                "err": None,
            }
        except (ValueError, KeyError, AttributeError, TypeError) as e:
            return {"strategy": strategy, "trades": 0, "err": str(e)[:120]}
    finally:
        try:
            await db.close()
        except (AttributeError, RuntimeError):
            pass


async def amain() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markets", type=int, default=50)
    args = parser.parse_args()

    print(f"[probe] last_n={args.markets} markets, {len(CANDIDATES)} strateji denenecek")
    print()
    results = []
    for s in CANDIDATES:
        print(f"[probe] {s} ...", flush=True)
        r = await probe(s, args.markets)
        results.append(r)
        if r.get("err"):
            print(f"    ERR: {r['err']}")
        else:
            print(f"    trades={r['trades']}  wins={r.get('wins', 0)}  "
                  f"losses={r.get('losses', 0)}  pnl={r.get('pnl', 0):+.2f}")

    print()
    print("=" * 60)
    print("Trade üreten stratejiler (desc):")
    print("=" * 60)
    good = [r for r in results if r.get("trades", 0) > 0]
    good.sort(key=lambda x: -x["trades"])
    for r in good:
        print(f"  {r['strategy']:<22} trades={r['trades']:<5} "
              f"pnl={r.get('pnl', 0):+.2f}  wr="
              f"{(r.get('wins', 0) / max(1, r['trades']) * 100):.0f}%")

    if not good:
        print("  (hiçbiri trade üretmedi — last_n'i arttır veya farklı aday)")
        return 1

    print()
    print(f"[probe] ÖNERİ — full sweep için: {good[0]['strategy']}")
    print(f"  py -3.11 scripts/sweep_fill_heuristic.py "
          f"--strategy {good[0]['strategy']} --markets 200")

    # Write "best strategy" to a sentinel file for the .bat pipeline
    sentinel = REPO_ROOT / "backtest" / "calibration" / "_probe_best_strategy.txt"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(good[0]["strategy"], encoding="utf-8")
    print(f"[probe] sentinel yazildi: {sentinel.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
