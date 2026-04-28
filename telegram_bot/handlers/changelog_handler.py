"""
PolyPaper Bot — /changelog Handler (Phase 82e Sprint A)
========================================================
Shows strategy_changelog table entries with source filtering and action-aware
formatting. Confirms to user that AI Brain /analyze approvals actually execute
real DB mutations.

Usage:
  /changelog                → last 20 entries, all sources
  /changelog ai             → last 20 entries, ai_brain only
  /changelog adaptive       → last 20 entries, adaptive_optimizer only
  /changelog user           → last 20 entries, user_telegram only
  /changelog all 50         → last 50 entries, all sources
  /changelog ai 10          → last 10 entries, ai_brain only

ADMIN ONLY — shows full action history including STOP/DELETE events.

ENV:
  CHANGELOG_DEFAULT_LIMIT=20  (default rows)
  CHANGELOG_MAX_LIMIT=100     (hard cap to prevent oversized messages)
"""
import json
import logging
import os
from telegram import Update
from telegram.ext import ContextTypes
from config.settings import Settings
from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.handlers.changelog")

# ── ENV-tunable limits ────────────────────────────────────────────────
CHANGELOG_DEFAULT_LIMIT = int(os.getenv("CHANGELOG_DEFAULT_LIMIT", "20"))
CHANGELOG_MAX_LIMIT = int(os.getenv("CHANGELOG_MAX_LIMIT", "100"))

# Source alias → DB source value
_SOURCE_ALIASES = {
    "ai": "ai_brain",
    "ai_brain": "ai_brain",
    "brain": "ai_brain",
    "adaptive": "adaptive_optimizer",
    "adaptive_optimizer": "adaptive_optimizer",
    "user": "user_telegram",
    "user_telegram": "user_telegram",
    "telegram": "user_telegram",
    # Hyperopt aliases removed 2026-04-28 (Heddas direktifi)
    "all": None,   # no filter
    "*": None,
}

# Action → emoji prefix
_ACTION_EMOJI = {
    "TUNE": "🎛",
    "SCALE": "📈",
    "STOP": "🛑",
    "DELETE": "🗑",
    "CREATE": "🆕",
    "RESTART": "🔄",
    "ADAPTIVE_THRESHOLD": "⚙️",
    "ROLLING_WR_KILL": "❌",
    "ADAPTIVE_DEAD": "💀",
    "LIFECYCLE_ADJUST": "🧬",
    # OPTIMIZE / APPLY_HYPEROPT removed 2026-04-28 (Heddas direktifi)
}


def _is_admin(context, telegram_id: int) -> bool:
    """Phase 54+: never fallback to True."""
    settings: Settings = context.bot_data.get("settings")
    if not settings:
        logger.warning(f"⚠️ _is_admin: settings missing, denying user {telegram_id}")
        return False
    return settings.is_admin(telegram_id)


def _parse_args(args: list) -> tuple[str | None, int]:
    """Parse [source] [limit] from command args.

    Returns: (source_db_value_or_None, limit)
    """
    source = None
    limit = CHANGELOG_DEFAULT_LIMIT

    for arg in args:
        arg_lower = arg.lower().strip()
        if arg_lower.isdigit():
            try:
                limit = min(int(arg_lower), CHANGELOG_MAX_LIMIT)
                limit = max(1, limit)
            except ValueError:
                pass
        elif arg_lower in _SOURCE_ALIASES:
            source = _SOURCE_ALIASES[arg_lower]
    return source, limit


def _format_change_compact(old_v: str, new_v: str) -> str:
    """Decode old→new JSON into 'key:old→new, key:old→new'."""
    if not (old_v and new_v):
        return ""
    try:
        old_d = json.loads(old_v) if isinstance(old_v, str) else {}
        new_d = json.loads(new_v) if isinstance(new_v, str) else {}
        changes = []
        for k in set(list(old_d.keys()) + list(new_d.keys())):
            o = old_d.get(k)
            n = new_d.get(k)
            if o != n:
                # Round floats for compact display
                if isinstance(o, float):
                    o = round(o, 4)
                if isinstance(n, float):
                    n = round(n, 4)
                changes.append(f"{k}:{o}→{n}")
        return ", ".join(changes)
    except (json.JSONDecodeError, AttributeError, TypeError):
        # T11.8-B (2026-04-24): narrow from bare Exception. JSON parse of
        # stored old/new snapshots. AttributeError on non-dict .keys() path.
        # Empty string keeps the row rendering compact.
        return ""


