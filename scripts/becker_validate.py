"""
Phase 65: Becker Calibration Validation
========================================
Mevcut trade verisiyle Becker δ(p) curve'ünün doğruluğunu ölçer.
trade_journal.jsonl → bucket by price → compare actual WR vs Becker prediction.

Kullanım: py -3.11 scripts/becker_validate.py
"""
import json
import os
import sys
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

JOURNAL_PATH = PROJECT_ROOT / "data_store" / "trade_journal.jsonl"
CALIB_DB = PROJECT_ROOT / "data_store" / "becker_calibration.db"
WEIGHT_STATE = PROJECT_ROOT / "data_store" / "becker_weight_state.json"

BIN_SIZE = 0.05  # 5% bins


def load_settled_trades():
    """Parse trade_journal.jsonl for settled trades with price + outcome."""
    trades = {}  # order_key → {price, outcome}
    if not JOURNAL_PATH.exists():
        print(f"❌ {JOURNAL_PATH} bulunamadı")
        return []

    with open(JOURNAL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line.strip())
            except Exception:
                continue
            etype = ev.get("event")
            key = ev.get("strategy_id", "") + ":" + ev.get("slug", "")

            if etype == "ENTRY":
                trades[key] = {
                    "price": ev.get("signal_price") or ev.get("limit_price", 0),
                    "direction": ev.get("direction"),
                    "slug": ev.get("slug", ""),
                    "asset": "",
                }
                # Extract asset from slug
                slug = ev.get("slug", "").lower()
                for tok in ("btc", "eth", "sol", "xrp"):
                    if tok in slug:
                        trades[key]["asset"] = tok.upper()
                        break

            elif etype == "SETTLEMENT" and key in trades:
                result = ev.get("result", "").lower()
                if result in ("won", "win", "yes"):
                    trades[key]["outcome"] = 1
                elif result in ("lost", "loss", "no"):
                    trades[key]["outcome"] = 0
                else:
                    trades[key]["outcome"] = None

    return [t for t in trades.values() if t.get("outcome") is not None and t.get("price", 0) > 0]


def load_becker_curves():
    """Load calibration curves from becker_calibration.db."""
    curves = {}
    if not CALIB_DB.exists():
        print(f"❌ {CALIB_DB} bulunamadı")
        return curves

    try:
        conn = sqlite3.connect(str(CALIB_DB))
        for source in ("poly_crypto", "kalshi_crypto"):
            try:
                # Try standard query
                rows = conn.execute(
                    f"""SELECT yes_price_bin * 0.05 + 0.025 as mid,
                           AVG(outcome) as actual_wr,
                           COUNT(*) as n
                    FROM {source}
                    GROUP BY yes_price_bin
                    ORDER BY yes_price_bin"""
                ).fetchall()
                if rows:
                    name = source.replace("_crypto", "")
                    curves[name] = [(r[0], r[1], r[2]) for r in rows]
            except Exception:
                pass

        # Fallback: try the calibration_curve query from becker_loader
        if not curves:
            try:
                from data.becker_loader import BeckerLoader
                bl = BeckerLoader()
                for src in ("poly", "kalshi"):
                    rows = bl.calibration_curve(src)
                    if rows:
                        curves[src] = rows
            except Exception as e:
                print(f"  Loader fallback: {e}")

        conn.close()
    except Exception as e:
        print(f"Becker DB error: {e}")
    return curves


def validate():
    """Main validation logic."""
    print("=" * 60)
    print("  BECKER CALIBRATION VALIDATION — Phase 65")
    print("=" * 60)

    # Load trades
    trades = load_settled_trades()
    print(f"\n📊 Settled trades: {len(trades)}")
    if len(trades) < 20:
        print("⚠️  Yeterli veri yok (min 20 trade gerekli)")
        return

    # Bucket by price
    bins = defaultdict(lambda: {"wins": 0, "total": 0, "trades": []})
    for t in trades:
        p = t["price"]
        bin_key = round(int(p / BIN_SIZE) * BIN_SIZE + BIN_SIZE / 2, 3)
        bins[bin_key]["total"] += 1
        bins[bin_key]["wins"] += t["outcome"]
        bins[bin_key]["trades"].append(t)

    # Print actual WR by price bin
    print(f"\n{'Bin':>8} {'Trades':>7} {'Wins':>5} {'WR':>7} {'δ(p)':>8} {'Verdict':>10}")
    print("-" * 55)

    total_correct = 0
    total_bins = 0
    for bin_mid in sorted(bins.keys()):
        b = bins[bin_mid]
        if b["total"] < 3:
            continue
        wr = b["wins"] / b["total"]
        # δ(p) = actual_wr - price → positive = market underprices YES
        delta = wr - bin_mid
        verdict = "✅ EDGE" if delta > 0.02 else ("⚠️ FLAT" if abs(delta) <= 0.02 else "❌ OVER")
        print(f"  {bin_mid:.2f}c  {b['total']:>5}   {b['wins']:>4}  {wr:>6.1%}  {delta:>+7.3f}   {verdict}")
        if abs(delta) < 0.05:
            total_correct += 1
        total_bins += 1

    # Per-asset breakdown
    print(f"\n📈 Per-Asset Breakdown:")
    assets = defaultdict(lambda: {"wins": 0, "total": 0})
    for t in trades:
        a = t.get("asset") or "?"
        assets[a]["total"] += 1
        assets[a]["wins"] += t["outcome"]

    for a in sorted(assets.keys()):
        d = assets[a]
        wr = d["wins"] / d["total"] * 100 if d["total"] > 0 else 0
        emoji = "🟢" if wr >= 55 else ("🟡" if wr >= 50 else "🔴")
        print(f"  {emoji} {a}: {d['total']}t  {wr:.1f}% WR")

    # Adaptive weight state
    print(f"\n🔧 Adaptive Becker Weight State:")
    if WEIGHT_STATE.exists():
        try:
            state = json.loads(WEIGHT_STATE.read_text())
            for k, v in state.items():
                if k == "last_update_ts":
                    continue
                print(f"  {k}: {v:.4f}x")
        except Exception:
            print("  (okunamadı)")
    else:
        print("  (henüz oluşmamış)")

    # Overall verdict
    overall_wr = sum(t["outcome"] for t in trades) / len(trades) * 100
    print(f"\n{'=' * 55}")
    print(f"  Toplam: {len(trades)} trade, {overall_wr:.1f}% WR")
    if overall_wr >= 55:
        print(f"  🟢 KARAR: Shadow live'a geçiş için uygun")
    elif overall_wr >= 52:
        print(f"  🟡 KARAR: Marjinal — fee sonrası breakeven riski")
    else:
        print(f"  🔴 KARAR: WR düşük — strateji optimizasyonu gerekli")
    print(f"{'=' * 55}")


if __name__ == "__main__":
    validate()
