# PolyPaper Bot — Konsolide Yol Haritası
**3 AI Analizi (Grok + DeepSeek + Opus 4.7) Sentezi**
*Hazırlanma: 2026-04-28 · Versiyon: 1.0*

---

## 0. Yönetici Özeti (TL;DR)

Üç bağımsız AI inceleme sistemi (Grok, DeepSeek, Opus 4.7) repoyu farklı açılardan analiz etti. **Üçü de aynı 7 kritik sorunda hemfikir.** Her biri buna ek olarak farklı 2-3 unique bulgu ekledi.

**Tek cümle teşhis (konsensus):**
> PolyPaper Bot, mühendislik disiplini ve dokümantasyon açısından gerçekten etkileyici, ama **bilimsel doğrulamadan yoksun ve aşırı mühendislik nedeniyle "edge var mı?" sorusuna cevap veremeyen** bir trading sistemidir. "Phase 82e + Epic 11" milestone enflasyonu, %21.2 test coverage, Windows-only deployment ve doğrulanmamış fee modeli, mainnet'e geçmeyi henüz mümkün kılmıyor.

**Genel skor (DeepSeek karne): 6/10** — sağlam temel, ciddi eksikler.

---

## 1. Üç Analizin Karşılaştırması

### 1.1 KONSENSUS — 3 AI'ın da işaretlediği kritik bulgular

| # | Bulgu | Grok | DeepSeek | Opus 4.7 | Aciliyet |
|---|---|:---:|:---:|:---:|:---:|
| C1 | Test coverage %21 — kabul edilemez | ✓ | ✓ | ✓ | 🔴 Kritik |
| C2 | Windows-only / Docker yok | ✓ | ✓ | (üstü kapalı) | 🔴 Kritik |
| C3 | Aşırı strateji + intelligence katmanı (overfitting) | ✓ | ✓ | ✓ | 🔴 Kritik |
| C4 | PnL istatistiksel olarak şüpheli | ✓ | ✓ | ✓ | 🔴 Kritik |
| C5 | Fee modeli doğrulanmamış | (kısmen) | ✓ | ✓ | 🟠 Yüksek |
| C6 | Phase/Epic chaos — proje yönetim disiplini | ✓ | ✓ | ✓ | 🟠 Yüksek |
| C7 | Dokümantasyon dili karması (TR/EN) | ✓ | ✓ | (övgü) | 🟡 Orta |

### 1.2 UNIQUE BULGULAR — sadece bir AI'ın yakaladığı

**Grok (öncelikli vurgular):**
- 🤖 **AI cost & nondeterminism riski**: Claude Sonnet saatlik cycle + nightly Optuna API maliyeti birikir. AI kararları reproducible olmalı; "black box" riski.
- 🌊 **Polymarket-spesifik riskler**: Liquidity, resolution dispute (UMA), whale manipulation yeterince ele alınmamış.
- 📊 **Web dashboard yokluğu**: Telegram-only UI uzun vadede yetersiz.

**DeepSeek (teknik detaycı):**
- 🔧 **Linting/Type-checking yok**: mypy, ruff, pylint hiç yok (skor 1/10). Modern Python projesi için olmazsa olmaz.
- 💀 **Backup'lar aylarca corrupt'ti**: 2026-04-20 ve 2026-04-23 backup'ları null header — atomic write fix yeni.
- 🛡️ **Classic plugin paradoksu**: Empirik olarak para kaybediyor (-$4.87 sweep) ama `PROTECTED_STRATEGY_TYPES={"classic"}` ile korunuyor. Mantıksız duygusal bağlılık.
- 🗑️ **30+ scratch `.bat`**: `scripts/_commit_*.bat` repo kirliliği.
- 🤔 **AI Brain öğrenme doğrulaması yok**: AI önerilerinin canlı sonuçlarla korelasyonu ölçülmüyor.

