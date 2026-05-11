"""Unit tests for core/circuit_breaker.py — Phase 48 3-state breaker.

Coverage gap baseline (2026-04-29): `circuit_breaker.py` 0% / 60 stmts.

Pure async logic — no DB, no network. Tests pin the contract:
  - closed → open after fail_threshold consecutive failures
  - open → half_open after cooldown_s elapsed
  - half_open success → closed (recovery)
  - half_open failure → re-open
  - CircuitOpen exception when called during open state
  - registry returns same instance for same name

Avoids `pytest_asyncio` plugin dependency by manually managing an
asyncio loop with `asyncio.new_event_loop` + `loop.run_until_complete`
(T9.5 doctrine: DI + asyncio.run pattern).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core import circuit_breaker as cb_mod
from core.circuit_breaker import (
    CircuitBreaker,
    CircuitOpen,
    all_breakers,
    get_breaker,
)


# ── Helpers ──────────────────────────────────────────────────────
def run(coro):
    """Run an async coroutine to completion in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def clear_registry():
    """Each test starts with a clean breaker registry."""
    cb_mod._registry.clear()
    yield
    cb_mod._registry.clear()


# ── Construction & defaults ──────────────────────────────────────
class TestConstruction:
    def test_defaults(self):
        b = CircuitBreaker(name="x")
        assert b.name == "x"
        assert b.fail_threshold == 5
        assert b.cooldown_s == 30.0
        assert b.state == "closed"
        assert b.failure_count == 0

    def test_explicit_args(self):
        b = CircuitBreaker(name="y", fail_threshold=3, cooldown_s=10.0)
        assert b.fail_threshold == 3
        assert b.cooldown_s == 10.0


# ── State transitions ───────────────────────────────────────────
class TestStateTransitions:
    def test_success_keeps_closed(self):
        b = CircuitBreaker(name="ok", fail_threshold=2)

        async def use():
            async with b:
                return 42

        result = run(use())
        assert result == 42
        assert b.state == "closed"
        assert b.failure_count == 0

    def test_failures_below_threshold_stay_closed(self):
        b = CircuitBreaker(name="t1", fail_threshold=3)

        async def fail():
            async with b:
                raise RuntimeError("upstream")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                run(fail())
        # Still closed — threshold is 3, only 2 failures so far
        assert b.state == "closed"
        assert b.failure_count == 2

    def test_threshold_reached_opens_breaker(self):
        b = CircuitBreaker(name="t2", fail_threshold=2)

        async def fail():
            async with b:
                raise RuntimeError("boom")

        # 2 failures hit threshold
        for _ in range(2):
            with pytest.raises(RuntimeError):
                run(fail())

        assert b.state == "open"
        assert b.failure_count == 2

    def test_open_breaker_raises_circuit_open(self):
        """When open, calls must raise CircuitOpen, not the original exc."""
        b = CircuitBreaker(name="t3", fail_threshold=1, cooldown_s=999)

        async def fail():
            async with b:
                raise RuntimeError("first fail")

        with pytest.raises(RuntimeError):
            run(fail())
        assert b.state == "open"

        # Next call should be refused
        async def use():
            async with b:
                return "should not reach"

        with pytest.raises(CircuitOpen):
            run(use())

    def test_cooldown_elapses_to_half_open(self):
        """After cooldown_s, breaker transitions to half_open on next call."""
        b = CircuitBreaker(name="t4", fail_threshold=1, cooldown_s=0.01)

        async def fail():
            async with b:
                raise RuntimeError("nope")

        with pytest.raises(RuntimeError):
            run(fail())
        assert b.state == "open"

        time.sleep(0.02)  # exceed cooldown

        # Probe call — successful → closed
        async def probe_ok():
            async with b:
                return "ok"

        result = run(probe_ok())
        assert result == "ok"
        assert b.state == "closed"
        assert b.failure_count == 0

    def test_half_open_failure_reopens(self):
        b = CircuitBreaker(name="t5", fail_threshold=1, cooldown_s=0.01)

        async def fail():
            async with b:
                raise RuntimeError("err")

        # Open breaker
        with pytest.raises(RuntimeError):
            run(fail())
        assert b.state == "open"

        time.sleep(0.02)  # exceed cooldown

        # Probe also fails → re-open
        with pytest.raises(RuntimeError):
            run(fail())
        assert b.state == "open"

    def test_circuit_open_does_not_count_as_failure(self):
        """Receiving a CircuitOpen back must not increment failure count
        (otherwise an open breaker would stay open forever)."""
        b = CircuitBreaker(name="t6", fail_threshold=2, cooldown_s=999)

        async def fail():
            async with b:
                raise RuntimeError("e")

        for _ in range(2):
            with pytest.raises(RuntimeError):
                run(fail())
        assert b.state == "open"
        prev_failures = b.failure_count

        async def call_open():
            async with b:
                pass

        with pytest.raises(CircuitOpen):
            run(call_open())

        # Failure count unchanged — CircuitOpen is not a real failure.
        assert b.failure_count == prev_failures


# ── Registry ────────────────────────────────────────────────────
class TestRegistry:
    def test_get_breaker_creates_lazy(self):
        b = get_breaker("api1")
        assert b.name == "api1"
        assert "api1" in all_breakers()

    def test_get_breaker_same_instance(self):
        """Same name → same instance."""
        b1 = get_breaker("api2", fail_threshold=3)
        b2 = get_breaker("api2", fail_threshold=99)  # ignored on second call
        assert b1 is b2
        assert b1.fail_threshold == 3

    def test_all_breakers_snapshot_shape(self):
        get_breaker("clob")
        get_breaker("gamma")
        snapshot = all_breakers()
        assert set(snapshot.keys()) == {"clob", "gamma"}
        for state in snapshot.values():
            assert "state" in state
            assert "failures" in state

    def test_all_breakers_reflects_state(self):
        b = get_breaker("test_state", fail_threshold=1, cooldown_s=999)

        async def fail():
            async with b:
                raise RuntimeError("x")

        with pytest.raises(RuntimeError):
            run(fail())

        snap = all_breakers()
        assert snap["test_state"]["state"] == "open"
        assert snap["test_state"]["failures"] == 1
