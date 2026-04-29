# Coverage Snapshot — 2026-04-29

**Bağlam:** Aşama 3.C (Becker tam silme) sonrası, FAZ 2.4 için baseline.
3-AI synthesis hedef: %21 → %60+ kademeli.

## Ölçüm

Sandbox subset (8 test dosyası × 226 test, async-free):

```
TOTAL  12283 stmts / 11409 miss / 4046 branch / 6.1% cover
```

Sandbox'ta `pytest-asyncio` yok → 1000+ test çalışmıyor. Windows lokal'de full suite ile gerçek baseline ~%21 (T9.6 referans).

## Modül Heatmap (sandbox subset)

### 🟢 İyi (>%50)
- `core/fees_v2.py` — **91.5%** (audit-kritik, FAZ 0.1 sonrası %100'e yaklaştı)
- `core/observability/__init__.py` — 45.9%

### 🟡 Orta (20-50%)
- `core/engine_support.py` — 44.1%
- `core/risk_manager.py` — 40.8%
- `core/kelly.py` — 39.4%
- `core/engine_fills.py` — 29.7%
- `core/bg_task.py` — 21.1%
- `core/observability/rest_timing.py` — 21.2%
- `core/trade_journal.py` — 20.8%

### 🔴 Düşük (<%20)
- `core/strategy_plugins.py` — 17.0% (765 stmts!)
- `core/engine_monitor.py` — 12.0%
- `core/auto_optimizer.py` — 8.7%
- `core/engine_settlement.py` — 8.2%
- `core/engine_signals.py` — 5.8% (1034 stmts!)

### ⚫ %0 (sandbox subset'te hiç çalıştırılmadı — Windows'ta async testleri olabilir)
- `core/ai_brain.py` (993 stmts)
- `core/engine.py` (653 stmts)
- `core/live_trader.py` (354 stmts)
- `core/signal_fusion.py` (331 stmts)
- `core/strategy_suggester.py` (224 stmts)
- `core/strategy_lifecycle.py` (190 stmts)
- `core/intent_parser.py` (188 stmts)
- `core/trade_memory.py` (186 stmts)
- `core/decision_explainer.py` (140 stmts)
- `core/autopilot.py` (124 stmts)
- `core/keepalive.py` (118 stmts) — phase 65 "removed" ama duruyor (FAZ 1.7 dead code purge candidate)
- `core/micro_weight_tracker.py` (103 stmts)
- `core/regime.py` (93 stmts)
- `core/strategy_selector.py` (89 stmts)
- `core/changelog.py` (72 stmts)
- `core/circuit_breaker.py` (60 stmts)
- `core/ev_tracker.py` (54 stmts)
- `core/kill_switch.py` (40 stmts)

## Kazanç Önceliği (FAZ 2.4 hedef %60)

Stmts × (1 - cover) sıralı, en yüksek kazançlı 5 modül:

| Modül | Stmts | Cover | Eksik | Test Tahmini |
|---|---:|---:|---:|---:|
| `core/engine_signals.py` | 1034 | 5.8% | 974 | 30+ test |
| `core/ai_brain.py` | 993 | 0% | 993 | ⚠ pseudo-LLM mock'lu integration |
| `core/strategy_plugins.py` | 765 | 17.0% | 635 | 20+ test |
| `core/engine.py` | 653 | 0% | 653 | mixin harness pattern (T9.6) |
| `core/auto_optimizer.py` | 384 | 8.7% | 351 | DB fixture + ROLLING_WR scenarios |

5 modülde her biri %50 hedefe çıkarsa: ~3000 stmts kazanç → toplam ~%18 → ~%39 baseline'dan **%57'ye** ulaşır.

## Düşük-Hanging Fruit (kolay kazanç)

1. **`core/keepalive.py`** (118 stmts, %0) — Eğer Phase 65'te gerçekten removed ise FAZ 1.7'de sil. Aksi takdirde 5 test ekle.
2. **`core/circuit_breaker.py`** (60 stmts, %0) — Pure logic, mock'suz 8-10 test = %80+
3. **`core/kill_switch.py`** (40 stmts, %0) — Telemetry-only, tek state machine, 5 test = %90+
4. **`core/changelog.py`** (72 stmts, %0) — DB write-only, fixture + 6 test = %70+
5. **`core/ev_tracker.py`** (54 stmts, %0) — In-memory metric, 4 test = %85+

5 küçük modülde %0 → %75 ortalama: **+260 stmts kazanç** = +2 puan TOTAL.

## Plan (FAZ 2.4 öncesi quick win)

**Fase A (1-2 saat, +2-3 puan):** 5 küçük modülün boş testlerini ekle.
**Fase B (1 hafta, +10-15 puan):** `engine_signals.py` + `strategy_plugins.py` mixin harness pattern.
**Fase C (FAZ 2.4 ana iş, 2-4 hafta, +20-25 puan):** ai_brain (LLM mock), engine, auto_optimizer.

Hedef: %21 → %35 (Fase A+B) → %60 (Fase C, FAZ 2.4 closure).

## Aşama 3.C Etkisi

Becker tam silme **net coverage etkisi: nötr**.
- 6 dosya 503 satır silindi → toplam stmts ~441 azaldı
- Silinen kod zaten test edilmiyordu (becker boost/decision)
- Coverage % aynı baseline'da, mutlak satır sayısı düştü

## Test sayımı parity

Memory'deki son baseline: **800 → 777 PASS** (FAZ 0.2 + Becker + Hyperopt + Polymarket Phase A-D).
Sandbox'ta async-eksik. Lokal Windows full suite çalıştırınca verifiye edilecek.
