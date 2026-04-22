# Pre-Existing Fail Triage Matrix — Epic 9 T9.3

**Date:** 2026-04-22
**Scope:** 6 pre-existing fail across 4 files (full suite stable baseline)
**Output:** fix / skip / delete karar matrisi + her fail için kök neden + eylem planı

## Summary

| # | Test | File | Fail Type | Decision |
|:-:|---|---|---|---|
| 1 | `test_phase77_handler` | `tests/test_phase77.py` | ENV (sandbox) | 🟡 **SKIP guarded** — `skipif(no telegram)` |
| 2 | `test_no_direction_no_bayesian` | `tests/unit/test_phase66.py` | Stale logic expectation | 🔴 **FIX test** — current behavior correct, test stale |
| 3 | `test_bayesian_added_to_result` | `tests/unit/test_phase66.py` | Flaky (state-leak) | 🟠 **FIX fixture** — conftest ENV isolation |
| 4 | `test_pipeline_optimize_bails_when_lock_held` | `tests/unit/test_phase82b.py` | ENV (sandbox) | 🟡 **SKIP guarded** — `skipif(no optuna)` |
| 5 | `test_signal_fusion_includes_whale_weight` | `tests/unit/test_whale_signal.py` | Stale (Bulgu D) | 🔴 **FIX test** — Phase 60 whale weight default 0.10→0.00 |
| 6 | `test_signal_fusion_whale_signal_in_composite` | `tests/unit/test_whale_signal.py` | Stale (Bulgu D) | 🔴 **FIX test** — needs `SIGNAL_W_WHALE=0.10` fixture |

**Karar dağılımı:** 2 SKIP + 4 FIX (0 DELETE).

## Detailed Analysis

### #1 — `test_phase77_handler` (ENV sandbox)

**Error:**
```
ModuleNotFoundError: No module named 'telegram'
telegram_bot/handlers/phase77_handler.py:16: in <module>
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
```

**Root cause:** Sandbox'ta `python-telegram-bot==21.6` yok. Windows PROD ortamında library kurulu. Test sandbox-incompatible ama PROD'da valid.

**Doğrulama:** `python -c "import telegram"` sandbox'ta fail, PROD'da pass.

