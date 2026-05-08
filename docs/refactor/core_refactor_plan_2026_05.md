# core/ Refactor Plan — P1.2 (signal + execution + risk modülleri)

**Tarih:** 2026-05-05 güncellendi (Heddas direktifi: P0+P1 paralel başla)
**Tetik:** YOL_HARITASI §5.2 P1.2 + Wave 7 coverage analiz (engine_signals %8.4 monolitik)
**Durum:** PLAN — Faz 1 (git mv) Sprint 3 sonu (Heddas onayı sonrası), Faz 2 (inner imports) hemen ardından, Faz 3 (mixin split) Sprint 5+

## 4-Paket Hedef Yapı

```
core/
├── signal_engine/   # SignalFusion, RegimeClassifier, StrategySelector, plugins, indicators
├── execution_engine/   # LiveTrader, Executor, MakerTakerDecision, Heartbeat, Reconciliation
├── risk_engine/   # RiskManager, KillSwitch, PortfolioKill, CircuitBreaker, Allowance, UMA, Fees
├── shared/   # stats, observability, error_handler, calibration, logging, bg_task
└── orchestration/   # Engine, mixins, AI Brain, AutoOptimizer, Lifecycle
```

## Faz 1 — git mv (no logic, ~2h)

`core/__init__.py` backward-compat shim:
```python
from core.signal_engine.fusion import SignalFusion, SignalWeights
from core.execution_engine.live_trader import LiveTrader
from core.risk_engine.manager import RiskManager, RiskLimits
# ... mevcut import'lar bozulmasın
```

## Faz 2 — Inner-import temizlik (~1h)

- Modüller kendi paketinden import: `from .executor import`
- Cross-package açıkça belirt
- Lazy imports gerekirse cycle önle

## Faz 3 — Mixin parçalama (Sprint 5+, ~4h)

`engine_signals.py` 1034 stmt → 5 alt-mixin:
- `MarketChecksMixin` (eval_market_checks)
- `SignalEvalMixin` (eval_signal + boosters)
- `GatesMixin` (eval_gates)
- `SizingMixin` (eval_sizing)
- `OrderPlaceMixin` (eval_place_order)

Her alt-mixin <300 stmt = kolay test, kolay maintain.

## Risk & Mitigation

| Risk | Mitigation |
|---|---|
| Import cycles | `core/__init__.py` lazy imports |
| Test break | Backward compat shim önce |
| Mainnet break | ENV-gated: `CORE_REFACTOR_LAYOUT=v2` |

## Heddas onay tablosu

- [ ] Faz 1 — git mv (~2h, no logic)
- [ ] Faz 2 — inner imports (~1h)
- [ ] Faz 3 — mixin split (Sprint 5+)

---
ESKİ PLAN (2026-04-30) aşağıda referans için tutuldu:
---

---

## 0 — Mevcut core/ Haritası (50+ dosya)

```
core/
├── ai_brain.py              ← AI Brain (Claude Sonnet 10dk cycle) — AYRI olmalı
├── allowance_preflight.py   ← P0.5 yeni
├── auto_optimizer.py        ← strategy lifecycle
├── bg_task.py               ← background task guard
├── changelog.py             ← strategy changelog
├── circuit_breaker.py       ← API breaker (3-state)
├── decision_explainer.py    ← Phase 77
├── engine.py                ← MAIN engine — God-module riski
├── engine_fills.py          ← fill detection
├── engine_monitor.py        ← cycle monitor
├── engine_settlement.py     ← order settlement
├── engine_signals.py        ← signal generation
├── engine_support.py        ← skip counter
├── error_handler/           ← P2.2 yeni
├── event_calendar.py        ← event monitor
├── executor.py              ← P1.8 yeni (ABSTRACT)
├── experiment.py            ← Phase 77 A/B
├── fees_v2.py               ← FAZ 0.1 SINGLE oracle
├── heartbeat.py             ← P1.6.1 yeni
├── intent_parser.py         ← Telegram intent
├── live_trader.py           ← Polymarket V2 SDK
├── maker_taker_decision.py  ← P1.6 yeni
├── micro_weight.py          ← Phase 79
├── observability/
├── portfolio_kill_switch.py ← P0.8 yeni
├── reconciliation/          ← P1.4 yeni
├── regime.py                ← drift detector
├── risk_manager.py          ← risk state
├── selector.py              ← Thompson Sampling
├── stats_utils.py           ← shared math
├── status_poller.py         ← P2.3 yeni
├── strategy_lifecycle.py    ← strategy mgmt
├── strategy_plugins.py      ← strategy registry
├── structured_logging.py    ← P1.7 yeni
├── trade_journal.py         ← jsonl logger
├── trade_memory.py          ← Phase 77
├── calibration/
└── ...
```

---

## 1 — Hedef Yapı (3 alt-modül)

