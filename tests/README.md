# PolyPaper Bot — Test Suite

Epic 9 T9.10 closing artifact. This file is the single entry point for
running, maintaining, and understanding the test suite.

## Layout

```
tests/
├── conftest.py                 # path bootstrap + env secret defaults
├── INVENTORY.md                # test catalog (file × phase × coverage)
├── TRIAGE_MATRIX.md            # historical fix/skip/delete decisions
├── FLAKY_AUDIT.md              # known flaky behaviour notes
├── COVERAGE_REPORT.md          # last measured critical-path coverage
├── README.md                   # this file
│
├── unit/                       # pure-logic + mixin harness tests
│   ├── test_engine_fills.py
│   ├── test_engine_settlement_helpers.py
│   ├── test_engine_signals_helpers.py
│   ├── test_engine_monitor.py
│   ├── test_live_trader.py
│   ├── test_ai_brain_ratelimit.py
│   ├── test_auto_optimizer_helpers.py
│   ├── test_kill_switch.py
│   ├── test_market_recorder_parity.py
│   ├── test_brain_flags_parity.py
│   ├── test_boot_sibling_sync.py
│   ├── test_whitelist_runtime_readiness.py
│   ├── test_ws_reconnect.py
│   ├── test_phase*.py          # historical phase-frozen invariants
│   └── ... (~28 files, ~600 tests)
│
├── integration/                # Epic 9 T9.8 smoke suite (auto-marked)
│   ├── conftest.py             # auto-applies @pytest.mark.integration
│   ├── test_engine_boot_smoke.py
│   ├── test_paper_shadow_divergence.py
│   └── test_ws_reconnect_smoke.py
│
└── smoke_phase*.py             # legacy phase smoke (being phased out)
```

## Quick commands

| What I want                                    | Command                                                   |
|-----------------------------------------------|-----------------------------------------------------------|
| Full suite (sandbox-safe)                      | `py -3.11 -m pytest`                                      |
| Unit only (fast, ~41s)                         | `py -3.11 -m pytest -m "not integration"`                 |
| Integration smoke only (~1.3s)                 | `py -3.11 -m pytest -m integration`                       |
| One file                                       | `py -3.11 -m pytest tests/unit/test_engine_fills.py`      |
| One test                                       | `py -3.11 -m pytest tests/unit/test_engine_fills.py::TestSnapToTick::test_rounds_to_tick_1c` |
| With seed (determinism check)                  | `py -3.11 -m pytest -p randomly --randomly-seed=42`       |
| With coverage                                  | `py -3.11 -m pytest --cov=core --cov-branch --cov-report=term-missing` |
| Full regression (Windows, with env)            | `run_full_regression.bat`                                 |
| Full regression (sandbox / WSL)                | `./run_full_regression.sh`                                |

## Markers

Declared in `pytest.ini`:

- **`integration`** — tests under `tests/integration/`. Construction-level +
  pure-logic + deterministic state sims (Epic 9 T9.8). Auto-applied by the
  folder's conftest — no per-class decorator needed. 50 tests.
- **`slow`** — reserved for >1s individual tests (none yet).
- **`network`** — reserved for real HTTP/WS calls. None yet; see T9.8-REG.
- **`windows_only`** — reserved for tests requiring py-clob-client / optuna /
  full aiosqlite WAL. None yet; see T9.8-REG.

`--strict-markers` is on: an undeclared marker raises at collection time.

## Doctrine

### 1. Three test layers

1. **Unit (`tests/unit/`)** — Pure logic. No DB, no network, no asyncio
   event loop (except via `asyncio.run()` for lock/coroutine-shape tests).
   Mixin harness pattern preferred: `class XHarness(XMixin): ...` with
   minimal stub `__init__`.

2. **Integration (`tests/integration/`)** — Smoke tests that touch the
   construction boundary: `TradingEngine(...)`, `PolymarketWebSocket()`,
   deterministic state simulations. Still sandbox-safe — no real DB, no
   real network.

3. **T9.8-REG Windows backlog (not yet written)** — Real asyncio
   `engine.start()`, real aiosqlite, real websockets.connect(),
   py-clob-client, optuna. These require Windows + credentials and live
   outside the sandbox-runnable suite.

