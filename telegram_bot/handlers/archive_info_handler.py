"""
PolyPaper Bot — /archive_info Handler (Phase 82e Sprint B.2)
============================================================
Shows ArchiveReader info: hot tier (SQLite) range, cold tier (Parquet)
range, file count, total size, row counts. Proves the archive reader
can see both tiers and backtest can span the full history.

Usage:
  /archive_info           → full info dump
  /ai_info                → short alias

ADMIN ONLY.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import Settings
from telegram_bot.handlers._exc_render import render_user_exception
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.archive_info")


def _is_admin(context, telegram_id: int) -> bool:
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


def _fmt_ts(ts_ms: int) -> str:
    if not ts_ms:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError, OverflowError, OSError):
        # T11.8-B (2026-04-24): narrow from bare Exception. fromtimestamp
        # raises ValueError (out of range), TypeError (non-numeric), and
        # OSError on platforms with 32-bit time_t. Fallback to raw int.
        return str(ts_ms)


def _span_days(a_ms: int, b_ms: int) -> float:
    if not a_ms or not b_ms or b_ms <= a_ms:
        return 0.0
    return (b_ms - a_ms) / (1000 * 86400)


async def archive_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/archive_info — show SQLite+Parquet tier diagnostics."""
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    try:
        from backtest.archive_reader import ArchiveReader
    except ImportError as e:
        return await update.message.reply_text(
            render_user_exception(e, "⚠️ ArchiveReader import failed"), parse_mode="HTML"
        )

    await update.message.reply_text(
        "⏳ Archive reader taranıyor (hot + cold tier)...", parse_mode="HTML"
    )

    try:
        reader = ArchiveReader()
        info = await asyncio.to_thread(reader.info)
        counts = await asyncio.to_thread(reader.count_rows)
    except Exception as e:  # noqa: BLE001
        logger.error(f"archive_info failed: {e}", exc_info=True)
        # T11.6-OK reason=/archive_info admin-only, parquet/sqlite I/O hatasi
        # operator icin gerekli (Disk full vs missing file ayrimi). Truncated.
        return await update.message.reply_text(  # noqa: T11.6-OK
            f"⚠️ Archive reader hata: <code>{esc(str(e)[:200])}</code>", parse_mode="HTML"
        )

    # Build HTML report
    hot = info.get("hot_range", {})
    cold = info.get("cold_range", {})

    hot_min = hot.get("min_ms", 0) if isinstance(hot, dict) else 0
    hot_max = hot.get("max_ms", 0) if isinstance(hot, dict) else 0
    cold_min = cold.get("min_ms", 0) if isinstance(cold, dict) else 0
    cold_max = cold.get("max_ms", 0) if isinstance(cold, dict) else 0

    hot_span = _span_days(hot_min, hot_max)
    cold_span = _span_days(cold_min, cold_max)

    # Combined span (min of cold_min..max of hot_max)
    all_min = min(hot_min or 10**18, cold_min or 10**18)
    if all_min == 10**18:
        all_min = 0
    all_max = max(hot_max, cold_max)
    total_span = _span_days(all_min, all_max)

    parquet_mb = info.get("parquet_size_mb", 0.0)
    pq_files = info.get("parquet_files", 0)

    text = (
        "📦 <b>Archive Reader Info</b>\n"
        f"<i>Phase 82e Sprint B.2 — hot (SQLite) + cold (Parquet)</i>\n"
        f"{'─' * 24}\n\n"
        f"<b>🔥 Hot Tier (SQLite, live)</b>\n"
        f"  Path: <code>{esc(info.get('db_path', ''))}</code>\n"
        f"  Available: {'✅' if info.get('hot_available') else '❌'}\n"
        f"  Rows: <code>{counts.get('hot', 0):,}</code>\n"
        f"  Range: <code>{esc(_fmt_ts(hot_min))}</code> → "
        f"<code>{esc(_fmt_ts(hot_max))}</code>\n"
        f"  Span: <b>{hot_span:.1f} days</b>\n\n"
        f"<b>🧊 Cold Tier (Parquet, archive)</b>\n"
        f"  Path: <code>{esc(info.get('archive_dir', ''))}</code>\n"
        f"  Files: <b>{pq_files}</b> parquet dosyasi\n"
        f"  Size: <b>{parquet_mb:.1f} MB</b>\n"
        f"  Rows: <code>{counts.get('cold', 0):,}</code>\n"
        f"  Range: <code>{esc(_fmt_ts(cold_min))}</code> → "
        f"<code>{esc(_fmt_ts(cold_max))}</code>\n"
        f"  Span: <b>{cold_span:.1f} days</b>\n\n"
        f"<b>📊 Combined</b>\n"
        f"  Total rows: <code>{counts.get('total', 0):,}</code>\n"
        f"  Full span: <b>{total_span:.1f} days</b>\n"
        f"  Earliest: <code>{esc(_fmt_ts(all_min))}</code>\n"
        f"  Latest:   <code>{esc(_fmt_ts(all_max))}</code>\n\n"
        f"<i>Backtest uses this via ReplayConfig.use_archive=True.</i>"
    )

    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
