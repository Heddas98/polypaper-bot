"""Observability helpers — telemetry primitives that don't gate runtime.

Modules here MUST be safe to import even when their telemetry feature is
turned off (default OFF). Each module exposes an `enabled()` function and
gates expensive operations behind it. Designed to be enabled per-deploy
via ENV without any code changes.

─────────────────────────────────────────────────────────────────────────
Phase 48 — Lightweight correlation-id plumbing
─────────────────────────────────────────────────────────────────────────
Provides a contextvar-based correlation_id that can be threaded through
logs to tie a single trade decision (eval → risk → fill → execution →
settlement) together without plumbing IDs through every function
signature.

Usage:
    from core.observability import new_correlation_id, get_correlation_id

    cid = new_correlation_id()
    # ... work ...
    logger.info("decision", extra={"cid": cid})

And in logging config, add %(cid)s to the format string, or use the
CorrelationFilter below which injects it automatically.

2026-04-22 — Rescued from the shadowed `core/observability.py` module
(introduced alongside this package in commit 3264add, Epic 4 T4.3). The
legacy file has been archived; this __init__.py is now the canonical
home for the correlation-id helpers. See `feedback_change_impact_check`
memory for the post-mortem.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Optional

__all__ = [
    "new_correlation_id",
    "set_correlation_id",
    "get_correlation_id",
    "clear_correlation_id",
    "CorrelationFilter",
    "CorrelationContext",
]

_current_cid: ContextVar[str] = ContextVar("polypaper_cid", default="-")


def new_correlation_id(prefix: str = "") -> str:
    """Generate a new short correlation id and set it in the current context."""
    cid = f"{prefix}{uuid.uuid4().hex[:8]}" if prefix else uuid.uuid4().hex[:8]
    _current_cid.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    _current_cid.set(cid)


def get_correlation_id() -> str:
    return _current_cid.get()


def clear_correlation_id() -> None:
    _current_cid.set("-")


class CorrelationFilter(logging.Filter):
    """Logging filter that injects the current correlation id on every record.

    Install once on the root logger (or any handler):
        for h in logging.getLogger().handlers:
            h.addFilter(CorrelationFilter())
    Then use %(cid)s in the format string.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        # T1.4 Faz 3 — ContextVar.get() with a default only raises LookupError
        # in edge cases (reset without re-entry). Keep the catch narrow so an
        # unexpected error surfaces instead of being silently swallowed.
        try:
            record.cid = _current_cid.get()
        except LookupError:
            record.cid = "-"
        return True


class CorrelationContext:
    """Context manager — set a cid for a block, restore on exit.

    with CorrelationContext("trade_eval"):
        # everything logged here gets the same cid
        engine._evaluate(...)
    """

    def __init__(self, prefix: str = "", cid: Optional[str] = None):
        self.cid = cid or new_correlation_id(prefix)
        self._token = None

    def __enter__(self) -> str:
        self._token = _current_cid.set(self.cid)
        return self.cid

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _current_cid.reset(self._token)
        return False
