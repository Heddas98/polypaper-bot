"""
core/engine_support.py — Phase 51 P51-02 pilot extraction
==========================================================
Shared helpers and small data classes used by core/engine.py.

This module is the first slice of the engine.py refactor. By moving
pure, context-free helpers out, the TradingEngine class in engine.py
shrinks without changing its public API or runtime behaviour —
`engine.py` re-exports every symbol defined here for back-compat, so
existing imports like `from core.engine import SkipCounter, VirtualOrder`
continue to work unchanged.

Contains:
  - `SkipCounter`  — per-heartbeat trade skip reason tallies
  - `VirtualOrder` — pending-fill representation used by the order lifecycle
  - Slug helpers (`_slug_end`, `_slug_start`) — Polymarket market-slug parsing
  - `_stagger`     — deterministic stagger delay per strategy id
  - `INTERVAL_SECS` / `MAX_MBE` / `WIDE_SPREAD` / `WS_STALE_THRESHOLD`

These symbols used to live at the top of engine.py. No logic changed.
"""
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INTERVAL_SECS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}
MAX_MBE = {"5m": 1.0, "15m": 3.0, "1h": 12.0, "4h": 48.0, "24h": 288.0}
WIDE_SPREAD = float(os.getenv("MAKER_WIDE_SPREAD", "0.03"))  # Phase 74: env-tunable, 0.10→0.03 default
WS_STALE_THRESHOLD = 60.0  # Phase 16.5: 60s for 5m market gaps


# ---------------------------------------------------------------------------
# SkipCounter
# ---------------------------------------------------------------------------


class SkipCounter:
    """Tracks ALL trade skip reasons for visibility. Resets each heartbeat.

    Phase 79b: Added per-strategy log throttle to suppress repetitive
    messages like EMA_BLOCK firing every second for the same strategy.
    """
    __slots__ = ('_counts', '_total', '_logged')

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._total = 0
        self._logged: set[str] = set()  # "sid:reason" keys already logged this cycle

    def record(self, reason: str):
        self._counts[reason] = self._counts.get(reason, 0) + 1
        self._total += 1

    def should_log(self, sid: str, reason: str) -> bool:
        """Return True only if this sid+reason combo hasn't been logged this cycle.

        Call this BEFORE logger.info() to suppress duplicate messages.
        Typical usage:
            self.skips.record("EMA_BLOCK")
            if self.skips.should_log(sid, "EMA_BLOCK"):
                logger.info(f"  [{sid}] ❌ EMA_BLOCK: ...")
        """
        key = f"{sid}:{reason}"
        if key in self._logged:
            return False
        self._logged.add(key)
        return True

    def summary(self) -> str:
        if not self._counts:
            return "no skips"
        top = sorted(self._counts.items(), key=lambda x: -x[1])[:4]
        parts = [f"{k}={v}" for k, v in top]
        return f"{self._total}skip [{' '.join(parts)}]"

    def get_counts(self) -> dict:
        """Sprint 2: Return copy of skip counts for cycle summary logging."""
        return dict(self._counts)

    def reset(self):
        self._counts.clear()
        self._total = 0
        self._logged.clear()


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _slug_end(slug):
    p = slug.split("-")
    if len(p) < 4:
        return None
    try:
        return datetime.fromtimestamp(
            int(p[3]) + INTERVAL_SECS.get(p[2], 300), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        # T1.4 Faz 3: int(p[3]) → ValueError on non-numeric slug segments.
        # datetime.fromtimestamp → ValueError/OverflowError for out-of-range
        # epochs (e.g. 32-bit clamp on some platforms) or OSError on
        # Windows for negative epochs. Malformed slug → None (caller skips).
        return None


def _slug_start(slug):
    p = slug.split("-")
    if len(p) < 4:
        return None
    try:
        return datetime.fromtimestamp(int(p[3]), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        # T1.4 Faz 3: same failure surface as _slug_end — non-numeric
        # timestamp segment or out-of-range epoch.
        return None


def _stagger(sid):
    return 0.001 + (int(hashlib.md5(sid.encode()).hexdigest()[:4], 16) % 9) * 0.001


# ---------------------------------------------------------------------------
# VirtualOrder
# ---------------------------------------------------------------------------


class VirtualOrder:
    __slots__ = ("strategy_id", "slug", "token_id", "direction",
                 "limit_price", "amount", "fee", "created_at",
                 "wallet_id", "user_id", "sl_pct", "sl_odds",
                 "tp_pct", "tp_odds", "threshold", "is_maker",
                 "signal_score", "signal_price",
                 # Phase 39 (P1.2): maker queue position simulation
                 "queue_ahead_usd", "cum_traded_at_price_usd",
                 "placement_ts_ms",
                 # Phase 43a: category tag for fee router (crypto, sports, …)
                 "category",
                 # Phase 59: structured trade reasoning for AI learning
                 "reasoning_json")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        if not hasattr(self, 'is_maker'):
            self.is_maker = False
        if not hasattr(self, 'signal_score'):
            self.signal_score = 0.0
        # Phase 38c: price seen at signal-generation time — used to compute
        # signal→fill slippage and mirror real Polymarket latency impact.
        if not hasattr(self, 'signal_price'):
            self.signal_price = 0.0
        # Phase 39 (P1.2): queue depth (USD) ahead of this order at its
        # limit price at placement time. Maker order is only considered
        # filled once cum_traded_at_price_usd >= queue_ahead_usd.
        if not hasattr(self, 'queue_ahead_usd'):
            self.queue_ahead_usd = 0.0
        if not hasattr(self, 'cum_traded_at_price_usd'):
            self.cum_traded_at_price_usd = 0.0
        if not hasattr(self, 'placement_ts_ms'):
            self.placement_ts_ms = int(time.time() * 1000)
        # Phase 43a: default category for fee router
        if not hasattr(self, 'category'):
            self.category = None
        # Phase 59: reasoning context for post-trade analysis
        if not hasattr(self, 'reasoning_json'):
            self.reasoning_json = None
        self.created_at = datetime.now(timezone.utc)
