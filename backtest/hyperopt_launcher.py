"""
backtest/hyperopt_launcher.py -- Phase 82e Headless Subprocess Launcher
=======================================================================

Purpose
-------
A Telegram-free, headless wrapper around ``backtest.hyperopt_worker`` for
callers that must NOT block the main event loop:

  * core.ai_brain.AIBrain._run_hyperopt_bg  (OPTIMIZE action)
  * telegram_bot.jobs.tournament_job        (nightly AI tournament)
  * any future automated trigger

Historical bug it fixes
-----------------------
Phase 82b moved the Telegram ``/hyperopt`` path to a subprocess so it could
never freeze the bot, but ``ai_brain.py`` still ran ``HyperOptPipeline.optimize``
INLINE via ``asyncio.create_task(self._run_hyperopt_bg(...))``. That task
shares the engine's event loop, so any heavy SQL inside discovery priming
(>= 90 s) tripped the engine stall watchdog. Observed on 2026-04-18
(cycles 120 & 121 frozen for 90 s each right after AI Brain fired OPTIMIZE).

With this launcher, ALL hyperopt work — Telegram, AI Brain, cron — runs in
the same subprocess worker with the same:
  * PID-file mutex (:class:`PidFileLock`)
  * Phase 82b.5 discovery cache priming
  * IPC event stream
  * stderr drain to parent logger
  * outer timeout guard (study_timeout + buffer)

What it returns
---------------
``launch_hyperopt_subprocess`` returns a :class:`StratDoneInfo` on success
(best params + score + elapsed), ``None`` on:
  * lock busy (another hyperopt already running)
  * subprocess spawn failure
  * worker exited without emitting STRAT_DONE (timeout / crash)
  * user abort

Callers MUST accept ``None`` as "no result, do not apply" — never treat it
as a neutral success. All failures are logged with structured context.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from backtest.hyperopt_ipc import (
    EventType,
    IPCEvent,
    PidFileLock,
    StratDoneInfo,
)

logger = logging.getLogger("polypaper.backtest.hyperopt_launcher")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Defaults — all overridable via env or per-call args
_DEFAULT_LOCK_PATH = str(_PROJECT_ROOT / ".hyperopt.lock")
_DEFAULT_STALL_SEC = int(os.getenv("HYPEROPT_SUBPROCESS_STALL_SEC", "120"))
_DEFAULT_STUDY_TIMEOUT_SEC = int(os.getenv("HYPEROPT_STUDY_TIMEOUT_SEC", "3600"))
# Extra slack on top of the study timeout before we SIGKILL a silent worker
_OUTER_GUARD_BUFFER_SEC = int(os.getenv("HYPEROPT_OUTER_GUARD_BUFFER_SEC", "120"))
# Sprint 1.3: graceful-kill escalation budgets
_SIGTERM_GRACE_SEC = float(os.getenv("HYPEROPT_SIGTERM_GRACE_SEC", "5.0"))
_SIGKILL_GRACE_SEC = float(os.getenv("HYPEROPT_SIGKILL_GRACE_SEC", "5.0"))


# ─────────────────────────────────────────────────────────────
# Phase 82e Sprint 1.3 — graceful kill escalation helper
# ─────────────────────────────────────────────────────────────

async def _terminate_subprocess(
    proc: asyncio.subprocess.Process,
    tag: str = "launcher",
    *,
    reason: str = "timeout",
) -> None:
    """Gracefully terminate a subprocess with SIGTERM → grace → SIGKILL.

    Contract
    --------
    * Safe to call multiple times (no-op if process already exited).
    * Never raises; every OS/IPC error is swallowed + logged at DEBUG.
    * Blocks at most ``_SIGTERM_GRACE_SEC + _SIGKILL_GRACE_SEC`` seconds
      (default 10s combined).

    Escalation ladder
    -----------------
    1. ``proc.terminate()``
       * POSIX  → SIGTERM (worker's atexit handler runs, PidFileLock released)
       * Windows → TerminateProcess() (effectively hard-kill; no graceful path)
    2. Wait up to ``_SIGTERM_GRACE_SEC`` for clean exit.
    3. Still alive? ``proc.kill()``
       * POSIX  → SIGKILL (immediate)
       * Windows → TerminateProcess() again
    4. Wait up to ``_SIGKILL_GRACE_SEC`` final grace.

    Called from every kill site in launcher_single, launcher_batch, and
    (via import) the Telegram /hyperopt path.
    """
    if proc.returncode is not None:
        return  # already exited
    pid = proc.pid

    # Step 1: polite terminate
    try:
        proc.terminate()
        logger.info(
            "kill_escalation[%s]: SIGTERM pid=%s (reason=%s)",
            tag,
            pid,
            reason,
        )
    except ProcessLookupError:
        return
    except Exception as e:
        logger.debug(
            "kill_escalation[%s]: terminate() raised: %s", tag, e
        )

    # Step 2: wait for graceful exit
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SIGTERM_GRACE_SEC)
        logger.info(
            "kill_escalation[%s]: pid=%s exited after SIGTERM in %.1fs",
            tag,
            pid,
            _SIGTERM_GRACE_SEC,
        )
        return
    except asyncio.TimeoutError:
        pass
    except Exception as e:
        logger.debug("kill_escalation[%s]: wait after terminate: %s", tag, e)

    # Step 3: hard kill
    try:
        proc.kill()
        logger.warning(
            "kill_escalation[%s]: SIGKILL pid=%s after %.1fs SIGTERM grace",
            tag,
            pid,
            _SIGTERM_GRACE_SEC,
        )
    except ProcessLookupError:
        return
    except Exception as e:
        logger.debug("kill_escalation[%s]: kill() raised: %s", tag, e)

    # Step 4: final grace wait
    try:
        await asyncio.wait_for(proc.wait(), timeout=_SIGKILL_GRACE_SEC)
    except asyncio.TimeoutError:
        logger.error(
            "kill_escalation[%s]: pid=%s still alive after SIGKILL + %.1fs "
            "— orphaned subprocess",
            tag,
            pid,
            _SIGKILL_GRACE_SEC,
        )
    except Exception as e:
        logger.debug("kill_escalation[%s]: wait after kill: %s", tag, e)


# ─────────────────────────────────────────────────────────────
# stderr pump (mirror of hyperopt_handler._pump_subprocess_stderr)
# ─────────────────────────────────────────────────────────────

async def _pump_stderr(proc, pid: int, tag: str) -> None:
    """Drain subprocess stderr line-by-line into the parent logger.

    The worker routes ALL Python logging to stderr because stdout is
    reserved for IPC JSON lines. Without this pump those log lines would
    be piped but never read and eventually fill the OS pipe buffer.
    """
    try:
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                return  # EOF
            text = raw.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            if "[worker:ERROR]" in text or " ERROR " in text:
                logger.error("hyperopt[%s,pid=%s] %s", tag, pid, text)
            elif "[worker:WARNING]" in text or " WARNING " in text:
                logger.warning("hyperopt[%s,pid=%s] %s", tag, pid, text)
            else:
                logger.info("hyperopt[%s,pid=%s] %s", tag, pid, text)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("stderr pump exited (%s,pid=%s): %s", tag, pid, e)


# ─────────────────────────────────────────────────────────────
# Public entrypoint
# ─────────────────────────────────────────────────────────────

async def launch_hyperopt_subprocess(
    strategy_name: str,
    n_trials: int = 30,
    source: str = "launcher",
    lock_path: Optional[str] = None,
    stall_sec: Optional[int] = None,
    study_timeout_sec: Optional[int] = None,
) -> Optional[StratDoneInfo]:
    """Run a single-strategy hyperopt in a subprocess.

    Parameters
    ----------
    strategy_name : str
        Key from ``backtest.hyperopt.PARAM_SPACES``. Unknown names are
        rejected by the worker (ERROR IPC + None return).
    n_trials : int
        Optuna trials budget. Default 30 (matches Telegram /hyperopt).
    source : str
        Tag persisted to ``hyperopt_results.source`` for provenance.
        Examples: ``"ai_brain"``, ``"tournament"``, ``"launcher"``.
    lock_path, stall_sec, study_timeout_sec : optional overrides
        Fall back to env defaults.

    Returns
    -------
    Optional[StratDoneInfo]
        Populated on success, ``None`` on any failure path. Never raises.
    """
    lock_p = lock_path or _DEFAULT_LOCK_PATH
    stall = int(stall_sec if stall_sec is not None else _DEFAULT_STALL_SEC)
    study_to = int(
        study_timeout_sec if study_timeout_sec is not None else _DEFAULT_STUDY_TIMEOUT_SEC
    )
    outer_budget = study_to + _OUTER_GUARD_BUFFER_SEC
    tag = source or "launcher"

    # ── Fast-path: if the lock file looks live, bail before spawning ──
    try:
        pre_lock = PidFileLock(Path(lock_p))
        probe = pre_lock._read_lock()  # type: ignore[attr-defined]
        if probe is not None and not pre_lock._is_stale(probe):  # type: ignore[attr-defined]
            logger.warning(
                "launcher[%s]: lock already held by pid=%s mode=%s strat=%s — "
                "skipping spawn",
                tag,
                probe.get("pid"),
                probe.get("mode"),
                probe.get("strategy"),
            )
            return None
    except Exception as e:
        logger.debug("launcher[%s] lock pre-probe failed (non-fatal): %s", tag, e)

    # ── Build command ──
    python_exe = sys.executable or "python"
    cmd = [
        python_exe,
        "-u",
        "-m",
        "backtest.hyperopt_worker",
        "--mode",
        "single",
        "--strategy",
        strategy_name,
        "--n-trials",
        str(int(n_trials)),
        "--lock-path",
        lock_p,
        "--source",
        f"launcher:{source}",
    ]

    logger.info(
        "launcher[%s]: spawning %s (trials=%d, stall=%ds, outer_budget=%ds)",
        tag,
        strategy_name,
        n_trials,
        stall,
        outer_budget,
    )

    # ── Spawn ──
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        logger.error("launcher[%s]: spawn failed: %s", tag, e, exc_info=True)
        return None

    pid = proc.pid
    logger.info("launcher[%s]: pid=%s", tag, pid)

    stderr_pump = asyncio.create_task(_pump_stderr(proc, pid, tag))
    start_ts = time.monotonic()

    done_info: Optional[StratDoneInfo] = None
    error_seen: Optional[str] = None

    async def _consume_ipc() -> None:
        nonlocal done_info, error_seen
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=stall)
            except asyncio.TimeoutError:
                logger.error(
                    "launcher[%s]: stall %ds without IPC — escalating kill pid=%s",
                    tag,
                    stall,
                    pid,
                )
                # Sprint 1.3: graceful → hard kill escalation
                await _terminate_subprocess(proc, tag, reason=f"stall_{stall}s")
                return
            if not line:
                return  # EOF

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            evt = IPCEvent.parse(raw)
            if evt is None:
                logger.debug("launcher[%s]: non-IPC stdout: %s", tag, raw[:200])
                continue

            if evt.event == EventType.STARTED.value:
                logger.info("launcher[%s]: worker STARTED msg=%s", tag, evt.message or "")
            elif evt.event == EventType.STRAT_START.value:
                logger.info(
                    "launcher[%s]: strat start %s trials=%s",
                    tag,
                    evt.strat,
                    evt.total,
                )
            elif evt.event == EventType.STATUS.value:
                logger.info(
                    "launcher[%s] status [%s]: %s",
                    tag,
                    evt.strat or "-",
                    evt.message or "",
                )
            elif evt.event == EventType.STRAT_DONE.value:
                done_info = StratDoneInfo(
                    name=str(evt.strat or strategy_name),
                    best_value=float(evt.best_value or 0.0),
                    best_params=dict(evt.best_params or {}),
                    elapsed_sec=float(evt.elapsed_sec or 0.0),
                    trial_count=int(evt.trial or 0),
                )
                logger.info(
                    "launcher[%s]: STRAT_DONE %s score=%.4f trials=%d elapsed=%.1fs",
                    tag,
                    done_info.name,
                    done_info.best_value,
                    done_info.trial_count,
                    done_info.elapsed_sec,
                )
            elif evt.event in (
                EventType.MEMORY_WARNING.value,
                EventType.MEMORY_CRITICAL.value,
                EventType.MEMORY_ABORT.value,
            ):
                logger.warning(
                    "launcher[%s] memory %s: %s",
                    tag,
                    evt.event,
                    evt.message or "",
                )
            elif evt.event == EventType.ERROR.value:
                error_seen = evt.message or "unknown"
                logger.error("launcher[%s] worker error: %s", tag, error_seen)
            elif evt.event == EventType.BATCH_DONE.value:
                # single-mode still emits BATCH_DONE at the end
                return

    try:
        await asyncio.wait_for(_consume_ipc(), timeout=outer_budget)
    except asyncio.TimeoutError:
        logger.error(
            "launcher[%s]: outer budget %ds exhausted — escalating kill pid=%s",
            tag,
            outer_budget,
            pid,
        )
        # Sprint 1.3: graceful → hard kill escalation (replaces raw SIGKILL)
        await _terminate_subprocess(
            proc, tag, reason=f"outer_budget_{outer_budget}s"
        )
    except Exception as e:
        logger.error("launcher[%s]: IPC consume crashed: %s", tag, e, exc_info=True)

    # ── Clean shutdown (Sprint 1.3: helper handles SIGTERM→SIGKILL path) ──
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(
            "launcher[%s]: proc.wait 15s timeout — escalating kill pid=%s",
            tag,
            pid,
        )
        await _terminate_subprocess(proc, tag, reason="post_consume_wait_timeout")

    try:
        if not stderr_pump.done():
            try:
                await asyncio.wait_for(stderr_pump, timeout=2.0)
            except asyncio.TimeoutError:
                stderr_pump.cancel()
    except Exception:
        pass

    elapsed = time.monotonic() - start_ts
    rc = proc.returncode
    logger.info(
        "launcher[%s]: exited rc=%s total_elapsed=%.1fs done=%s error=%s",
        tag,
        rc,
        elapsed,
        bool(done_info),
        error_seen,
    )

    return done_info


# ─────────────────────────────────────────────────────────────
# Batch entrypoint (Phase 82e Sprint 1.2)
# ─────────────────────────────────────────────────────────────

async def launch_hyperopt_batch_subprocess(
    strategies: list[str],
    n_trials: int = 50,
    source: str = "launcher",
    lock_path: Optional[str] = None,
    stall_sec: Optional[int] = None,
    study_timeout_sec: Optional[int] = None,
) -> list[StratDoneInfo]:
    """Phase 82e Sprint 1.2 -- run N strategies in a single batch subprocess.

    Used by :mod:`telegram_bot.jobs.tournament_job` so the nightly sweep
    cannot freeze the engine loop. Worker handles strategies sequentially
    with per-strat memory + priming guards; caller gets a list of
    :class:`StratDoneInfo` entries -- possibly SHORTER than ``strategies``
    if the worker aborted on memory, crashed, or timed out.

    Contract
    --------
    * Always returns a list (possibly empty). Never raises.
    * Each entry corresponds to a strategy that emitted STRAT_DONE.
      Missing strategies = failed / stalled / memory-aborted.
    * Callers should cross-reference returned names with ``strategies``
      to detect who failed.

    Outer budget
    ------------
    ``len(strategies) * study_timeout_sec + 300s`` -- gives each strat
    the same wall-clock the worker enforces internally plus a 5-minute
    slack for BATCH_DONE emission and clean shutdown.
    """
    # De-dup and strip in one pass so the worker doesn't see blanks
    clean: list[str] = []
    seen: set[str] = set()
    for s in strategies or []:
        if not s:
            continue
        s = s.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        clean.append(s)
    if not clean:
        logger.warning("launcher_batch[%s]: no strategies provided", source)
        return []

    lock_p = lock_path or _DEFAULT_LOCK_PATH
    stall = int(stall_sec if stall_sec is not None else _DEFAULT_STALL_SEC)
    study_to = int(
        study_timeout_sec if study_timeout_sec is not None else _DEFAULT_STUDY_TIMEOUT_SEC
    )
    # Each strategy bounded by study_to inside the worker; launcher gives
    # the whole batch that budget times the strat count, plus 5 min for
    # BATCH_DONE + graceful shutdown.
    outer_budget = len(clean) * study_to + 300
    tag = source or "launcher"

    # Fast-path lock probe (same as single mode)
    try:
        pre_lock = PidFileLock(Path(lock_p))
        probe = pre_lock._read_lock()  # type: ignore[attr-defined]
        if probe is not None and not pre_lock._is_stale(probe):  # type: ignore[attr-defined]
            logger.warning(
                "launcher_batch[%s]: lock already held by pid=%s mode=%s -- "
                "skipping spawn",
                tag,
                probe.get("pid"),
                probe.get("mode"),
            )
            return []
    except Exception as e:
        logger.debug("launcher_batch[%s] lock pre-probe failed: %s", tag, e)

    python_exe = sys.executable or "python"
    cmd = [
        python_exe,
        "-u",
        "-m",
        "backtest.hyperopt_worker",
        "--mode",
        "batch",
        "--strategies",
        ",".join(clean),
        "--n-trials",
        str(int(n_trials)),
        "--lock-path",
        lock_p,
        "--source",
        f"launcher:{source}",
    ]

    logger.info(
        "launcher_batch[%s]: spawning %d strats=%s "
        "(trials=%d each, stall=%ds, outer_budget=%ds)",
        tag,
        len(clean),
        ",".join(clean),
        n_trials,
        stall,
        outer_budget,
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        logger.error("launcher_batch[%s]: spawn failed: %s", tag, e, exc_info=True)
        return []

    pid = proc.pid
    logger.info("launcher_batch[%s]: pid=%s", tag, pid)

    stderr_pump = asyncio.create_task(_pump_stderr(proc, pid, f"batch:{tag}"))
    start_ts = time.monotonic()
    done_list: list[StratDoneInfo] = []

    async def _consume_ipc_batch() -> None:
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=stall)
            except asyncio.TimeoutError:
                logger.error(
                    "launcher_batch[%s]: stall %ds without IPC -- escalating kill pid=%s",
                    tag,
                    stall,
                    pid,
                )
                # Sprint 1.3: graceful → hard kill escalation
                await _terminate_subprocess(
                    proc, f"batch:{tag}", reason=f"stall_{stall}s"
                )
                return
            if not line:
                return  # EOF

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            evt = IPCEvent.parse(raw)
            if evt is None:
                logger.debug("launcher_batch[%s]: non-IPC stdout: %s", tag, raw[:200])
                continue

            if evt.event == EventType.STARTED.value:
                logger.info(
                    "launcher_batch[%s]: worker STARTED msg=%s",
                    tag,
                    evt.message or "",
                )
            elif evt.event == EventType.STRAT_START.value:
                logger.info(
                    "launcher_batch[%s]: strat %s start (idx=%s/%s)",
                    tag,
                    evt.strat,
                    evt.idx,
                    len(clean),
                )
            elif evt.event == EventType.STATUS.value:
                logger.info(
                    "launcher_batch[%s] status [%s]: %s",
                    tag,
                    evt.strat or "-",
                    evt.message or "",
                )
            elif evt.event == EventType.STRAT_DONE.value:
                info = StratDoneInfo(
                    name=str(evt.strat or "?"),
                    best_value=float(evt.best_value or 0.0),
                    best_params=dict(evt.best_params or {}),
                    elapsed_sec=float(evt.elapsed_sec or 0.0),
                    trial_count=int(evt.trial or 0),
                )
                done_list.append(info)
                logger.info(
                    "launcher_batch[%s]: STRAT_DONE %s score=%.4f trials=%d",
                    tag,
                    info.name,
                    info.best_value,
                    info.trial_count,
                )
            elif evt.event in (
                EventType.MEMORY_WARNING.value,
                EventType.MEMORY_CRITICAL.value,
                EventType.MEMORY_ABORT.value,
            ):
                logger.warning(
                    "launcher_batch[%s] memory %s: %s",
                    tag,
                    evt.event,
                    evt.message or "",
                )
            elif evt.event == EventType.ERROR.value:
                # Per-strategy error -- worker continues with the next
                logger.error(
                    "launcher_batch[%s] worker error (strat=%s): %s",
                    tag,
                    evt.strat or "-",
                    evt.message or "",
                )
            elif evt.event == EventType.BATCH_DONE.value:
                logger.info(
                    "launcher_batch[%s]: BATCH_DONE msg=%s",
                    tag,
                    evt.message or "",
                )
                return

    try:
        await asyncio.wait_for(_consume_ipc_batch(), timeout=outer_budget)
    except asyncio.TimeoutError:
        logger.error(
            "launcher_batch[%s]: outer budget %ds exhausted -- escalating kill pid=%s "
            "(%d/%d strats done before kill)",
            tag,
            outer_budget,
            pid,
            len(done_list),
            len(clean),
        )
        # Sprint 1.3: graceful → hard kill escalation (replaces raw SIGKILL)
        await _terminate_subprocess(
            proc, f"batch:{tag}", reason=f"outer_budget_{outer_budget}s"
        )
    except Exception as e:
        logger.error(
            "launcher_batch[%s]: IPC consume crashed: %s",
            tag,
            e,
            exc_info=True,
        )

    # Clean shutdown (Sprint 1.3: helper handles SIGTERM→SIGKILL path)
    try:
        await asyncio.wait_for(proc.wait(), timeout=15)
    except asyncio.TimeoutError:
        logger.warning(
            "launcher_batch[%s]: proc.wait 15s timeout -- escalating kill pid=%s",
            tag,
            pid,
        )
        await _terminate_subprocess(
            proc, f"batch:{tag}", reason="post_consume_wait_timeout"
        )
    try:
        if not stderr_pump.done():
            try:
                await asyncio.wait_for(stderr_pump, timeout=2.0)
            except asyncio.TimeoutError:
                stderr_pump.cancel()
    except Exception:
        pass

    elapsed = time.monotonic() - start_ts
    rc = proc.returncode
    logger.info(
        "launcher_batch[%s]: exited rc=%s total_elapsed=%.1fs done=%d/%d",
        tag,
        rc,
        elapsed,
        len(done_list),
        len(clean),
    )
    return done_list
