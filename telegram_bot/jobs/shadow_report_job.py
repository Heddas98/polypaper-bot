"""
Phase 47f.7+ Shadow Monitor — In-Bot Periodic Report
=====================================================
Replaces scripts/shadow_monitor_47f7.py + scheduled task.
Runs inside the bot's own asyncio loop using the bot's healthy aiosqlite
connection (no WSL mount / WAL race issues).

Wired in bot.py via JobQueue.run_repeating(). Pushes A/B report to ADMIN
chat every 30 min, with quiet-hours throttling and promotion-gate check.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import aiosqlite
from telegram.error import TelegramError

ADMIN_CHAT_FILE = Path(__file__).resolve().parents[2] / "data_store" / "admin_chat.json"


def _load_admin_chat_id_from_file() -> Optional[str]:
    try:
        if ADMIN_CHAT_FILE.exists():
            data = json.loads(ADMIN_CHAT_FILE.read_text(encoding="utf-8"))
            cid = data.get("chat_id")
            if cid and str(cid) != "0":
                return str(cid)
    except (OSError, json.JSONDecodeError, AttributeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. Path.read_text
        # OSError (missing/permission) + JSON parse + .get on non-dict.
        # Silent swallow correct — this is a fallback loader; ENV is primary.
        pass
    return None


def save_admin_chat_id(chat_id: int | str) -> None:
    """Persist admin chat id discovered at runtime so JobQueue auto-runs work."""
    try:
        ADMIN_CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
        ADMIN_CHAT_FILE.write_text(json.dumps({"chat_id": str(chat_id)}), encoding="utf-8")
    except OSError as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. Path.write_text
        # + mkdir surface OSError (PermissionError, disk full). Persistence
        # failure is non-fatal — ENV remains the primary source.
        logger.warning(
            f"[shadow_report] could not persist admin chat id: " f"{type(e).__name__}: {e}"
        )


def resolve_admin_chat_id() -> Optional[str]:
    """Env vars first; ignore literal '0'; then fall back to persisted file."""
    for var in ("ADMIN_TELEGRAM_ID", "ADMIN_CHAT_ID", "TELEGRAM_ADMIN_CHAT_ID"):
        v = os.getenv(var)
        if v and v.strip() and v.strip() != "0":
            return v.strip()
    return _load_admin_chat_id_from_file()


from telegram.ext import ContextTypes

logger = logging.getLogger("polypaper.shadow_report")

# Strategy types we report on. Phase 82c Task #16: env-driven + safe default.
# Previous hardcoded ["late_convergence"] always mismatched current DB strats
# (fusion/momentum/etc), spamming admin with mismatch warnings every 30min.
# Now: if SHADOW_WATCHED_TYPES is unset/empty, fall back to ALL DB types silently.
_raw_watched = (os.getenv("SHADOW_WATCHED_TYPES") or "").strip()
WATCHED_STRATEGY_TYPES = (
    [t.strip() for t in _raw_watched.split(",") if t.strip()] if _raw_watched else []
)

# Promotion gate thresholds
PROMOTION_MIN_TRADES = 50
PROMOTION_WR_DELTA_MIN = 0.0  # percentage points
PROMOTION_PNL_DELTA_MIN = 0.0  # USD


def _bucket_stats(rows: list) -> dict:
    if not rows:
        return {"trades": 0, "wins": 0, "wr": 0.0, "pnl": 0.0, "avg_pnl": 0.0}
    trades = len(rows)
    wins = sum(1 for r in rows if (r["pnl"] or 0) > 0)
    pnl = sum((r["pnl"] or 0) for r in rows)
    return {
        "trades": trades,
        "wins": wins,
        "wr": (wins / trades * 100.0) if trades else 0.0,
        "pnl": pnl,
        "avg_pnl": pnl / trades if trades else 0.0,
    }


async def _query_strategy_ab(db, strategy_type: str, cutoff: datetime) -> dict:
    """Return {before:{}, after:{}} A/B stats. Uses bot's aiosqlite conn."""
    cur = await db.conn.execute(
        "SELECT id FROM strategies WHERE strategy_type = ?",
        (strategy_type,),
    )
    rows = await cur.fetchall()
    sids = [r["id"] for r in rows]
    if not sids:
        return {"strategy_ids": [], "before": _bucket_stats([]), "after": _bucket_stats([])}

    placeholders = ",".join("?" for _ in sids)
    q = (
        f"SELECT created_at, status, pnl FROM executions "
        f"WHERE strategy_id IN ({placeholders}) "
        f"AND status IN ('closed', 'settled', 'won', 'lost')"
    )
    cur = await db.conn.execute(q, sids)
    all_rows = await cur.fetchall()

    before, after = [], []
    for r in all_rows:
        ts = None
        created_at_raw = r["created_at"]

        # Handle epoch (int) or ISO string
        if isinstance(created_at_raw, int | float):
            try:
                ts = datetime.fromtimestamp(created_at_raw, tz=UTC)
            except (ValueError, OSError):
                before.append(r)
                continue
        elif isinstance(created_at_raw, str):
            # ISO string: "2026-04-11T14:30:45.123Z" or "2026-04-11 14:30:45"
            ts_raw = (created_at_raw or "").replace("T", " ").split(".")[0]
            try:
                ts = datetime.strptime(ts_raw[:19], "%Y-%m-%d %H:%M:%S")
                ts = ts.replace(tzinfo=UTC)
            except ValueError:
                before.append(r)
                continue
        else:
            # Unknown type, assume old
            before.append(r)
            continue

        (before if ts < cutoff else after).append(r)

    return {
        "strategy_ids": sids,
        "before": _bucket_stats(before),
        "after": _bucket_stats(after),
    }


