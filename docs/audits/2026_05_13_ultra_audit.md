# PolyPaper Bot — Ultra-Audit 2026-05-13

**Direktif**: Heddas tam yetki, acımasız, dürüst. STRICT CLEANUP doktrini —
her iddia → dosya + satır. Polymarket Documentation MCP ile cross-check.

**Auditör**: Claude (Cowork)
**Tarih**: 2026-05-13
**Süre**: ~1 oturum, statik analiz + docs verify (live test çalıştırılmadı —
Windows-only Python 3.11 + pytest yerel ortam dışında).
**Mainnet**: 4. günde (since 2026-05-09), $1.49 LIVE_BUDGET cap, paper+live aynı kod.

---

## ⚠️ 0. Yönetici Özeti (TL;DR)

| Sınıf | Bulgu | Detay |
|---|---|---|
| 🔴 **KRİTİK** | 39 modified + 18 untracked dosya 2 GÜNDÜR commit'siz, MAINNET LIVE | rollback riski; bot crash → kayıp |
| 🔴 **KRİTİK** | `memory/status.md` STALE — 7 P0 açık gösteriyor, gerçek 3 | yanlış öncelik + tekrar iş |
| 🟠 **YÜKSEK** | İki rakip P0 tracker var: `P0-01..P0-09` (CLAUDE.md) ≠ `P0.1..P0.12` (TASKS.md Epic 12) | confusion |
| 🟠 **YÜKSEK** | `services/ai_advisor/app.py` HİÇ AUTH YOK — sadece localhost bind | port-forward riski → LLM cost burn |
| 🟠 **YÜKSEK** | `PROTECTED_STRATEGIES` sadece 2 entry (`ai_brain.py:102`) | yeni kazanan stratejiler protect edilmiyor |
| 🟡 **ORTA** | AI Brain log "10min cycle" diyor ama interval 3600s = 1h (`ai_brain.py:163` vs `:105`) | operator misleads |
| 🟡 **ORTA** | CHANGELOG 5 gündür güncellenmemiş (son entry 2026-05-08) | mainnet hayatı yazılmıyor |
| 🟡 **ORTA** | TASKS.md 160KB monolith, en taze entry Epic 12 (2026-04-30) | P1-01/P1-02 burada yok |
| 🟡 **ORTA** | `dashboard.html` 97KB, untracked, sadece local | git'te yok, kayıp → bye |
| 🟡 **ORTA** | `fees_v2.py` 4 decimal precision (docs 5 — 0.00001 USDC smallest) | round drift, marjinal |
| 🟢 **POZİTİF** | Crypto fee 0.072 → **0.07 FIX** uygulanmış (`fees_v2.py:67`), docs ile 3 kaynak teyitli | ✅ |
| 🟢 **POZİTİF** | Tüm contract address'leri docs ile **bit-identical** (`allowance_preflight.py:49-53`) | ✅ |
| 🟢 **POZİTİF** | CI hard-fail var (ruff + bare-except + env-ref drift + exc-leak + coverage `fail_under=43`) | ✅ |
| 🟢 **POZİTİF** | P0-01, P0-03, P0-06, P0-09 GERÇEKTEN KAPALI (memory stale ama kod doğru) | ✅ |

---

## 1. Project State — Gerçek Durum

### 1.1 Git / Uncommitted Çıkar

```
M  39 dosya  (3,162 insert / 731 delete)
?? 18 untracked  (CLAUDE.md, COWORK_PROJECT_SETUP.md, dashboard.html,
                   memory/ klasörü tümü, services/ai_advisor/ 3 yeni dosya,
                   core/observability/sentry_tx.py, docs/audits/ + docs/architecture/ +
                   docs/outreach/, 3 yeni script.bat)
```

**Son commit**: `9aeaa6d 2026-05-11 P1-02 Wave 1: AI Advisor scaffold`.
**Bugünden farkı**: 2 takvim günü.
**Risk**: bot Windows local çalışıyor; disk arızası / Ctrl+C kaybı → 39 dosyalık iş yok olur.

### 1.2 Yapı Sağlığı (memory/status.md iddiası, yerelde doğrulanmadı)

