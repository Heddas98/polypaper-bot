# PolyPaper Bot — Test Envanteri

**Tarih:** 2026-04-22 (Epic 9 T9.1)
**Kapsam:** 27 pytest dosyası + 1 standalone script + 2 smoke (legacy) + 1 conftest
**Toplam pytest case:** 510 (498 pass + 6 skip + 6 pre-existing fail)
**Amaç:** Her test dosyasının hangi faz/modül için yazıldığını belgelemek, "hâlâ anlamlı mı?" sorusuna karar vermek. T9.6'da coverage fill yaparken hangi alanın zaten örtülü olduğunu bilmek için kanonik başvuru.

---

## Özet Tablo

| Dosya | Case | Faz / Epic | Hedef Modül | Pass | Status |
|---|---:|---|---|:-:|---|
| `tests/test_phase55_critical.py` | 39 | Phase 55 (money-path) | risk_manager, fees_v2, kelly, websocket_client | ✅ | **LIVE** — kanonik P0 regression suite |
| `tests/test_phase56_engine.py` | 60 | Phase 56C (engine robustness) | engine, risk, fees_v2, kelly, atomic_deduct, settlement, safe_html | ✅ | **LIVE** — en büyük suite, unittest stili |
| `tests/test_phase77.py` | 28 | Phase 76+77 (AI/memory) | trade_memory, decision_explainer, experiment_runner, Markov, capital_allocator, BondingYield | ⚠️ **1 FAIL** | **LIVE but Handler import fail** — T9.3.a |
| `tests/test_phase82_metadata.py` | 15 | Phase 82 (plugin metadata) | strategy_plugins (6 live strategy + cache) | ✅ | **LIVE** |
| `tests/test_risk_limits_roundtrip.py` | 22 | Epic 3 T3.4 | risk_manager::RiskLimits to_dict/from_dict | ✅ | **LIVE CURRENT** |
| `tests/test_backfill_creds.py` | 0 | Phase 55 P55-05 | CLOB creds env check | — | **STALE** — pytest değil, standalone script (`def check_creds()`) |
| `tests/unit/test_atomic_deduct.py` | 6 | Epic 5 T5.2 | db/database::atomic_deduct_balance | ✅ | **LIVE CURRENT** |
| `tests/unit/test_bg_task_ref_capture.py` | 7 | Epic 7 B6 | core/bg_task::_BG_TASK_OBJECTS strong-ref | ✅ | **LIVE CURRENT** (2026-04-22) |
| `tests/unit/test_boot_sibling_sync.py` | 9 | Epic 6 T6.3e-fix-2 | engine::_sibling_gates sync (AST) | ✅ | **LIVE CURRENT** |
| `tests/unit/test_brain_flags_parity.py` | 12 | Epic 6 T6.3 | engine.brain_flags ↔ ai_handler UI parity (AST) | ✅ | **LIVE CURRENT** |
| `tests/unit/test_error_templates.py` | 4 | Phase 47f.10 P2#12 | telegram_bot/templates/errors::ERR + fmt_error | ✅ | **LIVE stable** |
| `tests/unit/test_fees_v2.py` | 12 | Phase 74+ (fees_v2 canonical) | core/fees_v2 | ✅ | **LIVE CURRENT** (T4.1 arşiv sonrası) |
| `tests/unit/test_kelly_mode_persistence.py` | 11 | Epic 6 T6.5 | engine.kelly_mode DB persist (AST) | ✅ | **LIVE CURRENT** |
| `tests/unit/test_pending_reserved.py` | 11 | Epic 5 T5.3 | engine_signals::_compute_pending_reserved (shim) | ✅ | **LIVE CURRENT** |
| `tests/unit/test_phase66.py` | 23 | Phase 66 (measurement) | signal_fusion::BayesianUpdater, SignalFusion, SignalWeights, risk_manager | ⚠️ **1 FAIL** | **LIVE but bayesian default drift** — T9.3.b |
| `tests/unit/test_phase67.py` | 15 | Phase 67 (param opt) | utils/mc_simulation::MonteCarloKelly, backtest/hyperopt | ✅ | **LIVE** (telegram import-skipli) |
| `tests/unit/test_phase68.py` | 21 | Phase 68 (signal enhance) | indicators/technical (RSI, Confluence Gate, BB Squeeze) | ✅ | **LIVE** |
| `tests/unit/test_phase69.py` | 20 | Phase 69 (AI Brain) | ai_brain::ModelRouter, reputation, champion tracker | ✅ | **LIVE** |
| `tests/unit/test_phase70.py` | 36 | Phase 70 (calibration) | calibration/surface_2d, MCI, penny contracts, EV threshold | ✅ | **LIVE** |
| `tests/unit/test_phase73.py` | 45 | Phase 73 (code quality) | skills/ema, volatility, orderbook + Sharpe/Sortino + Kelly Decay | ✅ | **LIVE** (unittest) |
| `tests/unit/test_phase82b.py` | 13 | Phase 82b (hyperopt IPC+mutex) | backtest/hyperopt_ipc, PidFileLock, ProgressState | ⚠️ **1 FAIL** | **LIVE but hyperopt mutex fail** — T9.3.c |
| `tests/unit/test_pnl_pause_runtime.py` | 8 | Epic 6 T6.1 | auto_optimizer::_get_pnl_pause_threshold runtime re-read | ✅ | **LIVE CURRENT** |
| `tests/unit/test_risk_manager.py` | 12 | core/risk_manager genel gate'ler | risk_manager gates | ✅ | **LIVE CURRENT** |
| `tests/unit/test_wal_checkpoint.py` | 6 | Epic 5 T5.5 | SQLite PRAGMA wal_checkpoint(TRUNCATE) | ✅ | **LIVE CURRENT** |
| `tests/unit/test_whale_signal.py` | 16 | Phase 60 (whale flow) | core/signals/whale_flow::WhaleFlowSignal + signal_fusion integration | ⚠️ **3 FAIL** | **LIVE but fusion weight=0** — T9.3.d-f / Task #44 |
| `tests/unit/test_whitelist_runtime_readiness.py` | 35 | Epic 6 T6.4 | config/env_whitelist + AST module-top scan | ✅ | **LIVE CURRENT** (T6.4 + T6.1 + T7.6 B8 kapsamı) |
| `tests/unit/test_ws_reconnect.py` | 10 | Epic 5 T5.4 | data/websocket_client::_connected_since + REST backfill | ✅ | **LIVE CURRENT** |
| `tests/unit/test_ws_subscribe_cap.py` | 14 | Epic 5 T5.6 | websocket_client::prune_stale_tokens + scanner wiring | ✅ | **LIVE CURRENT** |

