"""
T11.3 S4 DB Trade Count Helper
==============================

Standalone count script for DB snapshot restore dry-run.
Opens DB in read-only mode (WAL-safe when bot is stopped).

Usage:
    py -3.11 scripts/t11_3_s4_trade_count.py <db_path>

Output (stdout, single line):
    executions=12345
    trades=567
    paper_trades=89

On error (stderr + exit 1):
    NO_TABLE       -- none of the known trade tables exist
    <sqlite error> -- DB not readable
"""
from __future__ import annotations

import sqlite3
import sys


TRADE_TABLE_CANDIDATES = ["executions", "trades", "paper_trades"]


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: trade_count.py <db_path>", file=sys.stderr)
        return 2

    db_path = sys.argv[1]
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    except sqlite3.Error as e:
        print(f"CONNECT_FAIL: {e}", file=sys.stderr)
        return 1

    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        for candidate in TRADE_TABLE_CANDIDATES:
            if candidate in tables:
                count = conn.execute(
                    f'SELECT COUNT(*) FROM "{candidate}"'
                ).fetchone()[0]
                print(f"{candidate}={count}")
                return 0

        print(
            f"NO_TABLE (available: {sorted(tables)[:10]}...)",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
