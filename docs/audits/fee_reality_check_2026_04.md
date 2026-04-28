# Fee Reality Check — Polymarket vs `core/fees_v2.py`

**Tarih:** 2026-04-28
**Sahip:** Heddas + Lead AI Architect
**Konteks:** Yol haritası FAZ 0.1 — mainnet öncesi zorunlu doğrulama
**Karar:** Bot'un %100 trade ettiği **Crypto** kategorisi için fee modeli **MATEMATİKSEL OLARAK DOĞRU**.
Paper PnL'in (+$355 / 1417 trade) fee artefaktı olmadığı bu auditle netleşti.

---

## 1. Yöntem

1. Polymarket resmi dokümantasyon **MCP filesystem** üzerinden `/trading/fees.mdx` ve `/market-makers/maker-rebates.mdx` okundu.
2. Resmi formül kayda alındı:
   ```
   fee = C × feeRate × p × (1 − p)
   ```
   `C` = paylaşım sayısı, `p` = anlık fiyat. Tüm kategoriler için **lineer p×(1-p) curve** (yani exp=1 zorunlu).
3. `core/fees_v2.py::polymarket_taker_fee_v2` 15 örnekli matriks ile test edildi (her kategori 50¢, ek olarak crypto için 1¢/10¢/90¢/99¢).
4. Maker rebate yüzdeleri tek tek karşılaştırıldı.

---

## 2. Taker Fee — Numerik Sonuçlar

| Category    | Price | Notional | Docs Fee | Bot Fee | Match |
|-------------|------:|---------:|---------:|--------:|:-----:|
| crypto      | 0.01  |   $1.00  |   $0.07  |  $0.07  |  ✅   |
| crypto      | 0.10  |  $10.00  |   $0.65  |  $0.65  |  ✅   |
| **crypto**  | 0.50  |  $50.00  |   $1.80  |  $1.80  |  ✅   |
| crypto      | 0.90  |  $90.00  |   $0.65  |  $0.65  |  ✅   |
| crypto      | 0.99  |  $99.00  |   $0.07  |  $0.07  |  ✅   |
| sports      | 0.50  |  $50.00  |   $0.75  |  $0.75  |  ✅   |
| finance     | 0.50  |  $50.00  |   $1.00  |  $1.00  |  ✅   |
| politics    | 0.50  |  $50.00  |   $1.00  |  $1.00  |  ✅   |
| culture     | 0.50  |  $50.00  |   $1.25  |  $1.25  |  ✅   |
| tech        | 0.50  |  $50.00  |   $1.00  |  $1.00  |  ✅   |
| geopolitics | 0.50  |  $50.00  |   $0.00  |  $0.00  |  ✅   |
| economics   | 0.50  |  $50.00  |   $1.25  |  $2.50  |  ❌   |
| weather     | 0.50  |  $50.00  |   $1.25  |  $2.50  |  ❌   |
| mentions    | 0.50  |  $50.00  |   $1.00  |  $0.25  |  ❌   |
| other       | 0.50  |  $50.00  |   $1.25  |  $0.31  |  ❌   |

**Bulgu T1 (✅):** Crypto için 5/5 fiyat noktasında docs ile bit-identical match. Bot'un %100 trade ettiği kategori bu — **PnL artefaktı yok**.

**Bulgu T2 (❌):** `economics`, `weather` (`taker_exp=0.5`) ve `mentions`, `other` (`taker_exp=2`) kategorilerinde exponent yanlış. Resmi formül **uniform exp=1** kullanır, kategori bazında değişmez. Bot şu an bu 4 kategoriyi trade etmediği için canlı PnL etkisi sıfır, ama kod yanlış spec uyguluyor — temizlenmeli.

---

## 3. Maker Rebate — Numerik Sonuçlar

