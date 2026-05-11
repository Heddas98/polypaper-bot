"""
Phase 51 BUG-FIX — CallbackUpdateProxy
=======================================
Hub-style commands (/stats_hub, /risk_hub) route inline-button clicks to
downstream handlers that were written for direct `/command` invocation —
i.e. they use `update.message.reply_text(...)` throughout.

When those handlers are invoked from a CallbackQuery, `update.message` is
None (Telegram attaches the originating message to `update.callback_query
.message` instead), so the call raises `AttributeError`.

`CallbackUpdateProxy` wraps a real Update from a callback context and
exposes `.message` as the callback query's parent message, so downstream
`update.message.reply_text(...)` calls post a brand-new reply in the same
chat. Everything else (`effective_user`, `effective_chat`, `callback_query`)
delegates to the underlying Update.

Usage:

    async def stats_hub_callback(update, context):
        query = update.callback_query
        await query.answer()
        proxy = CallbackUpdateProxy.from_update(update)
        await stats_command(proxy, context)

This is a targeted fix. Handlers can also be refactored to use
`update.effective_message.reply_text(...)` which is callback-safe, but the
proxy is a zero-churn fix that keeps the regression surface tiny.
"""

from __future__ import annotations


class CallbackUpdateProxy:
    """Proxy an Update so downstream `update.message.*` calls work when the
    real update came from a callback query."""

    __slots__ = ("_real", "message")

    def __init__(self, real_update, message):
        self._real = real_update
        self.message = message

    @classmethod
    def from_update(cls, update):
        """Build a proxy using the callback_query.message as the .message
        attribute. Falls back to the original update untouched if no
        callback_query is present."""
        q = getattr(update, "callback_query", None)
        if q is None or getattr(q, "message", None) is None:
            return update
        return cls(update, q.message)

    def __getattr__(self, name):
        # Only called if the attribute wasn't found via __slots__/instance.
        return getattr(self._real, name)

    def __repr__(self):
        return f"<CallbackUpdateProxy wraps={self._real!r}>"
