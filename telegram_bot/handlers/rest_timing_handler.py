"""T4.8 — `/dump_rest_timing` admin command.

Exposes `core.observability.rest_timing.get_summary()` as a Telegram
HTML table. Enables the T4.7 empirical RTT calibration workflow:

  1. Operator enables: `/envt REST_TIMING_TELEMETRY true` (or .env + restart)
  2. Bot collects samples for N hours (default 24h target)
  3. Operator runs `/dump_rest_timing` → HTML table (p10/p50/p90/p99)
  4. Operator copies values into `config/settings.py` REST_LATENCY_MS /
     JITTER_MS defaults OR `.env` overrides

Optional: `/dump_rest_timing save` also writes JSON to
  `data_store/rest_timing_<TS>.json`
for offline analysis (preserves full buffer, not just percentiles).

Admin only. No state mutation — pure read.

ENV toggles the module respects:
  REST_TIMING_TELEMETRY=true  — required for sampling
  REST_TIMING_BUFFER_SIZE     — samples per label (default 10000)
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from core.observability import rest_timing
from telegram_bot.handlers._exc_render import render_user_exception

logger = logging.getLogger("polypaper.handlers.rest_timing")

ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
DUMP_DIR = Path("data_store")


async def dump_rest_timing_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler for `/dump_rest_timing [save]`.

    Admin-only. Returns aggregated REST RTT statistics.
    """
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Sadece admin komutu.")
        return

    # Check telemetry enabled
    if not rest_timing.enabled():
        await update.message.reply_text(
            "⚠️ <b>REST timing telemetry KAPALI.</b>\n\n"
            "Açmak için:\n"
            "<code>/envt REST_TIMING_TELEMETRY true</code>\n"
            "(veya .env'e ekle + bot restart).\n\n"
            "Açtıktan sonra en az 1-24 saat trafikle beslenmesini bekle, "
            "sonra tekrar <code>/dump_rest_timing</code> at.",
            parse_mode="HTML",
        )
        return

    try:
        summary = rest_timing.get_summary()
    except Exception as e:  # noqa: BLE001
        logger.exception("rest_timing summary failed")
        await update.message.reply_text(
            render_user_exception(e, "❌ Telemetry summary"),
            parse_mode="HTML",
        )
        return

    if not summary:
        await update.message.reply_text(
            "ℹ️ Telemetry açık ama henüz sample yok. "
            "Scanner cycle'ının ilk HTTP çağrısını bekle (yaklaşık 15-30s).",
            parse_mode="HTML",
        )
        return

    # Build HTML table
    lines = [
        "<b>📊 REST Timing Summary</b>",
        f"<i>Buffer: {rest_timing._BUFFER_SIZE}/label · Labels: " f"{len(summary)}</i>",
        "",
        "<pre>",
        f"{'Label':<25} {'n':>5} {'p50':>6} {'p90':>6} {'p99':>6} {'mean':>6}",
        "-" * 65,
    ]
    for label, stats in summary.items():
        lines.append(
            f"{label[:25]:<25} {stats['n']:>5} {stats['p50']:>6.0f} "
            f"{stats['p90']:>6.0f} {stats['p99']:>6.0f} {stats['mean']:>6.0f}"
        )
    lines.append("</pre>")
    lines.append("<i>Birim: milisaniye (ms)</i>")

    # Optional JSON dump to disk
    args = context.args or []
    save_requested = any(a.lower() in ("save", "dump", "json") for a in args)
    if save_requested:
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        dest = DUMP_DIR / f"rest_timing_{ts}.json"
        try:
            DUMP_DIR.mkdir(parents=True, exist_ok=True)
            ok = rest_timing.dump_to_file(str(dest))
            if ok:
                lines.append("")
                lines.append(f"💾 <code>{dest}</code> yazıldı.")
            else:
                lines.append("")
                lines.append("⚠️ JSON dump başarısız (bkz. log).")
        except OSError as e:
            logger.exception("rest_timing dump_to_file OS error")
            lines.append("")
            lines.append(render_user_exception(e, "⚠️ JSON dump I/O"))

    msg = "\n".join(lines)
    # Telegram 4096 char limit — truncate body if very long
    if len(msg) > 4000:
        msg = msg[:3950] + "\n...\n(truncated)"
    await update.message.reply_text(msg, parse_mode="HTML")
