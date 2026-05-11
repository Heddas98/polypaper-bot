"""P0-05d smoke test - snapshot + restore round-trip in isolated tmp dir."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="p0_05_smoke_"))
    db_path = tmp_root / "polypaper.db"
    backup_dir = tmp_root / "backups"
    backup_dir.mkdir()
    manifest_path = backup_dir / "manifest.json"
    print("[smoke] tmp_root=" + str(tmp_root))

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO schema_version VALUES (20)")
    conn.execute("CREATE TABLE dummy (k TEXT, v TEXT)")
    for i in range(1000):
        conn.execute("INSERT INTO dummy VALUES (?, ?)", ("k_" + str(i), "v_" + str(i)))
    conn.commit()
    conn.close()
    print("[smoke] DB built: " + str(db_path.stat().st_size) + " bytes")

    import telegram_bot.jobs.maintenance_jobs as mj

    mj.DB_PATH = db_path
    mj.BACKUP_DIR = backup_dir
    mj.MANIFEST_PATH = manifest_path

    class _Bot:
        send_message = AsyncMock()

    ctx = SimpleNamespace(bot=_Bot(), application=SimpleNamespace(bot_data={}))
    os.environ.pop("TELEGRAM_ADMIN_CHAT_ID", None)

    print("[smoke] running daily_db_snapshot_job")
    await mj.daily_db_snapshot_job(ctx)

    if not manifest_path.exists():
        print("[FAIL] manifest not created")
        return 2
    with manifest_path.open() as f:
        m = json.load(f)
    snaps = m.get("snapshots", [])
    print("[smoke] manifest count=" + str(len(snaps)))
    if not snaps:
        print("[FAIL] manifest empty")
        return 3
    latest = snaps[-1]
    print("[smoke] latest=" + json.dumps(latest))

    on_disk = backup_dir / latest["filename"]
    actual = mj._sha256_file(on_disk)
    if actual != latest.get("sha256"):
        print("[FAIL] hash mismatch")
        return 5
    if latest.get("schema_version") != 20:
        print("[FAIL] schema_version != 20: " + str(latest.get("schema_version")))
        return 6
    if latest.get("size_bytes") != on_disk.stat().st_size:
        print("[FAIL] size_bytes mismatch")
        return 7

    import importlib.util

    rspec = importlib.util.spec_from_file_location(
        "restore_cli", str(ROOT / "scripts" / "restore_from_backup.py")
    )
    rmod = importlib.util.module_from_spec(rspec)
    rspec.loader.exec_module(rmod)
    rmod.DB_PATH = db_path
    rmod.BACKUP_DIR = backup_dir
    rmod.MANIFEST_PATH = manifest_path

    print("[smoke] cmd_list:")
    if rmod.cmd_list() != 0:
        print("[FAIL] cmd_list")
        return 8
    print("[smoke] cmd_verify_all:")
    if rmod.cmd_verify_all() != 0:
        print("[FAIL] cmd_verify_all")
        return 9
    print("[smoke] cmd_restore dry-run:")
    args = SimpleNamespace(latest=True, restore=None, yes=True, dry_run=True)
    if rmod.cmd_restore(args) != 0:
        print("[FAIL] dry-run")
        return 10
    print("[smoke] cmd_restore real:")
    args = SimpleNamespace(latest=True, restore=None, yes=True, dry_run=False)
    if rmod.cmd_restore(args) != 0:
        print("[FAIL] real restore")
        return 11

    restored_hash = mj._sha256_file(db_path)
    if restored_hash != latest.get("sha256"):
        print("[FAIL] post-restore hash drift")
        return 12

    pre = list(backup_dir.glob("pre_restore_*.db"))
    if not pre:
        print("[FAIL] pre_restore backup missing")
        return 13
    print("[smoke] pre_restore=" + pre[0].name)

    print("[smoke] PASS sha=" + restored_hash[:16])
    shutil.rmtree(tmp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
