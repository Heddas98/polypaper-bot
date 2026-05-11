"""
P1-04-c (2026-05-09) Strategy pruning - Yol D (hybrid archive).
================================================================

Reduces strategy noise by stopping strategies that are clearly dead. WATCH
phase strategies (n>=1, last_trade <= 7 days) are NEVER touched - data
keeps accumulating so we can re-audit in 7-14 days for proven candidates.

Filter (must satisfy ALL):
  - status != 'stopped' (already stopped strategies are skipped)
  - (n == 0)  OR  (last_trade older than DEAD_AFTER_DAYS)

Action: UPDATE strategies SET status='stopped', updated_at=NOW
  - Idempotent: re-running has no effect on already-stopped rows
  - Reversible: row preserved, just status flipped (not DELETE)
  - Audit trail: writes data_store/audits/prune_<UTC>.md with full list

Usage:
    py -3.11 scripts/prune_strategies.py --dry-run        # preview
    py -3.11 scripts/prune_strategies.py --apply          # confirms first
    py -3.11 scripts/prune_strategies.py --apply --yes    # skip prompt
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data_store" / "polypaper.db"

DEAD_AFTER_DAYS = 7


def _iso_to_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _gather(conn):
    """Return list of strategy dicts that are pruning candidates."""
    cur = conn.execute("""
        SELECT id, label, asset, timeframe, direction, status,
               strategy_type, deploy_stage, started_at, created_at, updated_at
        FROM strategies
        ORDER BY created_at DESC
    """)
    strategies = [dict(r) for r in cur.fetchall()]

    stats = {}
    cur = conn.execute("""
        SELECT strategy_id,
               COUNT(*) AS n,
               MAX(closed_at) AS last_closed
        FROM executions
        WHERE strategy_id IS NOT NULL AND status='claimed'
        GROUP BY strategy_id
    """)
    for r in cur.fetchall():
        stats[r["strategy_id"]] = {
            "n": r["n"] or 0,
            "last_closed": r["last_closed"],
        }

    now = datetime.now(UTC)
    candidates = []
    for s in strategies:
        if s["status"] == "stopped":
            continue
        st = stats.get(s["id"], {"n": 0, "last_closed": None})
        n = st["n"]
        last_dt = _iso_to_dt(st["last_closed"])
        age_days = (now - last_dt).total_seconds() / 86400 if last_dt else None

        reason = None
        if n == 0:
            reason = "no_trades"
        elif age_days is not None and age_days > DEAD_AFTER_DAYS:
            reason = f"idle_{int(age_days)}d"
        # else: skip - active or recent
        if reason is None:
            continue

        candidates.append(
            {
                **s,
                "n": n,
                "last_closed": st["last_closed"],
                "age_days": age_days,
                "reason": reason,
            }
        )
    return candidates


def main():
    p = argparse.ArgumentParser(prog="prune_strategies")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="preview proposed changes (no DB writes)")
    g.add_argument(
        "--apply", action="store_true", help="execute the prune (with confirmation prompt)"
    )
    p.add_argument("--yes", action="store_true", help="skip the typed confirmation prompt")
    args = p.parse_args()

    if not DB_PATH.exists():
        print(f"[ERROR] DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    # Read-only first pass to gather candidates
    # SQLite online backup API: produces a consistent snapshot from the
    # live DB even while the bot is writing. This is the same approach
    # daily_db_snapshot_job uses; safe under WAL concurrent access.
    import os
    import tempfile

    snap_path = Path(tempfile.gettempdir()) / f"polypaper_ro_{os.getpid()}.db"
    src_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30.0)
    src_conn.execute("PRAGMA busy_timeout=30000")
    snap_conn = sqlite3.connect(str(snap_path), timeout=30.0)
    src_conn.backup(snap_conn, pages=200, sleep=0.05)
    snap_conn.close()
    src_conn.close()
    ro_conn = sqlite3.connect(f"file:{snap_path}?mode=ro", uri=True, timeout=10.0)
    ro_conn.row_factory = sqlite3.Row
    candidates = _gather(ro_conn)
    ro_conn.close()

    print(f"[prune] DB: {DB_PATH}")
    print(f"[prune] mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")
    print(f"[prune] candidates: {len(candidates)}")
    print()

    if not candidates:
        print(
            "[prune] Nothing to prune. All non-stopped strategies have "
            f"recent activity (within {DEAD_AFTER_DAYS} days)."
        )
        return 0

    # Group by reason for the summary
    by_reason = {}
    for c in candidates:
        by_reason.setdefault(c["reason"].split("_")[0], []).append(c)

    print("Reason breakdown:")
    for k, v in sorted(by_reason.items()):
        print(f"  {k}: {len(v)}")
    print()

    print("First 10 candidates:")
    for c in candidates[:10]:
        sid_short = (c["id"] or "?")[:12]
        label = (c["label"] or "?")[:30]
        atf = f"{c['asset']}/{c['timeframe']}"
        print(
            f"  {sid_short}  {atf:8s}  n={c['n']}  reason={c['reason']}  "
            f"label={label}  status={c['status']}"
        )
    if len(candidates) > 10:
        print(f"  ... and {len(candidates) - 10} more")
    print()

    if args.dry_run:
        print("[dry-run] No DB writes. Re-run with --apply to execute.")
        # Still write a preview audit md
        _write_audit_md(candidates, dry_run=True)
        return 0

    # APPLY: confirmation
    if not args.yes:
        print("This will set status='stopped' on the candidates above.")
        print("Operation is REVERSIBLE (rows preserved, only status flipped).")
        ans = input(f"Type 'prune {len(candidates)}' to proceed: ").strip()
        if ans != f"prune {len(candidates)}":
            print("Aborted.")
            return 0

    # Open RW connection (no immutable; lets bot keep running on WAL)
    rw_conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    rw_conn.row_factory = sqlite3.Row
    rw_conn.execute("PRAGMA journal_mode=WAL")
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    affected = 0
    skipped = 0
    for c in candidates:
        try:
            cur = rw_conn.execute(
                """UPDATE strategies
                   SET status='stopped', updated_at=?
                   WHERE id=? AND status != 'stopped'""",
                (now_iso, c["id"]),
            )
            if cur.rowcount > 0:
                affected += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            print(f"[prune] UPDATE failed for {c['id']}: {e}", file=sys.stderr)
            skipped += 1
    rw_conn.commit()
    rw_conn.close()

    print(f"[prune] DONE. affected={affected} skipped={skipped}")
    _write_audit_md(candidates, dry_run=False, affected=affected)
    print(
        "[prune] Bot may need a restart for the in-memory engine to drop "
        "these from its active set; alternatively the next "
        "_startup_health_check cycle will pick up the new status."
    )
    return 0


def _write_audit_md(candidates, dry_run, affected=None):
    now = datetime.now(UTC)
    out = (
        REPO_ROOT
        / "data_store"
        / "audits"
        / f"prune_{now.strftime('%Y%m%dT%H%M%SZ')}{'_dryrun' if dry_run else ''}.md"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    L = []
    L.append(f"# Strategy Prune {'DRY-RUN' if dry_run else 'APPLY'} Log")
    L.append("")
    L.append(f"**Time:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append(f"**Mode:** {'DRY-RUN (no DB writes)' if dry_run else 'APPLIED'}")
    L.append(f"**Candidates:** {len(candidates)}")
    if not dry_run:
        L.append(f"**Affected (status flipped):** {affected}")
    L.append("")
    L.append("| ID | Label | Asset/TF | n | Last Trade | Reason | Prev Status |")
    L.append("|---|---|---|--:|---|---|---|")
    for c in candidates:
        sid = (c["id"] or "?")[:12]
        label = (c["label"] or "?")[:35]
        atf = f"{c['asset']}/{c['timeframe']}"
        last = (c["last_closed"] or "-")[:16]
        L.append(
            f"| `{sid}` | {label} | {atf} | {c['n']} | {last} | " f"{c['reason']} | {c['status']} |"
        )
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[prune] audit log: {out}")


if __name__ == "__main__":
    sys.exit(main())
