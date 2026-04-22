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

### Senaryo 1 — M1 Git Revert (safe commit)

**Ön koşul:** Temiz ağaç (`git status` boş), bot çalışmıyor.

**Adımlar:**
1. Pre-state: `git log --oneline -5 > evidence/t11_3_s1_pre.txt`
2. Dummy commit: `docs/test_rollback_dummy.md` yarat + commit
3. Revert: `git revert HEAD --no-edit`
4. Post-state: `git log --oneline -5 > evidence/t11_3_s1_post.txt`
5. Doğrula: `git diff evidence/t11_3_s1_pre.txt evidence/t11_3_s1_post.txt`
6. Temizlik: `git reset --hard <pre_sha>` (dummy chain'i yok say)

**Pass kriteri:** Post log pre log + 2 yeni satır (dummy + revert). Revert
commit mesajı "Revert 'dummy...'" formatında.

**Kanıt:**

```
[YYYY-MM-DD HH:MM:SS]

<... dry-run kanıtı yapıştır ...>

Verdict: PASS / FAIL
```

---

### Senaryo 2 — M3 rollback_sprint_2_1.py (idempotent script)

**Ön koşul:** Bot çalışmıyor. `scripts/rollback_sprint_2_1.py` mevcut.

**Adımlar:**
1. Pre-state: `git log --oneline | head -3` + `grep -c "safe_create_task" core/*.py data/*.py` (sayaç)
2. `py -3.11 scripts/rollback_sprint_2_1.py` (ilk run)
3. Post-state-1: Aynı `grep -c` (0 olmalı) + `grep -c "asyncio.create_task" core/*.py data/*.py` (artmış olmalı)
4. `py -3.11 scripts/rollback_sprint_2_1.py` (ikinci run — idempotency)
5. Post-state-2: Aynı sayaçlar (değişmemiş olmalı — no-op)
6. Restore: `git checkout -- core/ data/` (script edit'lerini geri al, production state'e dön)

**Pass kriteri:**
- İlk run: N file değişti (script output), safe_create_task count 0, asyncio.create_task count +N
- İkinci run: "No changes" log, count'lar aynı
- Restore sonrası: safe_create_task count eski haline döndü

**Kanıt:**

```
[YYYY-MM-DD HH:MM:SS]

<... dry-run kanıtı yapıştır ...>

Verdict: PASS / FAIL
```

---

### Senaryo 3 — M4 /env_toggle restore (T11.2 T/S pattern)

**Ön koşul:** Bot çalışıyor, shadow aktif, admin Telegram.

**Adımlar:**
1. Pre-state: `/env` komutu → mevcut threshold değerleri screenshot
2. Test patch: `/envt ROLLING_WR_KILL 30` + `/env` → 30.0 yazıyor mu?
3. Restore: `/envt ROLLING_WR_KILL 40` + `/env` → 40.0 geri mi?
4. Audit log: `grep ROLLING_WR_KILL logs/env_toggle_audit.log | tail -5`

**Pass kriteri:** `/env` çıktısı step 1 == step 3 (tam geri dönüş). Audit
log 2 satır: patch + restore.

**Kanıt:**

```
[YYYY-MM-DD HH:MM:SS]

<... pre-state screenshot / post-state screenshot + audit log yapıştır ...>

Verdict: PASS / FAIL
```

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