def _format_row(row: tuple) -> str:
    """Format one changelog row as HTML-safe Telegram message line."""
    (label, action, source, old_v, new_v, reason,
     wr, pnl, trades, ts) = row

    label = label or "?"
    action = action or "?"
    source = source or "?"
    reason = reason or ""
    ts_short = (ts or "")[:16].replace("T", " ")

    emoji = _ACTION_EMOJI.get(action, "•")
    change_str = _format_change_compact(old_v or "", new_v or "")

    # Context: WR/PnL/trades at time of change
    ctx_parts = []
    if wr is not None:
        ctx_parts.append(f"WR={wr:.0f}%")
    if pnl is not None:
        ctx_parts.append(f"PnL={pnl:+.2f}")
    if trades is not None and trades > 0:
        ctx_parts.append(f"t={trades}")
    ctx = f" ({' '.join(ctx_parts)})" if ctx_parts else ""

    # Source tag shortening for readability
    src_short = {
        "ai_brain": "AI",
        "adaptive_optimizer": "ADPT",
        "user_telegram": "USR",
        # "hyperopt": "HO" removed 2026-04-28 (Heddas direktifi)
    }.get(source, source[:8])

    line = f"{emoji} <b>{esc(action)}</b> [{esc(src_short)}] <code>{esc(label)}</code>"
    if change_str:
        line += f"\n   <i>{esc(change_str)}</i>"
    if ctx:
        line += f"\n   {esc(ctx.strip())}"
    if reason:
        reason_trim = reason[:110] + "…" if len(reason) > 110 else reason
        line += f"\n   💬 {esc(reason_trim)}"
    line += f"\n   <code>{esc(ts_short)}</code>"
    return line


async def changelog_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/changelog [source] [limit] — show recent strategy_changelog entries.

    ADMIN ONLY. Reads directly from strategy_changelog table.
    """
    if not _is_admin(context, update.effective_user.id):
        return await update.message.reply_text("⛔ Sadece admin komutu.")

    engine = context.bot_data.get("engine")
    if not engine or not getattr(engine, "db", None):
        return await update.message.reply_text("Engine/DB hazir degil.")

    source, limit = _parse_args(context.args or [])

    # Build query
    if source:
        sql = """SELECT strategy_label, action, source,
                        old_value, new_value, reason,
                        wr_at_time, pnl_at_time, trades_at_time, created_at
                 FROM strategy_changelog
                 WHERE source = ?
                 ORDER BY created_at DESC
                 LIMIT ?"""
        params = (source, limit)
    else:
        sql = """SELECT strategy_label, action, source,
                        old_value, new_value, reason,
                        wr_at_time, pnl_at_time, trades_at_time, created_at
                 FROM strategy_changelog
                 ORDER BY created_at DESC
                 LIMIT ?"""
        params = (limit,)

    try:
        rows = await engine.db.conn.execute_fetchall(sql, params)
    except Exception as e:  # noqa: BLE001
        logger.error(f"changelog query failed: {e}")
        # T11.6-OK reason=/changelog admin-only, DB SQL hatasi operator icin
        # gerekli (table missing vs syntax vs lock). Truncated.
        return await update.message.reply_text(  # noqa: T11.6-OK
            f"⚠️ Changelog sorgusu basarisiz: {esc(str(e)[:100])}",
            parse_mode="HTML")

    if not rows:
        src_label = source or "all"
        return await update.message.reply_text(
            f"📋 <b>Strategy Changelog</b>\n\n"
            f"Kayit yok (source=<code>{esc(src_label)}</code>, limit={limit}).",
            parse_mode="HTML")

    # Header
    src_label = source or "all"
    header = (
        f"📋 <b>Strategy Changelog</b>\n"
        f"Source: <code>{esc(src_label)}</code> | Rows: {len(rows)}/{limit}\n"
        f"{'─' * 24}\n\n"
    )

    # Format rows
    formatted_lines = [_format_row(r) for r in rows]

    # Chunk into ≤ 3900-char messages (4096 Telegram limit, leave headroom)
    MAX_LEN = 3900
    chunks = []
    cur = header
    for line in formatted_lines:
        block = line + "\n\n"
        if len(cur) + len(block) > MAX_LEN:
            chunks.append(cur.rstrip())
            cur = block
        else:
            cur += block
    if cur.strip():
        chunks.append(cur.rstrip())

    # Send chunks
    for i, chunk in enumerate(chunks):
        footer = ""
        if len(chunks) > 1:
            footer = f"\n\n<i>[{i+1}/{len(chunks)}]</i>"
        try:
            await update.message.reply_text(
                chunk + footer,
                parse_mode="HTML",
                disable_web_page_preview=True)
        except Exception as e:  # noqa: BLE001
            # T11.8-B (2026-04-24): chunk send fallback intentionally wide.
            # HTML parse error (BadRequest) is the common case; falls back
            # to plain-text send. Admin-only changelog so truncated exc str
            # is acceptable for operator diagnosis.
            logger.error(f"changelog send chunk {i+1}/{len(chunks)} failed: {e}")
            # Fallback: no HTML
            await update.message.reply_text(
                f"Chunk {i+1} HTML hatasi, raw: {str(e)[:100]}")
