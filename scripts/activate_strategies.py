"""Activate selected strategies so Phase 47f δ(p) boost can fire live.

Only re-activates strategies that were previously used and have safe
threshold values. Does NOT create new strategies. Bot must be stopped.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data_store" / "polypaper.db"

# IDs or label prefixes to re-activate. Keep narrow.
TARGETS = [
    "F_BTC_5m_any_0.5",         # Original fusion, direction=any
    "AI_F_BTC_5m_any_0.5",      # AI-tuned fusion, direction=any (threshold 0.59)
]


def main() -> int:
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    before = cur.execute(
        "SELECT status, COUNT(*) FROM strategies GROUP BY status"
    ).fetchall()
    print("BEFORE:", before, flush=True)

    activated = 0
    for label in TARGETS:
        row = cur.execute(
            "SELECT id, label, status, odds_threshold FROM strategies WHERE label=?",
            (label,),
        ).fetchone()
        if not row:
            print(f"  [SKIP] label not found: {label}", flush=True)
            continue
        if row[2] == "active":
            print(f"  [SKIP] already active: {label}", flush=True)
            continue
        cur.execute(
            "UPDATE strategies SET status='active' WHERE id=?", (row[0],)
        )
        print(
            f"  [OK] activated id={row[0][:8]}.. label={row[1]} "
            f"thr={row[3]} (was {row[2]})",
            flush=True,
        )
        activated += 1

    conn.commit()
    after = cur.execute(
        "SELECT status, COUNT(*) FROM strategies GROUP BY status"
    ).fetchall()
    print("AFTER:", after, flush=True)
    print(f"Total activated: {activated}", flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
