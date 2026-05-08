# Project Simplification Analysis — 2026-05-05

**Heddas direktifi**: "Bazı özellikler gereksiz gibi sanki. Projeyi sadeleştirmemiz lazım."

## Mevcut feature surface (gözden geçirildi)

### ✅ KORU (production-critical)
- **TradingEngine + 4 mixin** — ana trade cycle
- **AI Brain** — strategy auto-tune (10dk cycle)
- **Signal Fusion** (6 sinyal, %66.8 cov)
- **Strategy Plugins** (20 strategy, %77.2 cov)
- **Risk Manager** (9 gate, %69 cov)
- **Live Trader** (V2 SDK, %59.4 cov)
- **Polymarket Portfolio** (read-only cache)
- **Polymarket Actions** (deposit, withdraw, allowance)
- **Live UI** (yeni Market BUY/SELL)
- **Strategy Registry** (Thompson Sampling)
- **UMA Dispute** (settlement gate)
- **Fees V2** (Mart 2026 model)
- **Engine Wire** (5 ENV-gated P1 modul)

### 🟡 GÖZDEN GEÇIR (kullanıcı kararı)

**1. AI Brain "INSIGHT" action**
- Sadece not yazıyor, hiçbir şey yapmıyor
- 10dk cycle'da sürekli LLM cost yiyor
- **Öneri**: INSIGHT action'ı kaldır, sadece DELETE/CREATE/SCALE/TUNE/RESTART tut. -%10 cost.

**2. Two-Agent mode (Optimist + Critic)**
- 2 LLM call per cycle (Groq + Claude) → 2× cost
- Sadece confidence tuning için
- **Öneri**: ENV `AI_BRAIN_TWO_AGENT=false` default (mevcut) iyi. Aktif olmasın.

**3. AutoOptimizer "Adaptive Threshold"**
- Strategy params kendi kendine değişiyor
- AI Brain TUNE ile çakışıyor
- **Öneri**: Birini seç. AI Brain TUNE daha akıllı, AutoOptimizer'ın TUNE'unu kaldır.

**4. Strategy Suggester (`_discover_niches`)**
- 4 saatte 1 LLM çağrısı, yeni strategy önerir
- AI Brain CREATE zaten yapıyor
- **Öneri**: Strategy Suggester'ı emekliye ayır, CREATE'i AI Brain'de tut.

**5. Becker (silindi 2026-04-28)** ✅ zaten silindi
**6. Hyperopt (silindi 2026-04-28)** ✅ zaten silindi

**7. 30+ Telegram handler dosyası**
- `archive_info_handler` (sadece /archive_info komutu)
- `rest_timing_handler` (telemetry)
- `brier_handler` (calibration view)
- `phase77_handler` (eski phase debugging)
- **Öneri**: 4 handler'ı `/diagnose` altında tek menü'ye birleştir. -%5 dosya sayısı.

**8. KeepAlive HTTP server**
- Replit free tier için. Heddas Windows local'de çalıştırıyor.
- **Öneri**: ENV `KEEPALIVE_ENABLED=false` default kalsın (Heddas Windows local).

### 🔴 SİL (dead code candidates)

**1. `backtest/data_sources/binance_hist.py` (%10.6)**
- Backtest için Binance kline indirici
- AKTIF backtest yapılmıyor (Heddas live focus)
- **Karar**: KORUMA — Sprint 4'te yeniden bakacağız

**2. `core/keepalive.py` DASHBOARD_HTML**
- 100+ satır embedded HTML
- Replit-specific, Heddas Windows kullanmıyor
- **Karar**: ENV-gated kalsın, default off

**3. `_archive/` klasörü**
- Eski silinmiş kodun yedeği (Becker, Hyperopt, vs)
- Git history'de zaten var
- **Karar**: 30+ gün sonra sil (Sprint 4 Temmuz)

## Önerilen aksiyon planı

| # | İş | Sprint | Kazanım |
|---|---|---|---|
| 1 | AI Brain INSIGHT action kaldır | Hemen | -%10 LLM cost |
| 2 | Strategy Suggester emekliye ayır | Sprint 3 sonu | -224 stmt, AI Brain çakışması yok |
| 3 | AutoOptimizer TUNE → AI Brain'e taşı | Sprint 4 | +deduplication |
| 4 | 4 handler `/diagnose` altında birleştir | Sprint 4 | -800 stmt, daha temiz menü |
| 5 | `_archive/` 30 gün sonra sil | Sprint 4 Tem | disk -50MB |

## Heddas Onay Bekliyor

- [ ] AI Brain INSIGHT kaldır (5dk iş)
- [ ] Strategy Suggester emekliye ayır (1h)
- [ ] 4 handler birleştir (Sprint 4)

## Mevcut Sprint 2 SHADOW ACTIVE — Hiçbir şey değişmiyor

Bu analiz sadece dokümantasyon. Production kod değişikliği yapılmadı.
Heddas onayından sonra Sprint 3'te uygula.
