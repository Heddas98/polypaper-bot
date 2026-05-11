"""
PolyPaper Bot — REST Timing Telemetry (Epic 4 T4.7 helper)
==========================================================

Lightweight RTT (round-trip time) recorder for live HTTP calls to
Polymarket CLOB and Gamma endpoints. Designed to feed Faz B empirical
calibration of `REST_LATENCY_MS` / `REST_LATENCY_JITTER_MS` defaults
(currently HEURISTIC 200ms / 80ms — never measured against live).

DEFAULT: OFF. Enable via ENV `REST_TIMING_TELEMETRY=true`.

USAGE
-----
1. Wrap an httpx.AsyncClient call:

       from core.observability.rest_timing import time_call
       async with time_call("clob.create_order"):
           resp = await http.post(url, json=payload)

2. Or use the `record_ms(label, ms)` API directly when you already have
   an elapsed measurement (e.g. from a py-clob-client wrapper).

3. Inspect aggregates via `get_summary()` (returns p10/p50/p90/p99/n
   per label) or write to disk via `dump_to_file(path)`.

WHAT IT DOES NOT DO
-------------------
- It is NOT an HTTP middleware — no monkey-patching, no httpx event hook
  registration. Caller decides where to instrument (intentional: keeps
  the engine path explicit and avoids surprising telemetry overhead).
- It does NOT export to Prometheus / OpenTelemetry. Pure in-process
  rolling buffer suitable for 24h sample collection then offline analysis.
- It does NOT block on disk writes — `dump_to_file` is a manual op
  invoked by /env_toggle or a scheduled job.

MEMORY BUDGET
-------------
Default: keep last 10,000 samples per label (rolling deque). At 8 labels
× 8 bytes × 10k = ~640KB. Override via `REST_TIMING_BUFFER_SIZE`.

THREAD/ASYNC SAFETY
-------------------
The append-only deque is thread-safe enough for our single-event-loop
asyncio use. If a future caller spawns workers, wrap with `asyncio.Lock`.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Optional

logger = logging.getLogger("polypaper.observability.rest_timing")

# ── Module state ─────────────────────────────────────────────────────────
_ENABLED: Optional[bool] = None
_BUFFER_SIZE: int = 10_000
_samples: dict[str, deque] = defaultdict(lambda: deque(maxlen=_BUFFER_SIZE))


def enabled() -> bool:
    """Cache + return whether telemetry is on. ENV is read once per process.

    Cache is intentional — ``record_ms`` is a hot path; re-reading os.getenv
    on every sample would waste cycles. Boot-time configuration only.
    Tests that need to flip the toggle mid-process must call
    :func:`_reset_cache` (see T7.6 B5).
    """
    global _ENABLED, _BUFFER_SIZE
    if _ENABLED is None:
        _ENABLED = os.getenv("REST_TIMING_TELEMETRY", "false").lower() == "true"
        try:
            _BUFFER_SIZE = max(100, int(os.getenv("REST_TIMING_BUFFER_SIZE", "10000")))
        except (TypeError, ValueError):
            _BUFFER_SIZE = 10_000
        if _ENABLED:
            logger.info(f"REST timing telemetry ENABLED (buffer={_BUFFER_SIZE}/label)")
    return _ENABLED


def _reset_cache() -> None:
    """Test-only: clear the ENV cache so the next :func:`enabled` call re-reads.

    T7.6 B5 (2026-04-22): added to let test suites that mutate
    ``REST_TIMING_TELEMETRY`` between tests actually observe the new value
    without tearing down the process. Not part of the public API; do NOT
    call from production code.
    """
    global _ENABLED
    _ENABLED = None


def record_ms(label: str, elapsed_ms: float) -> None:
    """Record a single RTT sample for `label` (e.g. 'clob.create_order')."""
    if not enabled():
        return
    if elapsed_ms < 0:
        return
    _samples[label].append(float(elapsed_ms))


@asynccontextmanager
async def time_call(label: str):
    """Async context manager: time an awaited HTTP call.

    Example::

        async with time_call("clob.create_order"):
            resp = await http.post(url, json=payload)

    No-op (zero overhead beyond a context-manager call) when telemetry
    is disabled.
    """
    if not enabled():
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        _samples[label].append(elapsed_ms)


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Inclusive linear-interpolation percentile (matches numpy default)."""
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (n - 1)
    lo = int(rank)
    hi = min(lo + 1, n - 1)
    frac = rank - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def get_summary() -> dict[str, dict]:
    """Return p10/p50/p90/p99/n/mean per label (sorted by label).

    Returns empty dict when telemetry is disabled.
    """
    if not enabled():
        return {}
    out: dict[str, dict] = {}
    for label in sorted(_samples.keys()):
        buf = list(_samples[label])
        if not buf:
            continue
        s = sorted(buf)
        out[label] = {
            "n": len(s),
            "mean": round(sum(s) / len(s), 1),
            "p10": round(_percentile(s, 10), 1),
            "p50": round(_percentile(s, 50), 1),
            "p90": round(_percentile(s, 90), 1),
            "p99": round(_percentile(s, 99), 1),
            "min": round(s[0], 1),
            "max": round(s[-1], 1),
        }
    return out


def dump_to_file(path: str) -> bool:
    """Write current summary as JSON to `path`. Returns True on success."""
    if not enabled():
        return False
    try:
        summary = get_summary()
        payload = {
            "ts": time.time(),
            "labels": summary,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"REST timing summary dumped to {path} ({len(summary)} labels)")
        return True
    except OSError as e:
        logger.warning(f"REST timing dump failed: {type(e).__name__}: {e}")
        return False


def reset() -> None:
    """Clear all sample buffers. For test isolation."""
    _samples.clear()
