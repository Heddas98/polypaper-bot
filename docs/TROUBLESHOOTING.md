# Sorun Giderme Rehberi

Operasyon sırasında karşılaşılan yaygın hatalar ve çözümleri. Her phase'den biriken "known issues" bu dosyada konsolide.

## Kurulum

### `py -3.11` çalışmıyor
- Python 3.11 kurulumu "Add to PATH" işareti olmadan yapılmış olabilir.
- **Çözüm:** Python'u kaldırıp yeniden kur, mutlaka "Add python.exe to PATH" işaretle.

### `pip install` hata veriyor
- SSL hatası: `pip install --upgrade pip` ile pip'i güncelle.
- Permission hatası: PowerShell'i **Run as Administrator** aç.
- `py-clob-client` wheel bulunamadı: Visual Studio C++ Build Tools gerekli.

## Runtime

### Bot başlar sonra kapanır
**Sebep:** Hourly crash loop (Phase 57 tespit).

**Kontrol:**
```powershell
Get-Content logs\bot.log -Tail 100
```

WS empty-string veya WAL busy_timeout hatası görürsen:
- `.env`'e şu ENV'leri ekle:
  ```
  WS_STALE_SEC=20
  WS_FORCE_RECONNECT_SEC=300
  ```
- Watchdog v2 (Phase 57) single-instance check yapar.

### `database is locked` hatası
**Sebep:** WAL mode + concurrent writers.