### 2. ENV runtime re-read doctrine (T6.1 / T7.6 A5)

Module-top `os.getenv(...)` constants are a **ghost-toggle antipattern**:
`/env_toggle` flips the env var but the constant stays frozen.

Guard pattern for every runtime helper:

```python
def test_runtime_reread(monkeypatch):
    monkeypatch.setenv("FLAG", "false")
    assert module._runtime_helper() is False
    monkeypatch.setenv("FLAG", "true")
    assert module._runtime_helper() is True
```

Two sequential `monkeypatch.setenv` calls with different values asserting
different outputs = the canonical T6.1 guard.

### 3. Float precision — `pytest.approx`, not `==`

`0.55 - 0.50 = 0.050000000000000044`. Any float arithmetic path needs:

```python
fav, adv = result
assert fav == pytest.approx(0.05, abs=0.001)
assert adv == 0.0
```

### 4. Seed determinism

Tests using randomness should run GREEN across multiple seeds:

```
py -3.11 -m pytest -p randomly --randomly-seed=42
py -3.11 -m pytest -p randomly --randomly-seed=1337
py -3.11 -m pytest -p randomly --randomly-seed=9001
```

All three must pass. Non-deterministic tests = broken tests.

### 5. Single Fee Oracle

Paper and shadow paths must both call `core.fees_v2.polymarket_taker_fee_v2`.
`core/fees.py` (v1) is archived under `_archive/fee_consolidation_2026_04_21_T41/`.
`tests/integration/test_paper_shadow_divergence.py::TestSingleFeeOracle`
pins this.

### 6. 5-ghost doctrine (T6.3 + T9.7)

Every brain flag must have symmetric visibility across five layers:

1. UI button + label in `ai_handler.py`
2. `callback_data` literal
3. `valid_features` allow-list in the toggle handler
4. Engine init dict `'flag': True`
5. DB boot-restore path `brain_flags.{flag}`

Canonical set (6 flags): `ai_brain, autopilot, candle_collector,
market_recorder, regime_detection, thompson_sampling`. Adding a 7th
requires touching all five layers — regression-guarded by
`test_brain_flags_parity.py` + `test_market_recorder_parity.py` +
`test_engine_boot_smoke.py::TestBrainFlags`.

## Writing a new test

1. **Decide the layer** — unit if pure logic; integration if touching a
   construction boundary; Windows backlog if DB/network-heavy.
2. **Use a harness** — `class XHarness(XMixin): ...` for mixin modules.
3. **Pin the invariant** — each test should answer "what breaks if this
   goes RED?" in its docstring.
4. **ENV runtime re-read** — if the code reads an env var, write the
   two-setenv guard.
5. **Float precision** — use `pytest.approx(value, abs=0.001)`.
6. **Run once, then seed-check** — `pytest file -q` then `pytest file -p
   randomly --randomly-seed=N` for three seeds.
7. **Full regression before commit** — `py -3.11 -m pytest -q`. Expected:
   723 pass + 8 skip + 0 fail (as of 2026-04-22 T9.10 closure).

## Current baseline (2026-04-22)

- **Total:** 723 pass + 8 skip + 0 fail (31 files, ~731 items collected)
- **Critical-path coverage:** ~24% avg across 7 modules (was 9.7% pre-T9.6)
- **TOTAL `core/` coverage:** 21.2% (was 17.5% pre-T9.6)
- **Determinism:** GREEN across seeds 42, 1337, 9001
- **Runtime:** ~42s full suite, ~1.3s integration-only

## Epic 9 closure artifacts

- T9.1 — `INVENTORY.md`
- T9.2 — `COVERAGE_REPORT.md`
- T9.3 — `TRIAGE_MATRIX.md`
- T9.4 — `FLAKY_AUDIT.md`
- T9.5 — `5a73c7e` pre-existing fail cleanup
- T9.6 — 8 critical-path unit files (160 tests)
- T9.7 — `test_market_recorder_parity.py`
- T9.8 — `tests/integration/` (50 tests × 3 files)
- T9.9 — `pytest.ini` + `tests/integration/conftest.py`
- T9.10 — this README + `run_full_regression.bat` + `run_full_regression.sh`
