"""
scripts/verify_migration_v15.py — Phase 82e Sprint 5 FINAL.

Read-only schema check for hyperopt_results (v15: asset + timeframe).

We intentionally do NOT open the DB via Database.initialize() because
that path runs migrations, tries to WRITE the journal mode, and blocks
when the live bot holds the WAL lock. Instead we open a RO sqlite3
connection using the `file:...?mode=ro&immutable=0` URI.

Exit:
  0  v15 uygulandi (kolonlar mevcut)
  1  kolonlar yok  (migration uygulanmamis)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    db_path = _ROOT / "data_store" / "polypaper.db"
    print(f"[v15-check] DB: {db_path}")
    print(f"[v15-check] exists: {db_path.exists()}")
    if not db_path.exists():
        print("[v15-check] FAIL: DB bulunamadi — bot henuz hic baslamamis")
        return 1

    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as e:
        print(f"[v15-check] FAIL: RO connect error: {e}")
        return 1

    try:
        rows = conn.execute("PRAGMA table_info(hyperopt_results)").fetchall()
        cols = [r[1] for r in rows]
        print(f"[v15-check] hyperopt_results kolonlari ({len(cols)}): {cols}")

        missing = [c for c in ("asset", "timeframe") if c not in cols]
        if missing:
            print(f"[v15-check] FAIL: eksik kolon(lar): {missing}")
            print("           v15 migration UYGULANMAMIS - bot restart migrations'i calistirir")
            return 1

        # Index check (non-fatal)
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='hyperopt_results'"
        ).fetchall()
        idx_names = [r[0] for r in idx_rows]
        if "idx_hopt_atf" in idx_names:
            print("[v15-check] OK: idx_hopt_atf mevcut")
        else:
            print(f"[v15-check] WARN: idx_hopt_atf yok (indexler: {idx_names})")

        print("[v15-check] OK - v15 uygulanmis")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
