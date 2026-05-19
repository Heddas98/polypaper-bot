# Sprint 2 — Mainnet Mikro Test Rehberi

**Tarih:** 2026-05-03
**Durum:** Heddas Polymarket'e para yatırdı, mikro test başlıyor
**Süre:** 14 gün shadow live trading
**Hedef:** Paper PnL vs Live PnL drift < %10 doğrula

---

## 🛡️ ADIM 1: .env Güvenlik Ayarları

`.env` dosyasının **EN SONUNA** aşağıdaki blok ekle (notepad ile):

```bash
# ═══════════════════ SPRINT 2 MAINNET MIKRO TEST ═══════════════════
# 14 gün shadow live trading. Paper-live drift ölçümü.
# Karar gate (Hafta 4): drift <%10 → Sprint 3, drift ≥%10 → fix.

# Live trader aktivasyon
LIVE_ENABLED=true
LIVE_MAX_TRADE=1.00              # Trade başına max $1
LIVE_BUDGET=10.0                 # Toplam max $10 risk

# Per-trade hard caps (P0.10)
ORDER_VALIDATOR_ENABLED=true
ORDER_MAX_USD=10                 # Tek trade max $10
ORDER_MIN_USD=5                  # Polymarket V2 floor
ORDER_MIN_PRICE=0.05
ORDER_MAX_PRICE=0.95

# Drawdown kill-switch (P0.8)
KILL_SWITCH_ENABLED=true
KILL_DAILY_MAX_LOSS_PCT=0.10     # Günlük -%10 → HALT
KILL_CONSECUTIVE_LOSS_LIMIT=5    # 5 ardışık zarar → 1h cooldown
KILL_CONSECUTIVE_COOLDOWN_S=3600
KILL_WEEKLY_MAX_DD_PCT=0.20      # Haftalık -%20 → emergency stop

# Fill heuristic recalibration (P0.7 — T4.6-B sweep)
FILL_SPREAD_COST=0.023
FILL_IMPACT=0.025
LATENCY_DRIFT=0.04

# Heartbeat (FOK-only ise opsiyonel; post-only GTC eklenirse zorunlu)
HEARTBEAT_ENABLED=false
HEARTBEAT_INTERVAL_S=5

# Maker/Taker karar matrisi (P1.6 — istersen aç)
MAKER_MODE_ENABLED=false         # true → spread>2tick'te post-only GTC
MAKER_SPREAD_THRESHOLD_TICKS=2

# Reconciliation loop (P1.4 — Polygon RPC gerek)
RECON_ENABLED=false              # true önce RPC URL kontrol et
RECON_INTERVAL_S=300
RECON_MISMATCH_THRESHOLD_USD=1.0
POLYGON_RPC_URL=https://polygon-rpc.com
```

Kaydet ve kapat.

---

## 🧹 ADIM 2: ENV Cleanup (46 DEAD env sil)

```cmd
cd C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31

REM Önce dry-run preview
py -3.11 scripts\env_cleanup_apply.py

REM OK ise gerçek silme
py -3.11 scripts\env_cleanup_apply.py --apply
```

Bu `.env.example`'dan 46 dead var (Cascade, Capital, EventWaves, Evolutionary, Lag, vs) siler. Yedek otomatik alınır.

---

## 🚀 ADIM 3: Mainnet Aktivasyon

```cmd
.\stop_bot.bat
scripts\sprint2_activate_mainnet.bat
```

Script şunları kontrol eder:
- ✅ Bot kapalı
- ✅ V2 SDK import OK
- ✅ Yeni modüller import OK
- ✅ .env'de safety env'ler set
- ✅ start.bat çalıştırır

---

## 📊 ADIM 4: İlk 5 Dakika Gözlem

Telegram'dan:

```
/h          → heartbeat (PnL, açık pozisyon)
/portfolio  → Polymarket gerçek balance + 0 hata bekleniyor
/risk       → Risk limits + budget
/live       → Live trader status (auth=✅ + LIVE MODE banner)
```

**Beklenti:**
- Banner: 💰 REAL (kırmızı/yeşil)
- `auth=✅`
- Polymarket Budget gerçek bakiye gözükür
- Cloudflare 403 yok (cross-module shared cache çalışıyor)

---

## 📅 ADIM 5: Günlük Monitoring (14 Gün)

Her gün 1-2 dakika:

```cmd
py -3.11 scripts\sprint2_daily_check.py --days 1
```

Çıktı:
- Live trade count
- Net PnL
- Paper-Live drift % (T4.6-B style)
- Win rate
- Strateji breakdown (top 5)
- Polymarket portfolio snapshot
- Promotion gate check

---

## 🎯 KARAR GATE (Hafta 4 Sonu — 17 Mayıs 2026 civarı)

```cmd
py -3.11 scripts\sprint2_daily_check.py --days 14
```

| Sonuç | Karar |
|---|---|
| ≥200 trade + drift <%10 + PnL ≥+%5 | 🟢 **Sprint 3 ($100 promotion)** |
| 50-200 trade + drift <%10 | 🟡 14 gün daha gözle |
| Drift %10-25 | ⚠️ Simulator fix (P0.7 fill heuristic re-tune) |
| Drift >%25 | 🚨 LIVE_ENABLED=false → fix paper engine |
| Edge < +%5 (3+ ay) | 🔄 **SaaS pivot** (sermaye yerine ürün) |

---

## 🆘 ACIL DURUM (Kill-Switch)

Bot kötü gidiyorsa:

**Telegram:**
```
/halt       → tüm trading durdur (in-memory)
/mode       → Paper'a geri al (LIVE_ENABLED=false runtime patch)
```

**Manuel (.env):**
```bash
LIVE_ENABLED=false
```
Sonra `.\stop_bot.bat && .\start.bat`

**Tam emergency (kill-switch trigger):**
```cmd
echo HALT > polypaper.stop
```
Bot file-channel kill switch ile durur (T11.2 G1 doctrine).

---

## 📋 14 Gün Boyunca Beklenen

| Gün | Hedef trade sayısı | PnL beklentisi | Aksiyon |
|---|---|---|---|
| 1-3 | 5-30 trade | break-even ±$2 | Cloudflare 403 yok mu kontrol |
| 4-7 | 30-80 trade | ±$5 | Drift ölç |
| 8-11 | 80-150 trade | edge görünüyor mu? | Strateji breakdown analiz |
| 12-14 | 150-200+ trade | edge net mi? | Karar gate evaluation |

---

## 🔍 Bilinen Riskler

1. **Cloudflare 403 inisial derive** — boot'ta 1 kez log'a düşebilir. Cross-module shared cache devreye girince 60s job'larda spam YOK.
2. **`balance_allowance 401`** — eğer hala görüyorsan, stored creds yine eski. Bot otomatik derive fallback yapar.
3. **Resolution feed** — 5m + 15m crypto markets **Chainlink BTC/USD data stream**'e settle olur (2026-05-19 Gamma API ile doğrulandı; eski "Binance" varsayımı yanlıştı). RTDS feed (`data/polymarket_rtds.py`) main.py'ye bağlandı (P1.10); `RTDS_ENABLED` default açık, aktivasyon için bot restart gerekir.

---

## 📞 Destek

Sorun olursa logu paylaş:
- `data_store\polypaper.log` (son 100 satır)
- `evidence\sprint2_daily_check_<TS>.txt` (varsa)

Sources: `scripts\sprint2_activate_mainnet.bat` · `scripts\sprint2_daily_check.py` · `scripts\env_cleanup_apply.py` · 8 P1/P2 modül + V2 SDK + Cross-module cache.
