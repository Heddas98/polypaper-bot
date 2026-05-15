"""
P2-04 (2026-05-11) — Sentry transaction context manager (env-gated).

ENV-gated zero-cost wrapper for Sentry Performance custom transactions.

When ``SENTRY_DSN`` is empty (Heddas default), the context manager is a
no-op — yields None, does not import sentry_sdk lazily, and never touches
the network. Only when DSN is configured do we wrap real Performance
transactions.

Hot paths instrumented (Phase 48 + P2-04):

  - ``engine.cycle`` — main trading-engine cycle (core/engine.py)
  - ``ai_brain.advise`` — AI Brain cycle invocation (core/ai_brain.py)
  - ``live_trader.execute_buy`` — live USDC buy submission (core/live_trader.py)

Sentry free plan: 5,000 events / month. With ``traces_sample_rate=0.05``
and ~12 cycles/min (5min interval), we expect:

    12 cycles/min × 60 min/hr × 24 hr/day × 30 day/mo × 0.05 sample =
    25,920 cycle events/month × 3 transaction types ≈ 78k events/mo

That overshoots the 5k cap. Default sample rate stays 0.0 (off). Heddas
can opt-in with a much lower rate (0.001 = ~520 events/mo) via
``SENTRY_TRACES_SAMPLE_RATE=0.001``.

Usage::

    from core.observability.sentry_tx import sentry_transaction

    with sentry_transaction(op="ai_brain.advise", name="cycle_42") as tx:
        result = await brain.advise(...)
        if tx is not None:
            tx.set_data("market_count", len(markets))
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("polypaper.observability.sentry_tx")


def _sentry_enabled() -> bool:
    """SENTRY_DSN set + sentry_sdk importable → Performance can record.

    Runtime-read so ``/env_toggle`` SENTRY_DSN changes apply immediately
    (no bot restart). Empty / missing DSN → False (no work, no import).
    """
    if not os.getenv("SENTRY_DSN", "").strip():
        return False
    try:
        import sentry_sdk  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def sentry_transaction(op: str, name: str) -> Iterator[Any]:
    """Context manager yielding a Sentry transaction or None.

    Args:
        op: Sentry operation type — e.g. "engine.cycle", "ai_brain.advise".
        name: Specific transaction name (often includes correlation id or
              cycle number).

    Yields:
        Sentry transaction handle when active, None when no-op. Callers
        should check ``if tx is not None`` before ``tx.set_data(...)``.
    """
    if not _sentry_enabled():
        yield None
        return
    try:
        import sentry_sdk

        with sentry_sdk.start_transaction(op=op, name=name) as tx:
            yield tx
    except Exception as e:  # noqa: BLE001
        # Sentry is best-effort. Any sdk failure must not crash the hot path.
        logger.debug(f"sentry_transaction {op}/{name} failed: {e}")
        yield None
