# Coverage Report — Epic 9 T9.2

**Date:** 2026-04-22
**Source:** `python -m coverage run -m pytest tests/` (510 collected, 498 pass + 6 skip + 6 pre-existing fail)
**Tool:** coverage.py 7.13.5 (branch=True)
**Config:** `.coveragerc` (source=core, omit=_archive/tests/__pycache__/dead_code_nuke)
**HTML rapor:** `htmlcov/index.html` (gitignored)

## TL;DR

| Metric | Value | Bucket |
|---|---|---|
| **TOTAL core/** | **17.5%** | (8625 stmts × 6943 miss × 2914 branches × 122 bpart) |
| **Critical-path aggregate** | **9.7%** | 4539 stmts × 4098 miss (8 hot-path modül) |
| **Uncovered (0%)** | **21 modül** | %52 dosya sayısı |
| **Good (>60%)** | 7 modül | %18 dosya sayısı |
| **Excellent (>90%)** | 2 modül | %5 (fees_v2 + signals/__init__) |

Kritik yargı: **Bot'un hot-path'i (engine, live_trader, engine_fills, engine_monitor)
sıfır test satırı çalıştırıyor.** 510 test 8625 stmt'nin sadece 1682'sini dokunduruyor.
Regression test suit'ı şu an **birim testler + 4 Epic faz'ının ayaküstü snapshot'ları** üzerine oturmuş — integration smoke ve hot-path unit test boş.

## Full Breakdown (by bucket)

### 🔴 UNCOVERED (0.0%) — 21 modül

Test tarafından **hiçbir satırı çalıştırılmamış** modüller. P1 (critical path hot) / P2 (high-traffic) / P3 (auxiliary) öncelik kolonu T9.6 planlaması için.

| Coverage | Stmts | Missing | Module | Priority | Motivasyon |
|---:|---:|---:|---|:-:|---|
| 0.0% | 678 | 678 | `core/engine.py` | **P1** | Bot'un kalbi — 3 supervisor loop + main evaluate + fill flow. 0 test. |
| 0.0% | 280 | 280 | `core/engine_fills.py` | **P1** | Fill lifecycle + stuck cancel (v6 hotfix). 0 test. |
| 0.0% | 218 | 218 | `core/engine_monitor.py` | **P1** | `/risk` `/metrics` `/diagnose` veri kaynağı. 0 test. |
| 0.0% | 271 | 271 | `core/live_trader.py` | **P1** | Shadow-mirror CLOB path. ENV-override live ama test 0 (T7.6 post-audit A5 sadece ENV guard ekledi). |
| 0.0% | 190 | 190 | `core/strategy_lifecycle.py` | **P1** | Strategy activate/pause/kill. 0 test. |
| 0.0% | 102 | 102 | `core/trade_journal.py` (üstten sadece import edilmiş) | P2 | Audit log + weekly PnL. GC-safe refactor sonrası 0 test. |
| 0.0% | 40 | 40 | `core/kill_switch.py` | **P1** | Emergency stop. 0 test — kritik. |
| 0.0% | 124 | 124 | `core/autopilot.py` | P2 | Brain flag gate logic (Epic 6 T6.3c). 0 test. |
| 0.0% | 93 | 93 | `core/regime.py` | P2 | Market regime detector. 0 test. |
| 0.0% | 60 | 60 | `core/circuit_breaker.py` | P2 | Rate/error limit guard. 0 test. |
| 0.0% | 118 | 118 | `core/becker_rolling_recal.py` | P3 | Kalibrasyon rolling. 0 test. |
| 0.0% | 102 | 102 | `core/becker_weight_tracker.py` | P3 | Weight tracking. 0 test. |
| 0.0% | 103 | 103 | `core/micro_weight_tracker.py` | P3 | Micro weight. 0 test. |
| 0.0% | 89 | 89 | `core/strategy_selector.py` | P3 | Selector logic. 0 test. |
| 0.0% | 224 | 224 | `core/strategy_suggester.py` | P3 | Suggestion engine. 0 test. |
| 0.0% | 188 | 188 | `core/intent_parser.py` | P3 | Telegram intent. 0 test. |
| 0.0% | 118 | 118 | `core/keepalive.py` | P3 | Keepalive loop. 0 test. |
| 0.0% | 72 | 72 | `core/changelog.py` | P3 | Changelog tracker. 0 test. |
| 0.0% | 54 | 54 | `core/ev_tracker.py` | P3 | EV telemetry. 0 test. |
| 0.0% | 35 | 35 | `core/observability/__init__.py` | P3 | Observability base. 0 test. |
| 0.0% | 77 | 77 | `core/observability/rest_timing.py` | P3 | T4.9 REST timing stub (henüz entegre değil). Bekleniyor. |
| 0.0% | 20 | 20 | `core/stats_utils.py` | P3 | T7.6 post-audit extract. Pearson/Spearman helpers — 3 çağıran modül dolaylı test ediyor ama direkt coverage 0. |

**Toplam uncovered:** 3309 stmt (core/'in %38'i).

### 🟠 LOW (<20%) — 6 modül

| Coverage | Stmts | Missing | Module | Priority | Notlar |
|---:|---:|---:|---|:-:|---|
| 4.7% | 1123 | 1054 | `core/engine_signals.py` | **P1** | Engine signal compute — en büyük hot-path. 69 stmt covered (mostly imports + constant parsing). |
| 6.0% | 1120 | 1033 | `core/ai_brain.py` | **P1** | LLM brain + T8.2 rate-limit guard. 87 stmt covered. |
| 8.1% | 353 | 321 | `core/engine_settlement.py` | **P1** | Settlement path. 32 stmt covered. |
| 9.2% | 381 | 337 | `core/auto_optimizer.py` | **P1** | ROLLING_WR + pause logic. 44 stmt covered (T6.1 + T7.6 B8 testlerinden gelen dolaylı). |
| 10.5% | 50 | 42 | `core/indicators.py` | P2 | TA indicators. 8 stmt. |
| 16.1% | 21 | 16 | `core/becker_calibration.py` | P3 | Calibration shim. |

**Toplam low:** 3048 stmt × 2803 missing.

### 🟡 MEDIUM (20-60%) — 6 modül

| Coverage | Stmts | Missing | Module | Priority | Notlar |
|---:|---:|---:|---|:-:|---|
| 20.8% | 102 | 77 | `core/trade_journal.py` | P2 | GC-safe refactor sonrası. Indirect via Epic 7 B6 testleri. |
| 39.7% | 765 | 417 | `core/strategy_plugins.py` | P2 | strategy_plugins çok sayıda branch. |
| 42.6% | 117 | 64 | `core/kelly.py` | P2 | Kelly sizing. Epic 6 T6.5 DB persist testleri covered. |
| 44.1% | 76 | 41 | `core/engine_support.py` | P2 | Support utilities. |
| 57.0% | 100 | 40 | `core/bg_task.py` | P2 | Epic 7 B6 ref capture testleri. |
| 59.7% | 331 | 111 | `core/signal_fusion.py` | P1 | Phase 74 fusion. phase66/74/84 testlerinden gelen coverage. |

### 🟢 GOOD (60-90%) — 5 modül

| Coverage | Stmts | Missing | Module | Notlar |
|---:|---:|---:|---|---|
| 62.1% | 333 | 124 | `core/risk_manager.py` | Epic 3 T3.x + T6.x audit testleri. Per-asset limit round-trip covered. |
| 69.9% | 186 | 46 | `core/trade_memory.py` | Memory fixture testleri. |
| 74.7% | 140 | 28 | `core/decision_explainer.py` | Decision explainer. |
| 75.5% | 152 | 28 | `core/experiment_runner.py` | Experiment runner. |
| 88.8% | 74 | 6 | `core/signals/whale_flow.py` | Whale signal komponent — **ama `signal_fusion.py`'deki whale fusion coverage YOK** (Bulgu D / Task #44). |

### 🔵 EXCELLENT (>90%) — 2 modül

| Coverage | Stmts | Miss | Module | Notlar |
|---:|---:|---:|---|---|
| 91.5% | 45 | 4 | `core/fees_v2.py` | Single fee oracle — Epic 4 T4.1 closure'dan kaynaklı |
| 100% | 2 | 0 | `core/signals/__init__.py` | Trivial |

## Critical-Path Drill-Down

Bot'un ana hot-path'i (INVENTORY.md'de belirlenen 8 modül):

| Modül | Coverage | Risk |
|---|---:|---|
| `core/engine.py` | 0.0% | 🔴 CRITICAL — main evaluate loop + 3 supervisor loop 0 test. |
| `core/engine_fills.py` | 0.0% | 🔴 CRITICAL — v6 fill starvation hotfix (Phase 82e Sprint 5), TAKER stuck auto-cancel 120s. 0 test. |
| `core/engine_settlement.py` | 8.1% | 🟠 Settlement path — 28 bare-except narrow edildi (T1.4 Faz 1) ama test 8%. |
| `core/engine_signals.py` | 4.7% | 🟠 Signal compute — 1054 missing stmt. |
| `core/live_trader.py` | 0.0% | 🔴 CRITICAL — shadow-mirror live path. LIVE_ENABLED=false olsa bile mantık canlı. |
| `core/auto_optimizer.py` | 9.2% | 🟠 Pause + rolling WR logic. T6.1 + T7.6 B8 testleri dolaylı. |
| `core/ai_brain.py` | 6.0% | 🟠 T8.2 LLM rate-limit guard + cost caps. |
| `core/risk_manager.py` | 62.1% | 🟢 En iyi coverage'a sahip hot-path modülü. |

**Aggregate: 4539 stmt × 4098 missing = 9.7% coverage.**

## Branch Coverage

`branch=True` config'i açık. 2914 toplam branch, 122 bpart (kısmen dallanmış). Branch hit rate da %17.5 seviyesinde — line coverage ile paralel seyrediyor.

## Missing Test Types (INVENTORY.md cross-ref)

INVENTORY.md'deki 6 test pattern'inden **integration + smoke** neredeyse yok:
- Integration tests: `tests/test_phase77.py` (tek dosya, 1 fail).
- Smoke tests: `tests/smoke_phase49.py` + `smoke_phase51.py` (2 dosya, pytest discovery dışında standalone).
- **Engine boot smoke yok** — T9.8 ana hedef.
- **Fill lifecycle end-to-end smoke yok** — T9.8 ikinci hedef.
- **Shadow-live divergence smoke yok** — T9.8 üçüncü hedef.

## Top-Level Recommendations → T9.6 priority sırası

Coverage gap analizi **T9.6 kritik-path regression fill** planını doğruluyor:

1. **P1 Tier 1 — Hot-path zero-coverage (3 modül):**
   - `engine.py` → evaluate loop smoke + supervisor loop survival test
   - `engine_fills.py` → fill flow + stuck cancel (Phase 82e Sprint 5 invariant)
   - `live_trader.py` → ENV gate + LIVE_ENABLED=false guard + signal threshold

2. **P1 Tier 2 — Low-coverage HIGH-risk (2 modül):**
   - `engine_signals.py` (4.7%) → signal fusion + gate invariants
   - `ai_brain.py` (6.0%) → T8.2 rate-limit cooldown + MIN_COST anti-bypass

3. **P2 — Auxiliary hot-path (3 modül):**
   - `engine_monitor.py` (0%) — `/risk` `/metrics` doğruluğu
   - `engine_settlement.py` (8.1%) — settlement + unsellable path
   - `kill_switch.py` (0%) — emergency stop sanity

4. **P3 — Non-hot coverage fills:** strategy_plugins / strategy_selector / regime / intent_parser — backlog, Epic 10+.

## Exit Criteria (T9.6 için)

- **Critical-path aggregate:** 9.7% → ≥60% hedef.
- **TOTAL core/:** 17.5% → ≥40% hedef.
- **Zero-coverage hot-path:** engine + engine_fills + live_trader + engine_monitor + kill_switch ≥ %30 her biri.
- Bu hedeflerle Epic 9 mainnet-ready exit criterion karşılanıyor.

## Dosyalar

- `.coveragerc` — config (committed)
- `htmlcov/` — HTML report (gitignored)
- `coverage.json` — raw JSON (gitignored)
- `.coverage` — binary data (gitignored)

Sonraki adım: **T9.3** — 6 pre-existing fail'in detaylı triage'ı ve fix/skip/delete kararı. Bulgu D (whale_signal ×3) için Task #44 drain, diğer 3 için ayrı ayrı kök neden.
