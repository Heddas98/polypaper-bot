# PolyPaper Temizleme & Refactor Planı *(META-PLAN)*
**Tarih:** 2026-04-20
**Durum:** Bot v9.7.9 / Engine v34 — Shadow Live ON, Mainnet hazırlığı öncesi **freeze & cleanup**
**Kural:** Bu plan bitene kadar **hiçbir yeni feature eklenmez.** Sadece: anla → tartış → sil → düzelt → test et → commit.

> **⚠️ T0.4 2026-04-20 NOTU:** Bu dosya **meta-plan**'dır — metodoloji, açılış promptu, modül şablonu, süreç kuralları. **Aktif backlog (Epic listesi + somut task'lar) sadece [`TASKS.md`](TASKS.md) içindedir.** Faz sırası, Epic detayı veya task durumu için her zaman önce TASKS.md'ye bak; bu dosyada yer alan Epic örnekleri yalnızca tarihsel referanstır.

---

## 0. Neden Bu Yaklaşım?

Sen diyorsun ki: "80K satır büyüdü, tek seferde AI'ye attırırsam bozarım." Haklısın. Doğru yöntem:

1. **Freeze:** Feature geliştirme durur. Sadece temizlik.
2. **Anatomi çıkar:** Önce haritayı çıkarırız, kod yazmayız.
3. **Modül modül ilerleriz:** Her modül için *ayrı chat oturumu* (context patlamasın).
4. **Her adımda tartışırız:** Claude tek taraflı karar veremez. Önce sana seçenek sunar, onay bekler.
5. **Kalıcı TODO:** Her oturum açılırken `TASKS.md`'yi okur, bitirince günceller, yeni iş çıkarsa ekler.
6. **Test olmadan refactor yok:** Syntax check + static test + mümkünse smoke run.
7. **Küçük commit:** Her anlamlı değişiklik ayrı commit. Geri dönebilmek için.

Bu yaklaşım üç şey yapar: (a) context yorulmaz, (b) sen hâkim kalırsın, (c) bozulursa 1 commit geri.

---

## 1. Açılış Prompt'u — Yeni Chat'e Bunu Yapıştır

Aşağıdaki metni **olduğu gibi** yeni bir Claude Cowork chat'ine yapıştır. Bot'un GitHub private repo'sunun PC'deki klasörünü Cowork folder olarak seçtiğinden emin ol.

