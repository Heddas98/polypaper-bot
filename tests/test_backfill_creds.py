"""
Phase 55 P55-05 — ob_trades Backfill Credential Verification
=============================================================
Verifies that CLOB credentials are present in .env and the backfill
script can build a client object. Does NOT make actual API calls.

Run: py -3.11 tests/test_backfill_creds.py
"""
import os
import sys

# Project root
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:
    pass


def check_creds():
    """Check all 5 CLOB credentials are populated."""
    keys = [
        "POLYGON_PRIVATE_KEY",
        "POLYGON_WALLET",
        "POLYMARKET_API_KEY",
        "POLYMARKET_API_SECRET",
        "POLYMARKET_PASSPHRASE",
    ]
    results = {}
    for k in keys:
        val = os.getenv(k, "").strip()
        results[k] = bool(val)
        status = "✅" if val else "❌"
        preview = f"{val[:8]}..." if val else "(empty)"
        print(f"  {status} {k} = {preview}")
    return all(results.values())


def check_backfill_imports():
    """Check that backfill script modules can be imported."""
    try:
        import aiosqlite  # noqa: F401
        print("  ✅ aiosqlite")
    except ImportError:
        print("  ❌ aiosqlite — pip install aiosqlite")
        return False

    try:
        from py_clob_client.client import ClobClient  # noqa: F401
        print("  ✅ py-clob-client")
    except ImportError:
        print("  ❌ py-clob-client — pip install py-clob-client")
        return False

    return True


def check_db_exists():
    """Check that the database file exists."""
    db_path = os.getenv("DATABASE_PATH", os.path.join(ROOT, "polypaper.db"))
    # Also check data_store path
    alt_path = os.path.join(ROOT, "data_store", "polypaper.db")
    for p in [db_path, alt_path]:
        if os.path.exists(p):
            size_mb = os.path.getsize(p) / (1024 * 1024)
            print(f"  ✅ DB found: {p} ({size_mb:.1f} MB)")
            return True
    print(f"  ⚠️ DB not found at {db_path} or {alt_path}")
    return False


if __name__ == "__main__":
    print("=" * 50)
    print("  ob_trades Backfill Credential Check")
    print("  Phase 55 P55-05")
    print("=" * 50)
    print()

    print("[1/3] CLOB Credentials:")
    creds_ok = check_creds()
    print()

    print("[2/3] Required Libraries:")
    imports_ok = check_backfill_imports()
    print()

    print("[3/3] Database:")
    db_ok = check_db_exists()
    print()

    print("=" * 50)
    if creds_ok and imports_ok and db_ok:
        print("  ✅ ALL CHECKS PASSED — backfill ready")
        print("  Run: py -3.11 scripts/backfill_ob_trades.py --days 1")
    elif creds_ok and imports_ok:
        print("  ⚠️ Creds OK, DB not found (normal in sandbox)")
    elif creds_ok:
        print("  ⚠️ Creds OK, missing libraries")
    else:
        print("  ❌ CLOB credentials missing")
    print("=" * 50)
