"""
Phase 82e Sprint 2.1 — Background Task Exception Guard
======================================================
Wrapper around asyncio.create_task() that prevents silent failures:

  1. Catches all uncaught exceptions in background coroutines.
  2. Logs full stack trace to polypaper.log.
  3. Notifies admin Telegram chat (rate-limited per task name).
  4. Registers task state in a process-local registry so /diagnose
     can report "last 20 background task errors".

THE PROBLEM IT SOLVES:
  Before: `asyncio.create_task(self._run())` — if _run() raises,
          Python logs a warning ("Task exception was never retrieved")
          only when the task is garbage-collected. By that point the
          coroutine has been dead for minutes, no user notification,
          and the engine silently stops recording / trading / etc.
  After:  `safe_create_task(self._run(), name="engine_main")` — any
          exception is logged, surfaced to Telegram, and retained for
          /diagnose. The task name becomes the key for rate-limiting
          and history.

USAGE:
    from core.bg_task import safe_create_task, set_notify_handler

    # At bot startup (once):
    set_notify_handler(my_async_telegram_notify_fn)

    # Replace create_task call sites:
    self._task = safe_create_task(self._run(), name="engine_main")

    # Optional: custom on_error callback for cleanup
    safe_create_task(self._ws_loop(), name="binance_spot",
                     on_error=lambda e: self.reset())

ENV:
  BG_TASK_NOTIFY_COOLDOWN_SEC   (default 300)  # min sec between notifies/task
  BG_TASK_HISTORY_SIZE          (default 50)   # recent errors retained
  BG_TASK_NOTIFY_ENABLED        (default "1")  # 0 = log only
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time
import traceback
from collections import deque
from typing import Any, Awaitable, Callable, Deque, Optional

logger = logging.getLogger("polypaper.bg_task")

# ── Module-level state ────────────────────────────────────────────────
_BG_TASK_REGISTRY: dict[str, dict] = {}
_RECENT_ERRORS: Deque[dict] = deque(
    maxlen=int(os.getenv("BG_TASK_HISTORY_SIZE", "50"))
)
_NOTIFY_COOLDOWN: dict[str, float] = {}
_NOTIFY_HANDLER: Optional[Callable[[str, str, str], Awaitable[None]]] = None
_NOTIFY_ENABLED = os.getenv("BG_TASK_NOTIFY_ENABLED", "1") in ("1", "true", "True", "yes", "on")
# Epic 7 B6 (2026-04-22): strong-reference container for every running task.
# Python's event loop keeps only WEAK references to tasks via `_all_tasks`
# (CPython 3.11 `asyncio.tasks._all_tasks` is a WeakSet). That means a
# fire-and-forget `safe_create_task(..., name="x")` whose return value the
# caller discards can be garbage-collected mid-execution — yielding the
# exact "Task was destroyed but it is pending!" RuntimeWarning and silent
# death that `_BG_TASK_REGISTRY` was built to prevent. The registry stored
# metadata DICTS, not the task objects, so it didn't actually protect tasks
# from GC. `_BG_TASK_OBJECTS` holds the asyncio.Task itself; the
# `add_done_callback(_BG_TASK_OBJECTS.discard)` line inside `safe_create_task`
# releases the reference once the coroutine finishes or raises.
# See Python docs: https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_BG_TASK_OBJECTS: set[asyncio.Task] = set()


def set_notify_handler(
    fn: Callable[[str, str, str], Awaitable[None]],
) -> None:
    """Register an async callable that delivers a notification.

    Signature: `async fn(task_name: str, err_short: str, traceback_snippet: str)`

    Called at most once per BG_TASK_NOTIFY_COOLDOWN_SEC per task name.
    Any exception inside the handler is swallowed to prevent notification
    failures from masking the original error.
    """
    global _NOTIFY_HANDLER
    _NOTIFY_HANDLER = fn
    logger.info("bg_task notify handler registered")


def _should_notify(task_name: str) -> bool:
    """Return True if we haven't recently notified for this task."""
    if not _NOTIFY_ENABLED:
        return False
    try:
        cooldown = int(os.getenv("BG_TASK_NOTIFY_COOLDOWN_SEC", "300"))
    except ValueError:
        cooldown = 300
    now = time.time()
    last = _NOTIFY_COOLDOWN.get(task_name, 0.0)
    if now - last >= cooldown:
        _NOTIFY_COOLDOWN[task_name] = now
        return True
    return False


