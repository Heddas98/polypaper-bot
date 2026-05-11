"""
PolyPaper Bot - Reality Gap Nightly Job (P1-03-a, 2026-05-09)
==============================================================

Compares aggregate paper PnL × MULT against actual live PnL for the trailing
window. Drift = how far reality diverged from the simulator's prediction.

The 0.66 multiplier comes from `T4.6-B Fill Heuristic Sweep` (2026-04-24):
classic 199 trades × 200 markets backtest measured HEURISTIC -$4.87 vs
EMPIRICAL -$6.51 — delta_pnl_pct ~= -33.68%. So paper * 0.66 ~= live expectation.

If `|drift_pct| > REALITY_GAP_ALERT_PCT` (default 10%), an admin Telegram alert
fires. A markdown report is always written to data_store/audits/.

Env knobs:
    REALITY_GAP_ENABLED          default "true"
    REALITY_GAP_WINDOW_H         default 168 (last 7 days)
    REALITY_GAP_MULT             default 0.66 (paper -> live expectation)
    REALITY_GAP_ALERT_PCT        default 10.0 (% drift threshold)
    REALITY_GAP_MIN_TRADES       default 10 (insufficient_data otherwise)

The job is non-blocking and defensive — any failure logs warning, never crashes
the scheduler.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram.error import TelegramError
from telegram.ext import ContextTypes

from telegram_bot.jobs.shadow_report_job import resolve_admin_chat_id

logger = logging.getLogger("polypaper.reality_gap")

AUDIT_DIR = Path("data_store/audits")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, "true" if default else "false").strip().lower() in {
        "1", "true", "yes", "on"}


async def _fetch_aggregate(db, since_iso: str) -> dict:
    """SUM paper_pnl, SUM pnl, COUNT FROM live_trades."""
    async with db.conn.execute(
        """SELECT COUNT(*) AS n,
                  COALESCE(SUM(paper_pnl), 0) AS paper_sum,
                  COALESCE(SUM(pnl), 0) AS live_sum,
                  COALESCE(SUM(CASE WHEN result='won' THEN 1 ELSE 0 END), 0) AS wins
           FROM live_trades
           WHERE settled_at IS NOT NULL
             AND settled_at >= ?""",
        (since_iso,)
    ) as cur:
        row = await cur.fetchone()
    return {
        "n": int(row[0] or 0),
        "paper_sum": float(row[1] or 0.0),
        "live_sum": float(row[2] or 0.0),
        "wins": int(row[3] or 0),
    }


async def _fetch_per_strategy(db, since_iso: str, limit: int = 10) -> list[dict]:
    """Per-strategy drift breakdown for worst-N report."""
    async with db.conn.execute(
        """SELECT strategy_label,
                  COUNT(*) AS n,
                  COALESCE(SUM(paper_pnl), 0) AS paper_sum,
                  COALESCE(SUM(pnl), 0) AS live_sum
           FROM live_trades
           WHERE settled_at IS NOT NULL
             AND settled_at >= ?
             AND strategy_label IS NOT NULL
           GROUP BY strategy_label
           ORDER BY ABS(COALESCE(SUM(pnl), 0) - COALESCE(SUM(paper_pnl), 0) * ?) DESC
           LIMIT ?""",
        (since_iso, _env_float("REALITY_GAP_MULT", 0.66), limit)
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        out.append({
            "label": r[0] or "?",
            "n": int(r[1] or 0),
            "paper_sum": float(r[2] or 0.0),
            "live_sum": float(r[3] or 0.0),
        })
    return out


def _compute_drift(paper_sum: float, live_sum: float, mult: float) -> tuple:
    """Return (expected_live, drift_abs, drift_pct).

    drift_pct = (live - expected) / max(|expected|, 0.01) * 100
    Positive = live outperformed expectation; negative = underperformed.
    """
    expected = paper_sum * mult
    drift_abs = live_sum - expected
    denom = abs(expected) if abs(expected) > 0.01 else 0.01
    drift_pct = (drift_abs / denom) * 100.0
    return expected, drift_abs, drift_pct


def _classify(drift_pct: float, n: int, min_trades: int, alert_pct: float) -> str:
    if n < min_trades:
        return "insufficient_data"
    if abs(drift_pct) > alert_pct:
        return "alert"
    if abs(drift_pct) > alert_pct / 2:
        return "warn"
    return "ok"


def _format_markdown(window_h: int, mult: float, alert_pct: float, min_trades: int,
                     agg: dict, per_strategy: list[dict],
                     expected: float, drift_abs: float, drift_pct: float,
                     status: str, now: datetime) -> str:
    L: list[str] = []
    L.append("# Reality Gap Report")
    L.append("")
    L.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append(f"**Window:** last {window_h}h")
    L.append(f"**Multiplier (paper -> live):** {mult}")
    L.append(f"**Alert threshold:** ±{alert_pct}% drift  |  "
             f"**Min trades:** {min_trades}")
    L.append("")

    status_label = {
        "ok": "[OK] within tolerance",
        "warn": "[WARN] >50% of alert threshold",
        "alert": "[ALERT] drift exceeds threshold",
        "insufficient_data": "[INSUFFICIENT_DATA] not enough trades",
    }.get(status, status)
    L.append(f"## Status: {status_label}")
    L.append("")

    if agg["n"] == 0:
        L.append("> No live_trades in window. Bot may not have settled any "
                 "live trades yet (paper-only mode or fresh deployment).")
        return "\n".join(L) + "\n"

    wr = (agg["wins"] / agg["n"] * 100) if agg["n"] > 0 else 0.0
    L.append("## Aggregate")
    L.append("")
    L.append(f"- Trades: **{agg['n']}**")
    L.append(f"- Win rate: **{wr:.1f}%**")
    L.append(f"- Paper PnL sum: **${agg['paper_sum']:+.2f}**")
    L.append(f"- Expected live (paper * {mult}): **${expected:+.2f}**")
    L.append(f"- Actual live PnL sum: **${agg['live_sum']:+.2f}**")
    L.append(f"- Drift (actual - expected): **${drift_abs:+.2f}**")
    L.append(f"- Drift %: **{drift_pct:+.1f}%**")
    L.append("")

    if per_strategy:
        L.append("## Per-Strategy Drift (top 10 by |drift|)")
        L.append("")
        L.append("| Strategy | N | Paper | Expected | Live | Drift $ | Drift %% |")
        L.append("|---|--:|--:|--:|--:|--:|--:|")
        for r in per_strategy:
            exp_r = r["paper_sum"] * mult
            d_abs = r["live_sum"] - exp_r
            denom = abs(exp_r) if abs(exp_r) > 0.01 else 0.01
            d_pct = d_abs / denom * 100.0
            L.append(
                f"| `{(r['label'] or '?')[:30]}` | {r['n']} | "
                f"${r['paper_sum']:+.2f} | ${exp_r:+.2f} | "
                f"${r['live_sum']:+.2f} | ${d_abs:+.2f} | {d_pct:+.1f}% |"
            )
        L.append("")

    L.append("## Interpretation")
    L.append("")
    if status == "alert":
        L.append(f"> 🚨 **Drift exceeds ±{alert_pct}% threshold.** Paper "
                 f"simulator does not match live execution. Investigate: "
                 f"fill heuristic recalibration (T4.6 family), fee model "
                 f"drift, slippage estimation, or fundamental edge erosion.")
    elif status == "warn":
        L.append(f"> ⚠️ **Drift >{alert_pct/2}% but <{alert_pct}%.** "
                 f"Monitor — not yet actionable but trend bears watching.")
    elif status == "insufficient_data":
        L.append(f"> ℹ️ Need ≥{min_trades} live trades in window for "
                 f"meaningful drift. Currently {agg['n']}. Wait for more "
                 f"data or expand window via REALITY_GAP_WINDOW_H.")
    else:
        L.append("> ✅ Paper-vs-live drift within tolerance. Simulator "
                 "predictions match actual execution to within "
                 f"±{alert_pct}%.")

    return "\n".join(L) + "\n"


async def reality_gap_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Nightly reality gap check. Wired in bot.py via JobQueue.run_daily."""
    if not _env_bool("REALITY_GAP_ENABLED", True):
        logger.info("[reality_gap] disabled via REALITY_GAP_ENABLED=false")
        return

    db = context.application.bot_data.get("db")
    if db is None or getattr(db, "conn", None) is None:
        logger.warning("[reality_gap] db unavailable — skip")
        return

    try:
        window_h = _env_int("REALITY_GAP_WINDOW_H", 168)
        mult = _env_float("REALITY_GAP_MULT", 0.66)
        alert_pct = _env_float("REALITY_GAP_ALERT_PCT", 10.0)
        min_trades = _env_int("REALITY_GAP_MIN_TRADES", 10)

        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=window_h)
        since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        agg = await _fetch_aggregate(db, since_iso)
        per_strategy = await _fetch_per_strategy(db, since_iso, limit=10)

        expected, drift_abs, drift_pct = _compute_drift(
            agg["paper_sum"], agg["live_sum"], mult)
        status = _classify(drift_pct, agg["n"], min_trades, alert_pct)

        md = _format_markdown(window_h, mult, alert_pct, min_trades,
                              agg, per_strategy,
                              expected, drift_abs, drift_pct, status, now)

        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        out = AUDIT_DIR / f"reality_gap_{now.strftime('%Y%m%dT%H%M%SZ')}.md"
        out.write_text(md, encoding="utf-8")
        # Also keep a stable "latest" symlink-equivalent (regular copy)
        latest = AUDIT_DIR / "reality_gap_latest.md"
        latest.write_text(md, encoding="utf-8")

        logger.info(
            f"[reality_gap] n={agg['n']} drift={drift_pct:+.1f}% "
            f"status={status} -> {out.name}")

        # Telegram alert on alert/warn status (and on first INSUFFICIENT_DATA
        # only — but for now keep INSUFFICIENT_DATA quiet to avoid noise)
        if status in ("alert", "warn"):
            admin_id = resolve_admin_chat_id()
            if admin_id:
                emoji = "🚨" if status == "alert" else "⚠️"
                try:
                    text = (
                        f"{emoji} <b>Reality Gap {status.upper()}</b>\n"
                        f"window: <code>{window_h}h</code>  "
                        f"trades: <code>{agg['n']}</code>\n"
                        f"paper: <code>${agg['paper_sum']:+.2f}</code>\n"
                        f"expected (×{mult}): "
                        f"<code>${expected:+.2f}</code>\n"
                        f"actual:  <code>${agg['live_sum']:+.2f}</code>\n"
                        f"drift:   <code>${drift_abs:+.2f}</code> "
                        f"(<code>{drift_pct:+.1f}%</code>)\n"
                        f"\n<i>Detail: <code>{out.name}</code></i>"
                    )
                    await context.bot.send_message(
                        chat_id=admin_id, text=text, parse_mode="HTML")
                except (TelegramError, asyncio.TimeoutError) as e:
                    logger.warning(
                        f"[reality_gap] notify failed: "
                        f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        # Outermost wrapper intentionally wide — DB/disk/Telegram surfaces
        # all possible. Job-safety exemption: scheduler stays alive.
        logger.exception(f"[reality_gap] failed: {e}")