**Toplam:** 510 pytest case (test_backfill_creds.py hariç). Genel pass oranı **%97.6** (498/510), pre-existing fail %1.2.

---

## Kategori Analizi

### A. Kanonik "LIVE CURRENT" testler (2026-04 sonrası — T7.6/Epic 6/Epic 7 itibariyle yazılmış veya güncellenmiş)

Bu testler mevcut doktrinleri (T6.1 ghost-toggle, T6.3 brain_flags parity, T6.4 whitelist guard, T6.5 Kelly persist, T5.2-T5.6 WS/DB ailesi, T7.6 B6 bg_task GC, T8.2 LLM rate-limit) koruyor. **Dokunma** — sadece coverage genişletilir.

- `test_atomic_deduct.py` — 6 case / Epic 5 T5.2
- `test_bg_task_ref_capture.py` — 7 case / Epic 7 B6
- `test_boot_sibling_sync.py` — 9 case / Epic 6 T6.3e
- `test_brain_flags_parity.py` — 12 case / Epic 6 T6.3
- `test_fees_v2.py` — 12 case / Phase 74+ (fees_v2 canonical)
- `test_kelly_mode_persistence.py` — 11 case / Epic 6 T6.5
- `test_pending_reserved.py` — 11 case / Epic 5 T5.3
- `test_pnl_pause_runtime.py` — 8 case / Epic 6 T6.1
- `test_risk_limits_roundtrip.py` — 22 case / Epic 3 T3.4
- `test_risk_manager.py` — 12 case / core gates genel
- `test_wal_checkpoint.py` — 6 case / Epic 5 T5.5
- `test_whitelist_runtime_readiness.py` — 35 case / Epic 6 T6.4 + T7.6 B8
- `test_ws_reconnect.py` — 10 case / Epic 5 T5.4
- `test_ws_subscribe_cap.py` — 14 case / Epic 5 T5.6
- `test_error_templates.py` — 4 case / Phase 47f.10 P2#12 (stable)

