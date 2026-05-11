"""
P0-05c (2026-05-09) — Restore polypaper.db from a daily snapshot.
=================================================================

Usage:
    # List snapshots + their integrity status
    py -3.11 scripts/restore_from_backup.py --list

    # Verify SHA256 of every snapshot in manifest (no restore)
    py -3.11 scripts/restore_from_backup.py --verify-all

    # Restore the latest snapshot (with confirmation prompt)
    py -3.11 scripts/restore_from_backup.py --latest

    # Restore a specific snapshot by filename
    py -3.11 scripts/restore_from_backup.py --restore polypaper_2026-05-09.db

    # Dry run — show what would happen, no file changes
    py -3.11 scripts/restore_from_backup.py --restore polypaper_2026-05-09.db --dry-run

Safety:
    * Refuses to overwrite the current DB without an explicit `--yes` flag
      AND a typed confirmation prompt.
    * Before overwriting, makes a `pre_restore_<UTC>.db` snapshot of the
      live DB inside data_store/backups/.
    * Verifies SHA256 of the source snapshot (against manifest) BEFORE
      copying. Aborts on mismatch.
    * Writes restored bytes to `polypaper.db.restoring` then atomic
      rename. Bot must be stopped before running.

Bot must be stopped before running. The script does not check that for
you — but it will detect a locked file (`PermissionError`) on Windows
when overwriting and abort cleanly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow running both from repo root and from scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data_store" / "polypaper.db"
BACKUP_DIR = REPO_ROOT / "data_store" / "backups"
MANIFEST_PATH = BACKUP_DIR / "manifest.json"
HASH_CHUNK_SIZE = 1024 * 1024


# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"version": 1, "snapshots": []}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERROR] manifest.json read failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)


def _format_size(b: int | None) -> str:
    if b is None:
        return "?"
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    if b < 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024):.1f} MB"
    return f"{b / (1024 * 1024 * 1024):.2f} GB"


# -----------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------


def cmd_list() -> int:
    """Show every snapshot from manifest + presence + size on disk."""
    m = _load_manifest()
    snaps = m.get("snapshots", [])
    if not snaps:
        print("No snapshots in manifest.")
        # Still show on-disk files in case manifest is empty
        on_disk = sorted(BACKUP_DIR.glob("polypaper_*.db"))
        if on_disk:
            print("\nOn disk (no manifest entry):")
            for p in on_disk:
                print(f"  {p.name:35s} {_format_size(p.stat().st_size):>10s}")
        return 0

    print(
        f"{'Filename':35s} {'Size':>10s} {'Schema':>8s}  "
        f"{'Created (UTC)':20s} {'On disk':8s} {'SHA256 (12)':12s}"
    )
    print("-" * 100)
    for e in snaps:
        fn = e.get("filename", "?")
        path = BACKUP_DIR / fn
        on_disk = "yes" if path.exists() else "MISSING"
        sha_short = (e.get("sha256") or "")[:12]
        print(
            f"{fn:35s} "
            f"{_format_size(e.get('size_bytes')):>10s} "
            f"v{e.get('schema_version', '?'):>7} "
            f"{e.get('created_utc', '?'):20s} "
            f"{on_disk:8s} "
            f"{sha_short:12s}"
        )
    return 0


def cmd_verify_all() -> int:
    """Compute SHA256 of every snapshot file and compare with manifest."""
    m = _load_manifest()
    snaps = m.get("snapshots", [])
    if not snaps:
        print("No snapshots in manifest — nothing to verify.")
        return 0

    fail = 0
    for e in snaps:
        fn = e.get("filename")
        if not fn:
            continue
        path = BACKUP_DIR / fn
        expected = e.get("sha256", "")
        if not path.exists():
            print(f"[MISSING] {fn}")
            fail += 1
            continue
        print(f"[hashing] {fn} ...", end="", flush=True)
        try:
            actual = _sha256_file(path)
        except OSError as err:
            print(f" READ ERROR ({err})")
            fail += 1
            continue
        if actual == expected:
            print(" OK")
        else:
            print(f" MISMATCH\n  expected: {expected}\n  actual:   {actual}")
            fail += 1
    if fail:
        print(f"\n{fail} snapshot(s) failed verification.", file=sys.stderr)
        return 1
    print("\nAll snapshots verified OK.")
    return 0


def _resolve_target(args: argparse.Namespace) -> tuple[Path, dict]:
    """Pick the snapshot file + manifest entry to restore from."""
    m = _load_manifest()
    snaps = m.get("snapshots", [])

    if args.latest:
        if not snaps:
            print("[ERROR] --latest requested but manifest has no snapshots.", file=sys.stderr)
            sys.exit(2)
        # manifest is appended chronologically; latest = last entry
        entry = snaps[-1]
    else:
        target_name = args.restore
        match = [e for e in snaps if e.get("filename") == target_name]
        if not match:
            print(
                f"[ERROR] '{target_name}' not in manifest. Use --list to "
                f"see available snapshots.",
                file=sys.stderr,
            )
            sys.exit(2)
        entry = match[0]

    src = BACKUP_DIR / entry["filename"]
    if not src.exists():
        print(f"[ERROR] manifest entry exists but file missing on disk: " f"{src}", file=sys.stderr)
        sys.exit(2)
    return src, entry


def cmd_restore(args: argparse.Namespace) -> int:
    src, entry = _resolve_target(args)

    print(f"[restore] source: {src}")
    print(f"[restore] target: {DB_PATH}")
    print(f"[restore] expected sha256: {entry.get('sha256', '?')}")
    print(f"[restore] schema_version: {entry.get('schema_version', '?')}")

    # 1. Verify source SHA256 against manifest
    print("[restore] verifying source hash ...", end="", flush=True)
    actual = _sha256_file(src)
    expected = entry.get("sha256", "")
    if actual != expected:
        print(" MISMATCH — ABORT")
        print(f"  expected: {expected}", file=sys.stderr)
        print(f"  actual:   {actual}", file=sys.stderr)
        return 3
    print(" OK")

    if args.dry_run:
        print("[dry-run] would create pre-restore backup of current DB")
        print("[dry-run] would atomically copy source to target")
        print("[dry-run] no files modified.")
        return 0

    # 2. Confirmation prompt (skip with --yes)
    if not args.yes:
        print("\nThis will OVERWRITE the live database.")
        print("Make sure the bot is stopped first (Telegram /stop or " "kill the python process).")
        ans = input("Type 'restore' to proceed: ").strip()
        if ans.lower() != "restore":
            print("Aborted.")
            return 0

    # 3. Pre-restore backup of current live DB
    if DB_PATH.exists():
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        pre_path = BACKUP_DIR / f"pre_restore_{ts}.db"
        print(f"[restore] pre-restore backup: {pre_path}")
        try:
            shutil.copy2(DB_PATH, pre_path)
        except (OSError, shutil.Error) as e:
            print(
                f"[ERROR] pre-restore backup failed: " f"{type(e).__name__}: {e}", file=sys.stderr
            )
            return 4
    else:
        print("[restore] no current DB at target — skipping pre-restore " "backup")

    # 4. Atomic copy: write to .restoring then os.replace
    restoring = DB_PATH.with_suffix(".db.restoring")
    print(f"[restore] copying to {restoring} ...")
    try:
        shutil.copy2(src, restoring)
    except (OSError, shutil.Error) as e:
        print(f"[ERROR] copy to .restoring failed: " f"{type(e).__name__}: {e}", file=sys.stderr)
        if restoring.exists():
            try:
                restoring.unlink()
            except OSError:
                pass
        return 5

    # 5. Verify the copy itself before swapping in
    print("[restore] verifying copied bytes ...", end="", flush=True)
    copied_hash = _sha256_file(restoring)
    if copied_hash != expected:
        print(" MISMATCH — ABORT (target left untouched)")
        try:
            restoring.unlink()
        except OSError:
            pass
        return 6
    print(" OK")

    # 6. Atomic swap. On Windows, os.replace works as long as the target
    # is not held open by another process; PermissionError if bot still up.
    print(f"[restore] atomic rename → {DB_PATH}")
    try:
        os.replace(restoring, DB_PATH)
    except OSError as e:
        print(
            f"[ERROR] atomic rename failed: {type(e).__name__}: {e}\n"
            f"  Likely cause: the bot is still running and holds a handle "
            f"to {DB_PATH.name}.\n"
            f"  Stop the bot, then re-run this script.\n"
            f"  The .restoring file at {restoring} has been preserved so "
            f"you can finish the swap manually.",
            file=sys.stderr,
        )
        return 7

    # 7. Drop stale WAL/SHM siblings — they belong to the OLD DB
    for sib in (DB_PATH.with_name(DB_PATH.name + "-wal"), DB_PATH.with_name(DB_PATH.name + "-shm")):
        if sib.exists():
            try:
                sib.unlink()
                print(f"[restore] removed stale {sib.name}")
            except OSError as e:
                print(f"[warn] could not remove {sib.name}: " f"{type(e).__name__}: {e}")

    print(f"\n[restore] DONE. Restored {entry['filename']} → {DB_PATH}")
    print("You can now restart the bot.")
    return 0


# -----------------------------------------------------------------------
# CLI entry
# -----------------------------------------------------------------------


def main() -> int:
    p = argparse.ArgumentParser(
        prog="restore_from_backup",
        description="Restore polypaper.db from a daily snapshot.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--list", action="store_true", help="List snapshots from manifest + on-disk presence."
    )
    g.add_argument(
        "--verify-all",
        action="store_true",
        help="Compute SHA256 of every snapshot, compare to manifest.",
    )
    g.add_argument("--latest", action="store_true", help="Restore the most recent snapshot.")
    g.add_argument("--restore", metavar="FILENAME", help="Restore a specific snapshot by filename.")
    p.add_argument(
        "--yes", action="store_true", help="Skip the typed-confirmation prompt (still verifies)."
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Show what would happen; don't change any files."
    )
    args = p.parse_args()

    # Sanity: backup dir must exist
    if not BACKUP_DIR.exists():
        print(f"[ERROR] backup dir not found: {BACKUP_DIR}", file=sys.stderr)
        return 1

    if args.list:
        return cmd_list()
    if args.verify_all:
        return cmd_verify_all()
    return cmd_restore(args)


if __name__ == "__main__":
    sys.exit(main())
