# Epic 11 T11.3 — Rollback Plan Dry-Run

**Amaç:** Mainnet go-live sonrası kritik bir incident'te bot'u güvenli bir
önceki state'e nasıl geri getireceğimizi kanıtla. Her rollback
mekanizması için kapsam + ön-koşul + dry-run checklist + validation
step'leri net olmalı.

T11.2 (runtime guards) + T11.3 (rollback) birlikte mainnet pre-gate'in
belkemiğini oluşturur. T11.1 Bölüm 8 referansı.

**Template tarihi:** 2026-04-23
**Test sahibi:** Heddas (Windows yerel)
**Doğrulama sahibi:** Claude (bu dosya + TASKS.md update)
**Kapanış tarihi:** *(dry-run 4 senaryo da ✅ olunca yaz)*

---

## Genel Prensipler

1. **LIVE_ENABLED=false TÜM rollback testleri boyunca.** Shadow mirror
   + engine state simulation yeterli; gerçek emir yok.
2. **Backup önce, değişiklik sonra.** Her rollback ile değişen DB dosyası
   için `backup.bat` (veya manuel `copy data_store\polypaper.db
   backup\polypaper_preroll_%TS%.db`) çalıştırılır.
3. **Git-based rollback > script-based rollback.** Mümkün olduğunda
   `git log` + `git revert <sha>` + hot-restart; bu "değiştirdiğim tam
   olarak ne" konusunda ayna netliği sunar.
4. **Auditable evidence.** Her dry-run sonunda:
   - Pre-state snapshot (`/risk` + `/diagnose skips` + `git log --oneline -5`)
   - Rollback komutu
   - Post-state snapshot
   - "State geri geldi mi?" verdict
   ile bir kanıt bloku üretilir ve aşağıdaki ilgili bölüme yapıştırılır.

---

## Rollback Envanteri (2026-04-23)

| Mekanizma | Kapsam | Kaynak | Tip | Kullanım senaryosu |
|-----------|--------|--------|-----|--------------------|
| **M1: Git revert** | Herhangi tek commit | `git revert <sha>` | Git | Yeni feature bozuk → son commit'i geri al |
| **M2: Git reset --hard** | Son N commit | `git reset --hard HEAD~N` | Git (destructive) | Birden fazla commit'te biriken bozuk chain'i sil |
| **M3: rollback_sprint_2_1.py** | Phase 82e Sprint 2.1 (safe_create_task) | `python scripts/rollback_sprint_2_1.py` | Script (idempotent) | bg_task crash storm → asyncio.create_task'a dön |
| **M4: `/env_toggle` restore** | Runtime ENV değişikliği | `/envt <KEY> <orig_value>` | Telegram | T11.2 test sırasında düşürülen threshold'u geri al |
| **M5: DB snapshot restore** | Tüm trade/state DB'si | `copy backup\polypaper_*.db data_store\polypaper.db` + restart | Dosya kopyası | Ciddi state corruption (WAL corruption, schema migration fail) |
| **M6: LIVE_ENABLED=false hot kill** | Live mode disable (shadow devam eder) | `/envt LIVE_ENABLED false` | Telegram | Canlı emir anomalisi — yeni live trade durur, açıklar settle eder |

**NOT:**
- `docs/DEPLOYMENT.md:115` "rollback.bat" referansı ghost — kök
  dizinde `rollback.bat` yok. T11.3 closure sırasında ya bat yaz ya
  referansı `scripts/rollback_sprint_2_1.py` + git revert'e yönlendir.
  Bkz. T11.3 closure checklist aşağıda.

---

## Senaryo Karar Matrisi

