"""
Phase 47f.7+ Maintenance Jobs
=============================
Daily DB snapshot + 10-min heartbeat ping. Wired in bot.py via JobQueue.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime
from pathlib import Path

from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.maintenance")

DB_PATH = Path("data_store/polypaper.db")
BACKUP_DIR = Path("data_store/backups")
MAX_BACKUPS = 7  # keep last 7 daily snapshots


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

        if not DB_PATH.exists():
            logger.warning(f"[snapshot] DB not found at {DB_PATH} — skip")
            return

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
            async with aiosqlite.connect(str(dest), timeout=60) as target:
                # pages=200 + sleep=50ms: her 200 page sonrası 50ms uyu.
                # 8 GB / ~4KB page ≈ 2M page → 10K batch. Her batch ~100-150ms
                # işleme + 50ms sleep ≈ 200ms. Toplam ≈ 15-25 dakika.
                # Sleep asenkron event loop'a yield fırsatı verir;
                # engine'in DB query'leri bu boşluklarda işlenir.
                await source.backup(target, pages=200, sleep=0.050)
        finally:
            await source.close()

        elapsed = _time.monotonic() - t0

        # Prune old snapshots
        snaps = sorted(BACKUP_DIR.glob("polypaper_*.db"))
        for old in snaps[:-MAX_BACKUPS]:
            try:
                old.unlink()
            except Exception:
                pass

        size_mb = dest.stat().st_size / (1024 * 1024)
        logger.info(f"[snapshot] {dest.name} ({size_mb:.1f} MB) created "
                    f"in {elapsed:.1f}s")

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
                        f"kept: <code>{min(len(snaps) + 1, MAX_BACKUPS)}</code>"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"[snapshot] notify failed: {e}")
    except Exception as e:
        logger.exception(f"[snapshot] failed: {e}")


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
                except Exception as e:
                    logger.warning(f"[heartbeat] notify failed: {e}")

        context.application.bot_data["_hb_prev"] = {
            "halted": halted, "pnl": pnl, "streak": streak, "cycle": cycle,
            "pnl_warn": pnl_warn,
        }
    except Exception as e:
        logger.exception(f"[heartbeat] failed: {e}")
