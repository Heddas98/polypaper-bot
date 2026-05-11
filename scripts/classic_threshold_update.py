"""
scripts/classic_threshold_update.py — Classic strategy odds_threshold değiştirici.

ROOT CAUSE: Classic stratejinin threshold'u 0.85 idi. Market'te 5m BTC'de
up/down odds çoğu zaman 0.30-0.70 arasında; nadiren 0.80+. 0.85'i hiç
geçmediği için strateji asla fire etmedi.

Kullanım:
    py -3.11 scripts/classic_threshold_update.py show
    py -3.11 scripts/classic_threshold_update.py update 0.70
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

# Windows cp1252 console fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data_store" / "polypaper.db"


def show() -> int:
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute(
        "SELECT id, label, strategy_type, status, odds_threshold, direction, "
        "trade_amount, asset, timeframe "
        "FROM strategies WHERE strategy_type='classic'"
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("[show] No classic strategies found.")
        return 1

    print(f"[show] {len(rows)} classic strategy/ies:")
    for r in rows:
        print(
            f"  id={r[0]} label={r[1]!r} type={r[2]} status={r[3]} "
            f"threshold={r[4]} direction={r[5]} trade_amount=${r[6]} "
            f"asset={r[7]} tf={r[8]}"
        )
    return 0


def update(new_threshold: float) -> int:
    if not (0.10 <= new_threshold <= 0.95):
        print(f"[update] FAIL: threshold out of range (0.10..0.95): {new_threshold}")
        return 1

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute(
        "UPDATE strategies SET odds_threshold=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE strategy_type='classic'",
        (new_threshold,),
    )
    n = cur.rowcount
    conn.commit()
    # Update label if it contains old threshold pattern
    cur.execute(
        "UPDATE strategies SET label = REPLACE(label, '_0.85', ?) "
        "WHERE strategy_type='classic' AND label LIKE '%_0.85'",
        (f"_{new_threshold:.2f}",),
    )
    conn.commit()
    conn.close()
    print(f"[update] OK: {n} classic strategy row(s) updated -> threshold={new_threshold}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: classic_threshold_update.py (show | update <float>)")
        return 2
    cmd = sys.argv[1]
    if cmd == "show":
        return show()
    if cmd == "update":
        if len(sys.argv) < 3:
            print("Usage: classic_threshold_update.py update <float>")
            return 2
        try:
            t = float(sys.argv[2])
        except ValueError:
            print(f"[update] FAIL: not a float: {sys.argv[2]}")
            return 1
        return update(t)
    print(f"Unknown command: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
