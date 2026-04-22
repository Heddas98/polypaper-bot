# Flaky / Environment-Dependent Test Audit — Epic 9 T9.4

**Date:** 2026-04-22
**Scope:** 27 pytest dosyası × 510 case — env dependency, state leakage, order dependency tespiti
**Methodology:** (a) grep tabanlı `os.environ[]` + `importlib.reload` arama, (b) `pytest-randomly` çift seed koşumu, (c) konuşma context cross-ref

## TL;DR

| Risk | Count | Files |
|---|---:|---|
| 🔴 **CRITICAL — importlib.reload leak** | 1 | `test_whale_signal.py` |
| 🟠 **HIGH — raw `os.environ[]` assign (no monkeypatch)** | 5 | test_phase70, test_pnl_pause_runtime, test_whale_signal, test_whitelist_runtime_readiness, test_ws_subscribe_cap |
| 🟡 **MEDIUM — ENV-dependent skip gap** | 2 | test_phase77_handler, test_phase82b hyperopt |
| 🟢 **LOW — safe monkeypatch usage** | 3 | monkeypatch.setenv (proper pytest idiom) |

**pytest-randomly çift seed koşumu (seed=None + seed=42):** her iki durumda da aynı 6 fail set. **Yeni flaky bulunmadı** — mevcut 6 fail deterministic (4 sabit + 2 order-dep variant).

## 🔴 CRITICAL — `importlib.reload(core.signal_fusion)` state leak

**Dosya:** `tests/unit/test_whale_signal.py` L256-289
**Test:** `test_signal_fusion_whale_signal_disabled`

```python
try:
    os.environ["WHALE_SIGNAL_ENABLED"] = "false"
    importlib.reload(core.signal_fusion)
    sf = core.signal_fusion.SignalFusion()
    ...
finally:
    if old_val is not None:
        os.environ["WHALE_SIGNAL_ENABLED"] = old_val
    else:
        os.environ.pop("WHALE_SIGNAL_ENABLED", None)
    importlib.reload(core.signal_fusion)
```

**Risk:**
1. `SignalWeights` dataclass'ı `default_factory` olarak `os.getenv()` okuyor — reload sonrası tüm ENV durumu class-level default'lara yansıyor.
2. Eğer başka test `SIGNAL_W_WHALE`, `SIGNAL_W_MOMENTUM`, vs. set bıraktıysa reload sonrası default'lar o değerleri alıyor.
3. Reload ayrıca `_WHALE_SIGNAL_ENABLED` modül-level flag'ini de resetliyor.
4. `try/finally` ikinci `reload()` original state'i geri yüklemeye çalışıyor ama **ENV snapshot tutmadan yaptığı için** başka ENV değerlerini de yeniden okuyor — ringlenen problem.

**Etki:** `test_bayesian_added_to_result` tam-suite'te FAIL, isolated PASS sebebi bu olabilir (reload sonrası SignalWeights default'ları farklı). T9.5'te kapatılacak.

**Öneri:** `importlib.reload` tamamen kaldır. Yerine `SignalFusion(weights=SignalWeights(whale_flow=0.0))` ile dependency injection test et.

## 🟠 HIGH — Raw `os.environ[]` assign (5 dosya)

Raw `os.environ["X"] = "..."` kullanımı, pytest'in `monkeypatch` fixture'una tercih edilmiş. `monkeypatch` test bittiğinde otomatik cleanup yapar; raw assign bunu manuel finalize'a bırakır — exception halinde leak olur.

| File | Lines | ENV Vars | Cleanup Risk |
|---|---|---|---|
| `test_pnl_pause_runtime.py` | 62-144 (12 assign) | PNL_PAUSE_THRESHOLD | ✅ fixture `clean_env` finally ile restore |
| `test_whale_signal.py` | 262, 286 | WHALE_SIGNAL_ENABLED | ⚠️ try/finally var ama reload da var (kritik) |
| `test_phase70.py` | 153, 161 | MCI_ENABLED | ⚠️ inline assign, fixture yok |
| `test_whitelist_runtime_readiness.py` | ? (TBD) | whitelist knob | ⚠️ kontrol edilmeli |
| `test_ws_subscribe_cap.py` | ? (TBD) | WS knobs | ⚠️ kontrol edilmeli |

**Öneri:** Tümünü `monkeypatch.setenv(key, val)` pattern'ine taşı. Fixture isolation guarantilenir.

## 🟡 MEDIUM — ENV-dependent skip gap (2 test)

Zaten T9.3'te belirlendi. Sandbox'ta fail, PROD'da geçer:

1. `test_phase77.py::test_phase77_handler` — `from telegram import ...` (python-telegram-bot yok)
2. `test_phase82b.py::TestHyperOptPipelineMutex` — `optuna` yok

**Öneri:** `@pytest.mark.skipif(not HAS_X, reason="...")` guard — T9.5'te uygulanacak.

## 🟢 LOW — Proper monkeypatch (3 kullanım)

```python
grep -c "monkeypatch\." tests/**/*.py  # → 3 occurrence
```

3 kullanım (sayı az — çoğu test raw pattern kullanıyor). **T9.9 conftest refactor**'da pytest idiom'u genişletmek hedef.

## Order-Dependency Test (pytest-randomly)

**Seed=None (default random):**
```
6 failed, 498 passed, 6 skipped in 41.68s
FAILED test_whale_signal.py ×3
FAILED test_phase77.py
FAILED test_phase66.py::test_bayesian_added_to_result
FAILED test_phase82b.py
```