```
core/
├── signal_engine/           ← SİNYAL ÜRETİMİ
│   ├── __init__.py
│   ├── engine_signals.py    (← mevcut core/engine_signals.py)
│   ├── selector.py          (← Thompson Sampling)
│   ├── regime.py            (← drift detector)
│   ├── strategy_plugins.py  (← strategy registry)
│   ├── strategy_lifecycle.py
│   ├── micro_weight.py      ← Phase 79
│   └── auto_optimizer.py    ← param tuning
│
├── execution_engine/        ← ORDER PLACEMENT + FILL
│   ├── __init__.py
│   ├── engine_fills.py
│   ├── engine_settlement.py
│   ├── live_trader.py       (← Polymarket V2 SDK)
│   ├── executor.py          (← abstract paper/live)
│   ├── maker_taker_decision.py  (P1.6)
│   ├── status_poller.py     (P2.3)
│   ├── heartbeat.py         (P1.6.1)
│   └── allowance_preflight.py   (P0.5)
│
├── risk_engine/             ← RİSK + KILL-SWITCH
│   ├── __init__.py
│   ├── risk_manager.py
│   ├── portfolio_kill_switch.py (P0.8)
│   ├── circuit_breaker.py   (API)
│   ├── reconciliation/      (P1.4)
│   └── fees_v2.py           ← shared (signal+execution+risk hepsi kullanır)
│
├── shared/                  ← CROSS-CUTTING
│   ├── __init__.py
│   ├── stats_utils.py
│   ├── trade_journal.py
│   ├── changelog.py
│   ├── bg_task.py
│   ├── structured_logging.py    (P1.7)
│   └── error_handler/       (P2.2)
│
├── engine.py                ← ENGINE ORCHESTRATOR (slim, 200 satır)
└── observability/

services/
└── ai_brain/                ← AYRILDI (microservice candidate)
    ├── __init__.py
    ├── brain.py             (← core/ai_brain.py)
    ├── intent_parser.py     (← core/intent_parser.py)
    ├── trade_memory.py
    ├── decision_explainer.py
    └── experiment.py        (Phase 77)
```

**Toplam:** 4 ana paket (`signal_engine`, `execution_engine`, `risk_engine`, `shared`) + `services/ai_brain/`.

---

## 2 — Refactor Adımları (sandbox + Heddas yerel)

### Adım 1: Yeni klasör yapısı oluştur (sandbox)
```bash
mkdir -p core/signal_engine core/execution_engine core/risk_engine core/shared services/ai_brain
touch core/signal_engine/__init__.py core/execution_engine/__init__.py
touch core/risk_engine/__init__.py core/shared/__init__.py
touch services/__init__.py services/ai_brain/__init__.py
```

### Adım 2: Dosyaları taşı (`git mv` — history korunur)
```bash
git mv core/engine_signals.py core/signal_engine/
git mv core/selector.py core/signal_engine/
git mv core/regime.py core/signal_engine/
git mv core/strategy_plugins.py core/signal_engine/
git mv core/strategy_lifecycle.py core/signal_engine/
git mv core/micro_weight.py core/signal_engine/
git mv core/auto_optimizer.py core/signal_engine/

git mv core/engine_fills.py core/execution_engine/
git mv core/engine_settlement.py core/execution_engine/
git mv core/live_trader.py core/execution_engine/
git mv core/executor.py core/execution_engine/
git mv core/maker_taker_decision.py core/execution_engine/
git mv core/status_poller.py core/execution_engine/
git mv core/heartbeat.py core/execution_engine/
git mv core/allowance_preflight.py core/execution_engine/

git mv core/risk_manager.py core/risk_engine/
git mv core/portfolio_kill_switch.py core/risk_engine/
git mv core/circuit_breaker.py core/risk_engine/
git mv core/reconciliation core/risk_engine/

git mv core/fees_v2.py core/risk_engine/

git mv core/stats_utils.py core/shared/
git mv core/trade_journal.py core/shared/
git mv core/changelog.py core/shared/
git mv core/bg_task.py core/shared/
git mv core/structured_logging.py core/shared/
git mv core/error_handler core/shared/

git mv core/ai_brain.py services/ai_brain/brain.py
git mv core/intent_parser.py services/ai_brain/
git mv core/trade_memory.py services/ai_brain/
git mv core/decision_explainer.py services/ai_brain/
git mv core/experiment.py services/ai_brain/
```

