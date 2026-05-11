"""
PolyPaper Bot - Reconciliation Status Panel (P1-09-c, 2026-05-09)
==================================================================
Telegram /recon (alias /rc) - on-chain reconciliation loop status.

Heddas direktifi P1-09: bot DB pUSD bakiyesi vs Polygon on-chain pUSD
sapmasi (revert / exploit / drift) icin 5dk loop. Bu komut:
  - Loop calisma durumu
  - Son tick yasi
  - Mismatch sayisi (running session)
  - Mismatch gecmisi (top 5)

Loop kontrol:
  - Otomatik aktif: LIVE_ENABLED=true (mainnet shadow)
  - Explicit override: RECON_ENABLED=true|false
  - Threshold: RECON_MISMATCH_THRESHOLD_USD (default $1)
  - Interval:  RECON_INTERVAL_S (default 300s = 5dk)
"""
from __future__ import annotations

import logging
import time
from html import escape as _esc

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.telegram.recon")


def _fmt_age(secs):
    if secs is None:
        return "never"
    if secs < 60:
        return f"{int(secs)}s"
    if secs < 3600:
        return f"{int(secs/60)}m {int(secs%60)}s"
    return f"{int(secs/3600)}h {int((secs%3600)/60)}m"


async def recon_command(update: Update,
                         context: ContextTypes.DEFAULT_TYPE):
    """/recon (alias /rc) - reconciliation loop status panel."""
    engine = context.bot_data.get("engine")
    if engine is None:
        await update.message.reply_text("Engine baglantisi yok.")
        return

    task = getattr(engine, "recon_task", None)
    if task is None:
        await update.message.reply_text(
            "<b>Reconciliation</b>\n\n"
            "Task hic baslatilmamis (engine wire hatasi). "
            "Bot restart denenebilir; "
            "<code>core/engine.py</code> reconciliation wire "
            "kontrol edilmeli.",
            parse_mode="HTML"
        )
        return

    try:
        st = task.stats
        L = []
        L.append("<b>Reconciliation</b> (DB vs on-chain)")
        L.append("")

        # Status block
        status_emoji = "🟢" if st.get("running") else (
            "🟡" if st.get("enabled") else "⚪")
        if not st.get("enabled"):
            status_text = ("DISABLED — RECON_ENABLED unset/false AND "
                           "LIVE_ENABLED=false")
        elif st.get("running"):
            status_text = "RUNNING"
        else:
            status_text = "ENABLED but not running (start error?)"

        L.append(f"{status_emoji} <b>Status:</b> {_esc(status_text)}")
        L.append(f"  wallet: <code>{_esc(st.get('wallet') or '?')}</code>")
        L.append(f"  interval: <code>{st.get('interval_s')}s</code>")
        L.append(f"  threshold: <code>${st.get('threshold_usd'):.2f}</code>")

        last_age = st.get("last_check_age_s")
        if last_age is None:
            L.append("  last check: <i>never</i>")
        else:
            L.append(f"  last check: <code>{_fmt_age(last_age)} ago</code>")
        L.append("")

        # Mismatch summary
        mm = st.get("mismatch_count", 0)
        if mm == 0:
            L.append("✅ <b>No mismatches</b> (this session)")
        else:
            L.append(f"⚠️ <b>{mm} mismatch{'es' if mm != 1 else ''}</b> "
                     "(this session)")

            # Pull last 5 from task._mismatches if accessible
            history = getattr(task, "_mismatches", []) or []
            if history:
                L.append("")
                L.append("Son 5 olay:")
                for m in history[-5:]:
                    ts = m.get("ts", "?")
                    delta = m.get("delta_usd", 0)
                    onchain = m.get("onchain_pusd", "?")
                    db_val = m.get("db_pusd", "?")
                    L.append(
                        f"  <code>{_esc(str(ts))}</code> "
                        f"on={onchain} db={db_val} "
                        f"Δ=<code>{delta:+.4f}</code>"
                    )

        L.append("")
        L.append("<i>Sapma > $1 olursa Telegram'a alarm gelir + audit log "
                 "yazilir. Bot restart sonra yeni cycle baslar.</i>")
        L.append("")
        L.append("<i>Manuel kontrol: ENV "
                 "<code>RECON_ENABLED=true|false</code> "
                 "(LIVE_ENABLED=true ise zaten otomatik on).</i>")

        await update.message.reply_text("\n".join(L), parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        logger.exception(f"/recon failed: {e}")
        await update.message.reply_text(
            f"Recon panel uretilemedi: {type(e).__name__}: {e}")