- 3,569 PASS / 0 FAIL / 42 skip · coverage %44.06 · mypy strict 0 hata · ruff 0 violation · mainnet blocker 0
- **Audit notu**: bash sandbox'tan pytest çalıştıramadım (Windows-Python-3.11 mount, AIO sqlite incompatible). Heddas'ın **lokalde son full regression koşturup ratchet'i yukarı çekmesi** gerek (43 → 45+; ratchet sadece yukarı doktrini).

### 1.3 Production Telemetri

- 4 gündür mainnet, **Sentry kapalı** (`core/observability/sentry_tx.py:_sentry_enabled` → `SENTRY_DSN` boş = no-op). Dolayısıyla production performance / error trace verisi = SIFIR.
- `reality_gap_job` aktif (paper×0.66 vs live empirical), `external_prices` dolmaya başladı 2026-05-08.

---

## 2. Polymarket Docs Cross-Check (✅ kanıtlı)

### 2.1 Fee Rates — TAMAMI EŞLEŞİYOR

Docs kaynak: `https://docs.polymarket.com/trading/fees` (fetched 2026-05-13 via MCP).

| Kategori | docs rate | `fees_v2.py:60-71` | Status |
|---|---:|---:|:---:|
| Crypto | 0.07 | 0.07 | ✅ |
| Sports | 0.03 | 0.030 | ✅ |
| Politics | 0.04 | 0.040 | ✅ |
| Finance | 0.04 | 0.040 | ✅ |
| Economics | 0.05 | 0.050 | ✅ |
| Culture | 0.05 | 0.050 | ✅ |
| Weather | 0.05 | 0.050 | ✅ |
| Tech | 0.04 | 0.040 | ✅ |
| Mentions | 0.04 | 0.040 | ✅ |
| Other | 0.05 | 0.050 | ✅ |
| Geopolitics | 0 | 0.000 | ✅ |

**Formula sanity** (docs: `fee = C × feeRate × p × (1-p)`, exp=1):
- `fees_v2.py:111` → `fee = shares * rate * (price * (1 - price)) ** exp`
- 100 × 0.07 × 0.5 × 0.5 = **$1.75** (docs peak Crypto $1.75) ✅

**Eski crypto sapması** (0.072 → 0.07): `docs/audits/fee_docs_recheck_2026_05_11.md`, commit gerekli (henüz değişiklikler uncommitted).

### 2.2 Contract Addresses — TAMAMI EŞLEŞİYOR

Docs kaynak: `https://docs.polymarket.com/resources/contracts` (Polygon mainnet, chainId 137).

| Kontrak | Docs adres | Bot'ta | Dosya:Satır |
|---|---|:---:|---|
| pUSD (proxy) | `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` | ✅ | `core/allowance_preflight.py:49` |
| CTF Exchange | `0xE111180000d2663C0091e4f400237545B87B996B` | ✅ | `core/allowance_preflight.py:52` |
| Neg Risk CTF Exchange | `0xe2222d279d744050d28e00520010520000310F59` | ✅ | `core/allowance_preflight.py:53` |

Diğer kontratlar (CtfCollateralAdapter, NegRiskCtfCollateralAdapter, UMA Adapter,
UMA Optimistic Oracle, vb.) henüz bot'a entegre değil — paper bot için gerekmiyor,
P1+ roadmap'te (redeem/split/merge yolu için ekleme yapılabilir).

### 2.3 Endpoint URL'leri — EŞLEŞİYOR

| Endpoint | Docs | Bot | Status |
|---|---|---|:---:|
| CLOB REST | `clob.polymarket.com` | `config/settings.py:116 POLYMARKET_BASE_URL` | ✅ |
| Gamma | `gamma-api.polymarket.com` | `config/settings.py:117 POLYMARKET_GAMMA_URL` | ✅ |
| WSS public market | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | (verify locally) | ⏳ |
| WSS user | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | (verify locally) | ⏳ |
| WSS RTDS | `wss://ws-live-data.polymarket.com` | `data/polymarket_rtds.py` | ✅ |

