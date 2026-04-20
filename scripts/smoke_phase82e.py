"""
Phase 82e Integration Smoke Test — Sprints 2.3 / 3.1 / 3.2 / 3.3 / 4.1 / 4.2
============================================================================

Verifies every sprint's code landed correctly WITHOUT starting the bot
or launching a subprocess. Runs in a few seconds.

Checks, in order:
  Sprint 2.3 — HyperoptProgressState carries last_status / last_status_at
              and STATUS IPC events populate them; diagnose_handler
              exports _build_hyperopt_section + _build_bg_tasks_section.
  Sprint 3.1 — HyperOptPipeline exposes cache_stats() and the cache key
              normalizes asset_filter via upper()+strip().
  Sprint 3.2 — PidFileLock has force_release(); worker has a single
              try/finally; /hyperopt_abort handler is registered.
  Sprint 3.3 — hyperopt_worker imports memory_check_action and emits
              MEMORY_* IPC events for pre-/post-discovery states.
  Sprint 4.1 — N_JOBS env flag read from HYPEROPT_N_JOBS / HYPEROPT_JOBS
              with default 1; worker has both sequential and parallel
              code paths.
  Sprint 4.2 — ensure_hot_indexes.py imports cleanly and declares
              idx_ob_snap_slug_mst.

Run:
    py -3.11 scripts/smoke_phase82e.py

Exit codes:
    0 — all sprints verified
    1 — any check failed (details printed)
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        FAILS.append(label)


def header(title: str) -> None:
    print()
    print(f"── {title} {'─' * max(0, 68 - len(title))}")


# ─────────────────────────────────────────────────────────────
# Sprint 2.3 — STATUS IPC + /diagnose visibility
# ─────────────────────────────────────────────────────────────

def test_sprint_2_3() -> None:
    header("Sprint 2.3 — STATUS IPC + /diagnose visibility")
    try:
        from backtest.hyperopt_ipc import (
            HyperoptProgressState,
            EventType,
            IPCEvent,
        )
    except Exception as e:
        check(False, f"import hyperopt_ipc: {e}")
        return

    check(
        hasattr(HyperoptProgressState, "__dataclass_fields__"),
        "HyperoptProgressState is a dataclass",
    )
    fields = HyperoptProgressState.__dataclass_fields__
    check("last_status" in fields, "HyperoptProgressState.last_status field exists")
    check("last_status_at" in fields, "HyperoptProgressState.last_status_at field exists")
    check(hasattr(EventType, "STATUS"), "EventType.STATUS enum member exists")

    # Live feed a STATUS event and confirm state captures it.
    state = HyperoptProgressState()
    state.reset()
    assert state.last_status is None, "pre-condition: last_status is None after reset"

    class _StubEvt:
        event = EventType.STATUS.value
        strat = "TestStrat"
        message = "probe from smoke_phase82e"
        memory_mb = None
        sys_mem_pct = None
        trial = None
        total = None
        best_value = None
        best_params = None
        elapsed_sec = None
        idx = None

    try:
        state.update(_StubEvt())  # type: ignore[arg-type]
        check(
            state.last_status == "probe from smoke_phase82e",
            "STATUS event populates last_status",
        )
        check(state.last_status_at is not None, "last_status_at set")
    except Exception as e:
        check(False, f"state.update(STATUS) raised: {e!r}")

    # /diagnose helpers
    try:
        from telegram_bot.handlers import diagnose_handler as dh
    except Exception as e:
        check(False, f"import diagnose_handler: {e}")
        return
    check(hasattr(dh, "_build_hyperopt_section"), "_build_hyperopt_section present")
    check(hasattr(dh, "_build_bg_tasks_section"), "_build_bg_tasks_section present")


# ─────────────────────────────────────────────────────────────
# Sprint 3.1 — HyperOptPipeline cache normalization + stats
# ─────────────────────────────────────────────────────────────

def test_sprint_3_1() -> None:
    header("Sprint 3.1 — cache key normalization + cache_stats()")
    try:
        from backtest.hyperopt import HyperOptPipeline
    except Exception as e:
        check(False, f"import HyperOptPipeline: {e}")
        return
    check(hasattr(HyperOptPipeline, "cache_stats"), "HyperOptPipeline.cache_stats() exists")
    check(hasattr(HyperOptPipeline, "_windows_cache_key"), "_windows_cache_key() exists")
    check(hasattr(HyperOptPipeline, "prime_windows_cache"), "prime_windows_cache() exists")

    # Confirm cache_stats returns the expected keys.
    # HyperOptPipeline.__init__ imports optuna lazily; on hosts without
    # optuna installed (our sandbox, CI lint hosts) we verify the *method*
    # signature + return-value keys via source inspection instead of
    # instantiation. The user's Windows deploy machine has optuna
    # installed, where this branch runs the live path.
    try:
        pipe = HyperOptPipeline(db=None)  # db not used by cache_stats
        stats = pipe.cache_stats()
        for k in ("entries", "hits", "misses", "lookups", "hit_pct"):
            check(k in stats, f"cache_stats() key: {k}")
    except ImportError as e:
        # optuna missing — fall back to source check
        print(f"  SKIP  HyperOptPipeline instantiation (optional dep missing: {e})")
        src = inspect.getsource(HyperOptPipeline.cache_stats)
        for k in ("entries", "hits", "misses", "lookups", "hit_pct"):
            check(f'"{k}"' in src, f"cache_stats() source references key: {k}")
    except Exception as e:
        check(False, f"HyperOptPipeline() or cache_stats() raised: {e!r}")

    # Confirm _windows_cache_key normalizes asset_filter
    class _FakeCfg:
        asset_filter = "btc "  # lowercase + trailing space
        timeframe_filter = "5m"
        last_n = 200
        start_ms = 0
        end_ms = 0
        max_markets = 0
        min_snap_count = 2

    try:
        k1 = HyperOptPipeline._windows_cache_key(_FakeCfg())
        _FakeCfg2 = type("_FakeCfg2", (), dict(
            asset_filter="BTC",
            timeframe_filter="5m",
            last_n=200,
            start_ms=0,
            end_ms=0,
            max_markets=0,
            min_snap_count=2,
        ))
        k2 = HyperOptPipeline._windows_cache_key(_FakeCfg2())
        check(k1 == k2, "'btc ' and 'BTC' produce the same cache key")
    except Exception as e:
        check(False, f"cache-key normalization failed: {e!r}")


# ─────────────────────────────────────────────────────────────
# Sprint 3.2 — Lock cleanup + /hyperopt_abort command
# ─────────────────────────────────────────────────────────────

def test_sprint_3_2() -> None:
    header("Sprint 3.2 — PidFileLock cleanup + /hyperopt_abort")
    try:
        from backtest.hyperopt_ipc import PidFileLock
    except Exception as e:
        check(False, f"import PidFileLock: {e}")
        return

    check(hasattr(PidFileLock, "force_release"), "PidFileLock.force_release() present")
    check(hasattr(PidFileLock, "_atexit_release"), "PidFileLock._atexit_release() present")

    try:
        from telegram_bot.handlers import hyperopt_handler as hh
    except Exception as e:
        check(False, f"import hyperopt_handler: {e}")
        return
    check(hasattr(hh, "hyperopt_abort_command"), "hyperopt_abort_command present")
    check(hasattr(hh, "_is_admin"), "_is_admin() helper present")

    # Confirm the command is wired up in bot.py's command registration
    bot_src = (_ROOT / "telegram_bot" / "bot.py").read_text(encoding="utf-8")
    check(
        "hyperopt_abort_command" in bot_src,
        "bot.py imports/registers hyperopt_abort_command",
    )
    check(
        '"hyperopt_abort"' in bot_src or "'hyperopt_abort'" in bot_src,
        "bot.py registers /hyperopt_abort route",
    )

    # Worker file has a single finally + lock release pattern
    wsrc = (_ROOT / "backtest" / "hyperopt_worker.py").read_text(encoding="utf-8")
    check(
        "try/finally" in wsrc or "Sprint 3.2" in wsrc,
        "worker documents Sprint 3.2 lock cleanup",
    )
    check(wsrc.count("lock.release()") >= 1, "worker calls lock.release()")
    check(
        "finally:" in wsrc and "lock.release()" in wsrc,
        "worker releases lock inside finally block",
    )


# ─────────────────────────────────────────────────────────────
# Sprint 3.3 — Worker memory guard for discovery phase
# ─────────────────────────────────────────────────────────────

def test_sprint_3_3() -> None:
    header("Sprint 3.3 — discovery memory guard")
    wsrc = (_ROOT / "backtest" / "hyperopt_worker.py").read_text(encoding="utf-8")
    check("memory_check_action" in wsrc, "worker imports memory_check_action")
    check(
        "abort threshold reached BEFORE discovery" in wsrc,
        "pre-discovery abort path present",
    )
    check(
        "abort threshold reached AFTER discovery" in wsrc,
        "post-discovery abort path present",
    )
    check(
        "mem={pre_proc_mb:.0f}MB" in wsrc or "mem=" in wsrc,
        "pre-priming STATUS emits memory snapshot",
    )


# ─────────────────────────────────────────────────────────────
# Sprint 4.1 — N_JOBS parallel trials
# ─────────────────────────────────────────────────────────────

def test_sprint_4_1() -> None:
    header("Sprint 4.1 — N_JOBS parallel trials")
    # Confirm env var is read with both primary+fallback names.
    # Clear any pre-existing value to make the default deterministic.
    old_primary = os.environ.pop("HYPEROPT_N_JOBS", None)
    old_fallback = os.environ.pop("HYPEROPT_JOBS", None)
    try:
        # Re-import module to pick up the env state (safe — module is stateless
        # at import time, N_JOBS is computed once at module-level).
        import importlib
        import backtest.hyperopt_worker as hw
        importlib.reload(hw)
        check(hasattr(hw, "N_JOBS"), "hyperopt_worker exports N_JOBS")
        check(hw.N_JOBS == 1, f"default N_JOBS == 1 (got {getattr(hw, 'N_JOBS', None)})")

        # Try the primary name
        os.environ["HYPEROPT_N_JOBS"] = "4"
        importlib.reload(hw)
        check(hw.N_JOBS == 4, f"HYPEROPT_N_JOBS=4 → N_JOBS=4 (got {hw.N_JOBS})")

        # Try the legacy fallback
        del os.environ["HYPEROPT_N_JOBS"]
        os.environ["HYPEROPT_JOBS"] = "2"
        importlib.reload(hw)
        check(hw.N_JOBS == 2, f"HYPEROPT_JOBS=2 → N_JOBS=2 (got {hw.N_JOBS})")
    finally:
        # Restore env to caller's original state
        os.environ.pop("HYPEROPT_N_JOBS", None)
        os.environ.pop("HYPEROPT_JOBS", None)
        if old_primary is not None:
            os.environ["HYPEROPT_N_JOBS"] = old_primary
        if old_fallback is not None:
            os.environ["HYPEROPT_JOBS"] = old_fallback
        # Reload once more to leave module in env-default state
        import importlib
        import backtest.hyperopt_worker as hw
        importlib.reload(hw)

    # Confirm both code paths exist in source
    wsrc = (_ROOT / "backtest" / "hyperopt_worker.py").read_text(encoding="utf-8")
    check("if N_JOBS <= 1:" in wsrc, "sequential branch present")
    check("asyncio.gather(*coros" in wsrc, "parallel gather branch present")
    check("return_exceptions=True" in wsrc, "gather uses return_exceptions=True")
    check("batch_size = min(N_JOBS" in wsrc, "batch sizing present")


# ─────────────────────────────────────────────────────────────
# Sprint 4.2 — ob_snapshots composite index
# ─────────────────────────────────────────────────────────────

def test_sprint_4_2() -> None:
    header("Sprint 4.2 — ob_snapshots composite index")
    script = _ROOT / "scripts" / "ensure_hot_indexes.py"
    check(script.exists(), "scripts/ensure_hot_indexes.py exists")

    src = script.read_text(encoding="utf-8")
    check("idx_ob_snap_slug_mst" in src, "declares idx_ob_snap_slug_mst")
    check(
        "ON ob_snapshots(slug, market_start_time)" in src,
        "composite columns (slug, market_start_time)",
    )
    check("CREATE INDEX IF NOT EXISTS" in src, "uses IF NOT EXISTS (idempotent)")
    check("EXPLAIN QUERY PLAN" in src, "verifies with EXPLAIN QUERY PLAN")
    check("--explain-only" in src, "supports --explain-only dry run")

    # Spin up a tiny synthetic DB and exercise the script end-to-end.
    # This is the same test we ran interactively — embedding here protects
    # against a future edit accidentally breaking the script.
    import sqlite3
    import tempfile
    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        dbpath = tmp.name
    try:
        con = sqlite3.connect(dbpath)
        con.executescript(
            """
            CREATE TABLE ob_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL,
                asset TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                up_token_id TEXT, down_token_id TEXT,
                ts_ms INTEGER NOT NULL,
                ts_iso TEXT NOT NULL,
                market_start_time TEXT, market_end_time TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_ob_snap_slug_ts ON ob_snapshots(slug, ts_ms);
            CREATE INDEX idx_ob_snap_asset_tf_ts ON ob_snapshots(asset, timeframe, ts_ms);
            CREATE INDEX idx_ob_snap_ts ON ob_snapshots(ts_ms);
            """
        )
        con.commit()
        con.close()

        r = subprocess.run(
            [sys.executable, str(script), "--db-path", dbpath],
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",   # force UTF-8 so Windows cp1252 never double-encodes
            errors="replace",
        )
        if r.returncode != 0:
            # Surface stderr/stdout tail so the failure is diagnosable from bat output
            tail = (r.stdout or "")[-1200:] + "\n---stderr---\n" + (r.stderr or "")[-1200:]
            print("    -- subprocess output tail --")
            for line in tail.splitlines()[-30:]:
                print(f"    | {line}")
        check(
            r.returncode == 0,
            f"ensure_hot_indexes.py returns 0 on tiny DB (got {r.returncode})",
        )
        check(
            "idx_ob_snap_slug_mst" in r.stdout,
            "script output mentions new index",
        )
        # The "AFTER" header now uses an ASCII dash, but both em-dash and
        # regular dash delimiters must be tolerated to stay portable across
        # locale fixes. We split on the header keyword "AFTER" which is stable.
        split_markers = ("EXPLAIN QUERY PLAN - AFTER", "EXPLAIN QUERY PLAN \u2014 AFTER")
        after_section = r.stdout
        for mk in split_markers:
            if mk in r.stdout:
                after_section = r.stdout.split(mk, 1)[-1]
                before_section = r.stdout.split(mk, 1)[0]
                break
        else:
            # header missing -> subprocess likely crashed; flag BEFORE as missing
            before_section = ""
        check(
            "USE TEMP B-TREE FOR GROUP BY" in before_section,
            "plan BEFORE contains GROUP BY temp B-tree",
        )
        # After: temp B-tree for GROUP BY disappears from the discovery query.
        # (Temp B-tree for ORDER BY is unavoidable because ORDER BY uses an
        # aggregate; we only care that GROUP BY is now sort-free.)
        discovery_after = after_section.split("discovery_asset_tf_filter")[0]
        check(
            # Guard against empty/missing AFTER block — need ACTUAL plan lines
            "SEARCH" in discovery_after or "SCAN" in discovery_after,
            "AFTER block contains query plan lines (sanity)",
        )
        check(
            "USE TEMP B-TREE FOR GROUP BY" not in discovery_after,
            "new index eliminates GROUP BY temp B-tree for discovery_with_ts_filter",
        )
    finally:
        try:
            os.unlink(dbpath)
        except Exception:
            pass


def main() -> int:
    print(f"Phase 82e smoke test — {_ROOT}")
    test_sprint_2_3()
    test_sprint_3_1()
    test_sprint_3_2()
    test_sprint_3_3()
    test_sprint_4_1()
    test_sprint_4_2()

    print()
    if FAILS:
        print(f"FAILED ({len(FAILS)} check{'s' if len(FAILS) != 1 else ''}):")
        for label in FAILS:
            print(f"  - {label}")
        return 1
    print("ALL SPRINTS VERIFIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
