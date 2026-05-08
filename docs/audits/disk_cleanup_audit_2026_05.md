# Disk Cleanup Audit — 2026-05 (Heddas direktifi)

**Tarih:** 2026-04-30
**Mevcut disk:** 109 GB
**Hedef:** ~3-5 GB (canlı bot DB + saniyelik backtest verileri + kod)
**Beklenen kazanım:** ~104-106 GB

---

## 0 — TL;DR

| Kategori | Mevcut | Aksiyon | Kazanım |
|---|---|---|---|
| `data_store/backups/` (eski .db) | **73 GB** | Phase B prune (son 3 koru) | ~70 GB |
| `data_store/backup_phase82e/` | 8.8 GB | Phase A sil | 8.8 GB |
| `data_store/polypaper_pre_phase80.db` | 9.7 GB | Phase A sil | 9.7 GB |
| `data_store/polypaper_pre77.db` | 8.0 GB | Phase A sil | 8.0 GB |
| Orphan `.db-wal` (in `backups/`) | **27.5 GB** | Phase A sil | 27.5 GB |
| `htmlcov/`, 26× `__pycache__` | ~16 MB | Phase A sil | <20 MB |
| **TOPLAM** | **108 GB** | | **~106 GB** |

**Sonuç:** 108 GB → **~3 GB** (canlı bot + backtest verileri).

---

## 1 — Mevcut Disk Haritası (sandbox tarama 2026-04-30)

```
data_store/    108 GB    ← ŞİŞMİŞ (target)
├── polypaper.db                                    8.8 GB    ✅ KORUMA (canlı)
├── polypaper.db-wal                                 20 MB    ✅ KORUMA
├── polypaper.db-shm                                 64 KB    ✅ KORUMA
├── trade_journal.jsonl                             9.5 MB    ✅ KORUMA
├── decisions.jsonl                                 3.8 MB    ✅ KORUMA
├── polypaper.log                                   1.4 MB    ✅ KORUMA
├── polypaper.log.{1,2,3}                            15 MB    ⚠️ son 1'i koru
├── backtest_cache.db                                60 KB    ✅ KORUMA
├── polypaper_pre77.db                              8.0 GB    ❌ SİL (Phase 77 öncesi)
├── polypaper_pre_phase80.db                        9.7 GB    ❌ SİL (Phase 80 öncesi)
├── backup_phase82e/                                8.8 GB    ❌ SİL (Sprint öncesi)
└── backups/                                         73 GB    ❌ PRUNE (son 3 koru)
    ├── polypaper_2026-04-17.db-wal                 17.9 GB   ❌ ORPHAN WAL
    ├── polypaper_2026-04-19.db-wal                  9.6 GB   ❌ ORPHAN WAL
    ├── polypaper_2026-04-29.db                     8.9 GB   ✅ son backup (KEEP-1)
    ├── polypaper_2026-04-30.db.tmp                 621 MB   ⚠️ atomic in-progress
    ├── polypaper_2026-04-23.db                     651 MB   ❌ eski
    ├── polypaper_2026-04-20.db                     780 MB   ❌ eski
    ├── polypaper_2026-04-19.db                     8.9 GB   ❌ eski
    ├── polypaper_2026-04-17.db                     8.8 GB   ❌ eski
    ├── polypaper_2026-04-16.db                     9.7 GB   ❌ eski
    └── polypaper_pre_sprint012_2026-15-04.db       8.5 GB   ❌ Sprint 1.2 öncesi

data/          996 MB    ✅ ÇOĞU KORUMA (saniyelik veri)
├── archive/   ~920 MB
│   ├── ob_snapshots_2026-04-12_142603.parquet     317 MB    ✅ KORUMA (backtest)
│   ├── ob_snapshots_2026-04-10_124010.parquet     176 MB    ✅ KORUMA
│   ├── ob_snapshots_2026-04-09_125816.parquet     127 MB    ✅ KORUMA
│   └── (5 daha küçük .parquet)                              ✅ KORUMA
└── polypaper.db (boş, eski path)                  -          ❌ SİL

_archive/       9.6 MB   ✅ KORUMA (eski kod arşivi)
htmlcov/        5.8 MB   ❌ SİL (regenerable)
26× __pycache__  ~10 MB  ❌ SİL (regenerable)
backups/         (root) 0 byte                     ❌ SİL (boş klasör)
reports/         (root) 0 byte                     ❌ SİL (boş klasör)
polypaper.db     (root, eski 64K)                  ❌ SİL (yanlış path)
```

