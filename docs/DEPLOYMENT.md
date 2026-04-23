# Windows Deployment Rehberi

PolyPaper Bot yerel Windows PC'de çalışır — cloud değil. Bu rehber production-ready kurulumu anlatır.

## 1. Sistem Gereksinimleri

- Windows 10/11 (x64)
- Python 3.11.x (`py -3.11 --version` → çalışmalı)
- 8GB+ RAM (HyperOpt için)
- 50GB+ SSD (`data_store/` için — becker raw data)
- Kararlı internet (WebSocket feed'leri için)
- Telegram hesabı + [@BotFather](https://t.me/BotFather)

## 2. Python Kurulumu

1. [python.org/downloads](https://www.python.org/downloads/) üzerinden Python 3.11.x installer indir
2. Kurulum sırasında **"Add python.exe to PATH"** seçeneğini işaretle
3. Kurulum sonrası PowerShell'de doğrula:
   ```powershell
   py -3.11 --version
   # Python 3.11.9  (veya benzeri)
   ```

## 3. Proje Klonlama

```powershell
cd C:\
git clone https://github.com/YOUR_USERNAME/polyPaper-bot.git
cd polyPaper-bot
```

## 4. Sanal Ortam + Bağımlılıklar

```powershell
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

`requirements.txt` içeriği:
- python-telegram-bot==21.6
- aiosqlite==0.20.0
- pydantic==2.9.2
- httpx==0.27.2
- APScheduler==3.10.4
- pandas==2.2.3, numpy==2.1.3
- Pillow==11.0.0
- py-clob-client==0.18.0
- python-dotenv==1.0.1
- pyyaml==6.0.2

## 5. Environment Config

```powershell
copy .env.example .env
notepad .env
```

**Zorunlu alanlar:**

| Variable | Açıklama | Nereden alınır |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `ADMIN_TELEGRAM_ID` | Senin user ID'n | [@userinfobot](https://t.me/userinfobot) → ID'ni ver |
| `ANTHROPIC_API_KEY` | Claude API key | [console.anthropic.com](https://console.anthropic.com) |

**Opsiyonel — Shadow live için:**

| Variable | Açıklama |
|---|---|
| `POLYMARKET_API_KEY` | Polymarket CLOB API key |
| `POLYMARKET_API_SECRET` | CLOB secret |
| `POLYMARKET_PASSPHRASE` | CLOB passphrase |
| `POLYGON_WALLET` | 0x... EOA address |
| `POLYGON_PRIVATE_KEY` | 64-hex (ASLA paylaşma) |
| `LIVE_ENABLED` | `false` default, shadow-only |

## 6. İlk Çalıştırma (Test)

```powershell
py -3.11 -m telegram_bot.bot
```

- Banner görünmeli (`PolyPaper Bot vX.X.X`)
- "bg_task notify handler registered" log'u
- Telegram'da bot'a `/start` → hub keyboard açılmalı

Ctrl+C ile kapatabilirsin.

## 7. Production — Watchdog

Watchdog bot'u arkaplanda başlatır + çökerse otomatik restart eder.

```powershell
watchdog.bat
```

Bu bat:
1. VBS launcher ile bot'u detached process olarak başlatır
2. Single-instance lock (Phase 57 Watchdog v2)
3. Her 30s health check
4. Çökme tespit edilirse yeniden başlat
5. Log: `logs/watchdog.log`

**Startup'a ekle:**
1. `Win+R` → `shell:startup`
2. `watchdog.bat` dosyasının kısayolunu bu klasöre kopyala
3. PC her boot'ta bot otomatik başlar

## 8. Operational Commands

### Restart / Rollback

Projede `rollback.bat` dosyası **yoktur** (T11.3 Bulgu A, 2026-04-24 doc fix).
Geri alma için 3 yöntem — incident tipine göre:

```powershell
# 1) Son commit bozuk ise (en yaygın):
git revert HEAD --no-edit
# 2) Phase 82e Sprint 2.1 (safe_create_task) regression:
py -3.11 scripts\rollback_sprint_2_1.py
# 3) DB corruption — bot durdur, son sağlam backup'ı swap et:
#    data_store\backups\polypaper_YYYY-MM-DD.db → data_store\polypaper.db
#    (bkz. docs\mainnet\T11_3_rollback_plan.md Senaryo 4)

reset_and_start.bat   # DB wipe + 20 new strats (TEHLİKELİ, son çare)
```

Tam rollback matrisi + dry-run kanıtları için: **`docs/mainnet/T11_3_rollback_plan.md`**.

### Backup
```powershell
backup.bat            # Manuel full snapshot
                      # → backups/polypaper_YYYYMMDD_HHMMSS.db
```

Gecelik backup otomatik çalışır (Phase 47f.8 retention job).

### Monitor
```powershell
# PowerShell'de canlı log
Get-Content logs\bot.log -Wait -Tail 50

# HyperOpt monitor
analyze_ob_snapshots.bat
```

### Deploy New Phase
Her phase için `deploy_phase*.bat` dosyası vardır. Pattern:
```
/T+8s+recheck  — çalışma doğrulaması
goto :fail     — exit code handling
pause          — close-safe
```

**Kritik:** `py -c "..."` içinde multi-statement Python **asla yapma** — cmd silently exits, pencere kapanır. Script'leri `scripts/*.py` içine çıkar.

## 9. Deployment Dosyaları

| Dosya | Açıklama |
|---|---|
| `watchdog.bat` / `watchdog.vbs` | Production başlatıcı |
| `git revert HEAD --no-edit` + restart | Acil geri alma (rollback.bat dosyası YOK; T11.3 rollback_plan.md) |
| `backup.bat` | Manuel DB yedekleme |
| `reset_and_start.bat` | Strateji sıfırlama |
| `analyze_ob_snapshots.bat` | OB data analiz |
| `clean_overfit.bat` / `clean_overfit_force.bat` | Overfit hyperopt sonuçları temizle |
| `create_covering_index.bat` | Sprint 4.3 covering idx kurulum |
| `create_split_backtest_index.bat` | Sprint 4.4 split-BT idx |
| `deploy_phase*.bat` | Her phase'in kendi deploy'u |

## 10. İzleme & Sağlık Kontrolü

Telegram'da:
```
/health          # Tüm modüllerin durumu
/db_health       # DB bütünlüğü
/heartbeat       # 5dk'lık canlılık check
/diagnose        # Phase 63 diagnostic
/risk_hub        # Risk limitleri
```

## 11. Güvenlik Katmanları

1. **`.env` asla commit edilmez** — `.gitignore` + pre-commit hook
2. **`LIVE_ENABLED=false`** default — shadow-only
3. **`MIN_BALANCE_FLOOR=50.0`** — altına düşerse trade durur
4. **`MAX_DAILY_LOSS=50`** — günlük kayıp limiti
5. **`MAX_LOSS_STREAK=10`** — üst üste kayıp sonrası pause
6. **Circuit breaker** — anomali tespitinde kill switch

## 12. Sorun Giderme

Yaygın hatalar ve çözümleri: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