def _format_report(strategy_type: str, cutoff: datetime, ab: dict, decision_mode: str) -> str:
    b = ab["before"]
    a = ab["after"]
    lines = [
        f"<b>📊 Shadow Monitor — {strategy_type}</b>",
        f"<i>cutoff: {cutoff.strftime('%Y-%m-%d %H:%M')} UTC</i>",
        f"<i>becker_mode: <code>{decision_mode}</code></i>",
        "",
        "<b>Before cutoff</b>",
        f"  trades=<code>{b['trades']}</code> WR=<code>{b['wr']:.1f}%</code> "
        f"PnL=<code>{b['pnl']:+.2f}</code>",
        "<b>After cutoff</b>",
        f"  trades=<code>{a['trades']}</code> WR=<code>{a['wr']:.1f}%</code> "
        f"PnL=<code>{a['pnl']:+.2f}</code>",
    ]
    if b["trades"] > 0 and a["trades"] > 0:
        d_wr = a["wr"] - b["wr"]
        d_pnl_per = a["avg_pnl"] - b["avg_pnl"]
        lines.append(
            f"<b>Δ</b> WR <code>{d_wr:+.1f}pp</code>  " f"avg/trade <code>{d_pnl_per:+.4f}</code>"
        )
    elif a["trades"] == 0:
        lines.append("<i>(no executions in 'after' bucket yet)</i>")
    lines.append("")
    lines.append("<b>Promotion gate:</b> ≥50 trades after, WR Δ ≥ 0, PnL Δ ≥ 0")
    return "\n".join(lines)


def _check_promotion_gate(ab: dict, decision_mode: str) -> Optional[str]:
    """Return promotion message if gate is met, else None."""
    if decision_mode != "boost":
        return None  # already promoted
    a = ab["after"]
    b = ab["before"]
    if a["trades"] < PROMOTION_MIN_TRADES:
        return None
    if b["trades"] == 0:
        return None
    d_wr = a["wr"] - b["wr"]
    d_pnl = a["pnl"] - b["pnl"]
    if d_wr < PROMOTION_WR_DELTA_MIN or d_pnl < PROMOTION_PNL_DELTA_MIN:
        return None
    return (
        "🟢 <b>PROMOTION GATE READY</b>\n"
        f"trades after: <code>{a['trades']}</code>\n"
        f"WR Δ: <code>+{d_wr:.1f}pp</code>\n"
        f"PnL Δ: <code>+{d_pnl:.2f}</code>\n"
        "Run <code>47f7_flip.bat</code> to activate decision-mode flip@0.01."
    )


