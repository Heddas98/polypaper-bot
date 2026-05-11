r"""
Disk Cleanup Audit — read-only

Heddas yerel'de çalıştırılır:
    py -3.11 scripts\disk_cleanup_audit.py

Çıktı:
- stdout: kategori başına boyut tablosu
- evidence/disk_cleanup_audit_<TS>.md — human-readable rapor

Kesinlikle DELETE YAPMAZ — sadece envanter çıkarır.

Cleanup eylemleri için:
    scripts\disk_cleanup_phase_a.bat   # safe deletes (orphan WAL, pre-phase, pycache)
    scripts\disk_cleanup_phase_b.bat   # backups/ prune (son N adet koru)
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Korunacak dosyalar (whitelist)
KEEP_PATTERNS = {
    "data_store/polypaper.db",
    "data_store/polypaper.db-wal",
    "data_store/polypaper.db-shm",
    "data_store/polypaper.lock",
    "data_store/trade_journal.jsonl",
    "data_store/decisions.jsonl",
    "data_store/admin_chat.json",
    "data_store/micro_weight_state.json",
    "data_store/backtest_cache.db",
    "data_store/backtest_cache.db-shm",
    "data_store/backtest_cache.db-wal",
}

# Silinme adayları (kategori başına)
CATEGORIES = {
    "phase_pre_snapshots": [
        "data_store/polypaper_pre77.db",
        "data_store/polypaper_pre_phase80.db",
    ],
    "backup_phase82e": ["data_store/backup_phase82e"],
    "backups": ["data_store/backups"],
    "regenerable_caches": [
        "htmlcov",
        "__pycache__",
    ],
    "stale_logs": [
        "data_store/log_extract.txt",
        "data_store/log600.txt",
        "data_store/log_trades.txt",
        "data_store/last50.txt",
        "data_store/diagnose_result.txt",
        "data_store/syntax_result.txt",
        "data_store/verify_result.txt",
        "data_store/recent_decisions.txt",
        "data_store/recent_log.txt",
        "data_store/recent_trades.txt",
    ],
}


def file_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if path.is_dir():
        total = 0
        for sub in path.rglob("*"):
            if sub.is_file():
                try:
                    total += sub.stat().st_size
                except OSError:
                    continue
        return total
    return 0


def find_pycache(root: Path) -> list[Path]:
    return [p for p in root.rglob("__pycache__") if p.is_dir() and "_archive" not in str(p)]


def hr(b: int) -> str:
    """Human readable size."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def audit() -> dict:
    """Run audit, return dict of category → {paths: [...], total_bytes: N}."""
    result = {}

    for cat, patterns in CATEGORIES.items():
        cat_data = {"paths": [], "total_bytes": 0}
        for pat in patterns:
            if pat == "__pycache__":
                # Special: find all __pycache__ dirs
                for pc in find_pycache(ROOT):
                    sz = file_size(pc)
                    cat_data["paths"].append((str(pc.relative_to(ROOT)), sz, "dir"))
                    cat_data["total_bytes"] += sz
            else:
                p = ROOT / pat
                if p.exists():
                    sz = file_size(p)
                    kind = "dir" if p.is_dir() else "file"
                    cat_data["paths"].append((pat, sz, kind))
                    cat_data["total_bytes"] += sz
        result[cat] = cat_data

    return result


def find_orphan_wal(backups_dir: Path) -> list[tuple[str, int]]:
    """Orphan .db-wal: WAL dosyası var ama eşleşen .db yok."""
    if not backups_dir.exists():
        return []
    orphans = []
    for wal in backups_dir.glob("*.db-wal"):
        db_path = wal.with_suffix("")  # .db-wal → .db
        if not db_path.exists():
            try:
                orphans.append((str(wal.relative_to(ROOT)), wal.stat().st_size))
            except OSError:
                continue
    return orphans


def main():
    print(f"📊 Disk Cleanup Audit — {ROOT}")
    print()

    data = audit()
    grand_total = 0
    for cat, info in data.items():
        sz = info["total_bytes"]
        grand_total += sz
        print(f"  {cat:30s}  {hr(sz):>12s}  ({len(info['paths'])} item)")

    # Orphan WAL detection
    orphans = find_orphan_wal(ROOT / "data_store" / "backups")
    orphan_total = sum(sz for _, sz in orphans)
    print(f"  {'orphan_db_wal_files':30s}  {hr(orphan_total):>12s}  ({len(orphans)} item)")
    grand_total += orphan_total

    print()
    print(f"  {'TOTAL DELETABLE':30s}  {hr(grand_total):>12s}")
    print()

    # Per-file breakdown
    out_dir = ROOT / "evidence"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_md = out_dir / f"disk_cleanup_audit_{ts}.md"

    with out_md.open("w", encoding="utf-8") as f:
        f.write(f"# Disk Cleanup Audit — {ts}\n\n")
        f.write(f"Toplam silinebilir: **{hr(grand_total)}**\n\n")
        for cat, info in data.items():
            f.write(f"## {cat} — {hr(info['total_bytes'])}\n\n")
            for path, sz, kind in sorted(info["paths"], key=lambda x: -x[1]):
                f.write(f"- `{path}` ({kind}) — {hr(sz)}\n")
            f.write("\n")
        if orphans:
            f.write(f"## orphan_db_wal — {hr(orphan_total)}\n\n")
            for path, sz in sorted(orphans, key=lambda x: -x[1]):
                f.write(f"- `{path}` — {hr(sz)} ⚠️ orphan (parent .db yok)\n")
            f.write("\n")
        # Whitelist
        f.write("## KORUNAN (whitelist)\n\n")
        for keep in sorted(KEEP_PATTERNS):
            p = ROOT / keep
            if p.exists():
                f.write(f"- `{keep}` — {hr(file_size(p))} ✅\n")
            else:
                f.write(f"- `{keep}` — N/A\n")

    print(f"💾 Detail: {out_md}")
    print()
    print("📋 Aksiyon:")
    print(
        "  1. scripts/disk_cleanup_phase_a.bat  — safe deletes (orphan WAL, pre-phase, htmlcov, pycache)"
    )
    print("  2. scripts/disk_cleanup_phase_b.bat  — backups/ prune (son 3 backup koru)")


if __name__ == "__main__":
    main()