```
ROL:
Sen PolyPaper Bot projesinin Baş Geliştirici ve Kod Denetçisisin.
Quant trading, async Python, websocket pipeline, Telegram bot mimarisi ve
paper trading simülasyonu konusunda kıdemli bir mühendissin.

MOD: STRICT CLEANUP MODE
- Hiç kod tahmin etme. Yalnızca gerçekten var olan dosyaları analiz et.
- Referans edilen ama implementasyonu olmayan fonksiyonlar için:
  "Function referenced but implementation not found" yaz.
- UI'da olup engine'de kullanılmayan parametreler için: "Ghost Parameter Candidate" etiketi.
- Eksik context varsa: "Insufficient context to verify behavior" yaz.
- Üretme, spekülasyon yapma, dolgu cümle yazma.
- Her iddianı: dosya adı + fonksiyon adı + satır numarası ile destekle.

İLETİŞİM:
- Türkçe konuş, teknik terimler İngilizce kalabilir.
- Tek taraflı karar verme. Kod değiştirmeden önce ne yapacağını anlat, onayımı bekle.
- Her değişiklik syntax check + mümkünse static test ile doğrulanır.
- Küçük ve kontrollü değişiklikler. Büyük refactor'ları parçala.

PROJE DURUMU (2026-04-20):
- PolyPaper Bot v9.7.9 / Engine v34
- 54 dosya, ~12K+ Python satırı (komple hacim sen söyledin ~80K)
- Bakiye $10,386, +$355 PnL, 1417 trade, %57 WR
- Shadow live mode: ON ($1.49 USDC, $1/trade, 3 strateji)
- CLOB signature fix yapıldı (EOA type 0 + ApiCreds)
- LIVE_ENABLED=false (hâlâ paper + shadow)
- AI Brain Claude Sonnet 10dk cycle

HEDEF:
Mainnet'e geçmeden önce TÜM projeyi temizle:
- ghost parametreleri bul
- dead code sil
- duplicate handoff/audit dosyalarını arşivle
- race condition / concurrency riski tespit et
- simülasyon doğruluğunu denetle (fee, slippage, liquidity, latency)
- risk manager bypass yolu var mı?
- websocket reconnect sağlam mı?
- SQLite locking riski var mı?

KURAL #1 — TODO SİSTEMİ:
Her oturumun başında workspace'teki `TASKS.md` dosyasını OKU.
Yoksa şu yapıyla oluştur:

  # PolyPaper Cleanup Backlog
  ## Epic: <başlık>
  - [ ] Task: <görev> — risk: LOW/MED/HIGH — bağımlılık: <varsa>
    - [ ] Subtask: <mikro adım>

Her adımda TASKS.md'yi güncelle. Yeni iş çıkarsa oraya ekle. Bitenleri [x] işaretle.
Bir adım bitmeden yenisine geçme.

KURAL #2 — İŞ AKIŞI (her modül için):
  1. ANATOMI: Modülü oku, amacını özetle. Neyi import ediyor, kim import ediyor?
  2. BULGULAR: Dead code, ghost param, anti-pattern, duplicate logic, fragile try/except.
  3. RİSK: CRITICAL / HIGH / MEDIUM / LOW — neden?
  4. ÖNERİ: 2-3 seçenek sun. Hangisini öneriyorsun, neden?
  5. ONAY BEKLE. Ben "yap" demeden kod değiştirme.
  6. DEĞİŞİKLİK: En küçük diff. Her mantıksal değişiklik ayrı commit.
  7. DOĞRULAMA: syntax check + ilgili modülü import et + varsa test çalıştır.
  8. TASKS.md GÜNCELLE: bitirdiğini işaretle, çıkan yeni işi ekle.

KURAL #3 — YAPMAYACAKLARIN:
- Protected stratejileri (prefix "t") silme veya değiştirme.
- Hardcoded API key bırakma. Her yeni değişken os.getenv() kontrolünden geçsin.
- Büyük "all-in-one" refactor önerme — hep parçala.
- "Bunu da hallettim" deme. Her değişiklik önce onaydan geçer.
- Telegram mesajlarında Markdown kullanma. HTML parse_mode.

İLK GÖREV:
Bu ilk mesajımda kod yazmayacaksın. Sadece şunu yap:

  A) Proje kök klasörünü tara (read-only). Şu listeyi çıkar:
     - dosya adı | satır sayısı | son değişiklik | bir cümlelik görev
  B) Yüksek seviye mimari diyagramı (text/ASCII):
     Telegram → Command Parser → Strategy → Risk Manager → Execution → Simulator → DB
     Her katmanda hangi dosyalar var?
  C) Top-10 "en karmaşık ve en riskli" dosya listesi + neden.
  D) İlk önerilen refactor sırası (Epic listesi, öncelik sıralı).
  E) `TASKS.md` taslağını workspace'e yaz. Epic'ler ve ilk 5-10 Task dolu olsun.

Bunu bitirince DUR. Ben onaylayınca ilk Epic'e geçeriz.
Kod yazma. Analiz yap.
```

---

## 2. Modül Bazlı Çalışma Şablonu (Tekrar Kullanılabilir)

Her bir modülde derine indiğinde (örn. `engine/trader.py`, `telegram_bot/handlers/...`, `risk_manager.py`), yeni chat aç ve şunu yapıştır:

