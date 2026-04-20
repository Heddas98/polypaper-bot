"""
backtest/hyperopt_ipc.py — Phase 82b IPC + Mutex Foundation

Provides the shared building blocks used by both the parent bot process
(telegram_bot/handlers/hyperopt_handler.py) and the worker subprocess
(backtest/hyperopt_worker.py):

1. IPCEvent + EventType — JSON-line wire format for worker -> parent
2. PidFileLock           — cross-process mutex (file-based with PID liveness check)
3. HyperoptProgressState — parent-side in-memory state (read by /hyperopt_status)
4. Memory snapshot utils — used by worker memory guard
5. Formatting helpers    — hybrid ETA + memory status strings

All timestamps in IPC events use naive UTC ISO-8601 (datetime.utcnow().isoformat()).
Parent converts to local time for display.

Env vars (read at module import / as defaults, all overridable):
    HYPEROPT_LOCK_PATH            default: .hyperopt.lock
    HYPEROPT_LOCK_STALE_SEC       default: 3600
    HYPEROPT_LOCK_ENABLED         default: 1 (0 to bypass lock, debug only)
    HYPEROPT_MEMORY_WARN_MB       default: 800
    HYPEROPT_MEMORY_CRITICAL_MB   default: 1500
    HYPEROPT_MEMORY_ABORT_MB      default: 2500
    HYPEROPT_MEMORY_SYS_WARN_PCT  default: 75
    HYPEROPT_MEMORY_SYS_CRITICAL_PCT default: 85
    HYPEROPT_MEMORY_SYS_ABORT_PCT default: 92
"""

from __future__ import annotations

import atexit
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None  # fall back gracefully; memory guard becomes no-op

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Constants (resolved at import; callers can override per-call)
# ─────────────────────────────────────────────────────────────

LOCK_FILE_PATH = Path(os.getenv("HYPEROPT_LOCK_PATH", ".hyperopt.lock"))
LOCK_STALE_SEC = int(os.getenv("HYPEROPT_LOCK_STALE_SEC", "3600"))
LOCK_ENABLED = os.getenv("HYPEROPT_LOCK_ENABLED", "1") == "1"


# ─────────────────────────────────────────────────────────────
# Event Types
# ─────────────────────────────────────────────────────────────

class EventType(str, Enum):
    """Worker -> parent event types streamed via stdout JSON lines."""
    STARTED = "started"            # worker booted, study created
    STRAT_START = "strat_start"    # batch: new strategy begins
    TRIAL_DONE = "trial_done"      # trial completed (success, pruned, or failed)
    STRAT_DONE = "strat_done"      # strategy hyperopt complete
    BATCH_DONE = "batch_done"      # all batch strategies complete
    MEMORY_WARNING = "memory_warning"
    MEMORY_CRITICAL = "memory_critical"
    MEMORY_ABORT = "memory_abort"
    ERROR = "error"
    TIMEOUT = "timeout"
    STATUS = "status"              # periodic heartbeat (optional)


@dataclass
class IPCEvent:
    """
    IPC event sent worker -> parent. Serialized as a single JSON line on stdout.

    Only `event` is required. Other fields are event-specific and optional — they
    are stripped from the wire format when None.
    """
    event: str
    ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    strat: Optional[str] = None
    trial: Optional[int] = None
    total: Optional[int] = None
    idx: Optional[int] = None                 # 0-based strategy index in batch
    best_value: Optional[float] = None
    best_params: Optional[dict] = None
    memory_mb: Optional[float] = None
    sys_mem_pct: Optional[float] = None
    message: Optional[str] = None
    elapsed_sec: Optional[float] = None

    def emit(self) -> None:
        """Write JSON line to stdout (flushed). Called from worker process."""
        data = {k: v for k, v in asdict(self).items() if v is not None}
        try:
            print(json.dumps(data, ensure_ascii=False), flush=True)
        except (BrokenPipeError, OSError):
            # parent closed stdout (e.g. subprocess killed); nothing we can do
            pass

    @classmethod
    def parse(cls, line: str) -> Optional["IPCEvent"]:
        """
        Parse a JSON line into an IPCEvent. Returns None on any error —
        malformed lines must NEVER crash the parent reader.
        """
        if not line or not line.strip():
            return None
        try:
            data = json.loads(line.strip())
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.debug(f"IPC parse error: {e} | line={line!r}")
            return None
        if not isinstance(data, dict):
            return None
        evt = cls(event=str(data.get("event", "unknown")))
        for k, v in data.items():
            if k == "event":
                continue
            if hasattr(evt, k):
                try:
                    setattr(evt, k, v)
                except Exception:
                    pass  # type mismatch — skip this field
        return evt