**Fix (Phase 57'de kalıcı):**
- `db/ro_connect.py` retry/fallback logic var (Sprint 2.2)
- `.env`'e:
  ```
  SQLITE_BUSY_TIMEOUT=10000
  SQLITE_SYNCHRONOUS=NORMAL
  ```

### `MAX_OPEN_POSITIONS=5` — çoğu strateji trade açamıyor
**Sebep:** Phase 63 root cause. Default 5 ama 20 aktif strateji var.

**Fix:**
```
MAX_OPEN_POSITIONS=30
MAX_POSITION_SIZE=25.0
```

`/diagnose` komutu tam durumu gösterir.

## Admin & Auth

### `/force_settle` veya admin komutları çalışmıyor
**Sebep:** `ADMIN_TELEGRAM_ID` yanlış.

**Fix:**
- [@userinfobot](https://t.me/userinfobot)'a `/start` → sayısal ID'yi al
- `.env`'e `ADMIN_TELEGRAM_ID=123456789` (tırnak yok, boşluk yok)

### Shadow report gelmiyor
**Sebep:** Phase 47f.7'de `ADMIN_CHAT_ID` vs `ADMIN_TELEGRAM_ID` karışıklığı vardı. WSL 1.6GB hot WAL DB okuyamıyordu.

**Fix:** Shadow report artık bot içinde çalışıyor (`telegram_bot/jobs/shadow_report_job.py`, JobQueue 1800s). Eski `shadow_monitor_47f7.py` sandbox script'i kullanılmıyor.

## CLOB / Live Trading

### `Invalid signature` hatası
**Sebep:** CLOB signature tip uyuşmazlığı.

**Fix (Phase 82e):**
- EOA type 0 (standart) + ApiCreds gerekli
- `POLYGON_PRIVATE_KEY` 64-hex (0x prefix yok)
- `POLYMARKET_PASSPHRASE` boş olmamalı

### Resolution price 0 veya 1 olarak clamp edilmiş
**Sebep:** CLOB `get_resolution_price` default clamp yapıyor.

**Fix (HOTFIX v4):**
- Gamma API `outcomePrices` parse (unclamped)
- CLOB fallback: `get_resolution_price` unclamped versiyon
- TF-aware `force_after 900s`
- Acil durum: `/force_settle <market_slug>` admin komutu

## HyperOpt

### Trial'lar `Score 0.0000` döndürüyor
**Sebep (Phase 82b.5):** Discovery `wait_for(300s)` içinde çalışıyor, cache populate olmadan iptal ediliyor, her trial MISS.

**Fix:** `HyperOptPipeline.prime_windows_cache()` artık worker'da trial loop ÖNCESİ çağrılıyor (Sprint 2.5), `STUDY_TIMEOUT_SEC` ile bounded.

### Discovery 200+ saniye sürüyor
**Sebep:** Order book snapshot table üzerinde SQL plan TEMP B-TREE kullanıyor.

**Fix (Sprint 4.3/4.4):**
```bat
create_covering_index.bat           # idx_ob_snap_slug_mst_ts (single-path)
create_split_backtest_index.bat     # idx_ob_snap_atf_slug_mst_ts (5-col covering)
```

Bu index'ler kurulduktan sonra discovery **222s → 7s** (32x).

### HyperOpt worker `cp1252` encoding crash
**Sebep:** Windows default stdout encoding cp1252, delta (Δ) karakter'ı crash ettirir.

**Fix (Sprint 4.3):** `hyperopt_worker.py` stdout `utf-8` force-set edildi (`sys.stdout.reconfigure(encoding='utf-8')`).

### `/hyperopt_all` yarıda kesiliyor / trial budget dolu
**Sebep:** 21 strateji × trials = çok büyük bütçe.

**Fix (Sprint 4.5):** Apply-filter 21 → 8 strategy type (trial budget -62%). 0 unapplyable results.

## Strategy Issues

### Classic strategy trade açmıyor
**Sebep:** 14-gate pipeline bloke ediyor.

**Fix (Sprint 5 HOTFIX v3):**
```
CLASSIC_BYPASS_ALL_GATES=true
```

Opt-in flag'ler:
```
CLASSIC_RESPECT_UNSELLABLE=true  # Phase 66 UNSELLABLE gate
CLASSIC_RESPECT_ZONES=true       # ALLOWED_ZONES filter
```

### FUSION 30-40c zone'da kayıp bucket
**Sebep (Phase 82c):** FUSION stratejileri 30-40c zone'da AI_F_* loss pattern.

**Fix:** Bu zone FUSION için blocked. Telegram'da `/filters` komutu ile görebilirsin.

### Strateji "exploration" fazında takılmış
**Sebep:** Trade sayısı 30'dan az, henüz evaluation'a geçmemiş.

**Fix:** Otomatik — 30 trade sonrası evaluation, 100 trade sonrası proven. `/lifecycle <strat>` ile izle.

## Backup & Recovery

### Bot çöktü, son stable sürüme dönmek istiyorum
```powershell
rollback.bat
```

Son backup'tan DB restore eder. Logs'ta `backup.bat` output'unu görebilirsin.

### Tüm stratejileri sıfırlamak istiyorum
**UYARI:** Tüm trade history siler.

```powershell
reset_and_start.bat
```

20 yeni optimized strateji ile başlar (19 aktif).

## Performance

### Disk 195GB+ dolu
**Sebep:** `data_store/` içinde becker_raw (50GB) + polypaper DB büyümeleri.

**Fix:**
- `/db_health` ile büyüklükleri gör
- `DB_RETENTION_*` ENV'lerini ayarla (`.env.example`'da tam liste)
- Manual vacuum: `scripts/verify_db_health.py` içinde

### RAM sürekli 2GB+ kullanıyor
**Sebep:** HyperOpt worker + Optuna cache.

**Fix:**
```
HYPEROPT_MEMORY_WARN_MB=800
HYPEROPT_MEMORY_CRIT_MB=1500
HYPEROPT_MEMORY_ABORT_MB=2500
```

Abort edilirse otomatik restart.

## Log Yerleri

| Log | Konum | Ne için |
|---|---|---|
| Bot main log | `logs/bot.log` | Ana operasyon |
| Watchdog | `logs/watchdog.log` | Çökme/restart events |
| HyperOpt | `logs/hyperopt_worker.log` | Trial-by-trial |
| Deploy | `deploy_phase*_log.txt` | Deploy script çıktıları |
| Clean overfit | `clean_overfit_log.txt` | Overfit cleanup |

**Not:** `*.log` dosyaları `.gitignore` ile hariç tutulur, commit edilmez.

## Bilinen Edge Case'ler

### Windows cp1252 + emoji mesajlar
HTML parse mode kullan, **asla Markdown değil** — `$` işareti Markdown'ı bozar. Tüm handler'lar `parse_mode=HTML`.

### Replit free tier yanlış negatifleri
Proje Replit'te **DEĞİL** — local Windows PC'de. Bu memory note'u Phase 47'den kalma.

### 80 komut menüsünde sığmıyor
Phase 55 ve Phase 79'da sıkılaştırıldı: `set_my_commands` 15 + readable aliases. Tam liste Phase 79 sonrası 20 komut.
