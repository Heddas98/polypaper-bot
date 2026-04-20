"""
Phase 82b: Hyperopt subprocess worker + IPC + mutex tests
==========================================================

13 test cases covering:
    IPC             (4)  — event round-trip, parse malformed, enum values, dataclass
    PidFileLock     (3)  — acquire/release, stale cleanup, busy refusal
    ProgressState   (2)  — batch lifecycle, ETA/pct properties
    Pipeline        (2)  — lock bailout, no-nest-asyncio regression
    Handler         (2)  — subprocess launcher arg contract, /hyperopt_status

Run:
    pytest tests/unit/test_phase82b.py -v

All tests are hermetic — no real subprocess spawn, no real DB, no network.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root is on path (conftest.py already handles this but be explicit
# for the case where this file is run standalone).
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.hyperopt_ipc import (
    EventType,
    IPCEvent,
    PidFileLock,
    HyperoptProgressState,
    StratDoneInfo,
    format_eta_hybrid,
)


# ══════════════════════════════════════════════════════════════════════
# IPC tests (4)
# ══════════════════════════════════════════════════════════════════════


class TestIPCEvent:
    """1) Round-trip: emit() → JSON line → parse() returns equivalent event."""

    def test_ipc_event_emit_parse_roundtrip(self, capsys):
        evt = IPCEvent(
            event=EventType.TRIAL_DONE.value,
            strat="momentum",
            trial=5,
            total=30,
            best_value=0.4712,
            memory_mb=512.0,
            sys_mem_pct=40.0,
            message="trial 5/30 done",
        )
        evt.emit()
        captured = capsys.readouterr().out.strip().splitlines()
        assert len(captured) == 1, "emit() must write exactly one line"

        parsed = IPCEvent.parse(captured[0])
        assert parsed is not None
        assert parsed.event == "trial_done"
        assert parsed.strat == "momentum"
        assert parsed.trial == 5
        assert parsed.total == 30
        assert parsed.best_value == pytest.approx(0.4712)
        assert parsed.memory_mb == pytest.approx(512.0)
        assert parsed.message == "trial 5/30 done"

    def test_ipc_event_parse_malformed_returns_none(self):
        """2) Malformed JSON / garbage input must return None, never raise.

        Note: parse() is lenient on valid-JSON-but-missing-fields (returns
        an IPCEvent with event='unknown' so stream consumer can skip).
        What MUST return None is non-JSON / un-parseable input.
        """
        assert IPCEvent.parse("") is None
        assert IPCEvent.parse("not-json") is None
        # Non-JSON but printable garbage (e.g. logger line) must return None
        assert IPCEvent.parse("2026-04-17 09:00 INFO something happened") is None
        # Valid JSON missing 'event' — lenient parse yields event='unknown'
        lenient = IPCEvent.parse("{}")
        assert lenient is None or lenient.event in ("", "unknown")

    def test_ipc_event_type_enum_values(self):
        """3) EventType enum must expose the 11 documented event names."""
        expected = {
            "started", "strat_start", "trial_done", "strat_done",
            "batch_done", "memory_warning", "memory_critical",
            "memory_abort", "error", "timeout", "status",
        }
        got = {e.value for e in EventType}
        assert expected.issubset(got), (
            f"missing enum values: {expected - got}"
        )


class TestStratDoneInfo:
    """4) StratDoneInfo default construction."""

    def test_stratdone_info_dataclass_defaults(self):
        info = StratDoneInfo(name="momentum")
        assert info.name == "momentum"
        assert info.best_value == 0.0
        assert info.best_params == {}
        assert info.elapsed_sec == 0.0
        assert info.trial_count == 0


# ══════════════════════════════════════════════════════════════════════
# PidFileLock tests (3)
# ══════════════════════════════════════════════════════════════════════


class TestPidFileLock:

    def test_pidfilelock_acquire_release(self, tmp_path):
        """5) Basic acquire → release → re-acquire."""
        lock_path = tmp_path / "foo.lock"
        lock = PidFileLock(str(lock_path))

        assert lock.acquire(mode="test", strategy="momentum")
        assert lock_path.exists()

        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["mode"] == "test"
        assert data["strategy"] == "momentum"

        lock.release()
        assert not lock_path.exists()

        # Can re-acquire after release
        assert lock.acquire(mode="test2")
        lock.release()

    def test_pidfilelock_stale_cleanup(self, tmp_path):
        """6) Dead PID => lock is treated as stale and cleaned up."""
        lock_path = tmp_path / "stale.lock"
        # Write a lock file with a PID that cannot exist (PID 1 on Linux is init
        # but in a sandbox / container we inject a clearly-dead high PID).
        fake_payload = {
            "pid": 999_999_998,
            "started_at": "2020-01-01T00:00:00",
            "mode": "ghost",
            "strategy": "nobody",
        }
        lock_path.write_text(json.dumps(fake_payload))

        lock = PidFileLock(str(lock_path))
        # Status should flag it stale
        st = lock.status()
        assert st is not None
        assert st.get("is_stale") is True

        # A fresh acquire() should clean up and succeed
        assert lock.acquire(mode="cleaner")
        data = json.loads(lock_path.read_text())
        assert data["pid"] == os.getpid()
        assert data["mode"] == "cleaner"
        lock.release()

    def test_pidfilelock_refuse_when_live_pid_holds(self, tmp_path):
        """7) Existing lock held by a live PID => refuse new acquire."""
        lock_path = tmp_path / "busy.lock"
        # Use os.getpid() as a guaranteed-live PID but pretend we're a DIFFERENT
        # process (by writing the payload manually and creating a fresh lock
        # instance — PidFileLock doesn't track owner by instance if the pid on
        # disk matches, but matches imply it's us; to simulate "other live
        # process" we borrow os.getppid() which is our parent.
        live_pid = os.getppid() or 1
        try:
            import psutil
            assert psutil.pid_exists(live_pid), "parent PID must be alive"
        except ImportError:
            pytest.skip("psutil not available")

        fake_payload = {
            "pid": live_pid,
            "started_at": datetime.utcnow().isoformat(),
            "mode": "foreign",
            "strategy": "x",
        }
        lock_path.write_text(json.dumps(fake_payload))

        lock = PidFileLock(str(lock_path))
        # Must refuse (live non-stale lock)
        assert lock.acquire(mode="intruder") is False
        # Lock file must still have the foreign payload
        data = json.loads(lock_path.read_text())
        assert data["pid"] == live_pid


# ══════════════════════════════════════════════════════════════════════
# HyperoptProgressState tests (2)
# ══════════════════════════════════════════════════════════════════════


class TestHyperoptProgressState:

    def test_progress_state_lifecycle_batch(self):
        """8) Full batch lifecycle → finalize sets last_run_summary."""
        state = HyperoptProgressState()
        assert not state.active
        assert state.mode == "idle"

        state.start(mode="batch", strats_total=2, pid=1234)
        assert state.active
        assert state.mode == "batch"
        assert state.strats_total == 2
        assert state.worker_pid == 1234

        # STARTED event may update strats_total (no-op if already set)
        state.update(IPCEvent(event=EventType.STARTED.value, total=2, trial=15))
        assert state.strats_total == 2

        # Strategy 1 begins
        state.update(IPCEvent(
            event=EventType.STRAT_START.value,
            strat="momentum", idx=0, total=15,
        ))
        assert state.current_strat == "momentum"
        assert state.current_total == 15
        assert state.current_trial == 0

        # A few trials
        for i in range(1, 16):
            state.update(IPCEvent(
                event=EventType.TRIAL_DONE.value,
                strat="momentum", trial=i, total=15,
                memory_mb=500.0, sys_mem_pct=40.0,
            ))
        assert state.current_trial == 15
        assert state.memory_mb == 500.0

        # Strategy 1 done
        state.update(IPCEvent(
            event=EventType.STRAT_DONE.value,
            strat="momentum", best_value=0.42,
            best_params={"x": 1}, trial=15, elapsed_sec=90.0,
        ))
        assert len(state.strats_done) == 1
        assert state.strats_done[0].name == "momentum"
        assert state.strats_done[0].best_value == pytest.approx(0.42)
        assert state.current_strat is None  # reset between strats

        # BATCH_DONE auto-finalizes
        state.update(IPCEvent(event=EventType.BATCH_DONE.value))
        assert not state.active
        assert state.last_run_summary is not None
        assert state.last_run_summary["mode"] == "batch"
        assert state.last_run_summary["strats_done"] == 1
        assert len(state.last_run_summary["top_strats"]) == 1

    def test_progress_state_eta_and_pct_properties(self):
        """9) progress_pct + eta_sec derive correctly mid-run."""
        state = HyperoptProgressState()
        state.start(mode="single", strats_total=1)
        # Back-date started_at so elapsed > 10s threshold for eta
        state.started_at = datetime.utcnow() - timedelta(seconds=30)

        state.update(IPCEvent(
            event=EventType.STRAT_START.value,
            strat="momentum", idx=0, total=100,
        ))
        # Drive current_trial to 50 via TRIAL_DONE
        state.update(IPCEvent(
            event=EventType.TRIAL_DONE.value,
            strat="momentum", trial=50, total=100,
        ))

        pct = state.progress_pct
        assert 49.0 <= pct <= 51.0
        eta = state.eta_sec
        # 30s elapsed at 50% → ~30s remaining
        assert eta > 0
        assert abs(eta - 30.0) < 5.0

        # format_eta_hybrid should return a non-empty string
        label = format_eta_hybrid(eta, pct)
        assert isinstance(label, str) and label


# ══════════════════════════════════════════════════════════════════════
# Pipeline tests (2)
# ══════════════════════════════════════════════════════════════════════


class TestHyperOptPipelineMutex:

    def test_pipeline_optimize_bails_when_lock_held(self, tmp_path):
        """10) optimize() returns empty result if PidFileLock is held by another live PID.

        Uses asyncio.run() wrapper so this test does not depend on pytest-asyncio
        being installed on the dev machine.
        """
        try:
            from backtest.hyperopt import HyperOptPipeline
        except ImportError:
            pytest.skip("hyperopt pipeline not importable (optuna missing)")

        lock_path = tmp_path / "contested.lock"
        # Foreign-but-alive pid: our parent
        live_pid = os.getppid() or 1
        lock_path.write_text(json.dumps({
            "pid": live_pid,
            "started_at": datetime.utcnow().isoformat(),
            "mode": "foreign",
            "strategy": "x",
        }))

        class DummyDB:
            pass

        pipeline = HyperOptPipeline(DummyDB())

        async def _run():
            return await pipeline.optimize(
                strategy_name="momentum",
                n_trials=3,
                lock_path=str(lock_path),
            )

        result = asyncio.run(_run())
        assert result.n_trials == 0
        assert result.strategy_name == "momentum"
        assert result.best_score == 0.0


class TestHyperOptNoNestAsyncio:

    def test_hyperopt_no_nest_asyncio_import(self):
        """11) Regression — nest_asyncio must never be imported by hyperopt.py."""
        # encoding="utf-8" — Windows default is cp1252 which cannot decode
        # the UTF-8 box-drawing chars in our banner comments.
        hyperopt_src = (ROOT / "backtest" / "hyperopt.py").read_text(encoding="utf-8")
        # Allow it in comments/docstrings, but not as a real import/apply call.
        forbidden = [
            "\nimport nest_asyncio",
            "from nest_asyncio ",
            "nest_asyncio.apply()",
        ]
        for needle in forbidden:
            assert needle not in hyperopt_src, (
                f"hyperopt.py must not contain '{needle.strip()}' "
                "— Phase 82b removed nest_asyncio entirely."
            )


# ══════════════════════════════════════════════════════════════════════
# Handler tests (2)
# ══════════════════════════════════════════════════════════════════════


class TestHyperoptHandler:

    def test_hyperopt_handler_uses_subprocess_launcher(self, monkeypatch, tmp_path):
        """12) /hyperopt must spawn the worker subprocess with expected CLI args.

        Uses asyncio.run() wrapper so this test does not depend on pytest-asyncio.
        """
        try:
            from telegram_bot.handlers import hyperopt_handler as hh
        except ImportError:
            pytest.skip("telegram handler not importable")

        # Reset module state (singletons survive across tests)
        hh._progress_state.reset()

        captured_cmds = []

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            captured_cmds.append(list(cmd))

            proc = MagicMock()
            proc.pid = 55555
            proc.stdout = MagicMock()

            events = [
                json.dumps({
                    "event": "started", "ts": "2026-01-01T00:00:00",
                    "total": 1, "trial": 3, "message": "mode=single",
                }).encode() + b"\n",
                json.dumps({
                    "event": "strat_start", "ts": "2026-01-01T00:00:00",
                    "strat": "momentum", "idx": 0, "total": 3,
                }).encode() + b"\n",
                json.dumps({
                    "event": "strat_done", "ts": "2026-01-01T00:00:00",
                    "strat": "momentum", "best_value": 0.5,
                    "best_params": {"x": 1}, "trial": 3, "elapsed_sec": 1.2,
                }).encode() + b"\n",
                json.dumps({
                    "event": "batch_done", "ts": "2026-01-01T00:00:00",
                    "total": 1, "trial": 0,
                }).encode() + b"\n",
                b"",  # EOF
            ]
            it = iter(events)

            async def readline():
                return next(it)

            proc.stdout.readline = readline

            async def wait():
                return 0
            proc.wait = wait
            proc.kill = MagicMock()
            return proc

        monkeypatch.setattr(
            asyncio, "create_subprocess_exec", fake_create_subprocess_exec
        )
        # Redirect the lock file so we don't clobber the real one
        monkeypatch.setattr(hh, "_LOCK_PATH", str(tmp_path / "t.lock"))

        # Build a minimal fake Update object
        sent_msgs = []
        edits = []

        class FakeMsg:
            async def reply_text(self, text, **kw):
                m = MagicMock()
                m.text = text
                sent_msgs.append(text)
                m.edit_text = AsyncMock(
                    side_effect=lambda t, **k: edits.append(t)
                )
                return m

        class FakeChat:
            id = 7777

        class FakeUpdate:
            message = FakeMsg()
            effective_chat = FakeChat()

        async def run():
            return await hh._run_hyperopt_worker(
                FakeUpdate(),
                mode="single",
                strategy="momentum",
                strategies=None,
                n_trials=3,
            )

        done = asyncio.run(run())
        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        # Expect: [python, -u, -m, backtest.hyperopt_worker, --mode, single,
        #         --n-trials, 3, --lock-path, <tmp>, --strategy, momentum]
        assert "backtest.hyperopt_worker" in cmd
        assert "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "single"
        assert "--strategy" in cmd and cmd[cmd.index("--strategy") + 1] == "momentum"
        assert "--n-trials" in cmd and cmd[cmd.index("--n-trials") + 1] == "3"
        assert "--lock-path" in cmd
        # STRAT_DONE flowed through
        assert len(done) == 1
        assert done[0].name == "momentum"
        assert done[0].best_value == pytest.approx(0.5)

    def test_hyperopt_status_command_idle_and_active(self, monkeypatch, tmp_path):
        """13) /hyperopt_status — idle path and active path render distinct text.

        Uses asyncio.run() wrapper so this test does not depend on pytest-asyncio.
        """
        try:
            from telegram_bot.handlers import hyperopt_handler as hh
        except ImportError:
            pytest.skip("telegram handler not importable")

        monkeypatch.setattr(hh, "_LOCK_PATH", str(tmp_path / "s.lock"))
        hh._progress_state.reset()

        # ── Idle: no lock, no progress ──
        sent = []

        class Msg:
            async def reply_text(self, text, **kw):
                sent.append(text)

        class Upd:
            message = Msg()

        ctx = MagicMock()
        asyncio.run(hh.hyperopt_status_command(Upd(), ctx))
        assert len(sent) == 1
        assert "aktif değil" in sent[0].lower() or "aktif degil" in sent[0].lower()

        # ── Active: progress state mid-run + lock held by us ──
        sent.clear()
        lock = PidFileLock(str(tmp_path / "s.lock"))
        assert lock.acquire(mode="single", strategy="momentum")
        try:
            hh._progress_state.start(mode="single", strats_total=1, pid=os.getpid())
            hh._progress_state.started_at = (
                datetime.utcnow() - timedelta(seconds=20)
            )
            hh._progress_state.update(IPCEvent(
                event=EventType.STRAT_START.value,
                strat="momentum", idx=0, total=10,
            ))
            hh._progress_state.update(IPCEvent(
                event=EventType.TRIAL_DONE.value,
                strat="momentum", trial=4, total=10,
            ))
            asyncio.run(hh.hyperopt_status_command(Upd(), ctx))
        finally:
            lock.release()
            hh._progress_state.reset()

        assert len(sent) == 1
        msg = sent[0]
        assert "aktif" in msg.lower()
        assert "momentum" in msg
        assert "pid" in msg.lower() or "Mod" in msg