```
Mod: MODÜL ANALİZ VE TEMİZLİK
Modül: <YOL/DOSYA_ADI>

Önce TASKS.md'yi oku. Bu modül hangi Epic altında?

Adımlar:
1) Modülün ne yaptığını 3 cümleyle özetle.
2) Public interface (export edilen fonksiyon/sınıflar) listesi.
3) Bağımlılıklar:
   - Bu dosya kimleri import ediyor?
   - Bu dosyayı kim import ediyor? (grep)
4) Kod kalitesi raporu:
   - Dead code (hiçbir yerden çağrılmayan fonksiyon/değişken)
   - Ghost parameter (UI'da var, engine kullanmıyor)
   - Duplicate logic (aynı iş başka dosyada yapılıyor mu?)
   - Fragile error handling (bare except, silent pass)
   - Blocking call (asyncio içinde sync I/O)
   - Race condition potansiyeli (shared state, lock var mı?)
5) Anti-pattern listesi + satır numarası.
6) Refactor önerisi: en küçük güvenli diff ne?
7) Test durumu: bu modülün testi var mı? Yoksa ekleyelim mi?

Çıktı bittiğinde:
- Bulguları CRITICAL/HIGH/MED/LOW ile etiketle.
- TASKS.md'ye subtask ekle.
- ONAY BEKLE. Ben "yap" demeden hiçbir şey değiştirme.
```

---

## 3. TODO Sistemi — `TASKS.md` Kuralları

**Aktif backlog tek dosyadadır: [`TASKS.md`](TASKS.md)** (workspace kök klasöründe). Bu bölüm sadece **format standardını** tanımlar; somut Epic ve task listesi TASKS.md'de tutulur (T0.4 2026-04-20 ile buraya taşınan Epic 1-7 örneği kaldırıldı — artık tek kaynak TASKS.md).

**Format standardı:**

```
# PolyPaper Cleanup Backlog
_Güncelleme: YYYY-MM-DD_

## Legend
- 🔴 CRITICAL — mainnet engelleyici
- 🟠 HIGH — para riski / silent failure
- 🟡 MED — teknik borç
- 🟢 LOW — kozmetik / DX

## Epic N — <Başlık>  *(risk etiketi, opsiyonel)*

Hedef: <1-2 cümleyle hedef>

- [ ] **TN.1** <görev açıklaması> — risk: LOW/MED/HIGH, bağımlılık: <varsa>
  - <alt adım veya bağlam satırı>
- [x] **TN.2** <tamamlanmış görev> ✅ <tarih>
  - <ne yapıldı özeti>
- [~] **TN.3** <in-progress görev> — risk: LOW · IN PROGRESS <tarih>

## Done (arşiv)
```

**Kurallar:**
- Her oturum başlangıcında Claude `TASKS.md`'yi okur.
- Her adımdan sonra günceller: `[ ]` (yapılacak), `[~]` (devam eden), `[x]` (tamamlanan).
- Yeni iş çıkarsa ilgili Epic altına ekler; yeni Epic açmak için ONAY ister.
- Bitmiş Task'lar Epic içinde `[x]` kalır (silinmez, tarih yazılır). Toplu arşive almak istenirse `## Done (arşiv)` bölümüne taşınır.
- Epic/task numaraları kodda referanslanmaz — sadece dokümantasyon içi.

---

## 4. Kırmızı Çizgiler (Senin Koruman Gereken Kurallar)

1. **Asla "hepsini sen hallet" deme.** Her Epic'ten önce dur, önerileri oku, tartış, onayla.
2. **Her commit küçük olmalı.** Bir commit = bir mantıksal değişiklik. "Refactor 500 satır" commit'i red et.
3. **Smoke test olmadan merge yok.** Değişiklik sonrası `py -3.11 -m py_compile <file>` + mümkünse ilgili dosyayı import et.
4. **Paralel chat açma.** Aynı anda iki farklı modülde iki Claude çalışırsa birbirini ezer.
5. **Protected stratejiler (prefix "t") dokunulmaz.** Custom instruction'ında yazıyor.
6. **Shadow live çalışırken refactor yapma.** Önce `/live_disable`, temizle, sonra test, sonra geri aç.
7. **GitHub branch kullan.** `cleanup/epic-N-xxx` branch'i aç, main'e direkt commit yapma.

---

## 5. Faz Sırası — Önce Ne, Sonra Ne?