def safe_create_task(
    coro: Awaitable[Any],
    *,
    name: str,
    on_error: Optional[Callable[[BaseException], Any]] = None,
    notify: bool = True,
    reraise: bool = False,
) -> asyncio.Task:
    """Create an asyncio task with automatic exception guarding.

    Args:
      coro: The coroutine to run.
      name: Unique-ish task name (used for registry keys + notify rate-limit).
      on_error: Optional sync callable invoked with the exception.
                Useful for auto-restart/cleanup logic.
      notify: If False, do not send Telegram notification (log only).
      reraise: If True, propagate the exception after handling.
               Default False (fire-and-forget tasks shouldn't crash callers).
    Returns:
      The created asyncio.Task.
    """
    async def _wrapped():
        started = time.time()
        _BG_TASK_REGISTRY[name] = {
            "name": name,
            "state": "running",
            "started_at": started,
            "last_error": None,
            "last_traceback": None,
            "failed_at": None,
            "error_count": _BG_TASK_REGISTRY.get(name, {}).get("error_count", 0),
        }
        try:
            return await coro
        except asyncio.CancelledError:
            _BG_TASK_REGISTRY[name]["state"] = "cancelled"
            raise
        except BaseException as e:  # noqa: BLE001 - T1.4 Faz 3: module contract
            # Module philosophy (see module docstring): safe_create_task
            # exists precisely to intercept ANY uncaught exception from
            # background coroutines, including KeyboardInterrupt and
            # SystemExit. Narrowing here would defeat the entire point
            # of the wrapper. CancelledError is re-raised above at L130.
            tb = traceback.format_exc()
            err_short = f"{type(e).__name__}: {str(e)[:250]}"
            logger.error(
                "bg_task[%s] FAILED: %s\n%s", name, err_short, tb
            )
            # Update registry
            reg = _BG_TASK_REGISTRY.setdefault(name, {"name": name})
            reg["state"] = "failed"
            reg["last_error"] = err_short
            reg["last_traceback"] = tb[-2000:]
            reg["failed_at"] = time.time()
            reg["error_count"] = reg.get("error_count", 0) + 1

            # Append to ring buffer
            _RECENT_ERRORS.append({
                "name": name,
                "error": err_short,
                "ts": time.time(),
                "traceback": tb[-500:],
            })

            # Telegram notify (rate-limited)
            if notify and _NOTIFY_HANDLER and _should_notify(name):
                try:
                    tb_snippet = "\n".join(tb.splitlines()[-8:])
                    await _NOTIFY_HANDLER(name, err_short, tb_snippet)
                except Exception as notify_err:  # noqa: BLE001 - T1.4 Faz 3: user-supplied handler
                    # _NOTIFY_HANDLER is set via set_notify_handler() with
                    # caller-supplied code (telegram bot, slack, etc.).
                    # Exception types are unknowable at import time; any
                    # leak here would corrupt the bg_task guard itself.
                    # Docstring at set_notify_handler explicitly warns
                    # that exceptions will be swallowed.
                    logger.warning(
                        "bg_task[%s] notify handler failed: %s",
                        name, notify_err
                    )

            # Custom cleanup
            if on_error is not None:
                try:
                    result = on_error(e)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as hook_err:  # noqa: BLE001 - T1.4 Faz 3: user-supplied hook
                    # `on_error` is caller-provided (e.g. lambda e:
                    # self.reset()). Types are unknowable; leaking here
                    # would corrupt the bg_task guard itself after we've
                    # already handled the original exception. Intentional
                    # guard-of-guard.
                    logger.warning(
                        "bg_task[%s] on_error hook failed: %s",
                        name, hook_err
                    )

            if reraise:
                raise
        finally:
            # Mark completed if still running (no exception)
            reg = _BG_TASK_REGISTRY.get(name)
            if reg and reg.get("state") == "running":
                reg["state"] = "completed"
                reg["completed_at"] = time.time()

    task = asyncio.create_task(_wrapped(), name=name)
    # Epic 7 B6 (2026-04-22): hold a strong ref so fire-and-forget callers
    # don't lose their task to GC. Discarded automatically when the task
    # finishes (done-callback runs exactly once per task, CancelledError
    # included). This is independent of `_BG_TASK_REGISTRY` which tracks
    # metadata dicts for /diagnose observability.
    _BG_TASK_OBJECTS.add(task)
    task.add_done_callback(_BG_TASK_OBJECTS.discard)
    # Pre-populate registry so /diagnose can see "pending" tasks
    _BG_TASK_REGISTRY.setdefault(name, {}).update({
        "name": name,
        "state": "pending",
    })
    return task


