"""
PolyPaper Bot - Reference Price Audit Handler (P0-07-f, 2026-05-09)
====================================================================
Telegram /ref_audit (alias /ra) — son 7 gunluk reference price audit ozeti.

Heddas direktifi P0-07: bot'un local Binance/Chainlink feed'inin Polymarket
resolution kaynagi (Chainlink BTC/USD data stream — 2026-05-19 dogrulandi)
ile sapma derecesini canli takip et. >5 bps sistemik sapma -> edge alarmi.

Panel icerigi:
  - Son 7 gun audit row sayisi + data quality breakdown
  - Per (asset, tf) bias status (Y / Y / R)
  - Worst 3 deviation ornekleri
  - Sistemik bias alarmi (eger varsa)

Yeni satir uretimi /settle aninda live hook (P0-07-b) ile otomatik yapilir;
backfill icin `py -3.11 scripts/audit_reference_price.py --all --days 7`.
"""

from __future__ import annotations

import logging
import statistics
import time
from datetime import UTC, datetime
from html import escape as _esc

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.telegram.ref_audit")


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


async def ref_audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ref_audit (alias /ra) — last 7 days reference price audit summary."""
    db = context.bot_data.get("db")
    if db is None or db.conn is None:
        await update.message.reply_text("DB baglantisi yok.")
        return

    try:
        cutoff_ms = int((time.time() - 7 * 86400) * 1000)

        # Total + per-quality counts
        async with db.conn.execute(
            """SELECT data_quality, COUNT(*)
               FROM reference_price_audit
               WHERE settle_ts_ms >= ?
               GROUP BY data_quality""",
            (cutoff_ms,),
        ) as cur:
            quality_counts = {q: n for q, n in await cur.fetchall()}

        total = sum(quality_counts.values())

        if total == 0:
            await update.message.reply_text(
                "<b>Reference Price Audit</b>\n\n"
                "Son 7 gun icinde audit kaydi yok. Bot henuz settle uretmemis "
                "olabilir; veya settle hook kapali "
                "(<code>REFERENCE_PRICE_AUDIT_ENABLED=false</code>).\n\n"
                "Backfill: <code>py -3.11 scripts/audit_reference_price.py "
                "--all --days 7</code>",
                parse_mode="HTML",
            )
            return

        # Per (asset, tf) bias from rows where data_quality='ok'
        async with db.conn.execute(
            """SELECT asset, timeframe, dev_binance_bps, dev_chainlink_bps
               FROM reference_price_audit
               WHERE settle_ts_ms >= ? AND data_quality = 'ok'""",
            (cutoff_ms,),
        ) as cur:
            ok_rows = await cur.fetchall()

        groups: dict[tuple[str, str, str], list[float]] = {}
        for asset, tf, dev_b, dev_cl in ok_rows:
            for src, val in (("binance", dev_b), ("chainlink", dev_cl)):
                if val is None:
                    continue
                groups.setdefault((asset or "?", tf or "?", src), []).append(val)

        # Worst 3 deviations (by abs value)
        async with db.conn.execute(
            """SELECT settle_ts_ms, asset, timeframe, slug,
                      dev_binance_bps, dev_chainlink_bps,
                      bot_binance_ws_price, bot_chainlink_price,
                      official_resolution_price
               FROM reference_price_audit
               WHERE settle_ts_ms >= ? AND data_quality = 'ok'""",
            (cutoff_ms,),
        ) as cur:
            audit_rows = await cur.fetchall()

        worst_candidates: list[tuple[float, tuple]] = []
        for r in audit_rows:
            ts, a, tf, slug, dev_b, dev_cl, ws_p, cl_p, off = r
            for src, val, local in (("binance", dev_b, ws_p), ("chainlink", dev_cl, cl_p)):
                if val is not None:
                    worst_candidates.append((abs(val), (ts, a, tf, slug, src, val, local, off)))
        worst_candidates.sort(key=lambda x: x[0], reverse=True)
        top3 = worst_candidates[:3]

        # ── Format the panel ────────────────────────────────────────
        lines: list[str] = []
        lines.append("<b>Reference Price Audit</b> (son 7 gun)")
        lines.append("")
        lines.append(f"Toplam: <b>{total}</b> settle audit")
        lines.append("")
        lines.append("<b>Data Quality:</b>")
        for q, n in sorted(quality_counts.items()):
            pct = 100 * n / total if total else 0
            emoji = "OK" if q == "ok" else ("WAIT" if q == "missing_resolution" else "MISS")
            lines.append(f"  [{emoji}] <code>{_esc(q)}</code>: {n} ({pct:.0f}%)")
        lines.append("")

        if not groups:
            lines.append(
                "<i>Hicbir satirda full data quality yok. "
                "<code>--fetch-references</code> calistir.</i>"
            )
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        # Bias table
        lines.append("<b>Bias (asset/tf/src):</b>")
        alarms: list[str] = []
        for key in sorted(groups.keys()):
            asset, tf, src = key
            vals = groups[key]
            mean_bps = statistics.fmean(vals)
            n = len(vals)
            abs_m = abs(mean_bps)
            if abs_m > 5:
                tag = "RED"
                alarms.append(f"({asset}/{tf}/{src}) mean={mean_bps:+.2f}bps")
            elif abs_m > 2:
                tag = "YEL"
            else:
                tag = "GRN"
            lines.append(
                f"  [{tag}] {asset}/{tf}/{src} (n={n}) " f"mean=<code>{mean_bps:+.2f}</code> bps"
            )
        lines.append("")

        # Worst 3
        if top3:
            lines.append("<b>Worst 3 deviations:</b>")
            for _abs_v, (ts, a, tf, slug, src, val, local, off) in top3:
                slug_disp = (slug or "")[:36]
                local_str = f"{local:.4f}" if local is not None else "?"
                off_str = f"{off:.4f}" if off is not None else "?"
                lines.append(
                    f"  <code>{val:+7.2f}bps</code> {a}/{tf}/{src}  "
                    f"local=<code>{local_str}</code> vs "
                    f"official=<code>{off_str}</code>"
                )
                lines.append(f"    {_ms_to_iso(ts)}  " f"<code>{_esc(slug_disp)}</code>")
            lines.append("")

        # Alarms
        if alarms:
            lines.append("<b>[!] EDGE ESTIMATE INVALID</b> " "(systematic bias > 5 bps):")
            for a in alarms:
                lines.append(f"  - <code>{_esc(a)}</code>")
        else:
            lines.append("<b>[OK]</b> Hicbir grupta sistemik bias > 5 bps.")

        lines.append("")
        lines.append(
            "<i>Detayli markdown rapor: "
            "<code>scripts/audit_reference_price.py --report</code></i>"
        )

        msg = "\n".join(lines)
        await update.message.reply_text(msg, parse_mode="HTML")

    except Exception as e:  # noqa: BLE001
        # T11.6 doctrine: exception details go to log only.
        logger.exception(f"/ref_audit failed: {e}")
        await update.message.reply_text("Audit panel üretilemedi. Logları kontrol edin.")
