"""
backtest/hyperopt_worker.py -- Phase 82b + 82e Subprocess Worker

Standalone CLI entry point for hyperopt batch/single runs. Designed to be
launched by the parent bot process as a subprocess:

    py -3.11 -m backtest.hyperopt_worker --mode batch --n-trials 15
    py -3.11 -m backtest.hyperopt_worker --mode single --strategy TopBecker --n-trials 30

Key design choices:
  1. NO nest_asyncio. We use Optuna's ask/tell API to drive trials sequentially
     in the worker's own event loop — no threads, no nested event loops.
  2. Per-trial timeout via asyncio.wait_for(..., timeout=HYPEROPT_TRIAL_TIMEOUT).
  3. Memory guard between trials; abort gracefully if over limit.
  4. Emits IPC events (JSON lines) on stdout for parent bot to read.
  5. Acquires PID-file mutex before running; releases on exit.
  6. Writes each strategy's best params to hyperopt_results DB table.
  7. Exit code: 0 on success, 1 on fatal error, 2 on lock busy, 3 on user abort.

All output meant for parent IPC goes through stdout as JSON lines.
Logs / diagnostics go through stderr or python logging.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import logging
import os
import signal
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────
# Force UTF-8 on stdout/stderr BEFORE any IPC emit can run.
# Windows default subprocess pipe encoding is cp1252, which chokes on
# non-ASCII chars in IPC payloads (e.g. Greek Δ in param names) and
# crashes the worker with UnicodeEncodeError at hyperopt_ipc.py:101
# (`print(json.dumps(data, ensure_ascii=False), flush=True)`).
# Parent process already opens the pipe with encoding="utf-8", so this
# just aligns the child side. Safe no-op on POSIX.
# ─────────────────────────────────────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# Path setup — worker may be launched as `py -m backtest.hyperopt_worker`
# so __package__ is set. We just need CWD to be project root.
# ─────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────
# Configure logging — route stderr (not stdout, which is IPC)
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("HYPEROPT_WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [worker:%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("polypaper.hyperopt_worker")

# ─────────────────────────────────────────────────────────────
# Imports — after sys.path setup
# ─────────────────────────────────────────────────────────────

from backtest.hyperopt_ipc import (
    EventType,
    IPCEvent,
    PidFileLock,
    get_memory_snapshot,
    memory_check_action,
)

# Defer optuna import so we can emit a clean error if missing
try:
    import optuna
    from optuna.samplers import TPESampler
    from optuna.pruners import MedianPruner
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False

# ─────────────────────────────────────────────────────────────
# ENV defaults
# ─────────────────────────────────────────────────────────────
# Phase 82b.1 fix: accept BOTH legacy unsuffixed and _SEC-suffixed
# variable names to avoid the naming inconsistency that caused 15
# silent trial timeouts on 2026-04-17. Suffixed form wins if both
# are set. The _SEC-suffixed names are the canonical form going
# forward (they match hyperopt.py + deploy_phase82b.bat comments).

def _env_int(primary: str, fallback: str, default: int) -> int:
    """Read int from primary env var; fall back to legacy name; then default."""
    raw = os.getenv(primary)
    if raw is None or raw == "":
        raw = os.getenv(fallback)
    try:
        return int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default

TRIAL_TIMEOUT_SEC = _env_int("HYPEROPT_TRIAL_TIMEOUT_SEC", "HYPEROPT_TRIAL_TIMEOUT", 90)
STUDY_TIMEOUT_SEC = _env_int("HYPEROPT_STUDY_TIMEOUT_SEC", "HYPEROPT_STUDY_TIMEOUT", 1800)
MEMORY_GUARD_ENABLED = os.getenv("HYPEROPT_MEMORY_GUARD", "1") == "1"
PROGRESS_HEARTBEAT_SEC = _env_int("HYPEROPT_HEARTBEAT_SEC", "HYPEROPT_HEARTBEAT", 30)
# Sprint 4.1 — opt-in concurrent trials via asyncio.gather. Default 1 keeps
# the prior sequential behavior (safest, matches MedianPruner assumptions).
# Values >1 ask the study for N trials, run their backtests concurrently,
# then tell results in order. Expect roughly linear speedup until the DB
# becomes the bottleneck; tune in tandem with HYPEROPT_MEMORY_ABORT_MB since
# each trial holds its own ReplayEngine in memory.
N_JOBS = max(1, _env_int("HYPEROPT_N_JOBS", "HYPEROPT_JOBS", 1))

# Phase 82b.2 fix: cap backtest scope so trials finish within TRIAL_TIMEOUT_SEC.
# Without this, each trial replays the ENTIRE market history (~10GB) and every
# trial pruned at timeout → empty Best params. Default 200 recent markets is
# enough for TPE sampler to differentiate param sets while staying under 60s.
LAST_N = _env_int("HYPEROPT_LAST_N", "HYPEROPT_LAST", 200)
MAX_MARKETS = _env_int("HYPEROPT_MAX_MARKETS", "HYPEROPT_MAX_MKT", 0)  # 0 = unlimited (use LAST_N instead)

# Exit codes
EXIT_OK = 0
EXIT_FATAL = 1
EXIT_LOCK_BUSY = 2
EXIT_ABORTED = 3

# ─────────────────────────────────────────────────────────────
# Graceful shutdown flag (set by signal handler)
# ─────────────────────────────────────────────────────────────

_SHUTDOWN = False


def _signal_handler(signum, frame):
    global _SHUTDOWN
    _SHUTDOWN = True
    logger.warning(f"worker received signal {signum} — flagging shutdown")


# ─────────────────────────────────────────────────────────────
# Optimizer core
# ─────────────────────────────────────────────────────────────


async def _run_strategy(
    db,
    pipeline,
    strategy_name: str,
    n_trials: int,
    metric: str,
    strat_idx: int,
    strats_total: int,
    mode: str,
    source: Optional[str] = None,
    asset: str = "",
    timeframe: str = "",
) -> Optional[dict]:
    """
    Run Optuna hyperopt for one strategy. Uses study.ask/tell API to drive
    trials in-loop (no threads, no nest_asyncio).

    Returns a dict summary on success, or None on abort.
    """
    from backtest.hyperopt import HyperOptConfig, HyperOptResult, PARAM_SPACES

    # Sanity — unknown strategy -> skip
    if strategy_name not in PARAM_SPACES:
        IPCEvent(
            event=EventType.ERROR.value,
            strat=strategy_name,
            message=f"unknown strategy (not in PARAM_SPACES)",
        ).emit()
        return None

    cfg = HyperOptConfig(
        strategy_name=strategy_name,
        n_trials=n_trials,
        metric=metric,
        timeout_s=STUDY_TIMEOUT_SEC,
        last_n=LAST_N,
        max_markets=MAX_MARKETS,
        # Phase 82e Sprint 5 (FINAL): slice filter for granular per-asset
        # per-tf optimization (Fusion×29 use-case).
        asset_filter=(asset or "").strip().upper(),
        timeframe_filter=(timeframe or "").strip(),
    )
    logger.info(
        f"[{strategy_name}] cfg: trial_timeout={TRIAL_TIMEOUT_SEC}s "
        f"study_timeout={STUDY_TIMEOUT_SEC}s "
        f"last_n={LAST_N} max_markets={MAX_MARKETS} metric={metric}"
    )
    # Phase 82b.3: emit STATUS over IPC so the parent handler — which
    # only reads stdout, not stderr — can log the exact config values
    # the subprocess is running with. This gave us irrefutable proof
    # last_n=200 was actually reaching the worker.
    IPCEvent(
        event=EventType.STATUS.value,
        strat=strategy_name,
        message=(
            f"cfg trial_timeout={TRIAL_TIMEOUT_SEC}s "
            f"study_timeout={STUDY_TIMEOUT_SEC}s "
            f"last_n={LAST_N} max_markets={MAX_MARKETS} "
            f"metric={metric} n_trials={n_trials}"
        ),
    ).emit()

    # Sampler + pruner
    sampler = TPESampler(seed=42, n_startup_trials=min(10, max(3, n_trials // 3)))
    pruner = MedianPruner(n_startup_trials=5, n_warmup_steps=3)
    study = optuna.create_study(
        study_name=f"hopt_{strategy_name}_{strat_idx}",
        direction="maximize",
        sampler=sampler,
        pruner=pruner,
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    IPCEvent(
        event=EventType.STRAT_START.value,
        strat=strategy_name,
        idx=strat_idx,
        total=n_trials,
    ).emit()

    strat_start = datetime.utcnow()
    completed = 0
    pruned = 0
    failed = 0
    memory_abort = False

    # ─── Phase 82b.5: Prime discovery cache OUTSIDE the per-trial timeout ───
    # Phase 82b.3 added the in-memory cache, but discovery still ran INSIDE
    # the first trial's 300s wait_for. If a discovery pass takes longer than
    # TRIAL_TIMEOUT_SEC (real-world: 10GB ob_snapshots + live writes push it
    # past 5 minutes), the coroutine is cancelled mid-query, the cache is
    # never populated, and every subsequent trial re-runs discovery and
    # hits the same timeout. Observed symptom: 5/5 trials pruned, empty
    # Best params, Score=0.0000 despite study_timeout=3600s.
    #
    # Fix: prime the pipeline's _windows_cache BEFORE the first trial,
    # bounded only by STUDY_TIMEOUT_SEC (3600s default). After priming
    # succeeds every trial's _get_cached_windows() becomes an O(1) dict
    # lookup and finishes well under TRIAL_TIMEOUT_SEC.
    # Sprint 2.3 — emit STATUS BEFORE priming so /diagnose shows
    # "what stage the worker is stuck in" even if priming hangs.
    # Sprint 3.3 — guard priming with a memory check. A single discovery
    # pass can balloon RSS when the GROUP BY result set is huge (10GB DB
    # + long retention). Before this, only per-trial memory was checked;
    # priming ran unprotected. If we're already at 'abort' before priming
    # even starts we skip this strategy instead of risking OOM.
    if MEMORY_GUARD_ENABLED:
        pre_action = memory_check_action()
        pre_proc_mb, pre_sys_pct = get_memory_snapshot()
        if pre_action == "abort":
            IPCEvent(
                event=EventType.MEMORY_ABORT.value,
                strat=strategy_name,
                trial=0,
                memory_mb=pre_proc_mb,
                sys_mem_pct=pre_sys_pct,
                message="abort threshold reached BEFORE discovery",
            ).emit()
            return {
                "name": strategy_name,
                "best_value": 0.0,
                "best_params": {},
                "completed": 0,
                "pruned": 0,
                "failed": 0,
                "elapsed_sec": 0.0,
                "memory_abort": True,
                "saved_id": None,
            }
        if pre_action == "critical":
            # Don't bail — warn the operator, force GC, and proceed. Still
            # cheaper than a full skip if the GROUP BY might fit.
            gc.collect()
            IPCEvent(
                event=EventType.MEMORY_CRITICAL.value,
                strat=strategy_name,
                trial=0,
                memory_mb=pre_proc_mb,
                sys_mem_pct=pre_sys_pct,
                message="critical pre-discovery; forced GC",
            ).emit()
    else:
        pre_proc_mb, pre_sys_pct = 0.0, 0.0

    IPCEvent(
        event=EventType.STATUS.value,
        strat=strategy_name,
        message=(
            f"discovery starting: last_n={LAST_N} "
            f"max_markets={MAX_MARKETS} "
            f"(study_timeout={STUDY_TIMEOUT_SEC}s) "
            f"mem={pre_proc_mb:.0f}MB sys={pre_sys_pct:.0f}%"
        ),
    ).emit()
    prime_start = datetime.utcnow()
    try:
        window_count = await asyncio.wait_for(
            pipeline.prime_windows_cache(cfg),
            timeout=STUDY_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        prime_elapsed = (datetime.utcnow() - prime_start).total_seconds()
        logger.error(
            f"[{strategy_name}] discovery priming exceeded "
            f"{STUDY_TIMEOUT_SEC}s — skipping strategy"
        )
        IPCEvent(
            event=EventType.ERROR.value,
            strat=strategy_name,
            message=(
                f"discovery priming timeout after {prime_elapsed:.0f}s "
                f"(study_timeout={STUDY_TIMEOUT_SEC}s)"
            ),
        ).emit()
        IPCEvent(
            event=EventType.STRAT_DONE.value,
            strat=strategy_name,
            trial=0,
            total=n_trials,
            best_value=0.0,
            best_params={},
            elapsed_sec=prime_elapsed,
        ).emit()
        return {
            "name": strategy_name,
            "best_value": 0.0,
            "best_params": {},
            "completed": 0,
            "pruned": 0,
            "failed": 0,
            "elapsed_sec": prime_elapsed,
            "memory_abort": False,
            "saved_id": None,
        }
    except Exception as e:
        prime_elapsed = (datetime.utcnow() - prime_start).total_seconds()
        logger.error(
            f"[{strategy_name}] discovery priming failed: {e}", exc_info=True)
        IPCEvent(
            event=EventType.ERROR.value,
            strat=strategy_name,
            message=f"discovery priming crashed: {type(e).__name__}: {e}",
        ).emit()
        return {
            "name": strategy_name,
            "best_value": 0.0,
            "best_params": {},
            "completed": 0,
            "pruned": 0,
            "failed": 0,
            "elapsed_sec": prime_elapsed,
            "memory_abort": False,
            "saved_id": None,
        }

    prime_elapsed = (datetime.utcnow() - prime_start).total_seconds()
    # Sprint 3.3 — post-priming memory check. Discovery can balloon RSS if
    # the GROUP BY rows-returned is unexpectedly large. If we're now at
    # 'abort', fail fast before the trial loop consumes another 100-300MB
    # per trial. Post-check uses the same thresholds as the pre-check.
    post_proc_mb, post_sys_pct = get_memory_snapshot()
    if MEMORY_GUARD_ENABLED and memory_check_action() == "abort":
        IPCEvent(
            event=EventType.MEMORY_ABORT.value,
            strat=strategy_name,
            trial=0,
            memory_mb=post_proc_mb,
            sys_mem_pct=post_sys_pct,
            message=(
                f"abort threshold reached AFTER discovery "
                f"(was {pre_proc_mb:.0f}MB → {post_proc_mb:.0f}MB, "
                f"delta={post_proc_mb - pre_proc_mb:.0f}MB)"
            ),
        ).emit()
        return {
            "name": strategy_name,
            "best_value": 0.0,
            "best_params": {},
            "completed": 0,
            "pruned": 0,
            "failed": 0,
            "elapsed_sec": prime_elapsed,
            "memory_abort": True,
            "saved_id": None,
        }

    logger.info(
        f"[{strategy_name}] priming done: {window_count} windows "
        f"in {prime_elapsed:.1f}s mem={post_proc_mb:.0f}MB"
    )
    IPCEvent(
        event=EventType.STATUS.value,
        strat=strategy_name,
        message=(
            f"discovery primed: {window_count} windows "
            f"in {prime_elapsed:.1f}s (cache ready) "
            f"mem={post_proc_mb:.0f}MB Δ{post_proc_mb - pre_proc_mb:+.0f}MB"
        ),
    ).emit()

    if window_count == 0:
        logger.warning(
            f"[{strategy_name}] discovery returned 0 windows — skipping"
        )
        IPCEvent(
            event=EventType.ERROR.value,
            strat=strategy_name,
            message=(
                f"no market windows discovered "
                f"(last_n={LAST_N}, asset='', tf='')"
            ),
        ).emit()
        IPCEvent(
            event=EventType.STRAT_DONE.value,
            strat=strategy_name,
            trial=0,
            total=n_trials,
            best_value=0.0,
            best_params={},
            elapsed_sec=prime_elapsed,
        ).emit()
        return {
            "name": strategy_name,
            "best_value": 0.0,
            "best_params": {},
            "completed": 0,
            "pruned": 0,
            "failed": 0,
            "elapsed_sec": prime_elapsed,
            "memory_abort": False,
            "saved_id": None,
        }

    # ─── Sprint 4.1 — Parallel trial execution via asyncio.gather ────────
    # Two code paths:
    #   N_JOBS == 1 : sequential (original behavior, byte-for-byte identical).
    #   N_JOBS >  1 : batched; ask N_JOBS trials, run via gather(..., return_exceptions=True),
    #                  tell in order, emit TRIAL_DONE per trial. Memory check once per batch.
    # We keep the sequential loop verbatim because:
    #   (a) parity with every prior run on record,
    #   (b) MedianPruner's intermediate-reporting semantics behave best when
    #       trials complete strictly in order, which is what single-trial
    #       batches give us for free.
    # ─────────────────────────────────────────────────────────────────────

    if N_JOBS <= 1:
        # === Sequential path (default) ============================
        for trial_num in range(n_trials):
            if _SHUTDOWN:
                logger.info(f"shutdown flag set; stopping {strategy_name} at trial {trial_num}")
                break

            # Memory guard BEFORE starting next trial
            if MEMORY_GUARD_ENABLED:
                action = memory_check_action()
                if action == "abort":
                    proc_mb, sys_pct = get_memory_snapshot()
                    IPCEvent(
                        event=EventType.MEMORY_ABORT.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                        message="memory abort threshold exceeded",
                    ).emit()
                    memory_abort = True
                    break
                elif action == "critical":
                    gc.collect()
                    proc_mb, sys_pct = get_memory_snapshot()
                    IPCEvent(
                        event=EventType.MEMORY_CRITICAL.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                    ).emit()
                    # After GC, re-check: if still critical, prune this trial
                    if memory_check_action() == "critical":
                        pruned += 1
                        continue
                elif action == "warn":
                    proc_mb, sys_pct = get_memory_snapshot()
                    # Emit only once per cooldown to avoid spam (parent rate-limits anyway)
                    IPCEvent(
                        event=EventType.MEMORY_WARNING.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                    ).emit()

            # Sprint 2.3 — emit STATUS at trial start so /diagnose distinguishes
            # "worker frozen inside trial N" from "worker done, waiting for tell".
            # Emit on first trial + every 5th trial to avoid stdout spam (parent
            # also rate-limits). TRIAL_DONE already fires after each trial.
            if trial_num == 0 or (trial_num + 1) % 5 == 0:
                IPCEvent(
                    event=EventType.STATUS.value,
                    strat=strategy_name,
                    message=(
                        f"trial {trial_num + 1}/{n_trials} starting "
                        f"(timeout={TRIAL_TIMEOUT_SEC}s)"
                    ),
                ).emit()

            # Ask Optuna for next trial
            try:
                trial = study.ask()
            except Exception as e:
                logger.error(f"study.ask failed: {e}")
                failed += 1
                break

            # Run trial with per-trial timeout
            try:
                value = await asyncio.wait_for(
                    pipeline._run_trial(trial, cfg),
                    timeout=TRIAL_TIMEOUT_SEC,
                )
                # Guard against NaN / -inf
                if value is None or not isinstance(value, (int, float)):
                    value = float("-inf")
                study.tell(trial, value)
                completed += 1

            except asyncio.TimeoutError:
                study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                pruned += 1
                IPCEvent(
                    event=EventType.TIMEOUT.value,
                    strat=strategy_name,
                    trial=trial_num,
                    message=f"trial exceeded {TRIAL_TIMEOUT_SEC}s",
                ).emit()

            except optuna.exceptions.TrialPruned:
                study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                pruned += 1

            except Exception as e:
                # Single trial failure — log and continue
                logger.warning(f"trial {trial_num} failed: {e}")
                try:
                    study.tell(trial, state=optuna.trial.TrialState.FAIL)
                except Exception:
                    pass
                failed += 1

            # Emit progress event
            proc_mb, sys_pct = get_memory_snapshot()
            try:
                best_val = (
                    study.best_value
                    if study.best_trial is not None
                    else None
                )
            except ValueError:
                # No trials completed yet → study.best_value raises
                best_val = None

            IPCEvent(
                event=EventType.TRIAL_DONE.value,
                strat=strategy_name,
                trial=trial_num + 1,
                total=n_trials,
                best_value=best_val,
                memory_mb=proc_mb,
                sys_mem_pct=sys_pct,
            ).emit()

            # Study timeout check
            elapsed = (datetime.utcnow() - strat_start).total_seconds()
            if elapsed > STUDY_TIMEOUT_SEC:
                logger.warning(
                    f"study timeout reached ({elapsed:.0f}s > {STUDY_TIMEOUT_SEC}s); stopping"
                )
                break

    else:
        # === Parallel path (N_JOBS > 1) ==========================
        # Ask N_JOBS trials, gather concurrently, tell in order.
        # ask() / tell() are not thread-safe but we're on a single event loop,
        # so we call them between awaits — never interleaved with anything.
        IPCEvent(
            event=EventType.STATUS.value,
            strat=strategy_name,
            message=(
                f"parallel mode enabled: n_jobs={N_JOBS} "
                f"(trial_timeout={TRIAL_TIMEOUT_SEC}s each)"
            ),
        ).emit()

        trial_num = 0
        while trial_num < n_trials:
            if _SHUTDOWN:
                logger.info(
                    f"shutdown flag set; stopping {strategy_name} at trial {trial_num}"
                )
                break

            # Memory guard ONCE per batch (cheaper than per-trial; accepts
            # small overrun risk up to N_JOBS trials' worth of RSS).
            if MEMORY_GUARD_ENABLED:
                action = memory_check_action()
                if action == "abort":
                    proc_mb, sys_pct = get_memory_snapshot()
                    IPCEvent(
                        event=EventType.MEMORY_ABORT.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                        message=f"memory abort threshold exceeded (pre-batch, n_jobs={N_JOBS})",
                    ).emit()
                    memory_abort = True
                    break
                elif action == "critical":
                    gc.collect()
                    proc_mb, sys_pct = get_memory_snapshot()
                    IPCEvent(
                        event=EventType.MEMORY_CRITICAL.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                    ).emit()
                    if memory_check_action() == "critical":
                        # Prune a single-slot batch to bleed pressure; skip
                        # full parallel batch since N trials would double-compound.
                        pruned += 1
                        trial_num += 1
                        continue
                elif action == "warn":
                    proc_mb, sys_pct = get_memory_snapshot()
                    IPCEvent(
                        event=EventType.MEMORY_WARNING.value,
                        strat=strategy_name,
                        trial=trial_num,
                        memory_mb=proc_mb,
                        sys_mem_pct=sys_pct,
                    ).emit()

            batch_size = min(N_JOBS, n_trials - trial_num)
            IPCEvent(
                event=EventType.STATUS.value,
                strat=strategy_name,
                message=(
                    f"batch {trial_num + 1}-{trial_num + batch_size}/{n_trials} "
                    f"n_jobs={batch_size} (timeout={TRIAL_TIMEOUT_SEC}s)"
                ),
            ).emit()

            # Ask Optuna for all trials in this batch. Done sequentially
            # in a tight loop — no awaits, so TPE sampler state stays coherent.
            trials_asked: list = []  # list[tuple[int, optuna.Trial]]
            for slot in range(batch_size):
                try:
                    t = study.ask()
                    trials_asked.append((trial_num + slot, t))
                except Exception as e:
                    logger.error(f"study.ask failed at slot {slot}: {e}")
                    failed += 1
                    break  # stop asking; run what we got

            if not trials_asked:
                break

            # Build coroutines with per-trial timeout, then gather.
            async def _run_one(_trial, _tnum: int):
                # Wrapped wait_for so asyncio.gather sees TimeoutError
                # instead of cancellation propagating into the gather.
                return await asyncio.wait_for(
                    pipeline._run_trial(_trial, cfg),
                    timeout=TRIAL_TIMEOUT_SEC,
                )

            coros = [_run_one(t, idx) for idx, t in trials_asked]

            try:
                results = await asyncio.gather(*coros, return_exceptions=True)
            except Exception as e:
                # asyncio.gather(return_exceptions=True) should never raise,
                # but guard anyway (e.g. if _SHUTDOWN-triggered cancel bubbles).
                logger.error(f"gather crashed: {e}")
                # Mark all asked trials as failed so study stays consistent.
                for _, t in trials_asked:
                    try:
                        study.tell(t, state=optuna.trial.TrialState.FAIL)
                    except Exception:
                        pass
                failed += len(trials_asked)
                break

            # Tell results in ask() order; emit TRIAL_DONE per slot.
            for (idx, trial), result in zip(trials_asked, results):
                if isinstance(result, asyncio.TimeoutError):
                    try:
                        study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                    except Exception:
                        pass
                    pruned += 1
                    IPCEvent(
                        event=EventType.TIMEOUT.value,
                        strat=strategy_name,
                        trial=idx,
                        message=f"trial exceeded {TRIAL_TIMEOUT_SEC}s",
                    ).emit()
                elif isinstance(result, optuna.exceptions.TrialPruned):
                    try:
                        study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                    except Exception:
                        pass
                    pruned += 1
                elif isinstance(result, BaseException):
                    logger.warning(f"trial {idx} failed: {result!r}")
                    try:
                        study.tell(trial, state=optuna.trial.TrialState.FAIL)
                    except Exception:
                        pass
                    failed += 1
                else:
                    value = result
                    if value is None or not isinstance(value, (int, float)):
                        value = float("-inf")
                    try:
                        study.tell(trial, value)
                        completed += 1
                    except Exception as e:
                        logger.warning(f"study.tell failed for trial {idx}: {e}")
                        failed += 1

                # TRIAL_DONE per slot (each counts 1-by-1 so the parent
                # progress bar advances smoothly even in batched mode).
                proc_mb, sys_pct = get_memory_snapshot()
                try:
                    best_val = (
                        study.best_value
                        if study.best_trial is not None
                        else None
                    )
                except ValueError:
                    best_val = None

                IPCEvent(
                    event=EventType.TRIAL_DONE.value,
                    strat=strategy_name,
                    trial=idx + 1,
                    total=n_trials,
                    best_value=best_val,
                    memory_mb=proc_mb,
                    sys_mem_pct=sys_pct,
                ).emit()

            trial_num += len(trials_asked)

            # Study timeout check between batches
            elapsed = (datetime.utcnow() - strat_start).total_seconds()
            if elapsed > STUDY_TIMEOUT_SEC:
                logger.warning(
                    f"study timeout reached ({elapsed:.0f}s > {STUDY_TIMEOUT_SEC}s); stopping"
                )
                break

    # Strategy done — collect results
    elapsed = (datetime.utcnow() - strat_start).total_seconds()

    try:
        best_trial = study.best_trial
        best_params = dict(best_trial.params) if best_trial else {}
        best_value = best_trial.value if best_trial else 0.0
    except ValueError:
        # No successful trials at all
        best_trial = None
        best_params = {}
        best_value = 0.0

    # Save to DB
    saved_id = None
    if best_trial is not None:
        try:
            result = HyperOptResult(
                strategy_name=strategy_name,
                best_params=best_params,
                best_score=float(best_value),
                metric=metric,
                n_trials=n_trials,
                n_completed=completed,
                n_pruned=pruned,
                train_score=float(best_value),
                test_score=0.0,  # worker skips test split to save time; set 0
                overfit_ratio=0.0,
                duration_s=elapsed,
                timestamp=strat_start.isoformat(),
                # Phase 82e Sprint 5 (FINAL): tag the row so the apply
                # callback can target (strategy_type, asset, tf) precisely.
                asset=(asset or "").strip().upper(),
                timeframe=(timeframe or "").strip(),
            )
            # Phase 82e: honor --source override so launcher-initiated runs
            # (ai_brain, tournament, telegram) get distinct provenance tags
            _src = source if source else f"worker:{mode}"
            saved_id = await result.save_to_db(db, source=_src)
        except Exception as e:
            logger.error(f"DB save failed for {strategy_name}: {e}")

    IPCEvent(
        event=EventType.STRAT_DONE.value,
        strat=strategy_name,
        trial=completed,
        total=n_trials,
        best_value=float(best_value),
        best_params=best_params,
        elapsed_sec=elapsed,
    ).emit()

    return {
        "name": strategy_name,
        "best_value": float(best_value),
        "best_params": best_params,
        "completed": completed,
        "pruned": pruned,
        "failed": failed,
        "elapsed_sec": elapsed,
        "memory_abort": memory_abort,
        "saved_id": saved_id,
    }


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="hyperopt_worker",
        description="PolyPaper Bot Phase 82b hyperopt subprocess worker",
    )
    p.add_argument(
        "--mode",
        required=True,
        choices=("single", "batch"),
        help="single: one strategy; batch: all PARAM_SPACES keys",
    )
    p.add_argument(
        "--strategy",
        default="",
        help="strategy name (required for single mode)",
    )
    p.add_argument(
        "--strategies",
        default="",
        help="comma-separated list for batch subset (default: all PARAM_SPACES)",
    )
    p.add_argument(
        "--n-trials",
        type=int,
        default=0,
        help="trials per strategy (0 = env default)",
    )
    p.add_argument(
        "--metric",
        default=os.getenv("HYPEROPT_METRIC", "sharpe_ratio"),
    )
    p.add_argument(
        "--db-path",
        default=os.getenv(
            "DB_PATH",
            str(_PROJECT_ROOT / "data_store" / "polypaper.db"),
        ),
    )
    p.add_argument(
        "--lock-path",
        default=os.getenv("HYPEROPT_LOCK_PATH", str(_PROJECT_ROOT / ".hyperopt.lock")),
    )
    p.add_argument(
        "--no-lock",
        action="store_true",
        help="bypass PID-file mutex (tests only)",
    )
    # Phase 82e: subprocess caller identifies itself so DB rows carry
    # "launcher:ai_brain" / "launcher:tournament" / etc. instead of the
    # generic "worker:single" label that makes /hyperopt_status harder
    # to read when multiple entry points write in parallel.
    p.add_argument(
        "--source",
        default="",
        help="provenance tag written to hyperopt_results.source (default: worker:<mode>)",
    )
    # ── Phase 82e Sprint 5 (FINAL): Fusion×29 granular apply ──
    # When provided these flow into HyperOptConfig.asset_filter /
    # timeframe_filter (restricts discovery to the matching slice) AND
    # into HyperOptResult.asset/timeframe (tags the DB row so the apply
    # path can update every live strategy matching (type, asset, tf) —
    # solving the Fusion×29 "only rows[0] applied" bug). Empty → unfiltered.
    p.add_argument(
        "--asset",
        default="",
        help="asset filter (e.g. BTC, ETH, SOL). Empty = all assets.",
    )
    p.add_argument(
        "--timeframe",
        default="",
        help="timeframe filter (e.g. 5m, 15m, 1h). Empty = all timeframes.",
    )
    return p.parse_args()


def _load_live_strategy_types(db_path: str) -> Optional[set[str]]:
    """Phase 82e Sprint 4.5 — apply-filter helper.

    Returns the set of strategy_type values currently present in the
    ``strategies`` table. Used by batch-mode default path to skip strategy
    types that have NO live DB instance — their hyperopt best_params cannot
    be applied (UPDATE strategies WHERE strategy_type=? matches 0 rows).

    Returns None on any failure → caller must treat as "no filter", i.e.
    optimize all PARAM_SPACES keys. This keeps the worker running even if
    the DB is locked or schema drifted; better to waste a few trials than
    to fail the whole batch.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=5.0)
        try:
            rows = conn.execute(
                "SELECT DISTINCT strategy_type "
                "FROM strategies "
                "WHERE strategy_type IS NOT NULL "
                "  AND strategy_type != ''"
            ).fetchall()
            return {r[0] for r in rows if r[0]}
        finally:
            conn.close()
    except Exception as e:
        logger.warning(
            "apply-filter: live-types probe failed (%s); falling back to no filter",
            e,
        )
        return None