**Alt toplam:** 179 case, 15 dosya, 0 fail.

### B. Büyük faz-regression suite'leri (legacy ama hâlâ anlamlı)

Phase 55-82 arası dönemsel büyük suite'ler. Modern T6/T7/T8 fix'leri ile hâlâ uyumlu — ama güncelleme gerektiğinde "hangi invariant korunuyor?" sorusuyla bakılmalı.

- `test_phase55_critical.py` — 39 case / kanonik money-path (risk, fees, kelly, WS, settlement)
- `test_phase56_engine.py` — 60 case / Phase 56C engine+DB+robustness (unittest)
- `test_phase73.py` — 45 case / skills + performance metrics + Kelly decay (unittest)
- `test_phase70.py` — 36 case / calibration 2D + MCI + penny + EV
- `test_phase77.py` — 28 case / AI brain ailesi (trade_memory, decision_explainer, experiment_runner, Markov)
- `test_phase68.py` — 21 case / technical indicators (RSI, Confluence, BB)
- `test_phase69.py` — 20 case / AI brain ModelRouter + reputation
- `test_phase67.py` — 15 case / MC Kelly + HyperOpt components (telegram-conditional)
- `test_phase82_metadata.py` — 15 case / plugin metadata cache + 6 live strategies

**Alt toplam:** 279 case, 9 dosya, 1 fail (`test_phase77_handler`).

### C. Fail içeren testler (T9.3 triage hedefleri)

- `test_phase77.py` — **1 fail** / `TestHandlerImports::test_phase77_handler`
  - Hipotez: Handler dosya yolu Epic 2 root cleanup'tan sonra değişti veya Phase 77 handler arşivlendi.
  - T9.3.a aksiyonu: `import` yolunu grep'le doğrula, ya path düzelt ya xfail.
- `test_phase66.py` — **1 fail** / `TestSignalFusionBayesian::test_bayesian_added_to_result`
  - Hipotez: `BAYESIAN_ENABLED` ENV default değişti veya `SignalWeights` default değişti.
  - T9.3.b aksiyonu: git blame signal_fusion.py + grep `BAYESIAN` — ENV default'u test'e reddet veya conftest'e fix'le.
- `test_phase82b.py` — **1 fail** / `TestHyperOptPipelineMutex::test_pipeline_optimize_bails_when_lock_held`
  - Hipotez: optuna sandbox'ta yok veya asyncio lock timing race.
  - T9.3.c aksiyonu: optuna eksikse `pytest.importorskip("optuna")`; timing ise `asyncio.wait_for` narrow.
- `test_whale_signal.py` — **3 fail** / Bulgu D / Task #44
  - `test_signal_fusion_includes_whale_weight`
  - `test_signal_result_includes_whale_signal`
  - `test_signal_fusion_whale_signal_in_composite`
  - Hipotez: `SignalWeights().whale_flow = 0.0` default (test `>0` bekliyor). whale=0.0 ve whale=0.5 composite skor identical (`0.16959...`) — fusion'da whale katkısı sıfır.
  - T9.3.d-f aksiyonu: git blame + phase history → (a) weight kasıtlı 0'sa test güncelle, (b) regression ise default restore.

