# P1-08 — SQLite → PostgreSQL Migration: Deep Dive

**Tarih:** 2026-05-11
**Durum:** Açıklama dökümanı (kod yok, sadece anlam ve etki analizi)
**Heddas direktifi:** "c nedir, nasıl etkiler, neden yaparız" — kod uygulanmadan önce net karar bilgisi.

---

## 1. Ne — PostgreSQL nedir, projedeki yeri

### Mevcut durum
PolyPaper Bot şu an **tek bir SQLite dosyası** üzerinde çalışıyor:

- **Yol:** `data_store/polypaper.db` (Windows-local).
- **Mod:** WAL (Write-Ahead Log) — bir yazar + paralel okuyucular.
- **Boyut:** ~75 MB (1417 trade + 200+ market + ~3-4 GB OB arşivi kapsıyor — arşiv başka tabloda).
- **Erişim:** `aiosqlite` async wrapper, tek-süreç (bot.py + script'ler aynı dosyaya WAL contention'la giriyor).
- **Yedekleme:** Gece atomic snapshot (P0-05 — `daily_db_snapshot_job` SHA256 manifestli).

SQLite'ın güçlü yanları:
- Sıfır ops yükü (dosya = veritabanı).
- Bot restart bağımsız (dosya kalır).
- Backtest scriptleri aynı dosyayı read-only açabiliyor (P1-04-a `audit_strategies.py` `mode=ro` + online backup).

### PostgreSQL ne fark eder
PostgreSQL **bağımsız bir veritabanı sunucusu**dur. Polypaper bağlamında üç temel davranış değişikliği getirir:

| Boyut | SQLite (mevcut) | PostgreSQL (P1-08 sonrası) |
|---|---|---|
| **Erişim modeli** | Dosya, tek yazar | Network (localhost:5432), paralel yazar + okuyucular |
| **Çoklu kullanıcı** | Yok (tek sürec hakim) | Doğal (her bot ayrı user/schema) |
| **Yedekleme** | `cp` + WAL checkpoint | `pg_dump` / Point-In-Time Recovery (PITR) |
| **Schema versioning** | ALTER TABLE elle | Alembic / sqlx migration tooling |
| **Replication** | Manuel rsync | Streaming replication / read replica |
| **JSON sorgulama** | `json_extract()` yavaş | Native JSONB index'li (GIN) |
| **Concurrency** | Lock contention SHM/WAL | MVCC row-level locks |
| **Operasyon** | Dosya backup yeter | systemd / docker / WAL archive setup |

### Mimari değişiklik özetle
```
ŞIMDI:
  bot.py ──┐
  scripts/* ├── aiosqlite ──→ data_store/polypaper.db (1 dosya)
  audit ──┘                              ↑ Cross-FS WAL contention burada doğuyor

SONRA (P1-08):
  bot.py ──┐
  scripts/* ├── asyncpg ──→ postgres://localhost:5432/polypaper
  audit ──┘                              ↑ Native concurrent reader/writer
                                         ↑ Multi-tenant: ayrı schema'lar
                                            (P2-01 SaaS için ZORUNLU önkoşul)
```

---

## 2. Nasıl etkiler — bot davranışı + ops + yol haritası

### 2.1 Bot davranışı (hot path)

**Yazma yolu (engine → DB):**
- SQLite WAL'da: tek yazar, sequential commit. ~50-200 trade/saatte hiç sıkıntı yok.
- PostgreSQL'da: row-level lock MVCC. Çok daha fazla yazar paralel (örn. AI Brain decision logging + main trade insert + reconciliation worker aynı anda yazabiliyor — şu an WAL contention oluşturuyorlardı).

**Okuma yolu (Telegram /panel + scripts):**
- SQLite + bot uptime durumunda: read-only script (audit_strategies.py) `mode=ro` + online backup yöntemiyle WAL'a dokunmadan snapshot çekiyor — **çalışıyor ama karmaşık** (P1-04 doctrine).
- PostgreSQL: Native parallel reader, snapshot semantic'i (MVCC) gereği. `SELECT ... AS OF SYSTEM TIME` desteklenmese de `pg_export_snapshot()` ile point-in-time read tutarlılığı bedavaya geliyor.