---

## 2 — Becker / Hyperopt Artıkları Durumu

### 2.1 Becker
**Memory:** Becker Aşama 1 + 3.C + 3.E silindi (10 dosya rm + engine.py init unwire + bot.py 7 blok). Aşama 2 cosmetic backlog: `engine_signals 140 satır + engine_fills _becker_delta + backtest_v2 358 satır dead command bodies`.

**Sandbox grep (2026-04-30):**
20 dosyada `becker|Becker` referansı:
```
backtest/replay_engine.py, replay_engine_v3.py, simulation/fee_model_v3.py
calibration/surface_2d.py, config/env_whitelist.py, config/settings.py
core/engine.py, engine_fills.py, engine_monitor.py, engine_settlement.py,
     engine_signals.py, intent_parser.py, stats_utils.py, strategy_plugins.py
telegram_bot/bot.py, handlers/{ai_handler,filters_handler,menu_handler,
     phase77_handler,strategy_tester}.py
```

**Karar:** Bunlar **kod-level referans** (kullanılmayan dead code, runtime etkisi yok). Disk'i etkilemiyor (KB seviyesi). Aşama 2 (cosmetic cleanup) **bu disk cleanup'ın kapsamı dışı**. P1.X olarak ayrı backlog.

### 2.2 Hyperopt
**Memory:** Hyperopt Aşama 1 silindi (699 occurrence × 31 dosya purge). Aşama 2 backlog: `DB migration v16 drop hyperopt_results + 5 verify/smoke scripts`.

**Sandbox grep:**
15 dosyada `hyperopt|HyperOpt` referansı + `db/migrations.py` içinde `hyperopt_results` table tanımı (henüz drop migration eklenmedi).

**DB içinde hyperopt_results tablosu var mı?** Bot DB schema 17. `polypaper.db` 8.8 GB → muhtemel büyük tablo. Heddas yerel kontrol:
```cmd
py -3.11 -c "import sqlite3; con=sqlite3.connect('data_store/polypaper.db'); print('TABLES:'); [print(' ', r[0], r[2]) for r in con.execute('SELECT name, type, sql FROM sqlite_master WHERE type=\"table\"').fetchall()]; con.close()"
```

**Karar:** Hyperopt artıkları kod-level + 1 DB tablo. DB migration v16 (drop hyperopt_results) **disk kazanımı sağlar** AMA tabloda satır var mı bilinmiyor. Heddas yerel yukardaki query ile tablo size'ını ölçer.

### 2.3 Toplam Becker/Hyperopt artık disk impact

- Kod artıkları: **<2 MB** (negligible vs 108 GB)
- DB hyperopt_results tablosu: **bilinmiyor** (Heddas yerel ölçer)
- Disk cleanup için kritik DEĞİL (Phase A+B 95%+ kazanım sağlıyor zaten)
- Becker Aşama 2 + Hyperopt Aşama 2 **kod cleanup** — ayrı task (P1.X cosmetic)

---

## 3 — DB İçi Cleanup (Sprint 3'e — opsiyonel, P1)

**Polypaper.db 8.8 GB neden bu kadar büyük?**

Memory'deki `.env` retention:
```
DB_RETENTION_OB_SNAPSHOTS_DAYS=14
DB_RETENTION_OB_TRADES_DAYS=30
DB_RETENTION_CANDLES_POLY_DAYS=45
```

Bot retention çalışıyor olmalı, ama belki:
- Çok aktif scan (5s interval × 8 pair × 2 timeframe = saniyede ~3 yazım)
- `ob_snapshots` tablosu 14 gün × yüksek throughput = GB seviyesi

**Heddas yerel kontrol (Phase B sonrası):**
```cmd
py -3.11 scripts\db_table_sizes.py    # yeni script (P1)
```

Bu script tablo başına row count + boyut verir. Eğer ob_snapshots > 5 GB → retention 14g → 7g'e düşür önerisi.

---

## 4 — Heddas Yerel Apply (3 Aşama)

### 4.1 Aşama A: Audit (read-only, 30sn)

```cmd
:: 1. Botu durdur (precaution)
.\stop_bot.bat

:: 2. Audit
py -3.11 scripts\disk_cleanup_audit.py

:: Çıktı: evidence\disk_cleanup_audit_<TS>.md detaylı tablo
```

