"""
Phase 82c — Clean stale OVERFIT + -inf rows from hyperopt_results
=================================================================

Bot's AI Brain writes hyperopt results to DB. Some rows have
best_score = -inf / NaN / NULL or is_overfit=1 (failed trials).
These pollute /hyperopt_history and /optuna views.

This script cleans them up. Bot-safe: uses a separate sqlite3
connection with busy_timeout=30s; WAL mode allows bot writes
to continue.

Schema (observed):
  id, strategy_name, strategy_id, best_params, best_score, metric,
  train_score, test_score, overfit_ratio, is_overfit, applied,
  source, n_trials, duration_s, created_at

Usage:
    py -3.11 tools/clean_overfit_hyperopt.py                # dry-run
    py -3.11 tools/clean_overfit_hyperopt.py --apply        # prompt
    py -3.11 tools/clean_overfit_hyperopt.py --apply --force

Cleanup criteria (any match):
    - best_score IS NULL
    - best_score = '-inf' / 'inf' / 'nan' (case-insensitive)
    - is_overfit = 1   (only if --include-overfit passed)
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

# Force UTF-8 stdout (prevents cp1252 crashes on Windows)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data_store" / "polypaper.db"


def fmt(n: int) -> str:
    return f"{n:,}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this flag, dry-run only.")
    ap.add_argument("--force", action="store_true",
                    help="Skip confirmation prompt.")
    ap.add_argument("--include-overfit", action="store_true",
                    help="Also delete rows with is_overfit=1 (default: keep).")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"[ERR] DB not found: {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH), timeout=60)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row

    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hyperopt_results'"
        ).fetchone()
        if not has:
            print("[INFO] hyperopt_results table not found. Nothing to do.")
            return 0

        cols = [r[1] for r in conn.execute(
            "PRAGMA table_info('hyperopt_results')").fetchall()]
        print(f"[INFO] Columns: {', '.join(cols)}")

        # Determine score column name
        score_col = None
        for cand in ("best_score", "score"):
            if cand in cols:
                score_col = cand
                break
        if score_col is None:
            print("[ERR] No score column found (expected best_score or score).")
            return 1
        print(f"[INFO] Using score column: {score_col}")

        total = conn.execute("SELECT COUNT(*) FROM hyperopt_results").fetchone()[0]
        print(f"[INFO] Total rows: {fmt(total)}")

        # Show distribution by source + is_overfit
        print()
        print("[BREAKDOWN] source / is_overfit:")
        has_overfit = "is_overfit" in cols
        if has_overfit:
            q = f"SELECT source, is_overfit, COUNT(*) FROM hyperopt_results GROUP BY source, is_overfit ORDER BY source, is_overfit"
            for row in conn.execute(q).fetchall():
                src, isov, cnt = row[0], row[1], row[2]
                print(f"  source={src!r:25s} is_overfit={isov!s:>5}  n={cnt}")
        else:
            for row in conn.execute(
                "SELECT source, COUNT(*) FROM hyperopt_results GROUP BY source"
            ).fetchall():
                print(f"  source={row[0]!r:25s}  n={row[1]}")

        # Find bad rows
        seen_ids: set[int] = set()
        print()
        print("[DIAGNOSTIC] Bad rows:")

        # NULL score
        nulls = conn.execute(
            f"SELECT id FROM hyperopt_results WHERE {score_col} IS NULL"
        ).fetchall()
        for r in nulls:
            seen_ids.add(r[0])
        if nulls:
            print(f"  - {score_col} IS NULL       -> {len(nulls)}")

        # Text-form inf/nan
        for where, label in [
            (f"CAST({score_col} AS TEXT) LIKE '-inf%' COLLATE NOCASE", "-inf (text)"),
            (f"CAST({score_col} AS TEXT) LIKE 'inf%'  COLLATE NOCASE", "+inf (text)"),
            (f"CAST({score_col} AS TEXT) LIKE '%nan%' COLLATE NOCASE", "NaN (text)"),
        ]:
            rows = conn.execute(
                f"SELECT id FROM hyperopt_results WHERE {where}"
            ).fetchall()
            new = [r[0] for r in rows if r[0] not in seen_ids]
            for rid in new:
                seen_ids.add(rid)
            if rows:
                print(f"  - {label:20s} -> {len(rows)} ({len(new)} new)")

        # Numeric-side check
        extra = 0
        for rid, score in conn.execute(
            f"SELECT id, {score_col} FROM hyperopt_results"
        ).fetchall():
            if rid in seen_ids:
                continue
            try:
                f = float(score) if score is not None else float("nan")
                if math.isinf(f) or math.isnan(f):
                    seen_ids.add(rid)
                    extra += 1
            except (TypeError, ValueError):
                seen_ids.add(rid)
                extra += 1
        if extra:
            print(f"  - numeric inf/NaN    -> {extra} (extra)")

        # is_overfit rows (only if --include-overfit)
        if args.include_overfit and has_overfit:
            overfits = conn.execute(
                "SELECT id FROM hyperopt_results WHERE is_overfit=1"
            ).fetchall()
            new = [r[0] for r in overfits if r[0] not in seen_ids]
            for rid in new:
                seen_ids.add(rid)
            if overfits:
                print(f"  - is_overfit=1       -> {len(overfits)} ({len(new)} new)")

        total_bad = len(seen_ids)
        pct = 100 * total_bad / max(total, 1)
        print()
        print(f"[SUMMARY] Deletion candidates: {fmt(total_bad)} / {fmt(total)} ({pct:.1f}%)")

        if total_bad == 0:
            print("[OK] No bad rows found. Nothing to do.")
            return 0

        # Sample
        print()
        print("[SAMPLE] First 5:")
        sample_ids = ",".join(str(i) for i in list(seen_ids)[:5])
        for r in conn.execute(
            f"SELECT * FROM hyperopt_results WHERE id IN ({sample_ids})"
        ).fetchall():
            d = dict(r)
            keys = ["id", "strategy_name", "source", score_col]
            if has_overfit:
                keys.append("is_overfit")
            summary = {k: d.get(k) for k in keys if k in d}
            print(f"  {summary}")

        if not args.apply:
            print()
            print("[DRY-RUN] --apply not passed. No changes made.")
            print("          To delete:  py -3.11 tools/clean_overfit_hyperopt.py --apply --force")
            if has_overfit and not args.include_overfit:
                ov_count = conn.execute(
                    "SELECT COUNT(*) FROM hyperopt_results WHERE is_overfit=1"
                ).fetchone()[0]
                if ov_count:
                    print(f"          NOTE: {ov_count} is_overfit=1 rows exist. Add --include-overfit to also delete them.")
            return 0

        if not args.force:
            print()
            ans = input(f">>> Delete {fmt(total_bad)} rows? [yes/NO]: ").strip().lower()
            if ans not in ("y", "yes"):
                print("[ABORT] Deletion cancelled.")
                return 0

        print()
        print(f"[APPLY] Deleting {fmt(total_bad)} rows...")
        id_list = list(seen_ids)
        BATCH = 500
        deleted = 0
        for i in range(0, len(id_list), BATCH):
            chunk = id_list[i:i + BATCH]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(
                f"DELETE FROM hyperopt_results WHERE id IN ({placeholders})",
                chunk,
            )
            conn.commit()
            deleted += len(chunk)
            if len(id_list) > BATCH:
                print(f"  deleted {fmt(deleted)}/{fmt(total_bad)}")

        new_total = conn.execute("SELECT COUNT(*) FROM hyperopt_results").fetchone()[0]
        print()
        print(f"[DONE] Deleted {fmt(deleted)}. Rows: {fmt(total)} -> {fmt(new_total)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
