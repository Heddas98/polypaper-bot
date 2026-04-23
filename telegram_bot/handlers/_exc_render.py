"""T11.6 — User-facing exception render helper.

Policy doc: docs/security/T11_6_exception_render_policy.md

Before T11.6 (T10.7 partial fix):
  ~15 handlers used `f"❌ ...: {esc(str(e))}"` pattern to echo raw exception
  text to the user. This leaks internal state:
    - SQL fragments (`no such table: X`, `UNIQUE constraint failed: ...`)
    - File paths (`/path/to/private/data_store/...`)
    - Stack context (`TypeError: 'NoneType' object has no attribute X`)
  Even with HTML escape, semantic content is exposed.

After T11.6:
  User sees generic message + exception TYPE only. Operator uses:
    - `logger.exception(...)` on the server side (full traceback + context)
    - Optional `DEBUG_SHOW_EXC=true` env for admin diagnostic mode
      (shows exc str[:200] for troubleshooting; default OFF in prod)

Usage:
    from telegram_bot.handlers._exc_render import render_user_exception
    try:
        do_risky()
    except Exception as e:  # noqa: BLE001
        logger.exception("context for operator")
        await update.message.reply_text(
            render_user_exception(e, "❌ EV stats hatası"),
            parse_mode="HTML",
        )
"""
from __future__ import annotations

import os
from typing import Optional


# HTML escape — use the same helper handlers already import.
# We re-implement locally to avoid circular imports with escape_html
# utilities that live in handlers/__init__.py or similar.
_HTML_ESC = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
}


def _esc(text: str) -> str:
    """Minimal HTML escape for Telegram parse_mode=HTML."""
    return "".join(_HTML_ESC.get(c, c) for c in text)


def _debug_mode() -> bool:
    """Runtime re-read (T6.1 doctrine) so /envt DEBUG_SHOW_EXC flips
    behaviour without restart."""
    return os.getenv("DEBUG_SHOW_EXC", "false").lower() in (
        "true", "1", "yes", "on"
    )


def render_user_exception(
    exc: BaseException, prefix: Optional[str] = None
) -> str:
    """Policy-compliant user-facing exception message.

    Args:
      exc:    the exception instance caught
      prefix: optional user-visible context label (e.g. "❌ EV stats hatası")

    Returns:
      HTML-safe string (escape applied) suitable for
      `reply_text(..., parse_mode="HTML")`.

    Behaviour matrix:
      DEBUG_SHOW_EXC off (default prod):
        "❌ EV stats hatası — beklenmeyen hata (type=<code>ValueError</code>)"
      DEBUG_SHOW_EXC on (admin diagnostic):
        "❌ EV stats hatası — ValueError: <escaped str(e)[:200]>"
    """
    kind = type(exc).__name__
    label = prefix.strip() if prefix else ""

    if _debug_mode():
        # Admin diagnostic — first 200 chars, HTML-escaped
        detail = _esc(str(exc))[:200]
        body = f"{kind}: {detail}"
        if label:
            return f"{label} — <code>{body}</code>"
        return f"⚠️ <code>{body}</code>"

    # Default: generic user message, type only
    type_html = f"<code>{_esc(kind)}</code>"
    if label:
        return f"{label} — beklenmeyen hata (type={type_html})"
    return f"⚠️ Beklenmeyen hata (type={type_html})"