### Adım 3: Backward-compat shim (`core/__init__.py`)
```python
# core/__init__.py
"""Backward-compat shims — Phase 80+ refactor (P1.2 2026-05-XX).

Old imports keep working:
    from core.engine_signals import ...
    from core.live_trader import ...

forwarded to new locations.
"""
# Signal engine
from core.signal_engine import engine_signals, selector, regime  # noqa: F401
from core.signal_engine import strategy_plugins, strategy_lifecycle, micro_weight  # noqa: F401
from core.signal_engine import auto_optimizer  # noqa: F401

# Execution engine
from core.execution_engine import engine_fills, engine_settlement  # noqa: F401
from core.execution_engine import live_trader, executor, maker_taker_decision  # noqa: F401
from core.execution_engine import status_poller, heartbeat, allowance_preflight  # noqa: F401

# Risk engine
from core.risk_engine import risk_manager, portfolio_kill_switch  # noqa: F401
from core.risk_engine import circuit_breaker, reconciliation  # noqa: F401
from core.risk_engine import fees_v2  # noqa: F401

# Shared
from core.shared import stats_utils, trade_journal, changelog, bg_task  # noqa: F401
from core.shared import structured_logging, error_handler  # noqa: F401

# AI Brain (new path)
from services.ai_brain import brain as ai_brain  # noqa: F401
from services.ai_brain import intent_parser, trade_memory  # noqa: F401
from services.ai_brain import decision_explainer, experiment  # noqa: F401
```

Bu shim ile **mevcut import'lar bozulmaz**:
- `from core.engine_signals import ...` çalışır (forwarded)
- `from core.live_trader import ...` çalışır
- `from core.ai_brain import ...` çalışır (forwarded to services)

### Adım 4: Tests run
```cmd
py -3.11 -m pytest tests/ -q
:: Beklenti: 963 PASS sürer (shim sayesinde)
```

### Adım 5: Yeni import'ları gradüel update (Sprint 4+)
Yeni kod yazılırken:
- `from core.signal_engine.engine_signals import ...` (yeni path)
- Eski path → shim ile deprecate (warning)

### Adım 6: Shim'i kaldır (Sprint 6+)
6 ay sonra eski import path'leri temizle, sadece yeni path.

---

## 3 — Risk + Mitigation

| Risk | Mitigation |
|---|---|
| 50+ dosya taşıma → import path bozulur | Shim ile transparent forward, 0 import update gerek |
| pytest 963 PASS → bozulur | Shim ile geriye dönük uyum |
| Memory landmark'lar eski path göstermesi | `MEMORY.md` orientation'da yeni path notu |
| ai_brain ayrılması → engine reference karışıklığı | services/ai_brain/__init__.py shim |
| git history kaybı | `git mv` ile history korunur (rename detection) |

---

## 4 — Effort Tahmini

| Adım | Süre | Risk |
|---|---|---|
| 1. Klasör oluştur + __init__.py | 5 dk | DÜŞÜK |
| 2. `git mv` 30+ dosya | 30 dk | DÜŞÜK (atomic commits) |
| 3. Shim yazımı | 30 dk | ORTA (test cover) |
| 4. pytest baseline run | 5 dk | YÜKSEK (eğer fail → rollback) |
| 5. Memory landmark + docs update | 30 dk | DÜŞÜK |
| 6. (Sonra) Yeni import path adoption | gradual | DÜŞÜK |

**Toplam:** ~2 saat (shim sayesinde minimal risk).

---

## 5 — Sprint 4 Önkoşulları

Bu refactor **Sprint 4'e** alındı çünkü:
- Sprint 1-2: Mainnet doğrulama + edge ölçüm önemli
- Sprint 3: P1 bot kalite (heartbeat, kill-switch, recon, vb.) — ZATEN YAZILDI
- Sprint 4: SaaS hazırlık + bu refactor (multi-user önce mimari)

Refactor önkoşulları:
- ✅ pytest 963 PASS baseline
- ✅ Shim pattern test edilmiş (boot import smoke)
- ⏳ Sprint 2 mikro test PASS (paper-live drift <%10)
- ⏳ V2 SDK production stabil (Cloudflare 403 polish PASS)

---

## 6 — Memory Landmark Update (Refactor sonrası)

`memory/project_p12_core_refactor.md`:
```
P1.2 core/ refactor APPLIED <date>. 4 alt-paket (signal_engine + execution_engine
+ risk_engine + shared) + services/ai_brain/. 30+ git mv. Backward-compat shim
core/__init__.py. pytest 963 PASS sürdü. AI Brain ayrı microservice candidate.
```

`MEMORY.md` Orientation:
```
- [P1.2 core/ Refactor Applied <date>] (project_p12_core_refactor.md) — 4 alt-paket
  signal/execution/risk/shared + services/ai_brain. Shim ile transparent.
```

---

## 7 — Sonraki Adımlar (Refactor Sonrası)

P1.2 PASS olunca:
- AI Brain microservice ayırma (P2.X yeni)
- Signal engine plug-in pattern (yeni strateji ekleme cleaner)
- Risk engine state DB persist (P0.8 kill-switch state recovery)

**Sonuç:** Plan hazır, apply Sprint 4'e ertelenmiş. Sandbox'ta sadece plan dosyası, gerçek `git mv` Heddas yerel.