| Category    | Docs Rebate | Bot Rebate | Match |
|-------------|------------:|-----------:|:-----:|
| **crypto**  |       20%   |      25%   |  ❌   |
| sports      |       25%   |      25%   |  ✅   |
| politics    |       25%   |      25%   |  ✅   |
| finance     |       25%   |      25%   |  ✅   |
| economics   |       25%   |      25%   |  ✅   |
| culture     |       25%   |      25%   |  ✅   |
| weather     |       25%   |      25%   |  ✅   |
| other       |       25%   |      25%   |  ✅   |
| mentions    |       25%   |      25%   |  ✅   |
| tech        |       25%   |      25%   |  ✅   |
| geopolitics |        0%   |       0%   |  ✅   |

**Bulgu R1 (❌):** Crypto için maker rebate **%25 → gerçekte %20**. Pratik etki:
* Bot şu anda **shadow live'da 100% taker** (live_trader.py classic + AI mirror)
* Maker rebate sadece market-making varsa devreye girer — şu an aktif değil
* Yine de spec doğru kayda alınmalı: 5 puan fark, yarın market-maker stratejisi açılırsa cebimize gelmeyen rebate'i gerçekçi sanırız.

---

## 4. Tail-Zone Audit

`fees_v2.py` Z=0.15 / Z=0.85 tail eşiklerine sahip ve çağıranlar `FEE_TAIL` skip emit ediyor. Docs bunu doğrulamıyor — bu **mühendisin koruyucu kararı**, yanlış değil. Tail-zone'da fee mutlak küçük ama edge gereksinimi büyük — savunulabilir bir gate.

---

## 5. Anlamlılık Notu (FAZ 0.3'e ön-bilgi)

Crypto taker fee curve doğru → **paper PnL artefakt değil**. Ama:
* SE(WR) = √(0.57·0.43/1417) = **0.0132 (≈%1.3)**
* %95 CI lower bound = 0.57 − 1.96 × 0.0132 = **0.5441**
* Yani gerçek WR alt sınırı %54.4 — break-even %52'nin (taker-only crypto, ortalama p=0.5) **üstünde** ama tampon dar.
* FAZ 0.2 (live/paper drift) sonucu kötü çıkarsa bu tampon hızla yanılır.

**Erken karar:** Edge ihtimal dahilinde, ama bilimsel ispatın yarısı henüz yapılmadı. FAZ 0.2 (live-vs-paper drift) ve FAZ 0.3 (formal CI) zorunlu.

---

## 6. Düzeltme Backlog (Öneri)

| ID | Dosya / Satır | Bulgu | Öneri | Etki |
|---|---|---|---|---|
| F-01 | `core/fees_v2.py:33` | `crypto.maker_rebate_pct=0.25` | **0.20**'ye düşür | Düşük (taker-only mode) |
| F-02 | `core/fees_v2.py:37,39` | `economics/weather.taker_exp=0.5` | **1**'e çek | Sıfır canlı (trade edilmiyor) |
| F-03 | `core/fees_v2.py:41,42` | `mentions/other.taker_exp=2` | **1**'e çek | Sıfır canlı |
| F-04 | `core/fees_v2.py:9-13` docstring | "linear / near-linear" tabiri yanıltıcı (formül `p(1-p)` quadratic-in-p) | "fee curve `p×(1-p)` — bell-shape, peaks at p=0.5" diye netleştir | Sadece doc |

**Toplam değişiklik:** 4 satır kod + 1 docstring. Test imzası: `pytest tests/test_fees_v2.py -v` zorunlu, mevcut crypto rakamları bozulmamalı.

---

## 7. Verdict

> **Crypto taker fee modeli docs'la bit-identical doğru.** Paper PnL fee artefaktı **DEĞİL**. Mainnet'e ilerlemek için FAZ 0.2 (live/paper drift) + FAZ 0.3 (significance recompute) yapılmalı, ama bu auditle "fee yanlış olabilir" şüphesi kapatıldı.

**Sıradaki adım:** Heddas onayı sonrası ya (a) F-01/02/03/04 küçük temizlik PR'ı, ya (b) doğrudan FAZ 0.2 drift script'i.

---
*Bu doküman PolyPaper Bot mainnet karar zincirinin parçasıdır. Yol haritası: `uploads/YOL_HARITASI_3AI_SYNTHESIS.md`.*
