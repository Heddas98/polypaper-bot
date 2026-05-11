"""
Phase 47f.7+ Maintenance Jobs
=============================
Daily DB snapshot + 10-min heartbeat ping. Wired in bot.py via JobQueue.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.maintenance")

DB_PATH = Path("data_store/polypaper.db")
BACKUP_DIR = Path("data_store/backups")
MANIFEST_PATH = BACKUP_DIR / "manifest.json"
MAX_BACKUPS = 7  # keep last 7 daily snapshots
HASH_CHUNK_SIZE = 1024 * 1024  # 1 MB chunks for SHA256 streaming


def _sha256_file(path: Path) -> str:
    """P0-05a (2026-05-09): Compute SHA256 of a file, streaming 1 MB chunks.

    Sync I/O — caller wraps in `asyncio.to_thread` to keep event loop free.
    For 8 GB DB ~30-60s on local SSD; we accept this cost on the snapshot
    boundary (already on a slow path) for tamper-evidence + corruption
    detection beyond what atomic-rename gives us.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    """P0-05b (2026-05-09): Load manifest.json or return empty skeleton.

    Schema:
      {
        "version": 1,
        "snapshots": [
          {
            "filename": "polypaper_2026-05-09.db",
            "sha256": "abc123...",
            "size_bytes": 12345678,
            "created_utc": "2026-05-09T12:34:56Z",
            "schema_version": 20
          }
        ]
      }
    """
    if not MANIFEST_PATH.exists():
        return {"version": 1, "snapshots": []}
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "snapshots" not in data:
            logger.warning("[manifest] malformed — re-initializing")
            return {"version": 1, "snapshots": []}
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[manifest] load failed ({type(e).__name__}: {e}) "
                       "— re-initializing")
        return {"version": 1, "snapshots": []}


def _save_manifest(data: dict) -> None:
    """P0-05b: Atomic write of manifest.json via .tmp + os.replace."""
    tmp = MANIFEST_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(MANIFEST_PATH)


def _read_schema_version() -> int | None:
    """P0-05b: Read current schema_version from DB without aiosqlite."""
    try:
        import sqlite3
        with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True,
                             timeout=5.0) as conn:
            cur = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else None
    except (sqlite3.Error, OSError, ValueError) as e:
        logger.warning(f"[manifest] schema_version read failed: "
                       f"{type(e).__name__}: {e}")
        return None


