"""
Phase 47f.10 P2#12 — Centralized Error Message Templates
=========================================================
All user-facing error strings live here (HTML parse_mode). Handlers import
`ERR` or call `fmt_error(key, **kwargs)` so wording stays consistent and
i18n can be bolted on later without touching each handler.

Usage:
    from telegram_bot.templates import ERR, fmt_error
    await update.message.reply_text(ERR["DB_UNAVAILABLE"], parse_mode="HTML")
    await update.message.reply_text(
        fmt_error("NOT_FOUND", what="Strategy", key="abc"),
        parse_mode="HTML")
"""

from __future__ import annotations

ERR: dict[str, str] = {
    # Infrastructure
    "DB_UNAVAILABLE": "❌ <b>Database unavailable.</b> Try again in a moment.",
    "ENGINE_NOT_READY": "⏳ Engine still warming up — try again in a few seconds.",
    "ADMIN_ONLY": "🔒 This command is admin-only.",
    # Argument errors
    "USAGE": "❓ Usage: <code>{usage}</code>",
    "MISSING_ARG": "❓ Missing argument: <code>{name}</code>",
    "BAD_NUMBER": "❌ Invalid number: <code>{val}</code>",
    # Lookup failures
    "NOT_FOUND": "❌ {what} not found: <code>{key}</code>",
    "AMBIGUOUS_PREFIX": "⚠️ Ambiguous prefix <code>{key}</code> — matches {count} items.",
    "NO_RESULTS": "📭 No results.",
    # Permission / state
    "ALREADY_RUNNING": "ℹ️ Already running.",
    "ALREADY_STOPPED": "ℹ️ Already stopped.",
    "GATE_FAIL": "⛔ <b>Gate fail:</b> {reason}",
    # Risk
    "RISK_HALTED": "🛑 <b>Risk HALTED</b> — manual resume required (/resume).",
    "RISK_DAILY_LIMIT": "⚠️ Daily loss limit reached.",
    # Network / external
    "NETWORK_ERROR": "🌐 Network error — please retry.",
    "API_ERROR": "🌐 External API error: {detail}",
    # Generic fallback
    "UNEXPECTED": "❌ Unexpected error — try again or check /health.",
}


def fmt_error(key: str, **kwargs) -> str:
    """Return a formatted error message, or UNEXPECTED if the key is unknown."""
    template = ERR.get(key, ERR["UNEXPECTED"])
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template