**Cross-FS WAL Doctrine'ın geleceği:**
- Şu an `reference_cross_fs_wal_doctrine.md` memory: "Linux mount'tan Windows-tarafındaki canlı SQLite DB'ye RO/RW erişmek imkansız."
- PostgreSQL ile bu kısıt **otomatik kalkar** çünkü erişim TCP üzerinden. Linux sandbox bot'un Postgres'ine bağlanabilir (eğer Windows postgres servis dinleyici izin verirse). Cowork iş akışında script'leri Linux'tan çalıştırmak yeniden açılır.

### 2.2 Operasyon yükü (downside)

PostgreSQL **bedavaya gelmiyor**. Yeni yükler:
- **Kurulum:** Windows tarafında PostgreSQL 16 service install (`postgresql-x64-16` MSI) + initdb + `pg_hba.conf` localhost trust + service start. ~30 dakika one-time.
- **Connection pooling:** asyncpg + pool (default 10-20 conn). Bot.py'da `db.conn` yerine `db.pool` shimming gerekli.
- **Migration tooling:** Alembic schema versioning kurulması. Şu an `data/database.py` `CREATE TABLE IF NOT EXISTS` + `try ALTER TABLE` pattern — Postgres'te kaldırılıp `alembic upgrade head` yerine geçecek.
- **Yedekleme:** `pg_dump --format=custom polypaper > backup.dump` + cron. Atomic snapshot doctrine yeniden yazılacak (`daily_db_snapshot_job` → pg_dump çağrısı).
- **İzleme:** `pg_stat_statements` ile yavaş sorgu tespit (önceden bot içinde manuel timer'lar).

### 2.3 Şu an etkilenecek kritik path'ler

| Modül | Etki | Tahmini iş |
|---|---|---|
| `data/database.py` | tüm sorgu sürücüleri | aiosqlite → asyncpg (~300 satır rewrite) |
| `data/dbschema_v*.py` | tablo create | Alembic'e taşı (~20 migration file) |
| `core/changelog.py` | DB write | sorgu uyumluluğu (TEXT → JSONB diff?) |
| `core/engine_settlement.py` | DB read/write | placeholder farkı `?` → `$1` |
| `scripts/audit_strategies.py` | online backup yok | `pg_dump --schema-only` veya read-only role |
| `scripts/prune_strategies.py` | UPDATE statement | placeholder + transaction isolation |
| `backtest_v2/` | parquet okuma | etkilenmez (parquet ayrı dosya) |
| `daily_db_snapshot_job` | DB file copy | `pg_basebackup` veya `pg_dump` çağrısı |
| `restore_from_backup.py` | DB dosya copy | `pg_restore` çağrısı |

**Tahmini toplam refactor:** ~800-1200 satır net değişiklik + ~20 Alembic migration + 1 systemd/Windows service config.

### 2.4 Yol haritası bağımlılığı

PostgreSQL şu işlerin **önkoşulu**:
- **P2-01 SaaS multi-tenant** (zorunlu): SQLite tek dosya = tek bot. SaaS = N bot × N user = N tenant. Postgres olmadan schema isolation olmuyor.
- **P2-02 Dashboard / observability** (önerilen): Postgres'in `pg_stat_*` view'ları + JSONB query'leri dashboard'a doğal akıyor.
- **P1-09 Reconciliation Wave 2** (faydalı): Postgres advisory lock'ları reconciliation lease pattern'ı için temiz.

PostgreSQL şu işlerden **bağımsız** (önce ya da sonra yapılabilir):
- P0/P1 quick wins (saat-pin, audit prep, mypy, AI brain extraction)
- P1-01 coverage artırımı
- P1-03/04/06 mevcut işler

---

## 3. Neden yaparız — motivation

### 3.1 Bugün için (acil ihtiyaç var mı?)

**Hayır.** Mevcut SQLite bot için yeterli:
- 1417 trade × ~50 KB = ~70 MB; SQLite happily handles 10-100x bu boyutu.
- WAL contention sadece **cross-FS** durumunda olur (Linux sandbox + Windows bot aynı dosyaya); native Windows ops'ta sıkıntı yok.
- Bot tek-süreç olduğu için lock contention pratik değil.

Yani **kısa vadede ROI yok** — fix etmenin bizi daha hızlı yapması doğrudan değil.

### 3.2 SaaS pivot için (Plan B)

Memory `project_5ai_synthesis_2026_04_30.md`'de:
> "SaaS pivot Plan B" — eğer paper-only bot ticari hale gelirse, her kullanıcı için ayrı bir bot instance lazım. Tek SQLite = tek tenant. PostgreSQL = N tenant × ayrı schema.

P2-01 SaaS multi-tenant yapılacaksa P1-08 **zorunlu**. SaaS yapılmayacaksa P1-08 **opsiyonel**.

### 3.3 Operasyonel sağlamlık için (defense in depth)

- **PITR:** Postgres'in Point-In-Time Recovery'si gece snapshot'tan daha iyi. "Şu saatteki bot state'ine geri dön" oluyor (örn. bir AI Brain hyperopt fiyaskosu sonrası).
- **Replication:** Read replica → audit/backtest script'leri primary'yi etkilemeden okur. WAL contention doctrine'ı tamamen ortadan kalkar.
- **Schema migration history:** Alembic ile schema versiyonu commit'lere bağlanır. Şu an `data/dbschema_v23.py` gibi manuel "ALTER TABLE try-except" patternleriyle yönetiliyor.

### 3.4 Geliştirme deneyimi için

- **Cross-FS Linux sandbox** problemi tamamen ortadan kalkar — bot Windows'ta çalışırken Linux sandbox'tan `psql -h localhost` ile audit script'leri çalıştırabilirim.
- Mevcut "Cross-FS WAL Contention Doctrine" memory'si gerek kalmaz (artık önemsiz).
- Daha güvenli script'ler: read-only role oluşturup audit/backtest hep o role ile bağlanır.

---

## 4. Karar matrisi

| Soru | Cevap |
|---|---|
| **Acil mi?** | Hayır. Bot şu an stabil. |
| **Zaman maliyeti?** | ~3-5 gün net refactor + 1 gün ops setup + 2 gün hata düzeltme = ~1 hafta full effort. |
| **Risk seviyesi?** | Orta — DB migration en kritik path. Atomic backup + restore plan zorunlu. |
| **Geri dönülebilir mi?** | Evet, snapshot ile geriye SQLite'a alınabilir (ilk 2 hafta paralel run önerilir). |
| **SaaS pivot kararı var mı?** | Heddas direktifi bekleniyor. Pivot olursa P1-08 zorunlu önkoşul. |
| **Cross-FS contention can sıkıyor mu?** | Bu seansta tekrar çıktı (audit_strategies.py Linux mount stale). Memory'de doctrine var ama her seans tekrar yaşanıyor. |

---

## 5. Önerilen yol haritası (Heddas onayında implement)

### Faz 1 (~1.5 gün): Hazırlık + paralel run
1. Windows tarafında PostgreSQL 16 install + service.
2. `data/database_postgres.py` NEW — asyncpg pool wrapper, mevcut SQLiteDB API ile birebir uyumlu.
3. `ENV: DB_BACKEND={sqlite|postgres}` switch — runtime seçim. Default SQLite (zero-impact).
4. Tek bir yardımcı script (`scripts/migrate_sqlite_to_postgres.py`) ile mevcut data'yı kopyalama.

### Faz 2 (~2 gün): Migration tooling
5. Alembic init + ilk migration ('v23' snapshot baseline).
6. `data/dbschema_v*.py` patternlerini Alembic migration'larına dönüştürme.
7. CI'da migration apply check.

### Faz 3 (~1 gün): Hot path geçiş
8. Bot'u `DB_BACKEND=postgres` ile shadow paralel çalıştır (1-2 gün gözlem).
9. `daily_db_snapshot_job` → `pg_dump` çağrısı.
10. `restore_from_backup.py` → `pg_restore` çağrısı.

### Faz 4 (~1 gün): Ops + temizlik
11. Read replica setup (audit/backtest için).
12. SQLite kodunu `_archive/` altına taşı (geri çağırılabilir).
13. Cross-FS doctrine memory'sini güncelle ("artık geçerli değil — DB TCP üzerinden, mount sorunu yok").

---

## 6. Tek satır karar tavsiyesi

> **P1-08 sadece SaaS pivot kesinleştiğinde "P0 zorunlu" olur.** Aksi takdirde bekleyebilir — mevcut SQLite + WAL paper trading + shadow live için yeterli. SaaS yolu açıksa Faz 1 (paralel run, switch'li) en az 2 hafta önceden başlatılması önerilir.

Heddas karar: SaaS pivot var mı / yok mu? — bu Faz 1'in başlatılıp başlatılmayacağını belirleyecek.