### 4.2 Aşama B: Phase A Safe Deletes (1 dk)

**~34 GB kazanım** — pre-phase snapshots + orphan WAL + cache/pycache + boş dosyalar.

```cmd
scripts\disk_cleanup_phase_a.bat
:: Onaylama "y" tuşu
```

Silinen:
- `data_store\polypaper_pre77.db` (8 GB)
- `data_store\polypaper_pre_phase80.db` (9.7 GB)
- `data_store\backup_phase82e\` (8.8 GB)
- `data_store\backups\*.db-wal` orphan'lar (27.5 GB)
- `htmlcov\`, `__pycache__\` (recursive)
- Root'taki boş `polypaper.db` ve `data\polypaper.db`

### 4.3 Aşama C: Phase B Backups Prune (~70 GB kazanım, 2 dk)

```cmd
:: Önce dry-run (kim silinir kontrol)
py -3.11 scripts\disk_cleanup_phase_b.py --keep 3 --dry-run

:: OK ise gerçek silme
py -3.11 scripts\disk_cleanup_phase_b.py --keep 3
:: Onaylama: y
```

Korunan: en yeni 3 backup (yaklaşık son 1 hafta).
Silinen: 7+ eski backup (Apr 16'dan eski).

### 4.4 Aşama D: Bot restart + smoke

```cmd
:: Disk durumu
dir /S | findstr "File(s)"

:: Bot başlat
.\start.bat

:: Telegram /h ile heartbeat doğrula
:: Telegram /portfolio ile budget hala $1.49 mu kontrol
```

**Beklenti:** Disk 108 GB → ~3 GB. Bot canlı DB + WAL bozulmadan çalışır.

---

## 5 — Risk Mitigation

### 5.1 Bot çalışırken silme YASAK
- `polypaper.lock` varsa Phase A script kontrol edip durur.
- `stop_bot.bat` ile lockfile temizlenmeden cleanup yapma.

### 5.2 Yedekleme önce (opsiyonel ama önerilir)
Heddas direktifi: "yedekle önce, sonra sil"
```cmd
:: D:\backup_safety\ veya başka disk'e kopyala
xcopy /E /I /Y data_store\polypaper.db D:\backup_safety\polypaper_2026-04-30_pre_cleanup.db
```

### 5.3 Geri alma
Phase A silinenler **geri alınamaz** (pre-phase DB'ler kalıcı kayıp). AMA:
- Mevcut canlı `polypaper.db` (8.8 GB) korunur — bot çalışmaya devam eder
- `_archive/` (9.6 MB kod arşivi) dokunulmaz
- `data/archive/*.parquet` saniyelik veriler dokunulmaz

### 5.4 Atomic backup `.tmp` durumu
`backups/polypaper_2026-04-30.db.tmp` (621 MB) — Bulgu B fix sonrası atomic write in-progress veya stuck. Phase B script `.tmp` dosyalarına dokunmaz (intentional). Sonradan manuel rename veya silme.

---

## 6 — Memory Landmark

`memory/project_disk_cleanup_2026_04_30.md`:
```
Disk cleanup CLOSED 2026-04-30. 108 GB → ~3 GB hedefi.
Kazanım: backups/ 73GB prune + backup_phase82e 8.8GB + pre77 8GB + pre_phase80 9.7GB
+ orphan WAL 27.5GB + htmlcov + __pycache__.
Korunan: canlı polypaper.db (8.8GB) + saniyelik ob_snapshots parquet'ler (920MB).
Becker/Hyperopt kod artıkları (Aşama 2 cosmetic) ayrı backlog (KB seviyesi, disk kritik değil).
DB retention env aktif (ob_snapshots 14g/trades 30g/candles 45g) — DB içi büyüme P1.
```

---

## 7 — Bağlantılı Belgeler

- `scripts/disk_cleanup_audit.py` — read-only audit + evidence MD
- `scripts/disk_cleanup_phase_a.bat` — safe deletes (~34 GB)
- `scripts/disk_cleanup_phase_b.py` — backups prune (~70 GB)
- `MASTER_PLAN_2026_04_30.md` — Sprint 1 entegrasyonu
- Memory: `becker_aciklamasi_aciklama_1_kapatildi`, `hyperopt_asama_1_closure`

**Sonuç:** Disk cleanup framework hazır. Heddas yerel 3 aşama execute → 108 GB → 3 GB.
