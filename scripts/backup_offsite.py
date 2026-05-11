"""
Phase 48 — Off-site DB backup.

Takes a hot SQLite snapshot of polypaper.db and uploads it to an external
destination. Supports three backends:

  1. Local second disk   (BACKUP_MODE=local,  BACKUP_DIR=...)
  2. rclone-configured remote (BACKUP_MODE=rclone, BACKUP_RCLONE_REMOTE=remote:bucket/polypaper)
  3. S3/B2 via boto3     (BACKUP_MODE=s3,     BACKUP_S3_BUCKET=..., AWS env)

Usage:
  py -3.11 scripts/backup_offsite.py
  py -3.11 scripts/backup_offsite.py --mode local --dir D:/backups/polypaper

The script uses SQLite's online .backup API (sqlite3.Connection.backup),
which is safe to run against a live WAL-mode database while the bot is
writing to it.

Retention: keeps the last BACKUP_KEEP snapshots (default 14) in the
destination, deletes older ones.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

logging.basicConfig(
    format="%(asctime)s [backup] %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("polypaper.backup")


def snapshot(src: Path, dest: Path) -> None:
    """Create a consistent hot-copy of a live WAL-mode SQLite DB."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(src)) as src_conn:
        with sqlite3.connect(str(dest)) as dst_conn:
            src_conn.backup(dst_conn)
    log.info("Snapshot: %s → %s (%.1f MB)", src, dest, dest.stat().st_size / 1024 / 1024)


def upload_local(snap: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / snap.name
    shutil.copy2(snap, dest)
    log.info("Local copy: %s", dest)
    return dest


def upload_rclone(snap: Path, remote: str) -> None:
    cmd = ["rclone", "copy", str(snap), remote, "--progress"]
    log.info("rclone: %s", " ".join(cmd))
    subprocess.check_call(cmd)


def upload_s3(snap: Path, bucket: str, prefix: str = "polypaper-backups") -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        log.error("boto3 not installed; pip install boto3")
        raise
    s3 = boto3.client("s3")
    key = f"{prefix.rstrip('/')}/{snap.name}"
    s3.upload_file(str(snap), bucket, key)
    log.info("s3://%s/%s uploaded", bucket, key)


def retain_local(target_dir: Path, keep: int) -> None:
    """Keep last N .db files in target_dir, delete older."""
    files = sorted(target_dir.glob("polypaper-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
            log.info("Retention: removed %s", old)
        except Exception as e:
            log.warning("Failed to delete %s: %s", old, e)


def main() -> int:
    parser = argparse.ArgumentParser(description="Off-site backup for polypaper.db")
    parser.add_argument(
        "--src", default=os.getenv("BACKUP_SRC", "data_store/polypaper.db"), help="Source DB path"
    )
    parser.add_argument(
        "--mode", default=os.getenv("BACKUP_MODE", "local"), choices=["local", "rclone", "s3"]
    )
    parser.add_argument(
        "--dir", default=os.getenv("BACKUP_DIR", "backups"), help="Local target dir (mode=local)"
    )
    parser.add_argument(
        "--remote",
        default=os.getenv("BACKUP_RCLONE_REMOTE", ""),
        help="rclone remote (mode=rclone)",
    )
    parser.add_argument(
        "--bucket", default=os.getenv("BACKUP_S3_BUCKET", ""), help="S3 bucket (mode=s3)"
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=int(os.getenv("BACKUP_KEEP", "14")),
        help="How many snapshots to retain locally",
    )
    args = parser.parse_args()

    src = Path(args.src)
    if not src.exists():
        log.error("Source DB not found: %s", src)
        return 1

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    tmp_dir = Path("backups/_staging")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    snap = tmp_dir / f"polypaper-{ts}.db"

    try:
        snapshot(src, snap)
    except Exception as e:
        log.error("Snapshot failed: %s", e)
        return 2

    try:
        if args.mode == "local":
            upload_local(snap, Path(args.dir))
            retain_local(Path(args.dir), args.keep)
        elif args.mode == "rclone":
            if not args.remote:
                log.error("--remote required for rclone mode")
                return 3
            upload_rclone(snap, args.remote)
        elif args.mode == "s3":
            if not args.bucket:
                log.error("--bucket required for s3 mode")
                return 3
            upload_s3(snap, args.bucket)
    except Exception as e:
        log.error("Upload failed: %s", e)
        return 4
    finally:
        try:
            snap.unlink()
        except Exception:
            pass

    log.info("✅ Backup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