# ─────────────────────────────────────────────────────────────
# PID-file Mutex
# ─────────────────────────────────────────────────────────────

class PidFileLock:
    """
    Cross-process mutex backed by a JSON file containing {pid, started_at, mode, strategy}.

    Acquisition rules:
      - If file absent: write + acquire.
      - If file present with a live PID that isn't us and isn't stale: fail.
      - If file present with a dead PID or age > stale_sec: treat as zombie, remove, retry.

    Safety:
      - `release()` only deletes the file if the recorded PID == os.getpid().
      - Context-manager compatible (`with PidFileLock() as lock:`).
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        stale_sec: int = LOCK_STALE_SEC,
    ):
        # Phase 82c Task #12: None-safe. Callers sometimes pass `lock_path=None`
        # explicitly, which used to bypass the default kwarg and crash
        # `Path(None)` with "expected str, bytes or os.PathLike object, not
        # NoneType". Now we coerce any falsy value to LOCK_FILE_PATH.
        self.path = Path(path) if path else LOCK_FILE_PATH
        self.stale_sec = stale_sec
        self._owned = False
        # Phase 82e Sprint 3.2: belt-and-braces atexit hook so a Python crash
        # path that skips our try/finally still releases the lock file on
        # interpreter teardown. Registered in acquire() (after success) and
        # unregistered in release(); safe to register twice because release
        # is idempotent via PID check.
        self._atexit_registered: bool = False

    def _read_lock(self) -> Optional[dict]:
        """Read current lock metadata. Returns None if absent or malformed."""
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get("pid"), int):
            return None
        return data

    def _is_stale(self, data: dict) -> bool:
        """A lock is stale if the PID is dead or the age exceeds stale_sec."""
        pid = data.get("pid")
        if pid is None:
            return True
        # Dead PID check (best-effort — if psutil missing, fall back to os.kill)
        try:
            if psutil is not None:
                if not psutil.pid_exists(pid):
                    return True
            else:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return True
        except Exception:
            return True
        # Age check
        started = data.get("started_at")
        if started:
            try:
                started_dt = datetime.fromisoformat(started)
                age = (datetime.utcnow() - started_dt).total_seconds()
                if age > self.stale_sec:
                    return True
            except (ValueError, TypeError):
                pass
        return False

    def acquire(
        self, mode: str = "unknown", strategy: Optional[str] = None
    ) -> bool:
        """
        Try to acquire the lock. Returns True if acquired, False if another live
        process holds it.

        If HYPEROPT_LOCK_ENABLED=0, always returns True (debug bypass).
        """
        if not LOCK_ENABLED:
            self._owned = True
            return True

        existing = self._read_lock()
        if existing is not None:
            if self._is_stale(existing):
                logger.warning(
                    "hyperopt_lock: stale lock found "
                    f"(pid={existing.get('pid')}, started={existing.get('started_at')}), "
                    "cleaning up"
                )
                try:
                    self.path.unlink()
                except OSError as e:
                    logger.error(f"hyperopt_lock: failed to remove stale lock: {e}")
                    return False
            else:
                logger.info(
                    f"hyperopt_lock: held by pid={existing.get('pid')}, "
                    f"mode={existing.get('mode')}, "
                    f"strat={existing.get('strategy')}"
                )
                return False

        payload = {
            "pid": os.getpid(),
            "started_at": datetime.utcnow().isoformat(),
            "mode": mode,
            "strategy": strategy,
        }
        try:
            self.path.write_text(json.dumps(payload), encoding="utf-8")
            self._owned = True
            # Sprint 3.2: safety-net atexit hook. release() is idempotent
            # and PID-checked so re-entry from the try/finally path is OK.
            if not self._atexit_registered:
                try:
                    atexit.register(self._atexit_release)
                    self._atexit_registered = True
                except Exception as _reg_err:
                    logger.debug(
                        "hyperopt_lock: atexit.register skipped: %s",
                        _reg_err,
                    )
            return True
        except OSError as e:
            logger.error(f"hyperopt_lock: failed to write lock file: {e}")
            return False

    def release(self) -> None:
        """Release the lock — only if the on-disk PID matches ours."""
        if not LOCK_ENABLED:
            self._owned = False
            return
        try:
            current = self._read_lock()
            if current is not None and current.get("pid") == os.getpid():
                try:
                    self.path.unlink()
                except OSError as e:
                    logger.warning(f"hyperopt_lock: failed to unlink: {e}")
        finally:
            self._owned = False
            # Sprint 3.2: unregister the atexit hook once we've released
            # intentionally, so a second interpreter-shutdown call is a no-op
            # even if the file doesn't yet exist.
            if self._atexit_registered:
                try:
                    atexit.unregister(self._atexit_release)
                except Exception:
                    pass
                self._atexit_registered = False

    def _atexit_release(self) -> None:
        """Sprint 3.2: atexit callback — best-effort release with no raises.

        Wraps release() in a broad exception guard because atexit callbacks
        raising during interpreter shutdown produce ugly stderr output for
        zero user benefit. The file's PID check inside release() already
        protects us from deleting a different process's lock.
        """
        try:
            self.release()
        except Exception as e:
            try:
                logger.debug("hyperopt_lock: atexit release failed: %s", e)
            except Exception:
                pass

    def force_release(self, reason: str = "admin") -> bool:
        """Sprint 3.2: admin override — delete the lock regardless of PID.

        Use ONLY from explicit operator commands (e.g. /hyperopt_abort).
        Returns True if a lock file existed and was removed, False otherwise.
        Bypasses the PID-owns-us check so a stuck lock from a crashed worker
        can be cleared without waiting for stale_sec to elapse.
        """
        if not self.path.exists():
            return False
        try:
            data = self._read_lock() or {}
            self.path.unlink()
            logger.warning(
                "hyperopt_lock: force_release reason=%s (was pid=%s mode=%s)",
                reason, data.get("pid"), data.get("mode"),
            )
            self._owned = False
            if self._atexit_registered:
                try:
                    atexit.unregister(self._atexit_release)
                except Exception:
                    pass
                self._atexit_registered = False
            return True
        except OSError as e:
            logger.error(
                "hyperopt_lock: force_release unlink failed: %s", e,
            )
            return False

    def status(self) -> Optional[dict]:
        """
        Read current lock status without modifying it. Adds an `is_stale` bool.
        Used by /hyperopt_status handler.
        """
        data = self._read_lock()
        if data is None:
            return None
        result = dict(data)
        result["is_stale"] = self._is_stale(data)
        return result

    @property
    def owned(self) -> bool:
        return self._owned

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


# ─────────────────────────────────────────────────────────────
# Progress State
# ─────────────────────────────────────────────────────────────

@dataclass
class StratDoneInfo:
    """Record of a completed strategy in batch mode."""
    name: str
    best_value: float = 0.0
    best_params: dict = field(default_factory=dict)
    elapsed_sec: float = 0.0
    trial_count: int = 0


@dataclass
class HyperoptProgressState:
    """
    In-memory state tracked by the parent bot while a hyperopt run is active.
    Read by /hyperopt_status handler. Runs on the asyncio event loop (single
    thread) so no explicit lock is required.
    """
    mode: str = "idle"  # "idle", "single", "batch"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    current_strat: Optional[str] = None
    current_trial: int = 0
    current_total: int = 0
    strats_done: list = field(default_factory=list)   # list[StratDoneInfo]
    strats_total: int = 0
    strats_idx: int = 0
    memory_mb: float = 0.0
    memory_sys_pct: float = 0.0
    warnings: list = field(default_factory=list)      # list[str]
    last_event_at: Optional[datetime] = None
    last_push_at: dict = field(default_factory=dict)  # dict[str, datetime]
    worker_pid: Optional[int] = None
    completed: bool = False
    error: Optional[str] = None
    # Telegram live-edit message tracking
    progress_message_id: Optional[int] = None
    progress_chat_id: Optional[int] = None
    # Last snapshot for historical /hyperopt_status when idle
    last_run_summary: Optional[dict] = None
    # Sprint 2.3 — STATUS heartbeat visibility (surfaced in /diagnose)
    last_status: Optional[str] = None
    last_status_at: Optional[datetime] = None

    # ── derived ──

    @property
    def active(self) -> bool:
        return self.mode != "idle" and not self.completed and self.error is None

    @property
    def elapsed_sec(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at or datetime.utcnow()
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def progress_pct(self) -> float:
        if self.mode == "batch":
            if self.strats_total == 0:
                return 0.0
            strats_frac = len(self.strats_done) / self.strats_total
            if self.current_total > 0 and self.current_strat:
                strat_partial = (
                    self.current_trial / self.current_total / self.strats_total
                )
                return min(100.0, (strats_frac + strat_partial) * 100)
            return strats_frac * 100
        if self.mode == "single":
            if self.current_total == 0:
                return 0.0
            return min(100.0, self.current_trial / self.current_total * 100)
        return 0.0

    @property
    def eta_sec(self) -> float:
        """Estimated remaining seconds. -1 if too early to estimate."""
        pct = self.progress_pct
        if pct < 5.0:
            return -1.0
        elapsed = self.elapsed_sec
        if elapsed < 10:
            return -1.0
        return elapsed * (100.0 - pct) / pct

    # ── lifecycle ──

    def reset(self) -> None:
        """Reset state back to idle (but keep last_run_summary for history)."""
        prev_summary = self.last_run_summary
        self.mode = "idle"
        self.started_at = None
        self.ended_at = None
        self.current_strat = None
        self.current_trial = 0
        self.current_total = 0
        self.strats_done = []
        self.strats_total = 0
        self.strats_idx = 0
        self.memory_mb = 0.0
        self.memory_sys_pct = 0.0
        self.warnings = []
        self.last_event_at = None
        self.last_push_at = {}
        self.worker_pid = None
        self.completed = False
        self.error = None
        self.progress_message_id = None
        self.progress_chat_id = None
        self.last_status = None
        self.last_status_at = None
        self.last_run_summary = prev_summary

    def start(
        self,
        mode: str,
        strats_total: int = 1,
        pid: Optional[int] = None,
    ) -> None:
        """Initialize a new run. Implicitly resets prior state."""
        self.reset()
        self.mode = mode
        self.started_at = datetime.utcnow()
        self.strats_total = max(1, strats_total)
        self.worker_pid = pid

    def finalize(self, error: Optional[str] = None) -> None:
        """Mark the run done and snapshot a summary for future /hyperopt_status."""
        self.ended_at = datetime.utcnow()
        self.completed = error is None
        if error:
            self.error = error
        self.last_run_summary = {
            "mode": self.mode,
            "started_at": (
                self.started_at.isoformat() if self.started_at else None
            ),
            "ended_at": self.ended_at.isoformat(),
            "elapsed_sec": self.elapsed_sec,
            "strats_done": len(self.strats_done),
            "strats_total": self.strats_total,
            "top_strats": [
                {
                    "name": s.name,
                    "best_value": s.best_value,
                    "trial_count": s.trial_count,
                }
                for s in sorted(
                    self.strats_done, key=lambda x: x.best_value, reverse=True
                )[:5]
            ],
            "warnings": list(self.warnings),
            "error": self.error,
        }

    # ── update from IPC events ──

    def update(self, evt: IPCEvent) -> None:
        """Apply an incoming IPC event to the state. Never raises."""
        try:
            self.last_event_at = datetime.utcnow()
            et = evt.event

            if et == EventType.STARTED.value:
                if evt.total is not None:
                    self.strats_total = max(self.strats_total, evt.total)

            elif et == EventType.STRAT_START.value:
                if evt.strat:
                    self.current_strat = evt.strat
                if evt.idx is not None:
                    self.strats_idx = evt.idx
                if evt.total is not None:
                    self.current_total = evt.total
                self.current_trial = 0

            elif et == EventType.TRIAL_DONE.value:
                if evt.trial is not None:
                    self.current_trial = evt.trial
                if evt.total is not None and evt.total > 0:
                    self.current_total = evt.total
                if evt.memory_mb is not None:
                    self.memory_mb = evt.memory_mb
                if evt.sys_mem_pct is not None:
                    self.memory_sys_pct = evt.sys_mem_pct

            elif et == EventType.STRAT_DONE.value:
                if evt.strat:
                    self.strats_done.append(
                        StratDoneInfo(
                            name=evt.strat,
                            best_value=float(evt.best_value or 0.0),
                            best_params=dict(evt.best_params or {}),
                            elapsed_sec=float(evt.elapsed_sec or 0.0),
                            trial_count=int(evt.trial or 0),
                        )
                    )
                self.current_strat = None
                self.current_trial = 0
                self.current_total = 0

            elif et == EventType.BATCH_DONE.value:
                self.finalize()

            elif et == EventType.STATUS.value:
                # Sprint 2.3 — surface worker heartbeat in /diagnose
                msg = (evt.message or "").strip()
                if msg:
                    self.last_status = msg[:200]  # cap for display
                    self.last_status_at = datetime.utcnow()

            elif et in (
                EventType.MEMORY_WARNING.value,
                EventType.MEMORY_CRITICAL.value,
                EventType.MEMORY_ABORT.value,
            ):
                msg = (
                    f"[{et}] proc={evt.memory_mb}MB "
                    f"sys={evt.sys_mem_pct}%"
                )
                self.warnings.append(msg)
                if evt.memory_mb is not None:
                    self.memory_mb = evt.memory_mb
                if evt.sys_mem_pct is not None:
                    self.memory_sys_pct = evt.sys_mem_pct

            elif et == EventType.ERROR.value:
                self.error = evt.message or "unknown error"
                self.warnings.append(f"[error] {self.error}")

            elif et == EventType.TIMEOUT.value:
                self.warnings.append(
                    f"[timeout] strat={evt.strat} trial={evt.trial}"
                )
        except Exception as e:
            logger.warning(f"HyperoptProgressState.update failed: {e}")

    # ── push cadence ──

    def can_push(self, event_type: str, min_interval_sec: int = 30) -> bool:
        """Return True if cooldown has passed since last push of this event type."""
        last = self.last_push_at.get(event_type)
        if last is None:
            return True
        elapsed = (datetime.utcnow() - last).total_seconds()
        return elapsed >= min_interval_sec

    def mark_pushed(self, event_type: str) -> None:
        """Record that this event type was just pushed to Telegram."""
        self.last_push_at[event_type] = datetime.utcnow()


# ─────────────────────────────────────────────────────────────
# Formatting Helpers (parent-side Telegram)
# ─────────────────────────────────────────────────────────────

def format_eta_hybrid(eta_sec: float, pct: float) -> str:
    """
    Hybrid ETA format: '+64dk (15:43 civarı) %78'
    Returns 'hesaplanıyor... %X' if eta not yet determinable.
    """
    try:
        if eta_sec is None or eta_sec < 0:
            return f"hesaplanıyor... %{max(0.0, pct):.0f}"
        mins = max(0, int(eta_sec / 60))
        finish = datetime.now() + timedelta(seconds=eta_sec)
        return f"+{mins}dk ({finish.strftime('%H:%M')} civarı) %{pct:.0f}"
    except Exception:
        return f"%{pct:.0f}"


def format_memory_status(proc_mb: float, sys_pct: float) -> str:
    """Short memory status with severity hint based on env thresholds."""
    try:
        warn_mb = float(os.getenv("HYPEROPT_MEMORY_WARN_MB", "800"))
        crit_mb = float(os.getenv("HYPEROPT_MEMORY_CRITICAL_MB", "1500"))
    except ValueError:
        warn_mb, crit_mb = 800.0, 1500.0
    hint = ""
    if proc_mb > crit_mb:
        hint = " (CRITICAL)"
    elif proc_mb > warn_mb:
        hint = " (WARN)"
    return f"proc={proc_mb:.0f}MB sys={sys_pct:.0f}%{hint}"


# ─────────────────────────────────────────────────────────────
# Memory Monitoring (worker-side)
# ─────────────────────────────────────────────────────────────

def get_memory_snapshot() -> tuple:
    """Return (proc_mb, sys_percent). Graceful fallback if psutil absent."""
    if psutil is None:
        return 0.0, 0.0
    try:
        proc = psutil.Process()
        proc_mb = proc.memory_info().rss / (1024 * 1024)
        sys_pct = psutil.virtual_memory().percent
        return proc_mb, sys_pct
    except Exception as e:
        logger.warning(f"get_memory_snapshot failed: {e}")
        return 0.0, 0.0


def memory_check_action() -> str:
    """
    Check memory and return action keyword used by worker loop:
      'abort'    -> skip remaining trials, emit MEMORY_ABORT, exit gracefully
      'critical' -> force GC; if still over, skip this trial (TrialPruned)
      'warn'     -> log warning + emit MEMORY_WARNING event
      'ok'       -> no action
    """
    proc_mb, sys_pct = get_memory_snapshot()
    try:
        abort_mb = float(os.getenv("HYPEROPT_MEMORY_ABORT_MB", "2500"))
        crit_mb = float(os.getenv("HYPEROPT_MEMORY_CRITICAL_MB", "1500"))
        warn_mb = float(os.getenv("HYPEROPT_MEMORY_WARN_MB", "800"))
        abort_sys = float(os.getenv("HYPEROPT_MEMORY_SYS_ABORT_PCT", "92"))
        crit_sys = float(os.getenv("HYPEROPT_MEMORY_SYS_CRITICAL_PCT", "85"))
        warn_sys = float(os.getenv("HYPEROPT_MEMORY_SYS_WARN_PCT", "75"))
    except ValueError:
        abort_mb, crit_mb, warn_mb = 2500.0, 1500.0, 800.0
        abort_sys, crit_sys, warn_sys = 92.0, 85.0, 75.0

    if proc_mb > abort_mb or sys_pct > abort_sys:
        return "abort"
    if proc_mb > crit_mb or sys_pct > crit_sys:
        return "critical"
    if proc_mb > warn_mb or sys_pct > warn_sys:
        return "warn"
    return "ok"


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

__all__ = [
    "EventType",
    "IPCEvent",
    "PidFileLock",
    "HyperoptProgressState",
    "StratDoneInfo",
    "LOCK_FILE_PATH",
    "LOCK_STALE_SEC",
    "LOCK_ENABLED",
    "format_eta_hybrid",
    "format_memory_status",
    "get_memory_snapshot",
    "memory_check_action",
]