def _is_quiet_hours() -> bool:
    """Istanbul 00-07 = quiet (skip routine pushes)."""
    try:
        # Server may not have TZ data; fall back to UTC+3 calc.
        from zoneinfo import ZoneInfo

        h = datetime.now(ZoneInfo("Europe/Istanbul")).hour
    except Exception:  # noqa: BLE001
        # T11.8-B (2026-04-24): bare Exception kept on purpose. ZoneInfo
        # raises a typed ZoneInfoNotFoundError (subclass of KeyError) when
        # tzdata package missing on Windows, plus ImportError if Python <3.9.
        # Wide catch is the "no tz data available" fallback; UTC+3 approx
        # is correct for Istanbul year-round since 2016 (no DST).
        h = (datetime.utcnow().hour + 3) % 24
    return 0 <= h < 7


async def _discover_strategy_types(db) -> list:
    """Return distinct strategy_type values present in the strategies table."""
    cur = await db.conn.execute(
        "SELECT DISTINCT COALESCE(strategy_type, 'fusion') AS stype, "
        "COUNT(*) as n FROM strategies GROUP BY stype ORDER BY n DESC"
    )
    rows = await cur.fetchall()
    return [(r["stype"], r["n"]) for r in rows]


async def shadow_report_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    force: bool = False,
    override_chat_id: Optional[int | str] = None,
) -> int:
    """JobQueue callback. Runs every N minutes.

    Returns the number of messages pushed to Telegram (so callers like the
    manual /sr command can tell the user whether anything actually went out).
    When `force=True`, quiet-hours throttling is bypassed (used for manual
    triggers).
    """
    bot_data = context.application.bot_data
    db = bot_data.get("db")
    settings = bot_data.get("settings")
    if db is None or settings is None:
        logger.warning("[shadow_report] db or settings missing — skip")
        return 0

    admin_id = str(override_chat_id) if override_chat_id else resolve_admin_chat_id()
    if not admin_id or admin_id == "0":
        logger.warning("[shadow_report] no admin chat id configured — skip")
        return 0

    decision_mode = (os.getenv("BECKER_DECISION_MODE", "boost") or "boost").lower()

    # Cutoff = 6 hours ago by default (covers ~5m strategy settlement window).
    cutoff = datetime.utcnow() - timedelta(hours=6)

    quiet = _is_quiet_hours() and not force
    pushed = 0

    # 1) Discover what strategy_types are actually in the DB.
    try:
        all_types = await _discover_strategy_types(db)
    except aiosqlite.Error as e:
        # T11.8-B (2026-04-24): narrow from bare Exception. SELECT DISTINCT
        # surfaces aiosqlite.Error (missing column after migration, locked
        # DB). Empty list downstream triggers diagnostic ping.
        logger.exception(f"[shadow_report] discover failed: " f"{type(e).__name__}: {e}")
        all_types = []

    # 2) Build the effective list: WATCHED ∩ existing, else fall back to all.
    existing = {t for t, _n in all_types}
    targets = [t for t in WATCHED_STRATEGY_TYPES if t in existing]
    fallback_used = False
    if not targets and all_types:
        targets = [t for t, _n in all_types]
        fallback_used = True

    # 3) If DB has zero strategies at all, send a diagnostic ping.
    if not all_types:
        if not quiet:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "<b>📊 Shadow Monitor — diagnostic</b>\n"
                        "<i>strategies table is empty — nothing to report.</i>"
                    ),
                    parse_mode="HTML",
                )
                pushed += 1
            except (TimeoutError, TelegramError) as e:
                # T11.8-B (2026-04-24): narrow from bare Exception.
                # Diagnostic send is best-effort; empty-DB signal is already
                # in the log.
                logger.exception(f"[shadow_report] diag send failed: " f"{type(e).__name__}: {e}")
        return pushed

    # 4) If WATCHED list missed entirely, surface that to the user.
    # Phase 82c Task #16: only warn when a NON-EMPTY WATCHED list was set
    # but none of its types exist. An empty WATCHED list is the new default —
    # fall back silently to "all types" with no spam.
    if fallback_used and WATCHED_STRATEGY_TYPES and not quiet:
        type_list = ", ".join(f"<code>{t}</code>×{n}" for t, n in all_types)
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "<b>⚠️ Shadow Monitor — WATCHED mismatch</b>\n"
                    f"WATCHED={WATCHED_STRATEGY_TYPES} not found.\n"
                    f"Reporting on all existing types instead: {type_list}"
                ),
                parse_mode="HTML",
            )
            pushed += 1
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Mismatch
            # warn is best-effort.
            logger.exception(f"[shadow_report] mismatch warn failed: " f"{type(e).__name__}: {e}")

    # 5) Phase 79 S4-06: Collect ALL A/B reports into summary table format (not 10 separate messages).
    summary_lines = [
        "<b>📊 Shadow Monitor Summary</b>",
        f"<i>cutoff: {cutoff.strftime('%Y-%m-%d %H:%M')} UTC</i>",
        f"<i>becker_mode: <code>{decision_mode}</code></i>",
        "",
        "<b>Type        | Before  | After   | WR Δ   | PnL Δ</b>",
        "─────────────────────────────────────────────────",
    ]
    promo_msgs = []

    for stype in targets:
        try:
            ab = await _query_strategy_ab(db, stype, cutoff)
        except (aiosqlite.Error, KeyError, TypeError, ValueError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. A/B query
            # surfaces aiosqlite.Error (SQL) + row access Key/Type/ValueError
            # (datetime parse, missing column). Skip this stype, continue batch.
            logger.exception(
                f"[shadow_report] query failed for {stype}: " f"{type(e).__name__}: {e}"
            )
            continue

        promo_msg = _check_promotion_gate(ab, decision_mode)
        if promo_msg:
            promo_msgs.append(promo_msg)

        if quiet and promo_msg is None:
            logger.info(f"[shadow_report] quiet-hours skip ({stype})")
            continue

        # Build summary row for this strategy type
        b = ab["before"]
        a = ab["after"]
        trades_before = f"{b['trades']}t"
        trades_after = f"{a['trades']}t"

        # Delta calculations
        wr_delta = "-"
        pnl_delta = "-"
        if b["trades"] > 0 and a["trades"] > 0:
            d_wr = a["wr"] - b["wr"]
            d_pnl = a["pnl"] - b["pnl"]
            wr_delta = f"{d_wr:+.1f}pp"
            pnl_delta = f"{d_pnl:+.2f}"

        # Format row (left-aligned type, values right-aligned in columns)
        row = (
            f"{stype:<11} | {trades_before:>6} | {trades_after:>6} | {wr_delta:>6} | {pnl_delta:>7}"
        )
        summary_lines.append(row)

    summary_lines.append("")
    summary_lines.append("<b>Promotion gate:</b> ≥50 trades after, WR Δ ≥ 0, PnL Δ ≥ 0")

    # Combine summary and send (split into max 2 messages if needed)
    summary_html = "\n".join(summary_lines)

    # Split logic: if > 4096 chars, create 2 messages
    if len(summary_html) <= 4096:
        # Send as single message
        try:
            await context.bot.send_message(chat_id=admin_id, text=summary_html, parse_mode="HTML")
            pushed += 1
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Main summary
            # send; transport/BadRequest only. Pushed count already reflects.
            logger.exception(f"[shadow_report] tg send failed: " f"{type(e).__name__}: {e}")
    else:
        # Split into 2 messages: summary table + footer
        parts = summary_html.split("\n")
        mid = len(parts) // 2
        msg1 = "\n".join(parts[:mid])
        msg2 = "\n".join(parts[mid:])

        try:
            await context.bot.send_message(chat_id=admin_id, text=msg1, parse_mode="HTML")
            pushed += 1
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Part 1 of
            # split summary; same error surface as single-msg path.
            logger.exception(
                f"[shadow_report] tg send (part 1) failed: " f"{type(e).__name__}: {e}"
            )

        try:
            await context.bot.send_message(chat_id=admin_id, text=msg2, parse_mode="HTML")
            pushed += 1
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Part 2
            # counterpart.
            logger.exception(
                f"[shadow_report] tg send (part 2) failed: " f"{type(e).__name__}: {e}"
            )

    # Send promotion messages (separate, as they're actionable alerts)
    for promo_msg in promo_msgs:
        try:
            await context.bot.send_message(chat_id=admin_id, text=promo_msg, parse_mode="HTML")
            pushed += 1
        except (TimeoutError, TelegramError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. Promotion
            # alerts are actionable; log and continue iterating.
            logger.exception(f"[shadow_report] promo send failed: " f"{type(e).__name__}: {e}")

    if pushed:
        logger.info(f"[shadow_report] {pushed} message(s) pushed to telegram")
    else:
        logger.info("[shadow_report] no messages pushed (quiet hours or no data)")
    return pushed