**Seed=42:**
```
6 failed, 498 passed, 6 skipped in 41.04s
(aynı 6 fail)
```

**Sonuç:** Mevcut 6 fail'in tümü **deterministic**. Random order yeni flaky üretmiyor. `test_bayesian_added_to_result` her iki random order'da fail → order-dependency'nin kaynağı daha önce çalışan test'in bıraktığı state (muhtemelen `test_whale_signal.py` reload).

## Environment Variables Accessed in Tests

Audit kapsamında tüm ENV kullanımları:

**Constants set in conftest (safe):**
- `TELEGRAM_BOT_TOKEN` = "test-token"
- `ADMIN_CHAT_ID` = "0"
- `DATABASE_PATH` = ":memory:"

**Test-level assigns (audit needed):**
- `PNL_PAUSE_THRESHOLD` (pnl_pause_runtime) — fixture-protected
- `WHALE_SIGNAL_ENABLED` (whale_signal) — try/finally + reload (leak)
- `MCI_ENABLED` (phase70) — inline
- `TEST_PARAM_XYZ` (phase77) — inline
- `AUTO_RESUME_ON_STARTUP` (smoke_phase49) — pop + ctx

**Read-only (safe):**
- `CLOB_TIMEOUT`, `MAX_429_RETRIES` (phase56_engine) — read-only
- `DATABASE_PATH` (backfill_creds)

## Recommendations → T9.5 + T9.9

**T9.5 (6 pre-existing fail cleanup) — acil:**
1. whale_signal importlib.reload kaldır → SignalFusion(weights=SignalWeights(...)) DI pattern
2. whale_signal raw os.environ → monkeypatch.setenv
3. phase66 `test_bayesian_added_to_result` → test dosyası seviyesinde ENV isolation fixture (autouse)
4. phase77_handler → skipif guard
5. phase82b hyperopt → skipif guard

**T9.9 (conftest refactor) — backlog:**
1. Tüm `os.environ[X] = val` pattern'ini `monkeypatch.setenv(X, val)` ile refactor.
2. `tests/conftest.py`'ye global autouse fixture ekle: her test başında `SIGNAL_W_*`, `WHALE_*`, `MCI_*` knob'larını temizle.
3. `importlib.reload` kullanımı lintlensin — pyproject.toml `[tool.pylint.messages_control]` ile warning.

## Impact on coverage

T9.4 sonuçları T9.2 raporu ile cross-ref:
- `signal_fusion.py` 59.7% coverage — `SignalWeights` dataclass default'ları test tarafından reload'la zorlanıyor ama bu coverage'ın zayıf kaldığını gösteriyor. T9.6'da `signal_fusion.evaluate()` için direct unit test'ler yazılacak (DI pattern'li, reload'suz).
- `core/signals/whale_flow.py` 88.8% coverage — bu iyi, whale_flow modülü sağlam. Sorun fusion weight formülünde.

## Exit criteria

T9.4 complete when:
- [x] Tüm raw `os.environ[]` kullanımları listelendi (5 dosya)
- [x] importlib.reload tehlikesi tespit edildi (1 dosya, 2 çağrı)
- [x] Random order koşumu (çift seed) çalıştırıldı — yeni flaky yok
- [x] Fail kategorizasyonu T9.3 ile uyumlu
- [x] T9.5/T9.9 önerileri concrete action item

Sonraki adım: **T9.5** — T9.3+T9.4 kararlarını uygula.

---

## 📝 Addendum — T9.10 seed 1337 WS smoke race (2026-04-22)

**Discovery:** Epic 9 T9.10 3-seed determinism check (`./run_full_regression.sh seed 1337`) caught a transient 1-test fail **on first run only**:

```
tests/integration/test_ws_reconnect_smoke.py::TestBaselineHealthy::test_all_three_served
assert ws.get_live_price("btc-up") == 0.52
AssertionError: assert None == 0.52
```

**Root cause (dual):**

1. **Microsecond clock race.** The fixture `ws_with_three_markets` set
   `_connected_since = time.time()` then seeded live_prices with
   `datetime.now(timezone.utc)`. After ISO roundtrip through
   `datetime.fromisoformat(...).timestamp()`, the float values could
   collide on the same tick, making the gate
   `entry_dt.timestamp() < self._connected_since` falsely True and
   returning None.

2. **Latent env-leak risk.** 4 tests write `os.environ[KEY] = val`
   directly (pnl_pause_runtime, phase56_engine, phase70, ws_subscribe_cap).
   None target `WS_STALE_SEC`, but a future test that does could
   break the WS smoke fixture via the same `os.getenv("WS_STALE_SEC")`
   runtime read path.

**Fix (commit `4a06ea5`):** harden the fixture itself — pin
`WS_STALE_SEC=60` via `monkeypatch.setenv` (env-independent) and offset
`_connected_since` 1s into the past (race-independent).

**Verification:** 3-seed green sweep post-fix:
- seed 42 — 723 pass + 8 skip + 0 fail
- seed 1337 — 723 pass + 8 skip + 0 fail
- seed 9001 — 723 pass + 8 skip + 0 fail

**Doctrine:** integration fixtures that depend on env-readable gates
SHOULD pin the relevant env via `monkeypatch.setenv` inside the fixture
rather than rely on suite-level env hygiene.