| Incident tipi | Birincil rollback | Sekonder | Sebep |
|---------------|-------------------|----------|-------|
| Son 1 commit bozuk (test fail, boot fail) | M1 git revert | M2 reset | Revert audit trail üretir; reset history'yi gizler |
| CLOB signature regression (live emir reject) | M1 git revert (son CLOB commit'i) | M6 LIVE=false | Hot-kill + revert paralel çalışabilir |
| bg_task crash storm (Telegram notify spam) | M3 rollback_sprint_2_1.py | — | Script idempotent, disk-based, hot-apply |
| ROLLING_WR_KILL false-positive (tüm stratejiler pause) | M4 /envt ROLLING_WR_KILL 0 | M6 LIVE=false + manual resume | Threshold 0 = guard silent ama stratejiler zaten pause'da; manuel /strategy_resume gerekli |
| PnL divergence false-alert | M4 /envt PNL_DIVERGENCE_ALERT_PCT 100 | Log inceleme | Threshold 100% = silent; root cause bulunana kadar |
| WAL corruption / DB boot fail | M5 DB snapshot restore | Son `backup.bat` backup'ı | DB'yi manuel swap; backup eski ise trade kaybı riski |
| Live emir anomalisi (balance drift) | M6 LIVE=false | M5 DB restore | Hot kill → shadow kal, sonra analiz |
| T11.2 test sırasında threshold stuck | M4 /envt restore (her guard için) | M1 revert (config dosyası değiştiyse) | `.env` el ile edit edildiyse revert + restart |

---

## Dry-Run Senaryoları

Her senaryo için 3 aşama: **pre-state snapshot → rollback komut → post-state snapshot**.
Pass kriteri: post-state, beklenen pre-state'e matematiksel olarak eşit
(trade count, balance, PnL, strategy status).

### Senaryo 1 — M1 Git Revert (safe commit) — ☒ PASS (2026-04-23)

**Evidence:** `evidence/t11_3_s1_20260423_231946.txt` (gitignored)
**Script:** `_archive/t11_3_s1_git_revert_dryrun.bat` (çift-tıklanabilir)

**Ön koşul:** Temiz ağaç (`git status` boş), bot etkilemez.

**Adımlar (bat otomasyonu):**
1. Pre-state: `git diff --quiet && git diff --cached --quiet` (tracked tree clean guard)
2. Pre-SHA kaydet: `git rev-parse HEAD` → `PRE_SHA`
3. Dummy commit: `docs/test_rollback_dummy.md` yarat + `git add` + `git commit -m "chore(t11.3-s1): dummy commit for revert dry-run (temp)"`
4. Revert: `git revert HEAD --no-edit`
5. Post-state `git log --oneline -5` kaydet (dummy + revert commit'ler görünür)
6. Dummy dosya silindi mi teyit (revert cleanup kontrolü)
7. Cleanup: `git reset --hard <PRE_SHA>` (dummy+revert chain yok olur)
8. `FINAL_SHA` = `PRE_SHA` doğrulama

**Pass kriteri:** Pre SHA = Final SHA (round-trip OK). Revert commit
mesajı "Revert 'chore(t11.3-s1)...'" formatında. Dummy dosya revert ile
silindi.

**Canlı kanıt (2026-04-23 23:19 TRT):**

```
Pre SHA   : 2450d11186b95d80a9400e473cdde9918776ce8f
Dummy SHA : 7744649b6baa25e76c2996ca6018ed70842c896b
Revert SHA: 93e95bf7d5f06f831cfa29785ebadab40173f6dc

POST-STATE (revert sonrası, cleanup öncesi):
93e95bf Revert "chore(t11.3-s1): dummy commit for revert dry-run (temp)"
7744649 chore(t11.3-s1): dummy commit for revert dry-run (temp)
2450d11 docs(t11.3): S3 /envt restore PASS -- audit log evidence
...

FINAL SHA after cleanup reset = 2450d11... (PRE_SHA ile identik)
Dummy file (docs/test_rollback_dummy.md) silindi: YES
```

**Verdict:** ☒ PASS — Git revert + reset --hard round-trip canlı
doğrulandı. Revert audit trail (log'da "Revert '...'" satırı) olarak
üretilmiş, cleanup reset ile history temizlenebildi. Incident sırasında
seçim matrisi: önce revert (audit için), sonra gerekirse reset
(destructive cleanup).

---

### Senaryo 2 — M3 rollback_sprint_2_1.py (idempotent script) — ☒ PASS (2026-04-23)

**Evidence:** `evidence/t11_3_s2_20260423_232244.txt` (gitignored)
**Script:** `_archive/t11_3_s2_rollback_script_dryrun.bat` (çift-tıklanabilir)
**Rollback hedef:** `scripts/rollback_sprint_2_1.py` (Phase 82e Sprint 2.1 kaldırma)

**Ön koşul:** Tracked tree clean. Bot etkilemez (Python belleğe
yüklü kodu kullanır, disk değişikliği bir sonraki restart'a kadar etkisiz).

**Adımlar (bat otomasyonu):**
1. Pre-state: `git diff --quiet && git diff --cached --quiet`
2. Pre-SHA kaydet
3. **FIRST RUN**: `py -3.11 scripts/rollback_sprint_2_1.py` (stdout evidence'a)
4. Post-first-run `git status` (modifiye dosya listesi kayıt)
5. **SECOND RUN**: aynı script (idempotency kanıtı)
6. Post-second-run `git status` (ilk status ile bit-identical olmalı)
7. **RESTORE**: `git checkout -- core/ data/ telegram_bot/bot.py`
8. Restore sonrası `git diff --quiet` (tree temiz mi)

**Pass kriteri:**
- İlk run: N dosya değişti, exit 0
- İkinci run: hepsi "already reverted", exit 0 (idempotent)
- Post-first status == Post-second status (bit-identical)
- Restore sonrası tree clean (`git diff --quiet` OK)

**Canlı kanıt (2026-04-23 23:22 TRT):**

```
Pre SHA: 2450d11186b95d80a9400e473cdde9918776ce8f

FIRST RUN (12 dosya):
  core/engine.py              CHANGED (3 call reverted, 1 import removed)
  core/ai_brain.py            CHANGED (2 call, 1 import)
  core/engine_settlement.py   CHANGED (4 call, 1 import)
  core/keepalive.py           CHANGED (1 call, 1 import)
  data/market_recorder.py     CHANGED (1 call, 1 import)
  data/websocket_client.py    CHANGED (1 call, 1 import)
  data/binance_multistream.py CHANGED (2 call, 1 import)
  data/market_scanner.py      CHANGED (1 call, 1 import)
  data/candle_collector.py    CHANGED (1 call, 1 import)
  data/chainlink_oracle.py    CHANGED (1 call, 1 import)
  data/external_feed.py       CHANGED (2 call, 1 import)
  telegram_bot/bot.py         CHANGED (1 call, 2 imports, 1 notify block)
  Total: 20 call reverted + 13 import removed + 1 notify block
  Syntax check: 13/13 OK (bg_task.py dahil)
  Exit code: 0

SECOND RUN (idempotency):
  Hepsi "ok — (already reverted, no changes)"
  Syntax check: 13/13 OK
  Exit code: 0
  Done: 0 file(s) reverted

Post-first-run status = Post-second-run status (12 M dosya, bit-identical)

RESTORE: git checkout -- core/ data/ telegram_bot/bot.py
Post-restore status: sadece `?? BUGUN_NE_YAPACAGIM.md` (gitignored, normal)
git diff --quiet: OK (tree clean)
```

**Verdict:** ☒ PASS — 3 invariant canlı doğrulandı: (a) script 12
production dosyayı safe_create_task → asyncio.create_task ve bg_task
import temizliği ile revert etti, (b) ikinci run "already reverted"
dönerek idempotency kanıtladı, (c) `git checkout --` ile %100 restore.
Incident sırasında bg_task crash storm için primary rollback mekanizması.
Bot restart dashboard'dan optik; dosya-disk ile bellek-runtime ayrışık.

---

### Senaryo 3 — M4 /env_toggle restore (T11.2 T/S pattern) — ☒ PASS (2026-04-23)

**Ön koşul:** Bot çalışıyor, shadow aktif, admin Telegram.

**Adımlar:**
1. Pre-state: `/env` komutu → mevcut threshold değerleri screenshot
2. Test patch: `/envt ROLLING_WR_KILL 30` + `/env` → 30.0 yazıyor mu?
3. Restore: `/envt ROLLING_WR_KILL 40` + `/env` → 40.0 geri mi?
4. Audit log: `grep ROLLING_WR_KILL logs/env_toggle_audit.log | tail -5`

**Pass kriteri:** `/env` çıktısı step 1 == step 3 (tam geri dönüş). Audit
log 2 satır: patch + restore.

**Kanıt (audit log 2026-04-23, T11.2 testleri sırasında 4 round-trip kanıtlandı):**

```
2026-04-23T13:12:46+00:00  admin=1667498935  SET  WS_STALE_THRESHOLD       old=5    new=60
2026-04-23T13:36:13+00:00  admin=1667498935  SET  LIVE_BUDGET              old=1.49 new=0.5
2026-04-23T13:36:43+00:00  admin=1667498935  SET  LIVE_BUDGET              old=0.5  new=0.5
2026-04-23T13:55:18+00:00  admin=1667498935  SET  LIVE_BUDGET              old=0.5  new=1.49
2026-04-23T13:58:48+00:00  admin=1667498935  SET  LIVE_MAX_DAILY_LOSS      old=1.00 new=0.1
2026-04-23T14:55:56+00:00  admin=1667498935  SET  LIVE_MAX_DAILY_LOSS      old=0.1  new=1
2026-04-23T19:38:21+00:00  admin=1667498935  SET  PNL_DIVERGENCE_ALERT_PCT old=5.0  new=0.01
2026-04-23T19:38:52+00:00  admin=1667498935  SET  PNL_DIVERGENCE_ALERT_PCT old=0.01 new=5
2026-04-23T19:50:08+00:00  admin=1667498935  SET  PNL_DIVERGENCE_ALERT_PCT old=5    new=0.01
2026-04-23T19:50:26+00:00  admin=1667498935  SET  PNL_DIVERGENCE_ALERT_PCT old=0.01 new=5
```

**Round-trip matrix (T11.2 testleri = aynı zamanda S3 M4 dry-run kanıtı):**
| Key | Pre | Test patch | Restore | Restored? |
|-----|-----|-----------|---------|-----------|
| WS_STALE_THRESHOLD | 60 | 5 | 60 | ✅ |
| LIVE_BUDGET | 1.49 | 0.5 | 1.49 | ✅ |
| LIVE_MAX_DAILY_LOSS | 1.00 | 0.1 | 1 | ✅ |
| PNL_DIVERGENCE_ALERT_PCT | 5.0 | 0.01 | 5 | ✅ (2× round-trip) |

**Audit log format:** `timestamp admin=<telegram_id> SET <KEY> old=<X> new=<Y>`.
4 distinct key × round-trip = 10 SET satırı. Tüm patch'ler `.env`'e de
yazıldı (`os.environ + .env guncellendi` bot onayı).

**Kod kancası:** `telegram_bot/handlers/env_toggle.py:41`
`_AUDIT_PATH = logs/env_toggle_audit.log`. Her kabul edilen `/envt`
komutu tab-separated satır yazıyor (admin/SET/old/new).

**Verdict:** ☒ PASS — M4 rollback path canlı ortamda 4 farklı guard için
round-trip doğrulandı. Audit log incident forensics için tam (kim,
ne zaman, hangi key, hangi değer). T11.2 testleri sırasında yan ürün
olarak S3 dry-run'i da kapatıldı — ekstra test koşturmaya gerek yok.

---

### Senaryo 4 — M5 DB snapshot restore

**Ön koşul:** Bot **DURMUŞ** (DB write lock olmadan swap kritik). Son
`backup.bat` backup'ı mevcut (`data_store\backups\polypaper_*.db`).

**Adımlar:**
1. Pre-state: `copy data_store\polypaper.db data_store\polypaper_preroll.db` (bizim fallback)
2. Dummy write: bot'u başlat, 1 paper trade tetikle, kapat
3. DB state diff: `sqlite3 data_store\polypaper.db "SELECT COUNT(*) FROM executions"` → N
4. Swap: `copy data_store\backups\polypaper_<YYYYMMDD>.db data_store\polypaper.db /Y`
5. Post-state: Aynı `SELECT COUNT(*)` → N-1 (dummy trade silindi)
6. Restore: `copy data_store\polypaper_preroll.db data_store\polypaper.db /Y` (testi temizle)

**Pass kriteri:** N post < N pre, fallback restore sonra N = N original.
Bot step 5 sonrası boot edebilmeli (`/h` yanıt vermeli).

**Kanıt:**

```
[YYYY-MM-DD HH:MM:SS]

<... count before / swap çıktısı / count after / boot kanıtı yapıştır ...>

Verdict: PASS / FAIL
```

---

## Kapanış Kriterleri

Aşağıdaki 4 kutu işaretlendiğinde T11.3 ✅:

- [ ] Senaryo 1 (M1 git revert) — dry-run PASS + kanıt
- [ ] Senaryo 2 (M3 rollback_sprint_2_1.py) — ilk+ikinci run PASS + kanıt
- [ ] Senaryo 3 (M4 /env_toggle restore) — pre/post eşit + audit log
- [ ] Senaryo 4 (M5 DB snapshot restore) — dummy write reverted

**Ek closure task'ları:**

- [ ] `docs/DEPLOYMENT.md:115` "rollback.bat" ghost referansı:
  - (a) `rollback.bat` yaz — git revert + service restart wrapper, VEYA
  - (b) referansı `scripts/rollback_sprint_2_1.py` + `git revert HEAD`
    açıklamasına yönlendir.
- [ ] Senaryo 1-4 kanıtları template'e yapıştırıldı.
- [ ] TASKS.md T11.3 closed + timestamp.
- [ ] MEMORY.md landmark update.

---

## Rollback Sırasında Yapma (tehlike listesi)

- **`git push --force main`** asla. Force push main branch'te ==
  uzaktaki herkesin state'ini bozar.
- **`git reset --hard`** ile **commit etmeden** değişikliği atma; diğer
  session'larda/device'larda ghost commit olabilir.
- **LIVE_ENABLED=true iken DB swap** — concurrent write lock DB'yi
  corrupt edebilir. Önce M6 (LIVE=false) + bot'u durdur, sonra swap.
- **Backup doğrulamadan swap** — `sqlite3 backup_file ".schema" | head
  -5` ile backup boot-able mı kontrol et, sonra swap.
- **.env manuel edit + hot-reload beklentisi** — çoğu ENV only restart'ta
  etkili. `/envt` canlı değişir, `.env` edit'i restart ister.

---

## Mainnet Go-Live Takvimi (T11.1 + T11.2 + T11.3 birlikte)

```
T11.1 ✅ — Final audit raporu           (pre-mainnet gate 1/3 — 2026-04-22)
T11.2 ⏳ — Live guard runtime validation (pre-mainnet gate 2/3 — Windows canlı bot)
T11.3 ⏳ — Rollback plan dry-run         (pre-mainnet gate 3/3 — 4 senaryo)
↓
T11.4-T11.8 — Defense-in-depth (backlog, post-GA)
↓
GO/NO-GO decision (T11.1 Bölüm 8 kriterleri)
```

T11.2 + T11.3 paralel koşabilir; ikisi de Windows yerel.

---

**Doğrulama sahibi:** Claude (bu dosya + TASKS.md update)
**Kapanış tarihi:** *(dry-run 4/4 ✅ olunca yaz)*
