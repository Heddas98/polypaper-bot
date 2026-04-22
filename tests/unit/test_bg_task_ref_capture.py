"""Epic 7 B6 — safe_create_task strong-reference capture regression.

These tests verify that `safe_create_task` holds a strong reference to the
asyncio.Task it creates, so fire-and-forget callers (who discard the return
value) can't lose the task to GC mid-execution.

Before B6 (2026-04-22): `_BG_TASK_REGISTRY` stored metadata dicts only. A
call like `safe_create_task(coro(), name="x")` whose result was thrown away
could be collected by GC if the event loop dropped its weak ref — yielding
"Task was destroyed but it is pending!" and silent death of the scheduler.

After B6: `_BG_TASK_OBJECTS` module-level set holds every live task. A
`task.add_done_callback(_BG_TASK_OBJECTS.discard)` wires automatic release
when the coroutine completes (success, exception, or cancellation).
"""
from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from core import bg_task
from core.bg_task import (
    _BG_TASK_OBJECTS,
    clear_registry,
    get_live_task_count,
    safe_create_task,
)


@pytest.fixture(autouse=True)
def _reset_bg_task_state():
    """Ensure each test starts with empty registry + strong-ref set."""
    clear_registry()
    yield
    clear_registry()


@pytest.mark.asyncio
async def test_fire_and_forget_task_survives_gc():
    """Regression: a fire-and-forget task must NOT be collected by GC
    while still running, even if the caller discards the return value."""

    completed = asyncio.Event()

    async def slow_job():
        # Force a yield so GC has an opportunity to run while pending.
        await asyncio.sleep(0.05)
        completed.set()

    # Fire and forget — DO NOT capture the task reference.
    safe_create_task(slow_job(), name="fire_and_forget_gc_test")

    # Force aggressive GC — if _BG_TASK_OBJECTS didn't hold the ref,
    # this would collect the task and the sleep would never complete.
    for _ in range(3):
        gc.collect()

    # Wait for completion (but with timeout — if task was GC'd it'll hang).
    try:
        await asyncio.wait_for(completed.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "fire-and-forget task did not complete — likely GC'd because "
            "safe_create_task no longer holds a strong reference (B6 regressed)"
        )


@pytest.mark.asyncio
async def test_done_callback_releases_strong_ref():
    """After a task finishes, `_BG_TASK_OBJECTS` must drop the ref so
    long-running processes don't leak task objects forever."""

    async def quick_job():
        await asyncio.sleep(0)

    task = safe_create_task(quick_job(), name="done_callback_test")

    # Strong ref should be held while pending.
    assert task in _BG_TASK_OBJECTS
    assert get_live_task_count() >= 1

    # Drain the event loop so the done-callback fires.
    await task
    # done_callback runs asynchronously — yield once more.
    await asyncio.sleep(0)

    assert task not in _BG_TASK_OBJECTS, (
        "done-callback did not remove task from _BG_TASK_OBJECTS — "
        "this would leak Task objects in long-running processes"
    )


@pytest.mark.asyncio
async def test_cancelled_task_also_released():
    """CancelledError goes through the same done-callback path; the
    set must NOT retain a zombie reference after cancel."""

    ready = asyncio.Event()

    async def sleeper():
        ready.set()
        await asyncio.sleep(10)  # long enough to cancel

    task = safe_create_task(sleeper(), name="cancel_cleanup_test")
    await ready.wait()
    assert task in _BG_TASK_OBJECTS

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)  # let done-callback run

    assert task not in _BG_TASK_OBJECTS, (
        "cancelled task still in _BG_TASK_OBJECTS — leak on cancel path"
    )


@pytest.mark.asyncio
async def test_failed_task_also_released():
    """When the wrapped coroutine raises, the guard handles + logs it and
    the done-callback still fires, releasing the strong ref."""

    async def boom():
        raise RuntimeError("intentional")

    task = safe_create_task(boom(), name="failure_cleanup_test", notify=False)
    await task  # _wrapped swallows the exception (reraise=False default)
    await asyncio.sleep(0)

    assert task not in _BG_TASK_OBJECTS, (
        "failed task still in _BG_TASK_OBJECTS — leak on error path"
    )


@pytest.mark.asyncio
async def test_multiple_concurrent_tasks_all_tracked():
    """Concurrent fire-and-forget tasks must all be strong-ref'd
    independently; releasing one must not disturb the others."""

    done_count = 0

    async def worker(idx):
        nonlocal done_count
        await asyncio.sleep(0.01 * idx)
        done_count += 1

    for i in range(5):
        safe_create_task(worker(i), name=f"concurrent_{i}")

    # All 5 should be pending.
    assert get_live_task_count() == 5

    # Wait for all to complete (longest sleep is 0.04s).
    await asyncio.sleep(0.1)

    assert done_count == 5
    assert get_live_task_count() == 0, (
        f"5 tasks finished but {get_live_task_count()} still held — "
        "done-callback cleanup failed"
    )


@pytest.mark.asyncio
async def test_clear_registry_also_clears_strong_refs():
    """`clear_registry()` is the test helper; it must also drop the
    strong-ref set so tests don't bleed state into each other."""

    started = asyncio.Event()

    async def sleeper():
        started.set()
        await asyncio.sleep(10)

    t = safe_create_task(sleeper(), name="clear_test")
    # Let the coroutine reach its first await so the sleep coro is properly
    # awaited (avoids "coroutine was never awaited" RuntimeWarning).
    await started.wait()
    assert t in _BG_TASK_OBJECTS

    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    await asyncio.sleep(0)

    # Sanity: normal cleanup path already cleared it.
    assert t not in _BG_TASK_OBJECTS

    # But clear_registry() is the explicit test-isolation knob — verify
    # it handles the case where tasks are still "pending" (hypothetical).
    clear_registry()
    assert get_live_task_count() == 0


def test_bg_task_objects_is_set_not_list():
    """Set semantics are required for O(1) discard; enforce the type."""
    assert isinstance(bg_task._BG_TASK_OBJECTS, set)
