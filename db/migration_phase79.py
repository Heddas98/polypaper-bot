"""Phase 79 DB Migration — Add missing columns safely.

This standalone script adds Phase 79 missing columns to support crash fixes:
- executions.signal_score (REAL) — AI signal quality metric
- executions.conviction (REAL) — Trade conviction level (already in v3 as is_maker)
- whale_trades table — Whale trade tracking for signal detection

Safe for multiple runs: ALTER TABLE ADD COLUMN is idempotent (skips if exists).
CREATE TABLE IF NOT EXISTS is also safe.
"""
import sqlite3
import sys
from pathlib import Path


def migrate(db_path: str = "polypaper.db"):
    """Run Phase 79 migrations safely."""
    print(f"[Phase 79] Starting migration on: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Migrations: add missing columns if they don't exist
        migrations = [
            ("executions", "signal_score", "REAL DEFAULT NULL"),
            ("executions", "conviction", "REAL DEFAULT NULL"),
        ]

        for table, col, col_type in migrations:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                conn.commit()
                print(f"✓ Added {table}.{col} ({col_type})")
            except sqlite3.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    print(f"⊘ {table}.{col} already exists (skipped)")
                else:
                    print(f"✗ {table}.{col} error: {e}")
                    raise

        # Create whale_trades if missing
        try:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS whale_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT,
                    token_id TEXT,
                    direction TEXT,
                    side TEXT,
                    price REAL,
                    size REAL,
                    notional_usd REAL,
                    ts_ms INTEGER,
                    ts_iso TEXT
                )
            """)
            conn.commit()
            print("✓ whale_trades table ensured")
        except sqlite3.OperationalError as e:
            print(f"✗ whale_trades table error: {e}")
            raise

        conn.close()
        print("[Phase 79] Migration completed successfully ✓")
        return True

    except (sqlite3.Error, OSError) as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. sqlite3.Error
        # covers OperationalError (locked, schema), DatabaseError (corrupt);
        # OSError covers DB file missing/permission. Standalone migrate
        # script: False return signals failure to CLI exit code.
        print(f"[Phase 79] Migration FAILED: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "polypaper.db"
    success = migrate(db)
    sys.exit(0 if success else 1)