> **T0.4 2026-04-20 NOTU:** Aşağıdaki faz ağacı `TASKS.md` içindeki Epic 0-11 sırasıyla **birebir eşleşir**. Tek doğru kaynak TASKS.md; bu bölüm sadece yüksek seviye özet ve hikâye anlatımıdır.

| Faz | TASKS.md Epic | Süre tahmini | Kod değişir mi? |
|---|---|---|---|
| **Faz A — Baseline & Ground Truth** | Epic 0 | 1 oturum | Hayır (sadece docs/versiyon sync) |
| **Faz B — Ghost Modules & Dead Imports** | Epic 1 | 2-3 oturum | Evet (minimal, her modül ayrı commit) |
| **Faz C — Kök Klasör Arınması** | Epic 2 | 1-2 oturum | Hayır (dosya taşıma + arşiv) |
| **Faz D — Risk Manager & Bypass Denetimi** | Epic 3 | 1-2 oturum | Gerekirse (mainnet-blocker fix'leri) |
| **Faz E — Simulator Doğruluğu** | Epic 4 | 2-3 oturum | Evet (fee/slippage/latency kalibrasyonu) |
| **Faz F — Concurrency & State Hygiene** | Epic 5 | 2-3 oturum | Evet (race/lock fix'leri) |
| **Faz G — Ghost Parametreler (UI↔Engine)** | Epic 6 | 1-2 oturum | Evet (UI temizliği veya engine bağlama) |
| **Faz H — Dead Code & Duplicate Logic** | Epic 7 | 2-3 oturum | Evet (modül modül silme) |
| **Faz I — AI Brain & Auto-Optimizer** | Epic 8 | 1-2 oturum | Evet (bare except daraltma) |
| **Faz J — Test Kaplaması** | Epic 9 | 1-2 oturum | Evet (yeni testler) |
| **Faz K — Security Pass** | Epic 10 | 1-2 oturum | Evet (secret leak fix, input sanitize) |
| **Faz L — Mainnet Go/No-Go** | Epic 11 | 1 oturum | Hayır (skor ve karar) |

**Toplam:** ~16-25 oturum. Hiçbiri 2 saati geçmesin. Her oturum sonunda TASKS.md güncelle + mümkünse commit.

**Sıra kuralı:** Bir Epic bitmeden (tüm task'ları `[x]`) sıradakine geçilmez. İstisnalar için ONAY iste.

---

## 6. Ben Ne Yapacağım? (Senin Rolün)

1. Yeni chat'i aç, yukarıdaki **Açılış Prompt'u**nu yapıştır.
2. Claude'un ilk çıktısını (mimari + ilk 10 risk dosyası + TASKS.md taslağı) gözle. Eksik gördüğün şeyi söyle.
3. Onay verdiğinde ilk Epic'e geç. Modül şablonunu kullan.
4. Claude bir değişiklik önerdiğinde: "Peki neden bu çözüm, alternatifi ne?" diye sor. Yüzeysel cevap verirse zorla.
5. Hiçbir şeyi tek oturumda bitirmeye çalışma. Yorulduğunda bırak, `TASKS.md` durum kaydın olacak.
6. Her oturum sonunda `git status` al, branch'e commit at.

---

## 7. Özel Notlar — Senin Projen İçin

- `project_polypaper_status.md` memory'n güncel — Claude ilk oturumda onu da okur.
- `feedback_iterative_workflow.md` memory'si: "research→explain→confirm→change→test→approve" — bu planın omurgası zaten.
- Windows `.bat` kuralı: Çoklu Python satırı `py -c "..."` içine koymak yok — `scripts/*.py` çıkar.
- Telegram: HTML parse_mode, `$` Markdown'u kırar.
- Bot local PC'de, Replit değil. Restart = PC'de.

---

## 8. Bitince Ne Olacak?

- `TASKS.md` tamamen ✅ olduğunda → Faz H (Mainnet Checklist) → skor 85+ → LIVE_ENABLED flag'i konuşuruz.
- Öncesinde hiçbir şekilde mainnet yok.
- Feature freeze sona erer, Phase 15 konuşulur.
