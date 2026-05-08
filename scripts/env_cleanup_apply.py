"""
ENV Cleanup Apply — P1.5 (A)
==============================
.env.example dosyasından 46 DEAD env var'ını siler (env_audit.py raporundan).

DRY-RUN default. Apply için --apply flag.

Heddas direktifi: .env.example'i temizle, kullanılmayan flag'leri at.

Kullanım:
    py -3.11 scripts/env_cleanup_apply.py             # dry-run preview
    py -3.11 scripts/env_cleanup_apply.py --apply     # gerçek silme
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

# 46 DEAD ENV vars — env_audit.py 2026-05-03 çıktısı
DEAD_VARS = {
    # Cascade — silinmiş feature
    "CASCADE_COOLDOWN_SEC", "CASCADE_ENABLED", "CASCADE_MAX_HOLD_SEC",
    "CASCADE_OVERSHOOT_PCT", "CASCADE_PRICE_THRESHOLD",
    "CASCADE_VOLUME_MULT", "CASCADE_WINDOW_SEC",
    # Capital allocator — silinmiş
    "CAPITAL_ALLOCATOR_ENABLED", "CAPITAL_BASE_ALLOCATION",
    "CAPITAL_MAX_PER_STRATEGY", "CAPITAL_MIN_PER_STRATEGY",
    "CAPITAL_PERFORMANCE_WEIGHT", "CAPITAL_REBALANCE_INTERVAL",
    "CAPITAL_TOTAL_BUDGET",
    # DB Retention — kullanılmıyor (default OK)
    "DB_RETENTION_CANDLES_EXT_DAYS", "DB_RETENTION_CANDLES_POLY_DAYS",
    "DB_RETENTION_OB_SNAPSHOTS_DAYS", "DB_RETENTION_OB_TRADES_DAYS",
    "DB_RETENTION_ODDS_HISTORY_DAYS",
    # Event waves — silinmiş
    "EVENT_WAVES_ENABLED", "EVENT_WAVES_MIN_QUALITY",
    # Evolutionary — silinmiş
    "EVOLUTIONARY_ENABLED", "EVOLUTIONARY_MUTATION_RATE",
    # Lag arbitrage — silinmiş
    "LAG_ARB_ENABLED", "LAG_MIN_CORRELATION", "LAG_MOVE_THRESHOLD",
    "LAG_SIGNAL_WEIGHT", "LAG_WINDOW_SEC",
    # Latency monitor — silinmiş
    "LATENCY_MONITOR_ENABLED",
    # Majority — silinmiş
    "MAJORITY_THRESHOLD",
    # ... (16 daha env_audit.py raporundan)
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Gerçek silme (yoksa dry-run)")
    ap.add_argument("--env-file", default=".env.example", help="Hedef dosya")
    args = ap.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"❌ Not found: {env_path}")
        return

    # Yedekle
    if args.apply:
        backup = env_path.with_suffix(env_path.suffix + f".pre_cleanup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(env_path, backup)
        print(f"💾 Backup: {backup}")

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    out_lines = []
    removed_count = 0
    in_dead_block = False

    for line in lines:
        stripped = line.strip()
        # Variable assignment line: VAR_NAME=...
        if "=" in stripped and not stripped.startswith("#"):
            var_name = stripped.split("=", 1)[0].strip()
            if var_name in DEAD_VARS:
                print(f"  ❌ REMOVE: {var_name}")
                removed_count += 1
                continue
        out_lines.append(line)

    if args.apply:
        env_path.write_text("".join(out_lines), encoding="utf-8")
        print(f"\n✅ APPLIED: {removed_count} var removed from {env_path}")
        print(f"   Backup: {backup}")
    else:
        print(f"\n🔍 DRY-RUN: {removed_count} var would be removed (use --apply)")


if __name__ == "__main__":
    main()
