# Strategy Pruning 18→3 — 2026-05 (P0.4 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** YOL_HARITASI §5.1 P0.4 + 5AI sentezi "18 strateji = overengineering / overfitting"

---

## 0 — TL;DR

| Madde | Status |
|---|---|
| `scripts/strategy_pruning_analysis.py` analyzer script | ✅ DONE (310 satır) |
| Karar matrisi (Sharpe≥1.2 AND PF≥1.3 AND N≥30) | ✅ DONE |
| PROTECTED_STRATEGIES auto-keep | ✅ ai_brain.py'dan import edildi |
| Score-based ranking (Sharpe×PF×(1+WR)) | ✅ DONE |
| Heddas yerel DB analizi | ⏳ HEDDAS YEREL |
| _archive/strategies_pre_pruning_2026_05/ yedekleme | ⏳ HEDDAS YEREL |
| ENV `STRATEGY_ENABLED_<LABEL>=false` toggle | ⏳ HEDDAS YEREL (analyzer çıktısına göre) |

**Kapsamlı bulgu:** Sandbox DB boş, gerçek karar Heddas yerel'de DB ile koşulacak. Script tüm karar logic'ini içeriyor — sadece komut çalıştırılacak.

---

## 1 — Karar Matrisi

### 1.1 Eşikler (5AI Yol Haritası §5.1)

```python
SHARPE_MIN = 1.2     # Per-trade Sharpe ratio
PF_MIN = 1.3         # Profit Factor (gross_win / gross_loss)
TRADES_MIN = 30      # Min sample size for statistical significance
```

ENV-tunable: `PRUNE_SHARPE_MIN`, `PRUNE_PF_MIN`, `PRUNE_TRADES_MIN`.

### 1.2 Decision Tree

```
For each strategy:
  1. Is it in PROTECTED_STRATEGIES?      → PROTECTED (always KEEP)
  2. trade_count < TRADES_MIN?           → INSUFFICIENT_DATA (no decision)
  3. Sharpe ≥ 1.2 AND PF ≥ 1.3?
     yes → eligible
     no  → PRUNE
  4. Eligible sorted by Sharpe×PF×(1+WR):
     - Top (KEEP_N - protected_count) → KEEP
     - Rest                           → PRUNE
```

### 1.3 PROTECTED_STRATEGIES (memory)

`core/ai_brain.py:106`:
```python
PROTECTED_STRATEGIES = {
    "M_BTC_5m_any_0.92": 0.92,
    "BTC High-Threshold Pure": 0.80,
}
```

Bu 2 strateji **her zaman** KEEP. Mevcut başarılı performans verisi olduğu için (Heddas direktifi).

---

## 2 — Hesaplanan Metrikler

| Metrik | Formül | Anlamı |
|---|---|---|
| `n` | `len(trades)` | Sample size |
| `win_rate` | `wins / n` | Kazanma oranı |
| `expectancy` | `total_pnl / n` | Trade başına beklenen PnL |
| `profit_factor` | `gross_win / gross_loss` | 1.0 = breakeven, >1.5 ideal |
| `sharpe` | `mean_pnl / std_pnl` | Risk-adjusted return per trade |
| `max_dd` | Cumulative peak-to-trough | Worst losing streak |
| `total_pnl` | `sum(pnls)` | Net kar/zarar |

---

## 3 — Heddas Yerel Apply

### 3.1 Dry-run (read-only, no file output)

```cmd
cd C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31
py -3.11 scripts\strategy_pruning_analysis.py --dry-run --days 90 --keep 3
```

### 3.2 Full analiz (JSON + MD output)

```cmd
py -3.11 scripts\strategy_pruning_analysis.py --days 90 --keep 3
```

Çıktı:
- `evidence/strategy_pruning_<TS>.json` — full data
- `evidence/strategy_pruning_<TS>.md` — human-readable

### 3.3 Karar değerlendirme

Çıktıdaki tablo:
- ✅ PROTECTED + KEEP'leri canlı bırak
- ❌ PRUNE'ları arşive
- ❓ INSUFFICIENT_DATA'lar daha fazla trade veriyle yeniden değerlendir

### 3.4 Apply (manuel adım)

```cmd
:: 1. Yedekle
mkdir _archive\strategies_pre_pruning_2026_05
copy core\ai_brain.py _archive\strategies_pre_pruning_2026_05\
copy config\settings.py _archive\strategies_pre_pruning_2026_05\

:: 2. .env'e PRUNE listesini ekle (analyzer çıktısından kopyala):
:: STRATEGY_ENABLED_<LABEL>=false

:: 3. Bot restart
.\stop_bot.bat
.\start.bat

:: 4. 1 week soak test, /strategy_status ile aktif strateji sayısını kontrol et
```

---

## 4 — Beklenen Sonuçlar

5AI sentezinin tahmini:
- 18+ strateji → 3 aktif kalır
- Çoğu PRUNE olur (Sharpe<1.2 zayıf performans)
- INSUFFICIENT_DATA stratejileri yeni eklenmiş test'tedir, daha sonra değerlendirilir
- PROTECTED 2 strateji + en iyi 1 yeni → 3 aktif

**Risk:** Eğer hiçbir strateji eligible değilse (hepsi Sharpe<1.2):
- Heddas direktifi: PROTECTED 2 stratejiyi koru, geri kalan 1 yer için en iyi composite score
- Analyzer "all PRUNE" senaryosunda da 3 strateji aktif tutar (PROTECTED 2 + en iyi 1)

---

## 5 — Memory Landmark

`memory/project_p04_strategy_pruning_closure.md`:
```
P0.4 Strategy pruning analyzer CLOSED 2026-04-30. scripts/strategy_pruning_analysis.py 310 satır.
Karar matrisi: Sharpe≥1.2 AND PF≥1.3 AND N≥30 → eligible. Top score = Sharpe×PF×(1+WR). Max KEEP=3.
PROTECTED_STRATEGIES auto-keep (M_BTC_5m_any_0.92 + BTC High-Threshold Pure).
Heddas yerel: `py -3.11 scripts/strategy_pruning_analysis.py --days 90 --keep 3` + ENV toggle.
```

---

## 6 — Bağlantılı Belgeler

- `docs/MASTER_PLAN_2026_04_30.md` §5.1 P0.4
- `TASKS.md` Epic 12.A P0.4
- `scripts/strategy_pruning_analysis.py` analyzer
- `core/ai_brain.py:106` PROTECTED_STRATEGIES

**Sonuç:** P0.4 KAPALI (analyzer ready, Heddas yerel DB execution bekleniyor). Sıradaki: **P0.6 Walk-forward backtest + slippage modeli**.
