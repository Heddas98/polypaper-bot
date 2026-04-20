"""
Phase 82e Sprint 2.2 Smoke Test
===============================
Verifies db.ro_connect sync + async helpers against a throwaway SQLite DB.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    db_path = tempfile.mktemp(suffix=".db")
    try:
        # Seed
        c = sqlite3.connect(db_path)
        c.executescript("CREATE TABLE t(id INTEGER); INSERT INTO t VALUES (1);")
        c.commit()
        c.close()

        from db.ro_connect import open_ro, open_ro_aiosqlite

        # Sync
        with open_ro(db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM t").fetchone()
            assert row[0] == 1, f"sync: expected 1, got {row[0]}"
        print("  sync open_ro OK")

        # Async
        async def _a():
            conn = await open_ro_aiosqlite(db_path)
            try:
                cur = await conn.execute("SELECT COUNT(*) FROM t")
                row = await cur.fetchone()
                await cur.close()
            finally:
                await conn.close()
            assert row[0] == 1, f"async: expected 1, got {row[0]}"

        asyncio.run(_a())
        print("  async open_ro_aiosqlite OK")

        print("  OK (sync + async RO helpers verified)")
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  SMOKE FAIL: {type(e).__name__}: {e}")
        return 1
    finally:
        for suf in ("", "-wal", "-shm"):
            p = db_path + suf
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except Exception:
                    pass


if __name__ == "__main__":
    sys.exit(main())