async def daily_db_snapshot_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 82b.6 — Incremental, NON-blocking SQLite backup.

    ROOT CAUSE FIX (cycle 160-179+ stall loop):
      Eski kod `await db.conn.backup(target)` çağrısıyla engine'in
      AYNI aiosqlite Connection'ını kullanıyordu. 8.8 GB DB üzerinde
      backup tek seferde tüm sayfaları kopyalarken connection'un
      executor thread'i kilitleniyor, engine'in her DB query'si
      kuyruğa takılıyor, 90s içinde stall_watchdog cycle'ı cancel
      ediyor ve bu döngü snapshot bitene kadar (10+ dakika) sürüyor.

    FIX:
      1) Snapshot için AYRI aiosqlite.Connection aç (source) — engine
         DB bağlantısına dokunmaz.
      2) backup(target, pages=200, sleep=0.050) kullan: her 200 page
         sonrasında 50ms uyut — diğer koroutine'lere yield fırsatı ver
         ama toplam süreyi de makul tut (8 GB ≈ 15-25 dk).
      3) ENV ile tamamen kapatılabilir: `ENABLE_DAILY_DB_SNAPSHOT=false`.
    """
    if os.getenv("ENABLE_DAILY_DB_SNAPSHOT", "true").lower() != "true":
        logger.info("[snapshot] disabled via ENABLE_DAILY_DB_SNAPSHOT=false")
        return

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y-%m-%d")
        dest = BACKUP_DIR / f"polypaper_{ts}.db"
        # T11.3 Bulgu B fix (2026-04-23): atomic rename pattern
        #
        # ESKI davranis: `aiosqlite.connect(dest)` dogrudan ana path'e yazar.
        # 8GB DB 15-25 dk incremental backup sirasinda bot restart/Ctrl+C
        # olursa yari-yazilmis dosya KALICI olur (header null, NOT_SQLITE).
        # Kanit: 2026-04-20.db (780 MB) + 2026-04-23.db (729 MB) corrupt.
        #
        # YENI: backup'i `dest_tmp`'e yaz; basari sonrasi atomic rename
        # (`os.replace` / `Path.replace`) ile dest'e tasi. Interrupt ederse
        # tmp kalir, dest hicbir zaman yarim-yazilmis olmaz. Bir sonraki
        # cycle veya finally cleanup tmp'yi temizler.
        dest_tmp = dest.with_suffix(".db.tmp")

        if not DB_PATH.exists():
            logger.warning(f"[snapshot] DB not found at {DB_PATH} — skip")
            return

        # Ghost tmp cleanup: onceki yari-kesik backup'tan kalmis dosyalar
        # (ornegin bot Ctrl+C ile durdurulmussa finally blogu calismamis
        # olabilir). Cycle basinda temizle, disk doldurmasin.
        for ghost in BACKUP_DIR.glob("polypaper_*.db.tmp"):
            try:
                ghost.unlink()
                logger.info(f"[snapshot] cleaned ghost tmp: {ghost.name}")
            except OSError as e:
                logger.warning(f"[snapshot] ghost tmp cleanup failed "
                               f"{ghost.name}: {e}")

        import aiosqlite
        import time as _time
        t0 = _time.monotonic()

        # Phase 82b.6 — SEPARATE connection (read-only). Engine connection
        # stays free; WAL mode keeps this consistent without locking writes.
        # Phase 82e Sprint 2.2 — open with retry + immutable=1 fallback so
        # transient WAL checkpoints can't silently kill the backup.
        from db.ro_connect import open_ro_aiosqlite
        source = await open_ro_aiosqlite(DB_PATH, connect_timeout_s=60.0)
        try:
            async with aiosqlite.connect(str(dest_tmp), timeout=60) as target:
                # pages=200 + sleep=50ms: her 200 page sonrası 50ms uyu.
                # 8 GB / ~4KB page ≈ 2M page → 10K batch. Her batch ~100-150ms
                # işleme + 50ms sleep ≈ 200ms. Toplam ≈ 15-25 dakika.
                # Sleep asenkron event loop'a yield fırsatı verir;
                # engine'in DB query'leri bu boşluklarda işlenir.
                await source.backup(target, pages=200, sleep=0.050)
            # P0-05a (2026-05-09): SHA256 verification BEFORE atomic rename.
            # Read back the bytes we just wrote, hash them, capture file size.
            # If the read itself fails (disk error, truncation, corruption)
            # the exception propagates and the `finally` block cleans dest_tmp
            # — dest is never created. Atomic rename below only happens on
            # successful hash, so dest is guaranteed to be hash-verified.
            #
            # Run sync I/O in to_thread so the event loop stays free during
            # the ~30-60s hash on multi-GB DBs.
            sha256_hex = await asyncio.to_thread(_sha256_file, dest_tmp)
            size_bytes = dest_tmp.stat().st_size
            schema_v = await asyncio.to_thread(_read_schema_version)
            # Atomic rename: tmp -> dest. POSIX + Windows'ta tek syscall,
            # yarim-rename olmaz. Backup icin DURUM BURADA KESINLESIR.
            dest_tmp.replace(dest)
        finally:
            await source.close()
            # Fail path: backup ortasinda exception atildiysa dest_tmp
            # kismen yazilmis halde kalmis olabilir (rename asla calismamis).
            # Temizle ki bir sonraki cycle ghost-glob'a dusmesin hemen.
            if dest_tmp.exists():
                try:
                    dest_tmp.unlink()
                    logger.warning(f"[snapshot] cleaned failed tmp: "
                                   f"{dest_tmp.name}")
                except OSError as e:
                    logger.warning(f"[snapshot] failed tmp cleanup failed: {e}")

        elapsed = _time.monotonic() - t0

        # Prune old snapshots
        snaps = sorted(BACKUP_DIR.glob("polypaper_*.db"))
        for old in snaps[:-MAX_BACKUPS]:
            try:
                old.unlink()
            except OSError:
                # T11.8-B (2026-04-24): narrow from bare Exception. Path.
                # unlink() raises OSError (PermissionError/FileNotFoundError
                # subclasses). Silent swallow correct — pruning is best-
                # effort, missing/locked old snapshot can be retried tomorrow.
                pass

        # P0-05b (2026-05-09): manifest.json update.
        # Append the just-renamed snapshot's metadata (sha256, size, ts,
        # schema_version), then prune any entries whose underlying file no
        # longer exists (matches the file-prune above + handles manual
        # deletions). Atomic write via _save_manifest's .tmp+replace.
        try:
            manifest = _load_manifest()
            entry = {
                "filename": dest.name,
                "sha256": sha256_hex,
                "size_bytes": size_bytes,
                "created_utc": datetime.now(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "schema_version": schema_v,
            }
            # Replace any existing entry with same filename (re-run on same
            # day overwrites). Then append new entry.
            manifest["snapshots"] = [
                e for e in manifest.get("snapshots", [])
                if e.get("filename") != dest.name
            ]
            manifest["snapshots"].append(entry)
            # Prune entries for files that no longer exist on disk
            extant = {p.name for p in BACKUP_DIR.glob("polypaper_*.db")}
            manifest["snapshots"] = [
                e for e in manifest["snapshots"]
                if e.get("filename") in extant
            ]
            _save_manifest(manifest)
        except (OSError, KeyError, TypeError) as e:
            # Manifest update failure is non-fatal: snapshot file itself is
            # already on disk (atomic rename succeeded above). Log + continue.
            logger.warning(f"[manifest] update failed: "
                           f"{type(e).__name__}: {e}")

        size_mb = size_bytes / (1024 * 1024)
        logger.info(f"[snapshot] {dest.name} ({size_mb:.1f} MB) created "
                    f"in {elapsed:.1f}s sha256={sha256_hex[:12]}…")

        admin_id = resolve_admin_chat_id()
        if admin_id:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💾 <b>Daily DB Snapshot</b>\n"
                        f"<code>{dest.name}</code>\n"
                        f"size: <code>{size_mb:.1f} MB</code>\n"
                        f"süre: <code>{elapsed:.1f}s</code>\n"
                        f"sha256: <code>{sha256_hex[:16]}…</code>\n"
                        f"kept: <code>{min(len(snaps) + 1, MAX_BACKUPS)}</code>"
                    ),
                    parse_mode="HTML",
                )
            except (TelegramError, asyncio.TimeoutError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception. send_
                # message TelegramError + transport timeout. Snapshot itself
                # already succeeded and was logged; notify is best-effort.
                logger.warning(f"[snapshot] notify failed: "
                               f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. Daily snapshot touches OS / aiosqlite / file system — many
        # exception classes possible. logger.exception preserves full trace
        # while keeping JobQueue scheduler thread alive (T7.6 job-safety
        # exemption). Atomic rename above already guards backup integrity.
        logger.exception(f"[snapshot] failed: {e}")


async def wal_checkpoint_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Epic 5 T5.5 (2026-04-21) — Periodic WAL TRUNCATE checkpoint.

    `wal_autocheckpoint=5000` PASSIVE hiçbir writer'ı bloke etmez ama
    readers (daily_db_snapshot_job, ro_connect) checkpoint'i ilerletemez
    → WAL monotonik büyür (gözlemlenen: 79 MB vs 20 MB threshold).

    Bu job sadece shrink için mevcut: `PRAGMA wal_checkpoint(TRUNCATE)`.
    TRUNCATE mode:
      - Tüm committed frames'i ana DB'ye uygula
      - WAL dosyasını 0 byte'a kısalt (yeni frames için yeniden açılır)
      - Aktif reader varsa beklemez, partial progress yapar (busy ise
        busy-count döner ama error throw etmez)
      - Writer lock'u SADECE WAL'ı truncate ederken kısa bir an alır
        (~ms mertebesi); engine yazımına mesurable etkisi yok

    Engine DB bağlantısını kullanır (context.application.bot_data["db"]).
    Ayrı connection açmıyoruz çünkü checkpoint engine'in gördüğü WAL
    üzerinde çalışmalı — farklı connection farklı snapshot görebilir.

    ENV:
      ENABLE_WAL_CHECKPOINT        — "false" → job çalışmaz
      WAL_CHECKPOINT_INTERVAL_HOURS — default 6 (bot.py tarafında)
    """
    if os.getenv("ENABLE_WAL_CHECKPOINT", "true").lower() != "true":
        logger.info("[wal_checkpoint] disabled via ENABLE_WAL_CHECKPOINT=false")
        return

    db = context.application.bot_data.get("db")
    if db is None or getattr(db, "conn", None) is None:
        logger.warning("[wal_checkpoint] DB connection unavailable — skip")
        return

    try:
        import time as _time
        wal_path = DB_PATH.with_name(DB_PATH.name + "-wal")
        size_before = wal_path.stat().st_size if wal_path.exists() else 0

        t0 = _time.monotonic()
        # TRUNCATE checkpoint returns (busy, log_pages, checkpointed_pages)
        # busy=0 → fully succeeded; busy=1 → some readers blocked, partial
        cur = await db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = await cur.fetchone()
        elapsed_ms = (_time.monotonic() - t0) * 1000

        busy, log_pages, ckpt_pages = 0, 0, 0
        if row:
            # SQLite returns a 3-tuple; aiosqlite Row supports index access
            try:
                busy = int(row[0]) if row[0] is not None else 0
                log_pages = int(row[1]) if row[1] is not None else 0
                ckpt_pages = int(row[2]) if row[2] is not None else 0
            except (IndexError, TypeError, ValueError):
                pass

        size_after = wal_path.stat().st_size if wal_path.exists() else 0
        mb_before = size_before / (1024 * 1024)
        mb_after = size_after / (1024 * 1024)
        shrunk = mb_before - mb_after

        status = "OK" if busy == 0 else "PARTIAL"
        logger.info(
            f"[wal_checkpoint] {status}: {mb_before:.1f} MB → {mb_after:.1f} MB "
            f"(shrunk {shrunk:+.1f} MB, log={log_pages}, ckpt={ckpt_pages}, "
            f"busy={busy}, elapsed={elapsed_ms:.0f}ms)"
        )
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. PRAGMA wal_checkpoint is a writer-lock hot path; aiosqlite
        # / OperationalError + OS + timing surfaces all possible. Job-
        # safety exemption so the 6h scheduler stays alive.
        logger.exception(f"[wal_checkpoint] failed: {e}")


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lightweight liveness ping. Logs every cycle, sends Telegram only on
    state changes (halt, big PnL drop) to avoid spam."""
    try:
        engine = context.application.bot_data.get("engine")
        if engine is None:
            return
        risk = getattr(engine, "risk", None)
        state = getattr(risk, "state", None) if risk else None
        limits = getattr(risk, "limits", None) if risk else None
        halted = getattr(state, "halted", False) if state else False
        pnl = getattr(state, "daily_pnl", 0.0) if state else 0.0
        streak = getattr(state, "consecutive_losses", 0) if state else 0
        max_loss = getattr(limits, "max_daily_loss", 50.0) if limits else 50.0

        prev = context.application.bot_data.get("_hb_prev", {})
        prev_halted = prev.get("halted", False)
        prev_warn = prev.get("pnl_warn", False)

        # 80% drawdown warning: fires once per crossing
        pnl_warn = (pnl <= -0.8 * max_loss) and not halted
        warn_triggered = pnl_warn and not prev_warn

        logger.info(f"💓 [heartbeat] halted={halted} pnl={pnl:+.2f}/"
                    f"{-max_loss:+.2f} streak={streak}")

        # Ping admin on: halt state change, 80% warning crossing, or every 6 cycles
        cycle = prev.get("cycle", 0) + 1
        ping = (halted != prev_halted) or warn_triggered or (cycle % 6 == 0)

        if ping:
            admin_id = resolve_admin_chat_id()
            if admin_id:
                if halted:
                    emoji = "🛑"
                elif warn_triggered or pnl_warn:
                    emoji = "⚠️"
                else:
                    emoji = "✅"
                try:
                    msg = (
                        f"{emoji} <b>Heartbeat</b>\n"
                        f"halted=<code>{halted}</code> "
                        f"pnl=<code>{pnl:+.2f}</code>/"
                        f"<code>{-max_loss:+.2f}</code> "
                        f"streak=<code>{streak}</code>"
                    )
                    if warn_triggered:
                        msg += "\n⚠️ <b>Günlük zararın %80'i aşıldı</b>"
                    await context.bot.send_message(
                        chat_id=admin_id, text=msg, parse_mode="HTML",
                    )
                except (TelegramError, asyncio.TimeoutError) as e:
                    # T11.8-B (2026-04-24): narrow from bare Exception.
                    # Heartbeat send is best-effort; transport failure is
                    # logged but doesn't break the cycle bookkeeping below.
                    logger.warning(f"[heartbeat] notify failed: "
                                   f"{type(e).__name__}: {e}")

        context.application.bot_data["_hb_prev"] = {
            "halted": halted, "pnl": pnl, "streak": streak, "cycle": cycle,
            "pnl_warn": pnl_warn,
        }
    except Exception as e:  # noqa: BLE001
        # T11.8-B (2026-04-24): outermost job-runner wrapper intentionally
        # wide. Heartbeat reads engine internals (risk.state.halted,
        # daily_pnl, etc.) — AttributeError class drift possible during
        # refactors. Job-safety exemption keeps the 10-min scheduler alive.
        logger.exception(f"[heartbeat] failed: {e}")
