"""
PolyPaper Bot — Data Status Handler (P0-08-E7, 2026-05-08)
==========================================================
Telegram /data_status (alias /ds) komutu — backtest data storage canlı paneli.

Heddas direktifi 2026-05-08:
  - "Telegramda backtest kısmında sürekli ne kadar veri var, kaç markette
    işlem yapılabilir vs gibi infolar olsun"
  - Cold archive yok; backtest direkt ana DB'den veri çeker
  - Disk büyümesi sürekli (auto-prune yok)

Panel içeriği:
  - DB boyutu + disk free
  - Veri başlangıç tarihi + yaş
  - Tablolarda satır sayısı (ob_deltas, public_trades, external_prices,
    ob_snapshots, candles_ext, candles_poly)
  - Market readiness: hangi (asset, tf) ≥24h data biriktirilmiş
  - Live ingestion rate (son 1 dk)
  - Backtest komutu formatı
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from html import escape as _esc

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.telegram.data_status")


def _fmt_bytes(n: int) -> str:
    """Human-readable byte count."""
    if n is None:
        return "?"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def _fmt_count(n: int) -> str:
    """Human-readable row count."""
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _ts_age(ts_ms: int) -> str:
    """Human-readable age from ts_ms."""
    if not ts_ms:
        return "?"
    age_s = time.time() - (ts_ms / 1000)
    if age_s < 60:
        return f"{int(age_s)} sn"
    if age_s < 3600:
        return f"{int(age_s / 60)} dk"
    if age_s < 86400:
        return f"{int(age_s / 3600)} sa"
    return f"{int(age_s / 86400)} gün"


async def data_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /data_status — backtest data storage panel.
    Alias /ds.
    """
    db = context.bot_data.get("db")
    settings = context.bot_data.get("settings")
    if db is None or db.conn is None:
        await update.message.reply_text("⚠️ DB bağlantısı yok.")
        return

    try:
        # ── DB boyutu + disk free ──────────────────────────────────
        db_path = getattr(db, "db_path", "data_store/polypaper.db")
        try:
            db_size = os.path.getsize(db_path)
        except OSError:
            db_size = 0
        try:
            disk = shutil.disk_usage(os.path.dirname(db_path) or ".")
            disk_total = disk.total
            disk_free = disk.free
            disk_used_pct = 100 * (disk.used / disk.total) if disk.total else 0
        except OSError:
            disk_total = disk_free = 0
            disk_used_pct = 0

        # ── Tablolarda satır sayısı ────────────────────────────────
        TABLES = [
            "ob_deltas", "public_trades", "external_prices",
            "ob_snapshots", "candles_ext", "candles_poly",
        ]
        row_counts: dict[str, int] = {}
        for t in TABLES:
            try:
                async with db.conn.execute(f"SELECT COUNT(*) FROM {t}") as cur:
                    row = await cur.fetchone()
                    row_counts[t] = int(row[0]) if row else 0
            except Exception:  # noqa: BLE001
                row_counts[t] = -1  # Tablo yok / hata

        # ── Veri başlangıç tarihi ──────────────────────────────────
        oldest_ts = None
        for t in ("ob_deltas", "public_trades", "external_prices"):
            if row_counts.get(t, 0) <= 0:
                continue
            try:
                async with db.conn.execute(
                    f"SELECT MIN(ts_ms) FROM {t}"
                ) as cur:
                    row = await cur.fetchone()
                    if row and row[0]:
                        ts = int(row[0])
                        if oldest_ts is None or ts < oldest_ts:
                            oldest_ts = ts
            except Exception:  # noqa: BLE001
                pass

        # ── Live ingestion rate (son 1 dk) ─────────────────────────
        ingestion_1m = 0
        cutoff_ms = int(time.time() * 1000) - 60_000
        for t in ("ob_deltas", "public_trades", "external_prices"):
            if row_counts.get(t, 0) <= 0:
                continue
            try:
                async with db.conn.execute(
                    f"SELECT COUNT(*) FROM {t} WHERE ts_ms >= ?", (cutoff_ms,)
                ) as cur:
                    row = await cur.fetchone()
                    if row and row[0]:
                        ingestion_1m += int(row[0])
            except Exception:  # noqa: BLE001
                pass

        # ── Market readiness (≥24h data) ──────────────────────────
        # candles_poly'de timeframe başına en eski open_ts ve count'a bak
        readiness: list[tuple[str, str, int, bool]] = []
        # Get matrix from settings (P0-08-A)
        tf_matrix = getattr(settings, "TF_DISCOVERY_MATRIX", None) or {}
        cutoff_24h_ms = int(time.time() * 1000) - 24 * 3600 * 1000

        for tf, cfg in tf_matrix.items():
            if not isinstance(cfg, dict):
                continue
            method = cfg.get("method")
            if method == "slug_prefix":
                assets = cfg.get("assets", [])
            elif method == "series_id":
                assets = list((cfg.get("series_map") or {}).keys())
            else:
                continue
            for asset in assets:
                # candles_poly'de bu (asset, tf) için en eski open_ts
                try:
                    async with db.conn.execute(
                        """SELECT MIN(open_ts), COUNT(*)
                           FROM candles_poly WHERE asset=? AND timeframe=?""",
                        (asset, tf),
                    ) as cur:
                        row = await cur.fetchone()
                        oldest_ms = int(row[0]) if row and row[0] else None
                        cnt = int(row[1]) if row and row[1] else 0
                except Exception:  # noqa: BLE001
                    oldest_ms = None
                    cnt = 0
                ready = (oldest_ms is not None and oldest_ms <= cutoff_24h_ms)
                readiness.append((asset, tf, cnt, ready))

        # ── Disk uyarıları ─────────────────────────────────────────
        disk_emoji = "🟢"
        disk_note = ""
        gb_free = disk_free / (1024**3) if disk_free else 0
        if gb_free < 50:
            disk_emoji = "🔴"
            disk_note = " <b>CRITICAL</b>"
        elif gb_free < 100:
            disk_emoji = "🟡"
            disk_note = " <b>WARNING</b>"

        # ── Mesaj derlemesi ────────────────────────────────────────
        lines = [
            "📊 <b>Backtest Data Storage</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"💾 DB boyutu: <b>{_fmt_bytes(db_size)}</b>",
            f"{disk_emoji} Disk free: <b>{_fmt_bytes(disk_free)}</b> "
            f"/ {_fmt_bytes(disk_total)} ({disk_used_pct:.1f}% used){disk_note}",
        ]

        if oldest_ts:
            ts_iso = datetime.fromtimestamp(oldest_ts / 1000, tz=timezone.utc).isoformat()[:19]
            lines.append(f"📅 Veri başlangıcı: {_esc(ts_iso)}Z ({_ts_age(oldest_ts)})")
        else:
            lines.append("📅 Veri başlangıcı: <i>henüz veri yok</i>")

        lines += [
            "",
            "<b>📈 Tablo satır sayıları:</b>",
            f"  • ob_deltas:       <code>{_fmt_count(row_counts.get('ob_deltas', 0))}</code>",
            f"  • public_trades:   <code>{_fmt_count(row_counts.get('public_trades', 0))}</code>",
            f"  • external_prices: <code>{_fmt_count(row_counts.get('external_prices', 0))}</code>",
            f"  • ob_snapshots:    <code>{_fmt_count(row_counts.get('ob_snapshots', 0))}</code>",
            f"  • candles_ext:     <code>{_fmt_count(row_counts.get('candles_ext', 0))}</code>",
            f"  • candles_poly:    <code>{_fmt_count(row_counts.get('candles_poly', 0))}</code>",
            "",
            f"🔥 Live ingestion (son 1 dk): <b>{ingestion_1m}</b> row "
            f"(<code>{ingestion_1m / 60:.1f}/sec</code>)",
            "",
            "<b>🎯 Backtest hazır market'ler (≥24h data):</b>",
        ]

        if readiness:
            for asset, tf, cnt, ready in readiness:
                emoji = "✅" if ready else "⏳"
                lines.append(
                    f"  {emoji} {asset}_{tf}: <code>{cnt}</code> candle"
                )
        else:
            lines.append("  <i>matrix tanımlı değil</i>")

        lines += [
            "",
            "<b>📚 Backtest komutu:</b>",
            "<code>/backtest BTC 1h 7</code> — son 7 gün BTC 1h",
            "<code>/backtest BTC 5m 30</code> — son 30 gün BTC 5m",
            "<code>/backtest ETH 15m 14</code> — son 14 gün ETH 15m",
        ]

        text = "\n".join(lines)
        await update.message.reply_text(
            text, parse_mode="HTML", disable_web_page_preview=True
        )

    except Exception as e:  # noqa: BLE001
        logger.exception(f"data_status error: {e}")
        await update.message.reply_text(
            f"❌ Data status error: <code>{_esc(str(e)[:200])}</code>",
            parse_mode="HTML",
        )