async def _main_async(args: argparse.Namespace) -> int:
    if not OPTUNA_AVAILABLE:
        IPCEvent(
            event=EventType.ERROR.value,
            message="optuna not installed; cannot run hyperopt",
        ).emit()
        return EXIT_FATAL

    # ── Sprint 3.2: Acquire lock FIRST, then run everything inside a single
    # try/finally that guarantees release on any exit path. Previously the
    # worker had FIVE scattered `lock.release()` calls sprinkled across
    # error branches (argparse failure, DB init failure, pipeline init
    # failure, inner batch loop exception, normal cleanup). Miss any path
    # and the lock file is orphaned. With one finally and atexit safety net
    # the lock is always released — even on unhandled exceptions.
    lock = None
    if not args.no_lock:
        lock = PidFileLock(Path(args.lock_path))
        acquired = lock.acquire(
            mode=args.mode,
            strategy=(args.strategy if args.mode == "single" else "<batch>"),
        )
        if not acquired:
            status = lock.status() or {}
            IPCEvent(
                event=EventType.ERROR.value,
                message=(
                    f"another hyperopt is running "
                    f"(pid={status.get('pid')} mode={status.get('mode')})"
                ),
            ).emit()
            return EXIT_LOCK_BUSY

    # Everything below runs under the lock. Single finally covers all exits.
    db = None
    pipeline = None
    exit_code = EXIT_OK
    summaries: list = []
    strat_list: list[str] = []

    try:
        from backtest.hyperopt import PARAM_SPACES

        if args.mode == "batch":
            if args.strategies:
                # Explicit user override — honor as-is (discovery / research mode).
                # Filter bypass: user may deliberately want to optimize a
                # backtest-only type to gather data before creating an instance.
                strat_list = [s.strip() for s in args.strategies.split(",") if s.strip()]
            else:
                # Phase 82e Sprint 4.5 — apply-filter default.
                # Only optimize strategy_types that have >=1 row in the
                # `strategies` table; otherwise hyperopt best_params have
                # no destination (UPDATE ... WHERE strategy_type=? → 0 rows).
                all_spaces = list(PARAM_SPACES.keys())
                live_types = _load_live_strategy_types(args.db_path)
                if live_types is None:
                    # Probe failed — safest fallback is no filter so batch
                    # at least runs; user will see old behaviour this once.
                    strat_list = all_spaces
                    logger.warning(
                        "apply-filter disabled (probe failed); optimizing all %d types",
                        len(all_spaces),
                    )
                else:
                    strat_list = [s for s in all_spaces if s in live_types]
                    skipped = [s for s in all_spaces if s not in live_types]
                    if skipped:
                        # Emit a STATUS event so parent / log can see which types
                        # were pruned. STATUS is the closest non-error channel
                        # available in EventType; handler already tolerates it.
                        try:
                            IPCEvent(
                                event=EventType.STATUS.value,
                                message=(
                                    f"apply-filter: skipped {len(skipped)} type(s) "
                                    f"with no DB instance: {','.join(skipped)}"
                                ),
                            ).emit()
                        except Exception:
                            pass
                        logger.info(
                            "apply-filter: optimizing %d/%d strategy types; "
                            "skipped (no live instance): %s",
                            len(strat_list), len(all_spaces),
                            ",".join(skipped),
                        )
                    if not strat_list:
                        # Edge case: live_types probe returned empty set.
                        # Don't run a zero-strategy batch — fall back to all.
                        logger.warning(
                            "apply-filter resolved to empty list; falling back to all"
                        )
                        strat_list = all_spaces
            default_trials = int(os.getenv("HYPEROPT_BATCH_TRIALS", "15"))
        else:  # single
            if not args.strategy:
                IPCEvent(
                    event=EventType.ERROR.value,
                    message="--strategy required for single mode",
                ).emit()
                return EXIT_FATAL
            strat_list = [args.strategy]
            default_trials = int(os.getenv("HYPEROPT_SINGLE_TRIALS", "30"))

        n_trials = args.n_trials if args.n_trials > 0 else default_trials

        IPCEvent(
            event=EventType.STARTED.value,
            total=len(strat_list),
            trial=n_trials,
            message=f"mode={args.mode} strats={len(strat_list)} trials={n_trials}",
        ).emit()

        # Open DB
        from db.database import Database

        db = Database(args.db_path)
        try:
            await db.initialize()
        except Exception as e:
            IPCEvent(
                event=EventType.ERROR.value,
                message=f"db initialize failed: {e}",
            ).emit()
            return EXIT_FATAL

        # Build pipeline
        try:
            from backtest.hyperopt import HyperOptPipeline
            pipeline = HyperOptPipeline(db)
        except Exception as e:
            IPCEvent(
                event=EventType.ERROR.value,
                message=f"pipeline init failed: {e}",
            ).emit()
            return EXIT_FATAL

        try:
            for idx, strat_name in enumerate(strat_list):
                if _SHUTDOWN:
                    logger.warning(
                        "shutdown flag set; aborting remaining strategies"
                    )
                    exit_code = EXIT_ABORTED
                    break
                summary = await _run_strategy(
                    db=db,
                    pipeline=pipeline,
                    strategy_name=strat_name,
                    n_trials=n_trials,
                    metric=args.metric,
                    strat_idx=idx,
                    strats_total=len(strat_list),
                    mode=args.mode,
                    source=(args.source or None),
                    asset=getattr(args, "asset", "") or "",
                    timeframe=getattr(args, "timeframe", "") or "",
                )
                if summary is not None:
                    summaries.append(summary)
                    if summary.get("memory_abort"):
                        logger.warning(
                            "memory abort in %s; stopping batch",
                            strat_name,
                        )
                        exit_code = EXIT_ABORTED
                        break

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"worker fatal: {e}\n{tb}")
            IPCEvent(
                event=EventType.ERROR.value,
                message=f"fatal: {type(e).__name__}: {e}",
            ).emit()
            exit_code = EXIT_FATAL

        # BATCH_DONE emit
        try:
            total_trials = sum(
                s.get("completed", 0) + s.get("pruned", 0) for s in summaries
            )
            IPCEvent(
                event=EventType.BATCH_DONE.value,
                total=len(strat_list),
                trial=total_trials,
                elapsed_sec=sum(s.get("elapsed_sec", 0.0) for s in summaries),
                best_params={
                    s["name"]: s["best_params"]
                    for s in summaries
                    if s.get("best_params")
                },
                message=(
                    f"completed={len(summaries)}/{len(strat_list)} "
                    f"exit={exit_code}"
                ),
            ).emit()
        except Exception as e:
            logger.warning(f"failed to emit BATCH_DONE: {e}")

    finally:
        # Sprint 3.2: unified cleanup — lock release is the LAST step so that
        # pipeline + db cleanup happens before another worker can acquire.
        if pipeline is not None:
            try:
                await pipeline.close()
            except Exception as _pc:
                logger.debug("pipeline close failed: %s", _pc)
        if db is not None:
            try:
                await db.close()
            except Exception as _dc:
                logger.debug("db close failed: %s", _dc)
        if lock is not None:
            try:
                lock.release()
            except Exception as _lc:
                logger.warning("lock release failed: %s", _lc)

    return exit_code


def main() -> int:
    # Handle Ctrl-C and SIGTERM gracefully
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        # Some platforms / threads can't install signal handlers
        pass

    args = _parse_args()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logger.warning("worker KeyboardInterrupt")
        return EXIT_ABORTED
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"worker top-level fatal: {e}\n{tb}")
        try:
            IPCEvent(
                event=EventType.ERROR.value,
                message=f"top-level: {type(e).__name__}: {e}",
            ).emit()
        except Exception:
            pass
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
