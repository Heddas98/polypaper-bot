"""
PolyPaper Bot - Reality Gap Panel (P1-03-c, 2026-05-09)
========================================================
Telegram /reality_gap (alias /rg) - last nightly drift report.

Shows the most recent `reality_gap_latest.md` written by the nightly job +
quick stats from the live_trades table. If the job hasn't run yet, prints
a hint to wait for the first scheduled cycle.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from html import escape as _esc
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.telegram.reality_gap")

LATEST_PATH = Path("data_store/audits/reality_gap_latest.md")
HISTORY_GLOB = "reality_gap_*.md"


async def reality_gap_command(update: Update,
                               context: ContextTypes.DEFAULT_TYPE):
    """/reality_gap (alias /rg) - paper-vs-live drift latest report."""
    try:
        L: list[str] = []
        L.append("<b>Reality Gap</b> (paper * MULT vs live)")
        L.append("")

        mult = float(os.getenv("REALITY_GAP_MULT", "0.66"))
        alert_pct = float(os.getenv("REALITY_GAP_ALERT_PCT", "10.0"))
        window_h = int(os.getenv("REALITY_GAP_WINDOW_H", "168"))
        enabled = os.getenv("REALITY_GAP_ENABLED", "true").lower() in {
            "1", "true", "yes", "on"}

        status_icon = "🟢" if enabled else "⚪"
        L.append(f"{status_icon} <b>Job:</b> "
                 f"{'ENABLED' if enabled else 'DISABLED'}")
        L.append(f"  window: <code>{window_h}h</code>  "
                 f"mult: <code>{mult}</code>  "
                 f"alert: <code>±{alert_pct}%</code>")
        L.append("")

        # Quick stats from live_trades (last 24h - smaller window than the
        # nightly job, just for "what's happening right now").
        db = context.bot_data.get("db")
        if db is not None and getattr(db, "conn", None) is not None:
            from datetime import timedelta
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            try:
                async with db.conn.execute(
                    """SELECT COUNT(*) AS n,
                              COALESCE(SUM(paper_pnl), 0),
                              COALESCE(SUM(pnl), 0)
                       FROM live_trades
                       WHERE settled_at IS NOT NULL AND settled_at >= ?""",
                    (since,)
                ) as cur:
                    row = await cur.fetchone()
                n_24h = int(row[0] or 0)
                paper_24h = float(row[1] or 0.0)
                live_24h = float(row[2] or 0.0)
                exp_24h = paper_24h * mult
                drift_24h = live_24h - exp_24h
                denom = abs(exp_24h) if abs(exp_24h) > 0.01 else 0.01
                drift_pct_24h = (drift_24h / denom) * 100.0

                L.append("<b>Live snapshot (son 24h):</b>")
                L.append(f"  trades: <code>{n_24h}</code>")
                L.append(f"  paper: <code>${paper_24h:+.2f}</code>")
                L.append(f"  expected (×{mult}): "
                         f"<code>${exp_24h:+.2f}</code>")
                L.append(f"  live: <code>${live_24h:+.2f}</code>")
                if n_24h > 0:
                    L.append(f"  drift: <code>${drift_24h:+.2f}</code> "
                             f"(<code>{drift_pct_24h:+.1f}%</code>)")
                else:
                    L.append("  <i>no trades — wait for live activity</i>")
                L.append("")
            except Exception as e:  # noqa: BLE001
                L.append(f"<i>Live snapshot query failed: "
                         f"{type(e).__name__}: {e}</i>")
                L.append("")

        # Latest nightly report excerpt
        if LATEST_PATH.exists():
            try:
                content = LATEST_PATH.read_text(encoding="utf-8")
                # Strip markdown headers / bold to fit Telegram HTML;
                # take only the "Status:" + "Aggregate" sections (first ~25 lines)
                lines = content.split("\n")
                # Find "## Status:" line and grab through "## Per-Strategy"
                summary_lines = []
                in_summary = False
                for ln in lines:
                    if ln.startswith("## Status:"):
                        in_summary = True
                    if ln.startswith("## Per-Strategy"):
                        break
                    if in_summary and ln.strip():
                        # Convert markdown to HTML-friendly: ** → <b>
                        ln = ln.replace("**", "")
                        summary_lines.append(_esc(ln))
                if summary_lines:
                    L.append("<b>Son nightly rapor:</b>")
                    L.append("<pre>" + "\n".join(summary_lines[:15]) + "</pre>")
                    L.append("")

                # File age
                age_s = time.time() - LATEST_PATH.stat().st_mtime
                age_h = age_s / 3600
                L.append(f"<i>Rapor yaşı: {age_h:.1f}h</i>")
                L.append("")
            except OSError as e:
                L.append(f"<i>Latest rapor okunamadı: "
                         f"{type(e).__name__}: {e}</i>")
        else:
            L.append("<i>Henüz nightly rapor üretilmedi. "
                     "İlk çalışma bot başlatıldıktan 5 dk sonra. "
                     "Manuel tetikleme için bot restart.</i>")
            L.append("")

        L.append("<i>Detay: <code>data_store/audits/reality_gap_latest.md</code></i>")
        L.append("<i>Eşik aşıldığında otomatik Telegram alarm gelir.</i>")

        await update.message.reply_text("\n".join(L), parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        logger.exception(f"/reality_gap failed: {e}")
        await update.message.reply_text(
            f"Reality Gap panel uretilemedi: {type(e).__name__}: {e}")