**Karar:** **SKIP guarded.** Testi tutuyoruz (PROD'da değerli), sandbox'ta skip.

**Eylem:**
```python
import pytest
try:
    import telegram  # noqa: F401
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

@pytest.mark.skipif(not HAS_TELEGRAM, reason="python-telegram-bot not installed (sandbox)")
def test_phase77_handler(self):
    ...
```

### #2 — `test_no_direction_no_bayesian` (Stale logic expectation)

**Error:**
```
AssertionError: assert 0.2222222222222222 == 0
 +  where 0.2222 = SignalResult(...).bayesian_posterior
```

**Root cause:** Test ismi "no_direction" ama gerçekte `direction="up"` geçiyor (L90). Current kod direction belirtilmişse hem `up_odds=0.30` hem `down_odds=0.30` eşit olsa bile bayesian_posterior hesaplıyor. Test, eski behavior'a göre yazılmış — o zaman eşit odds'ta `bayesian_posterior=0` dönüyordu, artık prior'la start edip update-less bırakıyor.

**Current behavior doğru:** Direction belirtilmişse Bayesian her zaman posterior döner (0.22 = prior 0.50'den signal zayıflığı ile aşağı kaymış). `== 0` expectation anlamını kaybetmiş.

**Karar:** **FIX test.** İki yol:
- (a) Test adı `test_direction_given_bayesian_populated` yap, assert `> 0`.
- (b) Test intent'ini koru: `direction=None` ile test et, o zaman `bayesian_posterior == 0` mantıklı olur.

**Tercih:** (b) — test adı niyeti koruyor. `direction=None` parametresi `SignalFusion.evaluate()` imzasında mevcut mu kontrol edilmeli.

### #3 — `test_bayesian_added_to_result` (Flaky state-leak)

**Behavior:** Isolated run'da PASS, full suite'te FAIL.

**Hipotez:** Başka bir test `os.environ` veya global singleton (SignalFusion cache, BayesianUpdater class state) bırakıyor. Test order-dependent.

**Doğrulama gerekli:** `pytest tests/unit/test_phase66.py::TestSignalFusionBayesian::test_bayesian_added_to_result -v` tek başına → PASS. Full suite'te FAIL.

**Root cause ipucu:** Full suite fail'ini trigger eden test muhtemelen `SignalWeights` sınıfına mutable değişiklik yapıyor (e.g., SIGNAL_W_WHALE env patch). T5.4/T7.6 fixture yetmez olabilir.

**Karar:** **FIX fixture.** `tests/unit/test_phase66.py` → conftest seviyesinde ENV isolation (`monkeypatch.delenv` veya `patch.dict` clear-based fixture).

**Eylem:**
```python
@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    for var in ["SIGNAL_W_WHALE", "SIGNAL_W_MOMENTUM", ...]:
        monkeypatch.delenv(var, raising=False)
```

### #4 — `test_pipeline_optimize_bails_when_lock_held` (ENV sandbox)

**Error:**
```
ImportError: optuna not installed. Run: pip install optuna
backtest/hyperopt.py:486: in __init__
```

**Root cause:** `optuna` paketi sandbox'ta yok. `backtest/hyperopt.py` import-time optional olarak handle ediyor (L59 warning) ama `PipelineOptimizer.__init__` hard-require.

**Karar:** **SKIP guarded.** Class-level decorator:

```python
try:
    import optuna  # noqa: F401
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False

@pytest.mark.skipif(not HAS_OPTUNA, reason="optuna not installed (sandbox)")
class TestHyperOptPipelineMutex:
    ...
```

### #5 — `test_signal_fusion_includes_whale_weight` (Stale Bulgu D)

**Error:**
```
assert weights.whale_flow == 0.10
E   assert 0.0 == 0.1
```

**Root cause (INVENTORY.md Bulgu D, git blame confirm):**

```python
# core/signal_fusion.py L64
whale_flow: float = float(os.getenv("SIGNAL_W_WHALE", "0.00"))
```

**Git blame:** `c820906` docstring Phase 79b rebalance açıkça belirtiyor:
> "Phase 79b: Rebalanced for 'next candle prediction' focus.
> OLD: odds=0.25 ema=0.20 mom=0.18 vol=0.12 time=0.10 ob=0.15 **whale=0.10**
> NEW: odds=0.05 ema=0.25 mom=0.30 vol=0.00 time=0.10 ob=0.20 **whale=0.00**"

Yani **whale_flow kasıtlı olarak sıfırlandı** (Phase 79b), test'ler Phase 60 defaultuna göre yazılmış kalmış.

**Production reality:** `SIGNAL_W_WHALE` ENV'te set olmadıkça whale_flow = 0.0. Bu kasıtlı (fusion kapalı) — whale_flow ENV-tunable bir opt-in.

**Karar:** **FIX test.** İki yol:
- (a) Test default değerin 0.0 olduğunu assert etsin (canonical current behavior).
- (b) Test `monkeypatch.setenv("SIGNAL_W_WHALE", "0.10")` ile fusion'ı aktive edip 0.10 doğrulasın.

**Tercih:** (b) — test adı "includes_whale_weight" bu weight'i test ediyor. ENV set edip weight 0.10'u doğrulamak niyet koruyor.

### #6 — `test_signal_fusion_whale_signal_in_composite` (Stale Bulgu D)

**Error:**
```
assert result_with_whale.composite_score > result_no_whale.composite_score
E   AssertionError: assert 0.0838 > 0.0838
```

**Root cause:** Aynı sebep (#5). `whale_flow=0.0` iken composite formula `sum(weight[k] * signal[k])` whale katkısı 0. whale_signal=0.0 vs 0.5 aynı composite veriyor.

**Karar:** **FIX test.** `monkeypatch.setenv("SIGNAL_W_WHALE", "0.10")` ile fusion aktive edip composite farkı doğrula.

---

## Impact on T9.4 (Flaky / Env audit)

T9.3'ten T9.4'e çıkan input:
- **ENV-dependent sandbox-fail:** phase77_handler (telegram), phase82b (optuna). T9.4'te tüm test dosyaları için skipif guard audit — başka ENV-dep test var mı?
- **Flaky state-leak:** test_bayesian_added_to_result. T9.4'te order-dependency fuzz (pytest-randomly) çalıştır. Başka order-dep fail var mı?
- **Stale Bulgu D:** 2 whale test Task #44 drain — fusion reaktivasyon kararı verilince (whale_flow default = 0.05?) test fix güncellenir.

## Exit criteria → T9.5

T9.5'te uygulanacak 6 değişiklik:

1. `tests/test_phase77.py` L460-475 → skipif(no telegram) guard
2. `tests/unit/test_phase66.py` L86 → test rename + direction=None parameter
3. `tests/unit/test_phase66.py` → autouse env isolation fixture
4. `tests/unit/test_phase82b.py` → class-level skipif(no optuna)
5. `tests/unit/test_whale_signal.py` L200 → monkeypatch SIGNAL_W_WHALE=0.10
6. `tests/unit/test_whale_signal.py` L240 → monkeypatch SIGNAL_W_WHALE=0.10

**Acceptance:** 510 collected → 504 pass + 6 skip + 0 fail (ENV-dep 2 sandbox'ta skip, PROD'da run; stale 4 fix).

## Task linkage

- **Task #44 (whale_signal fusion weight investigation)** — T9.3 decision: FIX test assumption, **DO NOT change signal_fusion.py core**. Current behavior (whale_flow=0.00 default) kasıtlı — Phase 60 underperformance kararı. Task #44 close criteria: git blame confirm + memory note.
- **Task #47 (T9.3)** — bu dosya ile complete.
- **Task #49 (T9.5)** — T9.3 matrix'ten 6 eylem uygula.