**Opus 4.7 (en acımasız teşhis):**
- 🏗️ **God-class anti-pattern**: `engine.py` 1236 + `engine_signals.py` 1889 + `ai_brain.py` 2199 + `strategy_plugins.py` 55KB. 4 mixin "splitting a god class" şeklinde, doğru SRP refactor değil.
- 💉 **Constructor god-object**: `TradingEngine.__init__` 240 satır, 20+ alt sistemi `try/except logger.debug` ile başlatıyor — sessiz başarısızlık.
- 📈 **"Marking your own homework"**: Bot kârlı görünüyor çünkü kendi simülatörü kendine puan veriyor. SE(WR)=1.3% → fee modelin yanlışsa edge sıfır.
- 🩺 **Hekimlik analojisi**: 80+ phase = polipragmatik tedavi; 58 env sekmesi = polifarmasi; AI Brain+Hyperopt+Auto-Optimizer paralel = kombine kemoterapi disiplinsiz uygulanmış. **Her birini RCT ile ayrı doğrulamadın → ablation impossible.**
- ⚖️ **License paradoksu**: Public repo + Proprietary lisans = "worst of both worlds".
- 🔑 **Private key güvenliği**: `.env`'de düz metin Polygon private key. Ledger/encrypted keystore yok → 13 regex işe yaramaz.
- 💀 **Polymarket retail edge KARAMSARLIĞI**: Arbitrageur kapatması, weak liquidity, Binance close dependency → "muhtemelen break-even bile zor".

### 1.3 NELERİ İYİ BULDULAR (üçü de övgü hak gördü)