### D. Standalone / smoke scripts (pytest değil)

- `tests/test_backfill_creds.py` — CLOB creds script, `def check_creds()`, standalone Python.
- `tests/smoke_phase49.py`, `tests/smoke_phase51.py` — eski dönem smoke, muhtemelen arşiv adayı.

**Karar:** T9.10'da `tests/README.md` içinde "pytest ile koşturulmayan manual scripts" başlığı altında belgelenir. Şimdilik dokunma.

---

## Modül × Test Kapsamı Haritası

Aşağıda 38 core/ modülünün hangi test dosyalarında (ana veya yardımcı) örtüldüğünü gösteren ters indeks. Coverage.py raporu (T9.2) bu tabloyu sayısallaştıracak.

| core/ modül | Test dosyaları |
|---|---|
| `core/ai_brain.py` | test_phase69, test_phase77 (partial) |
| `core/auto_optimizer.py` | test_pnl_pause_runtime, test_whitelist_runtime_readiness (AST) |
| `core/autopilot.py` | test_brain_flags_parity (AST indirect) |
| `core/becker_calibration.py` | ❌ **coverage yok** |
| `core/becker_rolling_recal.py` | ❌ **coverage yok** |
| `core/becker_weight_tracker.py` | ❌ **coverage yok** |
| `core/bg_task.py` | test_bg_task_ref_capture |
| `core/changelog.py` | ❌ **coverage yok** |
| `core/circuit_breaker.py` | ❌ **coverage yok** (dolaylı: test_phase56_engine) |
| `core/decision_explainer.py` | test_phase77 |
| `core/engine.py` | test_phase56_engine, test_boot_sibling_sync (AST), test_brain_flags_parity (AST), test_kelly_mode_persistence (AST) |
| `core/engine_fills.py` | test_phase56_engine (partial) |
| `core/engine_monitor.py` | ❌ **coverage yok** |
| `core/engine_settlement.py` | test_phase56_engine (partial) |
| `core/engine_signals.py` | test_pending_reserved, test_phase55_critical (partial) |
| `core/engine_support.py` | ❌ **coverage yok** |
| `core/ev_tracker.py` | ❌ **coverage yok** (dolaylı: test_phase70) |
| `core/experiment_runner.py` | test_phase77 |
| `core/fees_v2.py` | test_fees_v2, test_phase55_critical, test_phase56_engine |
| `core/indicators.py` | test_phase68 (indirect — indicators/technical.py) |
| `core/intent_parser.py` | ❌ **coverage yok** |
| `core/keepalive.py` | ❌ **coverage yok** |
| `core/kelly.py` | test_phase55_critical, test_phase56_engine, test_phase67 |
| `core/kill_switch.py` | ❌ **coverage yok** |
| `core/live_trader.py` | ❌ **coverage yok** (kritik açık — T9.6 P2) |
| `core/micro_weight_tracker.py` | ❌ **coverage yok** |
| `core/observability/rest_timing.py` | ❌ **coverage yok** |
| `core/regime.py` | ❌ **coverage yok** |
| `core/risk_manager.py` | test_risk_manager, test_risk_limits_roundtrip, test_phase55_critical, test_phase56_engine, test_phase66 |
| `core/signal_fusion.py` | test_phase66, test_whale_signal |
| `core/signals/whale_flow.py` | test_whale_signal |
| `core/stats_utils.py` | ❌ **coverage yok** (T7.6 post-audit yeni dosya) |
| `core/strategy_lifecycle.py` | ❌ **coverage yok** |
| `core/strategy_plugins.py` | test_phase82_metadata |
| `core/strategy_selector.py` | ❌ **coverage yok** |
| `core/strategy_suggester.py` | ❌ **coverage yok** |
| `core/trade_journal.py` | ❌ **coverage yok** |
| `core/trade_memory.py` | test_phase77 |