def get_registry_snapshot() -> dict[str, dict]:
    """Non-mutating snapshot of the current task registry.

    Strips non-serializable fields (tasks, etc.) so the snapshot can
    be emitted to Telegram / JSON responses.
    """
    return {
        name: {
            k: v for k, v in info.items()
            if k not in ("task",)
        }
        for name, info in _BG_TASK_REGISTRY.items()
    }


def get_recent_errors(limit: int = 20) -> list[dict]:
    """Last N errors across all tasks (newest first)."""
    items = list(_RECENT_ERRORS)
    items.reverse()
    return items[:limit]


def clear_registry() -> None:
    """Mostly for tests."""
    _BG_TASK_REGISTRY.clear()
    _RECENT_ERRORS.clear()
    _NOTIFY_COOLDOWN.clear()
    # Epic 7 B6: test helper — also drop strong-ref container. Only safe
    # when no tasks are in-flight; pytest fixtures call this between tests.
    _BG_TASK_OBJECTS.clear()


def get_live_task_count() -> int:
    """Epic 7 B6 (2026-04-22): debuggability helper — how many
    `safe_create_task`-created tasks are still alive (strongly ref'd).
    Equal to `_BG_TASK_OBJECTS` size; exposed as a function so tests and
    /diagnose can snapshot without leaking the internal set."""
    return len(_BG_TASK_OBJECTS)


# ── Default Telegram notify handler (factory) ─────────────────────────
def make_telegram_notify_handler(
    bot_app,
    admin_chat_id: int | str,
):
    """Build a notify handler that sends to Telegram.

    Call from bot startup: `set_notify_handler(make_telegram_notify_handler(app, ADMIN_ID))`
    """
    async def _handler(task_name: str, err_short: str, tb_snippet: str):
        try:
            safe_err = html.escape(err_short)
            safe_tb = html.escape(tb_snippet)
            safe_name = html.escape(task_name)
            msg = (
                f"⚠️ <b>Background Task Failed</b>\n\n"
                f"<b>Name:</b> <code>{safe_name}</code>\n"
                f"<b>Error:</b> <code>{safe_err}</code>\n\n"
                f"<pre>{safe_tb}</pre>\n"
                f"<i>Full traceback in logs. /diagnose to list recent errors.</i>"
            )
            await bot_app.bot.send_message(
                chat_id=admin_chat_id,
                text=msg[:4000],
                parse_mode="HTML"
            )
        except Exception as e:  # noqa: BLE001 - T1.4 Faz 3: telegram SDK umbrella
            # python-telegram-bot's send_message can raise various
            # telegram.error.TelegramError subclasses (NetworkError,
            # TimedOut, Forbidden, BadRequest, RetryAfter, etc.) plus
            # httpx transport errors underneath. Importing telegram.error
            # at module top would couple bg_task to the telegram SDK,
            # which other non-telegram callers of this module (e.g.
            # discord/slack variants) shouldn't need.
            # Never let notify failure bubble up
            logger.warning("telegram notify handler failed: %s", e)

    return _handler