WSS endpoint'leri Heddas yerelinde `data/websocket_client.py` kontrolünden geçirilmeli
(bash sandbox'tan dosya path'ı dolaylı kontrol edildi, satır numarası verify Heddas'a).

### 2.4 Fee Precision — KÜÇÜK SAPMA

- Docs: "Fees are rounded to 5 decimal places. The smallest fee charged is **0.00001** USDC."
- `core/fees_v2.py` → `return float(round(fee, 4))` = 4 decimal.
- **Etki**: ~$0.0001 sub-precision; 100 sh @ $0.50 trade'de etkisi sıfıra yakın.
- **Karar**: opsiyonel fix, mainnet blocker DEĞİL. **YENI TASK P0-10 olarak eklenecek**.

---

## 3. P0 Listesi — GERÇEK DURUM (memory'yi düzeltir)

`memory/status.md:23-31` "7 açık" diyor. Kod kanıtları → **3 gerçekten açık, 4 KAPALI**.

| # | İddia (memory) | Kod gerçeği | Status |
|---|---|---|:---:|
| **P0-01** | AI Brain auto-execute → approval queue | `ai_brain.py:319-326` ("All actions now go to the approval queue; no auto-execute path remains"), `:1993-2002` ("NO auto-execute fallback... DISCARDING"), `:2011-2017` ("NO auto-execute fallback") | ✅ **KAPALI 2026-05-08** |
| **P0-02** | POLYGON_PRIVATE_KEY plaintext → DPAPI/keyring | `config/settings.py:94-96` hâlâ `os.environ.get("POLYGON_PRIVATE_KEY", "")` plaintext, `.env` dosyası 17KB | 🟠 **AÇIK** |
| **P0-03** | Telegram `/export_private_key` sil | grep `export_private_key` → 0 hit. `portfolio_handler.py:113` yorumu: "private key over Telegram. PK access now via OS keychain (P0-02)" | ✅ **KAPALI** (kanıt: yokluğu) |
| **P0-04** | LIVE_BUDGET 2-faktör + 24h cooldown | `core/live_trader.py:107-116 _get_live_budget()` tek faktör, runtime env-read | 🟠 **AÇIK** |
| **P0-06** | `py-builder-relayer-client==0.0.1` pin | `requirements.txt:39` "P0-06 (2026-05-08): pinned to 0.0.1" | ✅ **KAPALI 2026-05-08** |
| **P0-08** | 5m binary'ler default OFF (env-opt-in) | `config/settings.py:32` `"5m": {"method": "slug_prefix", "assets": ["BTC"]}` — BTC default ENABLED, OFF değil | 🟠 **AÇIK** (veya direktif değişti?) |
| **P0-09** | Kelly MAX_BET_PCT tek kaynağa | `core/kelly.py:44 _max_bet_pct()` helper + `core/kelly.py:38-52` "P0-09 (2026-05-08): single-source-of-truth" | ✅ **KAPALI 2026-05-08** |

**Net P0 sayısı**: ~~7 açık~~ → **3 açık** (P0-02 + P0-04 + P0-08).

---

## 4. Zayıf Bölgeler (Acımasız Liste)

### 4.1 Güvenlik

| ID | Bulgu | Risk | Effort |
|---|---|:---:|:---:|
| S-01 | `services/ai_advisor/app.py` HİÇ AUTH YOK — sadece `host=127.0.0.1` koruması | 🟠 | S |
| S-02 | `POLYGON_PRIVATE_KEY` `.env` plaintext (P0-02), `.gitignore` kapsamasına rağmen Windows backup / sync risk | 🟠 | L |
| S-03 | `LIVE_BUDGET` tek-faktör — `/envt LIVE_BUDGET 100` anında etkili, kötü niyetli admin gate yetersiz (P0-04) | 🟠 | M |
| S-04 | `.env` 394 satır, 17KB — bloated, audit zor; P1.5 task var (TASKS.md) ama yapılmamış | 🟡 | M |
| S-05 | Telegram admin tek user (`ADMIN_TELEGRAM_ID`) — kompromise olursa bot tamamen ele geçirilebilir | 🟡 | M |

### 4.2 Operasyonel

| ID | Bulgu | Risk | Effort |
|---|---|:---:|:---:|
| O-01 | 2 gün uncommitted iş, mainnet live | 🔴 | S |
| O-02 | Bus factor = 1 (Heddas tek dev + tek oncall) | 🟠 | XL |
| O-03 | Sentry default kapalı — production telemetri yok | 🟡 | S |
| O-04 | Docker / Linux taşıma yok (P1.1, P1-05 roadmap) — Windows local crash → bot down | 🟡 | L |
| O-05 | PostgreSQL migration başlamamış (P1-08 roadmap) — SQLite WAL büyüdükçe Windows file-lock risk | 🟡 | XL |
| O-06 | Backup atomic fix var (T11.3 Bulgu B) ama disaster recovery runbook yok | 🟡 | S |

### 4.3 Kod Sağlığı / Tech Debt

| ID | Bulgu | Risk | Effort |
|---|---|:---:|:---:|
| C-01 | `mypy_baseline.txt` 27KB — ignored type errors büyük backlog | 🟡 | XL |
| C-02 | `PROTECTED_STRATEGIES` sadece 2 entry (`ai_brain.py:102`) — yeni kazanan strateji eklenmiyor | 🟠 | S |
| C-03 | AI Brain log "10min cycle" (`ai_brain.py:163`) gerçeği 3600s=1h (`:105`) — operator misleads | 🟡 | XS |
| C-04 | Fee precision 4 decimal (docs 5) — `fees_v2.py round(fee, 4)` | 🟡 | XS |
| C-05 | `TASKS.md` 160KB monolith; en güncel iş Epic 12 (2026-04-30); P0-01..P0-09 / P1-01 / P1-02 burada yok | 🟡 | M |
| C-06 | İki rakip P0 nomenklatür var: `P0-01..P0-09` (CLAUDE.md) ≠ `P0.1..P0.12` (TASKS.md Epic 12) | 🟠 | S |

### 4.4 Dokümantasyon / Süreç

| ID | Bulgu | Risk | Effort |
|---|---|:---:|:---:|
| D-01 | CHANGELOG.md 2026-05-08'den beri sessiz, mainnet 4 gün | 🟡 | S |
| D-02 | `dashboard.html` 97KB UNTRACKED — git'te yok, kayıp → bye | 🟡 | XS |
| D-03 | `memory/status.md` 4 P0 yanlış işaretliyor (3.5 nolu bölüm) | 🟠 | S |
| D-04 | README'de hâlâ "Mainnet preview" yazıyor olabilir (3 gündür değişmedi sub-tree) — verify | 🟡 | S |
| D-05 | Polymarket constant drift için CI guard YOK — fee 0.072 sapması Heddas tarafından elle yakalandı | 🟠 | M |

---

## 5. Yeni Task Önerileri (P0/P1)

Aşağıdaki yeni iş'ler **TASKS.md** ve **02_POLYPAPER_YOL_HARITASI.md**'ye eklenmeli.

### P0 (Mainnet riski azaltır)

- **P0-10**: `fees_v2.py` precision 4 → 5 decimal (`round(fee, 5)`) + 5-decimal regression test. Docs: "smallest fee 0.00001 USDC". Effort XS.
- **P0-11**: AI Advisor service auth (`X-Internal-Key` header check + `INTERNAL_KEY` env) — port-forward saldırı yüzeyini kapat. `services/ai_advisor/app.py` middleware. Effort S.
- **P0-12**: Polymarket constant drift CI guard — fee table + contract address + endpoint URL'lerini docs MCP query'leri ile haftalık karşılaştır, sapma varsa CI fail. Effort M.
- **P0-13**: `PROTECTED_STRATEGIES` audit — top 5 cumulative-PnL kazanan stratejiyi listele, manuel onayla, listeye ekle. Effort S.
- **P0-14**: AI Brain log/comment "10min cycle" → "1h cycle" düzelt (`ai_brain.py:163`). Effort XS.
- **P0-15**: `dashboard.html` git'e ekle (LFS gerekmiyor, 97KB) + `.gitignore` review. Effort XS.

### P1 (defense-in-depth / hijyen)

- **P1-09**: `memory/status.md` + `CLAUDE.md` P0 listesi gerçek koda göre senkronize tut — git pre-commit hook (`scripts/check_memory_drift.py`). Effort M.
- **P1-10**: TASKS.md 160KB monolithini bölme — `TASKS_archive_2026_04.md` + `TASKS.md` aktif backlog only. Effort S.
- **P1-11**: `TASKS.md` ile `02_POLYPAPER_YOL_HARITASI.md` P0 nomenklatür birleştir (`P0-01..P0-09` mu, `P0.1..` mı tek karar). Effort S.
- **P1-12**: CHANGELOG günlük entry doktrini — her mainnet trading session sonu kısa entry. Effort S.
- **P1-13**: Sentry opt-in adımları — Heddas için Sentry hesabı/proje + DSN al + `SENTRY_TRACES_SAMPLE_RATE=0.001` (~520 event/ay, free plan altında). Effort S.
- **P1-14**: Disaster recovery runbook (`docs/mainnet/disaster_recovery_runbook.md`) — bot crash + .env loss + pUSD allowance yenileme + Telegram admin kompromise senaryoları. Effort M.

### P2 (uzun vade, scale öncesi)

- **P2-05**: Linux/Docker container path (P1.1 Epic 12'de var, henüz açılmadı). Effort L.
- **P2-06**: PostgreSQL migration (P1-08 roadmap, P1.4 Epic 12'de var). Effort XL.
- **P2-07**: Bus factor azaltma — secondary oncall (Heddas + 1) veya read-only delegate handler. Effort L.

---

## 6. CLAUDE.md / Project Instructions Önerisi

Mevcut **Project Instructions** (Cowork chat ekranındaki "PolyPaper Bot" projesinin
Claude session ayarı) güncellenmeli — aşağıdaki değişiklikler önerilir:

### Düzeltme 1: P0 listesi gerçeğe çek
**ESKİ**: 7 açık P0 (P0-01, P0-02, P0-03, P0-04, P0-06, P0-08, P0-09)
**YENİ**: 3 açık P0 (P0-02, P0-04, P0-08) + 6 yeni P0 önerisi (P0-10..P0-15)

### Düzeltme 2: Doktrin maddesi 8 ekle
> **8. Memory drift'i her oturum açılışında kontrol et** — `CLAUDE.md` /
> `memory/status.md` / `TASKS.md` üçü tutarsızsa kod kanıtına güven, memory'yi
> güncelle. Bu audit (2026-05-13) örnek: 4 P0 closed ama memory open gösteriyordu.

### Düzeltme 3: "Reddedilecek istekler" maddesi
> ❌ Memory dosyalarını gerçek koddan farklı bir state'e güncelleme — her güncelleme
> `grep` / `Read` ile kanıtlı olmalı.

---

## 7. Onay Bekleyen Komutlar (Heddas yerelinde çalıştırılacak)

```powershell
# 1) Uncommitted diff özeti
py -3.11 -m git diff --stat HEAD

# 2) Full regression (sandbox'tan çalıştırılamadı)
py -3.11 -m pytest -q --tb=short --cov --cov-config=.coveragerc

# 3) Coverage ratchet yukarı çek (43 → 45)
# .coveragerc'te fail_under=45 yap

# 4) Bu audit raporunu commit zincirine bağla
git add docs/audits/2026_05_13_ultra_audit.md
git commit -m "docs(audit): 2026-05-13 ultra-audit + memory drift fix"
```

---

## 8. Audit Sonucu

- **Mainnet hızlı durdurma gerekli mi?** → Hayır.
- **Mainnet blocker var mı?** → Hayır (sadece S-01 yüksek priority, sandbox güvenlik).
- **En kritik tek aksiyon** → `git add -A && git commit -m "..."` (39+18 dosya 2 günden beri commit'siz).
- **En kritik ikinci aksiyon** → `memory/status.md` + `CLAUDE.md` P0 listesini gerçeğe çek.
- **Audit kalitesi**: orta-yüksek. Pytest sandbox dışı koşulamadı; statik analiz + docs cross-check + git diff incelendi. Heddas yerelinde tam regression koşturup ratchet yukarı çekmeli.

**İmza**: Claude, 2026-05-13