- ✅ Modüler mimari (domain-driven, SRP'ye yakın)
- ✅ Strateji lifecycle (exploration → evaluation → proven)
- ✅ Secret yönetimi (13 regex × pre-commit + pip-audit)
- ✅ Telegram UI zenginliği
- ✅ Shadow-live konsepti (akıllı paradigma)
- ✅ Detaylı dokümantasyon (ARCHITECTURE/PHASES/TROUBLESHOOTING)

---

## 2. Mevcut Durum Snapshot

```
Python LOC:      82,190 (273 dosya)
core/ LOC:       17,864 (40 dosya, god classes)
telegram_bot/:   18,887 LOC
tests/:          12,655 LOC (test/kod = %15)
Test pass:       735 / 8 skip / 0 fail
Coverage:        21.2% — KABUL EDİLEMEZ
Markdown docs:   6,081 satır (35 dosya) — aşırı
TASKS.md:        974 satır — bir tez
.env.example:    333 satır, 58 sekme — overfitting machine
Phase sayısı:    80+ (1 ayda)
Epic sayısı:     11 (paralel)
PnL:             +$355 / 1417 trade / 57% WR (paper, doğrulanmamış)
Shadow live:     $1.49 USDC / 3 strateji
Star/Fork:       0/0
```

---

## 3. KONSOLİDE YOL HARİTASI

### 🟥 FAZ 0 — Reality Check (3-5 gün) [MAINNET ÖNCESİ ZORUNLU]

**Amaç:** Mainnet'e geçmeden önce paper PnL'in gerçek mi yoksa simülatör artefaktı mı olduğunu bilimsel olarak doğrulamak.

| Görev | Açıklama | Çıktı | Kabul Kriteri |
|---|---|---|---|
| **0.1** | **Fee model gerçek doğrulama** — Polymarket REST `/fee-rate` endpoint'i çağır. BTC Up/Down crypto markets için gerçek taker/maker fee'yi al. `fees_v2.py` içindeki `crypto.taker_rate=0.072` ile karşılaştır. | `docs/audits/fee_reality_check_2026_04.md` | Eğer fee gerçekte 0 (veya farklıysa) → `fees_v2.py` re-kalibre edilir, paper PnL re-hesaplanır. |
| **0.2** | **Live-vs-Paper drift ölçümü** — Son 14 gün shadow live trade'lerini (3 strateji, $1/trade) paper benzerleriyle eşleştir. Fill price, slippage, PnL deltası hesapla. | `backtest/calibration/live_paper_drift_2026_04_28.json` | drift_pnl_pct < %20 ise simülatör güvenilir, > %20 ise FAIL → kalibre et veya mainnet ertele. |
| **0.3** | **Statistical significance recompute** — Fee/slippage corrected PnL ile WR confidence interval'ı yeniden hesapla. SE(WR) = √(p×(1-p)/n) formülü, %95 CI bandı. | `docs/audits/edge_significance_2026_04.md` | Lower bound > 0.51 (yani fee/slippage sonrası bile edge anlamlı) ise 1. faza geç, değilse FAZ 4 (donduralım?) noktasına git. |

**Çıkış kriteri:** 3 doküman var ve verdict = "edge anlamlı" VEYA "edge yok, freeze". Her iki durumda da net karar.

---

### 🟧 FAZ 1 — Repo Hijyeni & Dondurma (1-2 hafta)

**Amaç:** Phase enflasyonunu durdurmak, temizlik yapmak, sürdürülebilir baseline oluşturmak.

| Görev | Açıklama | Çıktı |
|---|---|---|
| **1.1** | **Phase numerasyonunu DONDUR.** v1.0.0 = Frozen baseline. Bundan sonra **semver** (v1.1.0, v1.2.0). "Phase", "Epic", "Sprint HOTFIX vN" jargonunu yasakla. | `CHANGELOG.md` semver formatına geç, `docs/PHASES.md` → `docs/HISTORY.md` (read-only arşiv) |
| **1.2** | **`_archive/` klasörünü repodan sil**, `.gitignore`'a ekle. Git history zaten arşiv. | -97 dosya, ~30 MB temizlik |
| **1.3** | **`TASKS.md` 974 → 200 satır.** Sadece **açık** ve **mevcut sprint** task'ları. Geçmiş zafer notları → `CHANGELOG.md` | TASKS.md radikal kısaltma |
| **1.4** | **`scripts/_commit_*.bat` × 30+ dosyayı sil.** Bunlar geçici dev araçları, `git rebase -i` kullan. | -30 bat dosyası |
| **1.5** | **License netleştir.** Üç seçenek: (A) Repo private, (B) Proprietary kalsın ama lisans dosyası açıkça "no fork/no contribute" yazsın, (C) MIT/Apache-2.0'a aç. | LICENSE dosyası tutarlı |
| **1.6** | **`.env.example` audit.** 58 sekme → kullanılmayan/legacy parametreleri sil. Bir parametre **3 yerden fazla okunmuyorsa** → hardcode et. | `.env.example` 333 → ~150 satır |
| **1.7** | **Dead code purge.** `core/keepalive.py` (Phase 65'te "removed" ama duruyor), `core/engine.py` ghost yorumları, `_archive/observability_shadow_fix_*` — git rm. | -5/10 dosya |

**Çıkış kriteri:** `git status` temiz, repo boyutu < 50 MB, `find . -name '_archive*'` boş.

---

### 🟨 FAZ 2 — Mühendislik Disiplini (2-4 hafta)

**Amaç:** Modern Python projesi standartlarına çıkmak. CI/CD, linting, type-safety, container.

| Görev | Açıklama | Hedef |
|---|---|---|
| **2.1** | **`ruff` entegrasyonu.** `pyproject.toml`'a ruff config ekle, mevcut tüm `bare except` ve diğer ihlalleri narrow et veya `noqa` ile gerekçelendir. | `ruff check .` → 0 violation |
| **2.2** | **`mypy --strict`** entegrasyonu. Önce `core/fees_v2.py`, `core/risk_manager.py`, `core/kelly.py` (kritik path) için %100. Sonra kademeli yayılım. | Critical path %100, kalan %50+ |
| **2.3** | **GitHub Actions CI pipeline.** Her PR'da: `ruff check` + `mypy` + `pytest --cov` + `pip-audit` + secret scan. Coverage threshold gate: %50 (başlangıç). | `.github/workflows/ci.yml` aktif |
| **2.4** | **Test coverage %21 → %60+** kademeli. Öncelik: `engine.py`, `engine_signals.py`, `risk_manager.py`, `fees_v2.py`, `kelly.py`. Property-based testing (Hypothesis) ekle. | Coverage gate %60 PASS |
| **2.5** | **Docker container**. `Dockerfile` (Python 3.11-slim base) + `docker-compose.yml` (bot + sqlite volume + sentry sidecar). Linux'ta tek komutla çalışsın. | `docker compose up` → bot ayağa kalkar |
| **2.6** | **Constructor refactor.** `TradingEngine.__init__` 240 satır → 30 satır. Optional dep'ler factory pattern: `TradingEngine.minimal()`, `TradingEngine.with_full_brain()`. Sessiz `logger.debug` patları → açık `raise` veya `feature_disabled_reason` field'ı. | Constructor < 50 satır, init failure'lar görünür |
| **2.7** | **`engine.py`/`engine_signals.py`/`ai_brain.py` SRP refactor**. 4 mixin yerine gerçek SOLID classes. (Risk: aşamalı, regression test koruması altında.) | God class boyutu < 800 LOC her biri |

**Çıkış kriteri:** `make ci` lokalde geçer (lint+type+test+audit), Docker image hazır, CI badge yeşil.

---

### 🟩 FAZ 3 — Bilimsel Doğrulama (1-3 ay)

**Amaç:** "Edge gerçekten var mı? Hangi katman katkı sağlıyor?" sorusuna istatistik cevapla.

| Görev | Açıklama | Metrik |
|---|---|---|
| **3.1** | **Ablation Study Matrix.** Aşağıdaki katmanların her birini sırayla 7 gün kapat, paper PnL impact ölç:<br>(a) AI Brain<br>(b) Hyperopt<br>(c) Becker calibrator<br>(d) 2D Surface (C(K,τ))<br>(e) Auto-Optimizer<br>(f) Strategy Suggester<br>(g) Confluence Gate<br>(h) Signal Fusion<br>(i) Strategy Selector (Thompson) | Her katman için ΔPnL%. **\|ΔPnL%\| < %1** olanlar → SİL. |
| **3.2** | **Out-of-Sample (OOS) test.** Live shadow'u 30 gün **hiçbir parametre değiştirmeden** çalıştır. Paper'la paralel sonuç → simülatör doğru. Divergence > %20 → simülatör YANLIŞ. | OOS divergence raporu |
| **3.3** | **Single hypothesis test.** En yüksek WR'li 1 strateji al (örn. opening_breakout). Tek piyasa, dondurulmuş parametre, 90 gün live shadow. **"Bu strateji edge'e sahip mi?"** sorusuna p-value ile cevap. | p < 0.05 ise GÖ, değilse strateji çöp |
| **3.4** | **Strateji konsolidasyonu.** 18+ strateji → en iyi 3-5 strateji. Geri kalanlar `_legacy/` (silinmeden, ama runtime'da inactive). | Aktif strateji ≤ 5 |
| **3.5** | **Classic plugin kararı.** DeepSeek bulgusu: classic empirik olarak para kaybediyor (HEURISTIC -$4.87) ama PROTECTED. Karar: ya algoritmik edge ekle, ya kaldır. | classic ya çalışıyor ya yok |
| **3.6** | **AI Brain learning verification.** AI'nın önerdiği parametrelerin 30-60-90 gün sonraki PnL korelasyonu. Eğer korelasyon < 0.2 → AI Brain "öğrenmiyor", maliyeti ödüyor edge yaratmıyor. | AI Brain ROI raporu |

**Çıkış kriteri:** Repo'nun **gerçekten edge ürettiği bilimsel olarak kanıtlanmış** ya da edge yok diye karar verilmiş.

---

### 🟦 FAZ 4 — Karar Noktası (Faz 0+3 sonuçlarına bağlı)

Faz 0 (fee/drift) ve Faz 3 (ablation/OOS/single-hypothesis) sonuçları belirleyecek. **3 dürüst seçenek (Opus 4.7'den):**

#### **Seçenek A — Donduralım (TUS önceliği)**
- v1.0 freeze, sadece kritik bug-fix.
- Kod büyümesin.
- Live shadow $1.49 → $5'a çıkar, 6 ay observe.
- Heddas TUS'a odaklanır.
- **Yapılacak:** GitHub release v1.0.0 + freeze badge + minimal monitoring (Sentry + Telegram alert).

#### **Seçenek B — 10x küçült (Bilim botu)**
- 80,000 LOC → 8,000 LOC hedefi.
- **Tek strateji + tek piyasa + tek probability model + tek risk gate.**
- Phase yok, Epic yok, sadece edge testi.
- Fork yapılır → `polypaper-minimal/` branch.
- **Yapılacak:** Greenfield rewrite branch'i, sadece kanıtlanmış 1 stratejiyle.

#### **Seçenek C — Mainnet'e geç (Faz 3'te edge kanıtlandıysa)**
- Faz 0 + Faz 3 verdict = edge real.
- $1.49 → $50 → $500 → $5K kademeli ölçek.
- Independent third-party audit (security + strategy).
- Hedge fund grade monitoring (Prometheus + Grafana + PagerDuty).
- **Yapılacak:** T11.4-T11.8 epic'leri (defense-in-depth backlog).

#### **Seçenek D — Status quo (Önerilmez!)**
- "AI yazsın, ben fikir vereyim" devam.
- 6 ay sonra 200,000 LOC, 5,000 parametre, hala kâr yok.
- **Risk:** Repo öğrenilemez hale gelir, Heddas vazgeçer.
- **Üç AI'ın da uyarısı:** Bu yola sapma.

---

## 4. Risk Matrisi (Hangi yol ne riski taşıyor?)

| Yol | Para Riski | Zaman Riski | Bilimsel Risk | TUS Etkisi |
|---|---|---|---|---|
| Faz 0 (3-5 gün) | $0 | Düşük | Yok (sadece doğrulama) | İhmal edilebilir |
| Faz 1 (1-2 hf) | $0 | Düşük | Yok | Hafif |
| Faz 2 (2-4 hf) | $0 | Orta | Yok | Orta |
| Faz 3 (1-3 ay) | <$50 (shadow) | Yüksek | Çok yüksek (verdict belirler) | Yüksek |
| Faz 4-A (freeze) | $0 | Yok | Yok | **Pozitif** (zaman serbest) |
| Faz 4-B (rewrite) | $0 | Çok yüksek | Düşük | Çok yüksek |
| Faz 4-C (mainnet) | $50-5K+ | Yüksek | Düşük (ablation sonrası) | Yüksek |
| Status quo | Belirsiz | Sonsuz | Çok yüksek | Çok olumsuz |

---

## 5. Cowork için Eylem Planı (sırayla yürütülecek)

### Hemen başlanacak (bu hafta)
1. **Faz 0.1 — Fee reality check.** Polymarket `/fee-rate` endpoint'i çağır, `fees_v2.py` ile karşılaştır.
2. **Faz 0.2 — Live/paper drift script'i** yaz, son 14 gün shadow live datayı al.
3. **Faz 0.3 — SE(WR) recompute script'i**.

### Ertesi hafta
4. **Faz 1.1-1.7 — Cleanup.** _archive sil, TASKS.md kısalt, scripts temizle, License net.

### 2-4. hafta
5. **Faz 2 — Mühendislik disiplini.** ruff/mypy/CI/Docker.

### Faz 0+3 sonuçlarına göre dallanma
6. **Faz 4** — Heddas karar verecek (A/B/C/D).

---

## 6. Tek Cümle Tavsiye (Konsensus Sentezi)

> **"Önce ölç (Faz 0), sonra temizle (Faz 1), sonra disiplin et (Faz 2), sonra bilim yap (Faz 3), sonra karar ver (Faz 4). Üzerine yeni özellik EKLEME."**

İlk yapacak iş: **Faz 0.1 — Polymarket'in gerçek fee yapısını öğren.** Bu tek görev, repo'nun yarısının ne kadar gerçek olduğunu söyleyecek.

---

## 7. Ek: 3 AI'ın Skor Karnesi

| Kategori | Grok | DeepSeek | Opus 4.7 | Konsensus |
|---|:---:|:---:|:---:|:---:|
| Mimari Tasarım | 8/10 | 8/10 | 6/10 (god classes) | **7/10** |
| Kod Kalitesi | 7/10 | 5/10 | 5/10 | **5.5/10** |
| Test Altyapısı | 5/10 | 4/10 | 3/10 | **4/10** |
| Güvenlik | 8/10 | 8/10 | 6/10 (security theatre) | **7/10** |
| Strateji Mantığı | 7/10 | 7/10 | 4/10 (overfitting) | **6/10** |
| DevOps | 4/10 | 4/10 | 3/10 | **3.5/10** |
| Dokümantasyon | 8/10 | 7/10 | 9/10 | **8/10** |
| **Genel** | **7.5/10** | **6/10** | **5/10** | **6/10** |

---

*Bu yol haritası 3 AI'ın bağımsız analizinin sentezidir. Her görev için detaylı sub-task açılması Cowork session'ında yapılacaktır.*
