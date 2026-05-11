"""
Disk Cleanup — Phase B (Backups Prune)
=========================================

data_store\backups\\ klasöründe çok eski .db backup'ları var (~73 GB).
Bu script son N tanesini korur, geri kalanını siler.

Kullanım:
    py -3.11 scripts\\disk_cleanup_phase_b.py [--keep 3] [--dry-run]

Varsayılan: son 3 backup korunur (yaklaşık son 1 hafta).

Korunan:
- En yeni N tane .db dosyası (modtime'a göre)
- .db.tmp dosyaları (atomic write in-progress, dokunulmaz)

Heddas direktifi: "yedekle önce, sonra sil" — script execute-only,
yedek kopyalama (D:\backup_safety\\ vb.) Heddas yerel manuel.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


def hr(b: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=3, help="Korunacak en yeni .db sayısı")
    ap.add_argument("--dry-run", action="store_true", help="Sil yok, sadece liste")
    ap.add_argument("--backups-dir", default="data_store/backups", help="Backup dizini")
    args = ap.parse_args()

    bdir = Path(args.backups_dir)
    if not bdir.exists():
        print(f"❌ Backup dir not found: {bdir}")
        sys.exit(1)

    # All .db files (not .db.tmp, not .db-wal, not .db-shm)
    db_files = []
    for p in bdir.glob("*.db"):
        if not p.is_file():
            continue
        try:
            mtime = p.stat().st_mtime
            size = p.stat().st_size
            db_files.append((p, mtime, size))
        except OSError:
            continue

    if not db_files:
        print("📋 No .db backups found.")
        return

    # Sort by mtime descending (newest first)
    db_files.sort(key=lambda x: x[1], reverse=True)

    keep = db_files[: args.keep]
    delete = db_files[args.keep :]

    print(f"📊 Backups Prune (keep newest {args.keep})")
    print(f"   Total: {len(db_files)} backups, {hr(sum(s for _, _, s in db_files))}")
    print()
    print(f"✅ KEEP ({len(keep)}):")
    for p, mtime, size in keep:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"   {p.name:<55} {hr(size):>10}  ({ts})")
    print()
    print(f"❌ DELETE ({len(delete)}):")
    delete_total = 0
    for p, mtime, size in delete:
        ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"   {p.name:<55} {hr(size):>10}  ({ts})")
        delete_total += size
    print()
    print(f"💾 Delete savings: {hr(delete_total)}")

    # Also handle related .db-wal, .db-shm, .db.tmp files for deleted .dbs
    related_to_delete = []
    for p, _, _ in delete:
        for ext in ("-wal", "-shm"):
            related = p.with_name(p.name + ext)
            if related.exists():
                try:
                    related_to_delete.append((related, related.stat().st_size))
                except OSError:
                    pass
    if related_to_delete:
        rel_total = sum(s for _, s in related_to_delete)
        print(f"   + {len(related_to_delete)} related .db-wal/.db-shm  ({hr(rel_total)})")
        delete_total += rel_total

    print(f"\n💾 TOTAL savings: {hr(delete_total)}")

    if args.dry_run:
        print("\n🔍 DRY-RUN: no files deleted.")
        return

    print()
    confirm = (
        input(f"Delete {len(delete)} backups + {len(related_to_delete)} related? (y/N): ")
        .strip()
        .lower()
    )
    if confirm not in ("y", "yes"):
        print("❌ Aborted.")
        return

    deleted_count = 0
    deleted_bytes = 0
    for p, _, size in delete:
        try:
            p.unlink()
            deleted_count += 1
            deleted_bytes += size
        except OSError as e:
            print(f"   ⚠ {p}: {e}")
    for p, size in related_to_delete:
        try:
            p.unlink()
            deleted_count += 1
            deleted_bytes += size
        except OSError as e:
            print(f"   ⚠ {p}: {e}")

    print(f"\n✅ Deleted {deleted_count} files ({hr(deleted_bytes)})")


if __name__ == "__main__":
    main()
