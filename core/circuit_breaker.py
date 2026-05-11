"""
Phase 48 — Circuit breaker for external API calls.

Classic 3-state breaker (closed → open → half-open):

  closed    → calls pass through, failures counted
  open      → all calls raise CircuitOpen immediately, cooling down
  half_open → a single probe call is allowed; success closes, failure re-opens

Designed for the Polymarket CLOB endpoint but equally useful for Gamma,
Chainlink, and Binance. Instances are keyed by name so the bot can have
separate breakers for each upstream.

Wrap async callsites like:

    from core.circuit_breaker import get_breaker
    breaker = get_breaker("clob", fail_threshold=5, cooldown_s=30)
    try:
        async with breaker:
            result = await poly_client.create_order(...)
    except CircuitOpen:
        logger.warning("CLOB breaker open — skipping order")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger("polypaper.circuit_breaker")


class CircuitOpen(Exception):
    """Raised when a call is refused because the breaker is open."""


@dataclass
class CircuitBreaker:
    name: str
    fail_threshold: int = 5
    cooldown_s: float = 30.0
    # Internal state (do not set directly; use async with)
    _state: str = "closed"  # "closed" | "open" | "half_open"
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def __aenter__(self):
        async with self._lock:
            now = time.monotonic()
            if self._state == "open":
                if (now - self._opened_at) >= self.cooldown_s:
                    self._state = "half_open"
                    logger.info("🔌 %s: half_open (probe)", self.name)
                else:
                    remaining = self.cooldown_s - (now - self._opened_at)
                    raise CircuitOpen(f"{self.name} open, {remaining:.1f}s left")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        async with self._lock:
            if exc_type is None:
                # Success
                if self._state in ("half_open", "open"):
                    logger.info("🔌 %s: closed (recovery)", self.name)
                self._state = "closed"
                self._failures = 0
                return False

            # Failure path — don't count CircuitOpen itself
            if exc_type is CircuitOpen:
                return False

            self._failures += 1
            if self._state == "half_open":
                # Probe failed → re-open
                self._state = "open"
                self._opened_at = time.monotonic()
                logger.warning("🔌 %s: re-opened (probe failed: %s)", self.name, exc)
            elif self._failures >= self.fail_threshold:
                self._state = "open"
                self._opened_at = time.monotonic()
                logger.warning(
                    "🔌 %s: opened after %d failures (cooldown %.0fs)",
                    self.name,
                    self._failures,
                    self.cooldown_s,
                )
            return False  # let the exception propagate

    @property
    def state(self) -> str:
        return self._state

    @property
    def failure_count(self) -> int:
        return self._failures


# ── Registry ────────────────────────────────────────────────────
_registry: Dict[str, CircuitBreaker] = {}


def get_breaker(
    name: str,
    *,
    fail_threshold: int = 5,
    cooldown_s: float = 30.0,
) -> CircuitBreaker:
    """Return (or lazily create) the named breaker."""
    if name not in _registry:
        _registry[name] = CircuitBreaker(
            name=name,
            fail_threshold=fail_threshold,
            cooldown_s=cooldown_s,
        )
    return _registry[name]


def all_breakers() -> dict[str, dict]:
    """Return a dict snapshot of all registered breakers for /health output."""
    return {name: {"state": b.state, "failures": b.failure_count} for name, b in _registry.items()}