**Coverage açığı özeti (17 modül ❌):**
- **P1 (kritik hot-path, mutlaka T9.6):** `live_trader.py`, `engine_monitor.py`, `strategy_lifecycle.py`, `trade_journal.py`, `kill_switch.py`
- **P2 (orta):** `becker_weight_tracker.py`, `micro_weight_tracker.py`, `becker_rolling_recal.py`, `ev_tracker.py`, `strategy_suggester.py`, `strategy_selector.py`, `regime.py`
- **P3 (düşük — integration veya read-only):** `becker_calibration.py`, `changelog.py`, `intent_parser.py`, `keepalive.py`, `stats_utils.py`, `observability/rest_timing.py`

---

## Test Pattern Kataloğu (ne tür test yazıldığını görmek için)

1. **Logic isolation (pure)** — DB/network yok, in-memory state. Örnek: `test_fees_v2.py`, `test_risk_limits_roundtrip.py`.
2. **AST-based structural contract** — Kod yapısını grep ile pin'liyor, bootstrap gerektirmiyor. Örnek: `test_boot_sibling_sync.py`, `test_brain_flags_parity.py`, `test_whitelist_runtime_readiness.py`. Avantaj: telegram/httpx/websockets dep'siz koşuyor.
3. **Fixture-backed async DB** — `pytest_asyncio` + `tempfile.mkstemp` + in-memory SQLite. Örnek: `test_atomic_deduct.py`, `test_wal_checkpoint.py`.
4. **Helper shim** — Gerçek sınıfı mock'lamak yerine minimum shim yazıp kontrat pinning. Örnek: `test_pending_reserved.py::_HelperShim`.
5. **Mock-based integration** — `unittest.mock.AsyncMock` + dataclass stub. Örnek: `test_whale_signal.py`.
6. **unittest.TestCase stili** — Yeni test yazarken tercih edilmiyor ama eski phase suite'leri (Phase 56, Phase 73) bu stilde. Karıştırma.

**T9.6 için yönlendirme:** Yeni yazılacak critical-path test'ler **Pattern 2 veya 3**'ü tercih edecek. Pattern 6 legacy-only.

---

## Stale / Arşiv Adayları

- `tests/smoke_phase49.py`, `tests/smoke_phase51.py` — pytest tarafından toplanmıyor (`smoke_` prefix). Epic 7 T7.4 kapsamında zaten smoke audit yapıldı; bu ikisi `scripts/smoke_*.py` ile örtüşüyor olabilir. **Karar:** T9.10 `tests/README.md`'de yerlerini belgele, arşivleme kararı Epic 11 cleanup'ta.
- `tests/test_backfill_creds.py` — pytest değil. Belgelenmiş bir CLI doğrulayıcı. **Karar:** taşıma; `tests/README.md`'de "manual smoke" bölümü oluştur.

---

## Çıkarımlar (T9.2 - T9.10 için input)

1. **Kanonik core kütüphanesi korumada** — T5/T6/T7 ailesi 15 "LIVE CURRENT" dosya ile pin'lenmiş. Bu alan stabil.
2. **Büyük coverage açığı live_trader + engine_monitor'da** — T9.6 P1 önceliği buraya.
3. **6 pre-existing fail'in kökleri tespit edildi** — T9.3'te her biri için karar verilebilir durumda.
4. **17 core/ modülde hiçbir test yok** — bunların 5'i hot-path (P1), diğerleri öncelik sırasıyla örtülür.
5. **AST-based testler sandbox dostu** — ağır dep'ler gerektirmiyor, CI'da güvenle koşar. Yeni critical-path testlerde de öncelenmeli.
6. **Pattern karışıklığı:** unittest.TestCase + pytest pytest-asyncio + AST → conftest.py fixture konsolidasyonu T9.9'da kritik.

---

**Sonraki adım:** T9.2 — `coverage.py` ile satır bazında rapor, yukarıdaki coverage açığı iddialarını sayısallaştıracak.
