"""
Phase 49 P0-05 — HTML escape hardening for Telegram handlers.

Polymarket market slugs, user-provided strategy labels, AI-generated text,
and error messages are all interpolated into `parse_mode="HTML"` messages.
Any stray `<`, `>`, or `&` breaks Telegram's HTML parser and causes the
whole message to fail to render (error 400 "can't parse entities").

Use `esc(value)` anywhere you interpolate untrusted text into an HTML
message. Numbers and None are handled transparently so call sites stay
short:

    from telegram_bot.templates.safe_html import esc
    text = f"<b>{esc(strategy.label)}</b>\\n" \\
           f"PnL: {pnl:+.2f}\\n" \\
           f"Market: <code>{esc(slug)}</code>"

This module deliberately uses only the stdlib so it is safe to import
everywhere, including cold-start handlers.
"""
from __future__ import annotations

import html
from typing import Any


def esc(value: Any) -> str:
    """
    HTML-escape a value for Telegram parse_mode='HTML'.

    - None → empty string (safer than 'None' leaking into UI)
    - Non-strings (int/float/bool) → str(value), unescaped (no unsafe chars)
    - Strings → html.escape(s, quote=False)

    quote=False is chosen because Telegram HTML does NOT honor quoted
    attributes — the only unsafe characters in message bodies are `<`, `>`,
    and `&`. Escaping quotes just wastes bytes.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    return html.escape(value, quote=False)


def esc_code(value: Any) -> str:
    """
    Escape a value intended to sit inside a <code>...</code> block.
    Same rules as esc() plus it strips backticks which Telegram rejects
    inside <code>.
    """
    s = esc(value)
    return s.replace("`", "'")


def fmt_usd(value: float | int | None, sign: bool = False, decimals: int = 2) -> str:
    """Phase 56: Consistent USD formatting across all handlers.

    Examples:
        fmt_usd(10386.5)      → "$10,386.50"
        fmt_usd(-33.6, sign=True) → "-$33.60"
        fmt_usd(1.49)         → "$1.49"
        fmt_usd(0.0)          → "$0.00"
        fmt_usd(None)         → "$0.00"

    Use `sign=True` for PnL values where +/- is important.
    """
    if value is None:
        value = 0.0
    abs_val = abs(value)
    formatted = f"{abs_val:,.{decimals}f}"
    if sign and value > 0:
        return f"+${formatted}"
    elif value < 0:
        return f"-${formatted}"
    return f"${formatted}"
