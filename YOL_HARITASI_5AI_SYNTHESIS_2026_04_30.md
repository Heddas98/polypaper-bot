# PolyPaper Bot — 5 AI Acımasız Sentezi + Polymarket Docs (28 Nisan 2026) Yol Haritası

**Tarih:** 2026-04-30
**Önceki sürüm:** `YOL_HARITASI_3AI_SYNTHESIS.md`
**Sentez kaynakları:**
1. **Grok** — `Grok_260430_015651.pdf` (acımasız teknik + iş modeli)
2. **Gemini** — `Polypaper-bot- Kâr Odaklı Analiz ve Yol Haritası.pdf` (akademik, 35 atıf)
3. **Deepseek** — `Deepseek_260430_015717.pdf` (Gantt + somut aksiyon)
4. **GPT** — `Gpt_260430_015606.pdf` (klasör-klasör god-module audit)
5. **Comprehensive Audit** — `PolyPaper_Bot_Technical_Audit_Access_Barriers_and_Profitability.PDF` (en sert, en teknik, 6500 kelime)

**Ek kaynak:** Polymarket resmi docs (28 Nisan 2026 V2 cutover sonrası, MCP connector ile satır satır okundu).

> **Mihenk taşı kuralı:** Bu yol haritasındaki HER teknik karar Polymarket'in resmi dokümantasyonu ile satır satır eşleşmek zorundadır. Doc'tan sapan hiçbir öneri uygulanmaz. Memory'deki `polymarket_docs_compliance_phase_a_b_c_closure` referans alınır, Phase D maddeleri (8/9/10/11/12) bu yol haritasında kapatılır.

---

## BÖLÜM 1 — YÖNETİCİ ÖZETİ (Acımasız Sentez)

### 1.1 Beş AI'nin Üzerinde Anlaştığı 7 Sert Gerçek

| # | Bulgu | Hangi AI'ler | Sertlik |
|---|-------|--------------|---------|
| 1 | **Bot mühendislik harikası, ÜRÜN değil. Edge kanıtlanmamış.** | GPT, Grok, Deepseek, Audit | KRİTİK |
| 2 | **18 strateji = overengineering / overfitting riski.** Sadece 1-3 stabil olanı bırak. | GPT, Grok, Audit | KRİTİK |
| 3 | **AI Brain (Claude Sonnet 10dk cycle) = pahalı + non-deterministik + black-box.** Sermaye ölçeklenirse maliyet kâra yetişmez. | GPT, Grok, Gemini | YÜKSEK |
| 4 | **Backtest illüzyonu.** Hyperopt + AI param tuning birlikte → fake edge üretir. Walk-forward + slippage modeli yoksa rakam yalan. | GPT, Audit, Grok | KRİTİK |
| 5 | **Paper×0.66 = live heuristic** sapması (bizim T4.6-B sweep'inden) → fill heuristics RESET edilmeli. | Audit (paper-vs-live gap), GPT | KRİTİK |
| 6 | **Polymarket V2 cutover (28 Nisan 2026) yaşandı.** SDK güncel mi? `signature_type`/`funder` doğru mu? collateral pUSD mi? | Audit (en derin) | KRİTİK |
| 7 | **Ürünleştir, kendi paranı yakma.** Telegram SaaS ($9-79/ay) >>> $1k riske atmak. | Grok, Gemini, Deepseek, GPT | YÜKSEK |

### 1.2 Skor Kartı (5 AI Konsensüs)

| Boyut | Mevcut | Hedef (3 ay) | Ne yapılacak |
|-------|--------|--------------|--------------|
| Teknik kalite | 8/10 | 7/10 (basitleşecek) | -50% kod, -85% strateji, AI Brain dış servis |
| Ürün olgunluğu | 3/10 | 7/10 | Tek tıkla kurulum, multi-user, ödeme entegrasyonu |
| Edge kanıtı | 0/10 | 6/10 | Walk-forward + 60 gün live mikro-test + fill probability ölçümü |
| Polymarket compliance | 95% | 100% | Phase D 5 madde kapatılacak |
| Para kazanma potansiyeli | 4/10 | 8/10 | SaaS pivot — bot operatörü değil bot satıcısı ol |

### 1.3 Tek Cümle Hüküm

> **Bot mainnet'e teknik olarak hazır AMA ekonomik olarak hazır DEĞİL. Şu an gerçek $10k+ ile çalıştırılırsa beklenen değer NEGATİF.** 6 ay aşağıdaki yol haritası uygulanırsa, ya gerçek edge kanıtlanır (devam edilir), ya da edge yokluğu kanıtlanır ve sermaye yakılmadan SaaS modeline pivot edilir.

---

## BÖLÜM 2 — 5 AI'NIN TEK TEK ANALİZİ

### 2.1 GROK (`Grok_260430_015651.pdf`)

**Pozisyon:** Pratik + iş modeli odaklı. Senin seviyene en uygun ton.

**En değerli 5 görüş:**
1. **Windows bağımlılığı kır.** `watchdog.bat`+`.vbs` amatörce; Linux+systemd+Docker.
2. **AI maliyet katmanlaması.** Kritik kararlar Sonnet, rutin Haiku/Llama, OpenRouter fallback.
3. **18 strateji → 3'e indir.** "Çok iyi 3 strateji + sağlam risk yönetimi" > "18 ortalama strateji".
4. **Para modeli sıralaması:** Önce kendin kullan (6 ay) → Telegram sinyal kanalı ($50-200/ay) → SaaS multi-tenant.
5. **Drawdown kill-switch hayati.** MC Kelly güzel ama gerçek hayatta kill-switch olmadan ölür.

**Eleştirilebilir noktalar:**
- "AWS US-East" tavsiyesi → Polymarket sunucuları muhtemelen Cloudflare arkasında, location avantajı sınırlı. Audit bunu çürüttü (latency >100ms bile bot için kritik değil eğer arbitraj odaklı değilse).
- "Yıllık 12 aylık plan" çok iyimser; mainnet'e geçişten önce ≥6 ay paper-test gerekir.

**Roadmap'e taşıdığımız maddeler:** Linux/Docker (P1), AI tiering (P1), 18→3 strateji pruning (P0), kill-switch (P0).

---

### 2.2 GEMINI (`Polypaper-bot- Kâr Odaklı Analiz ve Yol Haritası.pdf`)

**Pozisyon:** Akademik, 35 atıflı, kapsamlı. Polymarket mikro-yapısını ve güvenlik tehditlerini en iyi anlatan rapor.

**En değerli 5 görüş:**
1. **P_YES + P_NO = 1.00 invariant'ı.** "Hayalet arbitraj" botları neden zarar eder — eşleştirme motoru bu invariant'ı dayatır, API'de görülen gap stale data'dır.
2. **Tedarik zinciri saldırısı (Dev Protocol hijack).** Açık kaynak Polymarket botları indirmeden önce yapı denetlenmeli; SSH backdoor + private key exfiltration vakaları gerçek.
3. **Off-chain ↔ on-chain senkronizasyon istismarı.** Saldırgan nonce manipülasyonu ile API'ye "trade executed" döndürtüp on-chain revert yapabiliyor; piyasa yapıcı botlarını zarara uğratıyor. Bot'umuz reconciliation loop ile bunu yakalamalı.
4. **API limit yönetimi.** Polling DEĞİL, WebSocket. (Bot v9.7.9 zaten WS kullanıyor — ✅ yeşil bayrak).
5. **Kârlı niş = "yavaş pazarlar".** 5 dakikalık BTC binary'de retail kazanamaz; 6+ saat yaşayan tezler (seçim, hava durumu, yargı) AI'nın multi-source veri sentezleyebileceği yerlerdir.

**Eleştirilebilir noktalar:**
- "OpenClaw" gibi referanslar tutarlılık açısından zayıf — gerçek ekosistemde marjinal.
- Telegram-SaaS teklifleri (Trojan/BONKbot benzetmesi) Polymarket prediction market için doğrudan transfer edilemez — kullanıcı kitlesi farklı.

**Roadmap'e taşıdığımız maddeler:**
- Reconciliation loop her 5 dakikada bir on-chain CTF balanceOf (P1)
- Tedarik zinciri audit: SDK + bağımlılıklar pip-audit + checksum (P1, T10.8 zaten yapıldı)
- 5dk binary'leri DEFAULT OFF yap, 1h+ pazarlara odaklan (P0)

---

### 2.3 DEEPSEEK (`Deepseek_260430_015717.pdf`)

**Pozisyon:** En somut + Gantt çizelgeli + sayısal. SaaS'a en hızlı geçiş planı.

**En değerli 5 görüş:**
1. **Bot 1417 işlem × $0.25 ortalama PnL/trade = mikroskobik marj.** Ya ölçek (sermaye) ya da satış lazım. Ölçek riskli, satış güvenli.
2. **Üç plan SaaS:** Starter $9 (2 strateji, paper) → Trader $29 (10 strateji, shadow) → Pro $79 (full live, AI). Net basamaklandırma.
3. **".env'de 100+ değişken" = kullanım engeli.** 20'nin altına indir.
4. **Test coverage %21.2 → %80.** Endüstri standardına çıkar; SaaS satışı için trust signal.
5. **30 gün somut aksiyon planı:** 1.hafta analiz + temizlik, 2.hafta Docker + Linux, 3.hafta multi-user + lisans, 4.hafta lansman.

**Eleştirilebilir noktalar:**
- "Stripe + kripto ödeme entegrasyonu" 5. günde bitmez — minimum 2 hafta + yasal.
- "Affiliate programı" 8-16 hafta erken; önce 10+ ödeme yapan müşteri lazım.

**Roadmap'e taşıdığımız maddeler:**
- 3 fiyat tier (P2)
- .env değişken sayısını 100+ → 20 indir (P1)
- Test coverage 21% → 60%+ (P1)
- 30 günlük başlangıç planı (P0/P1 birleştirme)

---

### 2.4 GPT (`Gpt_260430_015606.pdf`)

**Pozisyon:** En cesur, en agresif. "Kompleksitiyi yarıya indir" emrini en net veren AI.

**En değerli 5 görüş:**
1. **`core/` God-module riski.** Signal + execution + risk + AI Brain aynı yerde → maintain edilmez. **`core/` → `signal_engine/`, `execution_engine/`, `risk_engine/` olarak böl.**
2. **AI Brain core'dan ÇIK.** Non-deterministik, test edilemez, reproducible değil. Ayrı microservice + opsiyonel olmalı.
3. **`backtest/` = en tehlikeli klasör.** Hyperopt + replay engine = %80 fake edge üretici. Komple sil veya sadece "simple replay, no optimization" bırak.
4. **`indicators/` %70 redundant.** Sadece volatility + momentum (1 tane) bırak; gerisi noise.
5. **`skills/` overengineering.** "AI agent architecture" kokuyor ama biz OpenAI değiliz. Kaldır.

**En sert hüküm:**
> "Sen şu an: hedge fund simülasyonu yazmışsın. Ama kazanmak için: basit sinyal botu yazman lazım."

**Eleştirilebilir noktalar:**
- Backtest'i komple silmek aşırı — walk-forward + reality gap modellemesi ile düzeltilebilir.
- `calibration/` kategorik olarak silmek yanlış — Brier-style probability calibration prediction market'ta direkt edge.

**Roadmap'e taşıdığımız maddeler:**
- `core/` 3'e bölünme (P1, mimari refactor)
- AI Brain ayrı microservice (P2)
- Backtest → walk-forward + slippage simülasyon zorunlu (P0)
- 18 strateji → 3 (P0)

---

### 2.5 COMPREHENSIVE TECHNICAL AUDIT (`PolyPaper_Bot_Technical_Audit_Access_Barriers_and_Profitability.PDF`)

**Pozisyon:** En sert, en teknik, 6500 kelime. Repo'ya erişemediğini açıkça söyleyen AI — uydurmuyor, "bunlar yoksa P0" diyerek kanıtlanabilir checklist veriyor. **En önemli AI bu.**

**En değerli 10 görüş:**

1. **V2 cutover 28 Nisan 2026'da gerçekleşti.** `py-clob-client-v2` paketi gerekiyor. **Memory'deki "py-clob-client 0.34.6" yanlış olabilir** — V2 ayrı bir paket, sürüm numarası değil. (DOĞRULAMA GEREKİR.)

2. **EIP-712 imza 15+ subtle bug pattern içerir.** Kendi imzalayıcı yazılmamalı, SDK delege edilmeli. Memory'de `polymarket_signature_fix_closure` var — V2 ile bu kontrol tekrar yapılmalı.

3. **`signatureType` + `funder` 3 kombinasyon:**
   - Type 0 (EOA) → funder = signer
   - Type 1 (Magic Link / email login) → funder = proxy adresi
   - Type 2 (GNOSIS_SAFE — MetaMask, Rabby, embedded wallets) → funder = proxy adresi (en yaygın)
   - **Memory der ki bizimkisi `0xA7e758...BAAa` proxy + signature_type=2.** ✅ Doğru görünüyor.

4. **Collateral token migration:** V1'de USDC.e, V2'de **pUSD**. Hardcoded native USDC (`0x3c499c...`) varsa balance hep 0 görünür. Doğrulanması gerek.

5. **5 farklı approval gereksinimi:**
   - pUSD → CTF Exchange
   - pUSD → NegRiskCtfExchange
   - pUSD → NegRiskAdapter
   - CTF (ERC-1155) → setApprovalForAll(true) for all 3 exchanges
   - Memory'deki `polymarket_wallet_asama_1_2_closure` der ki "A1 allowance approve (SDK update_balance_allowance)" — ama bu SDK helper TÜM 5 approval'i yapıyor mu? Doğrulanmalı.

6. **Tick size + neg_risk PER MARKET çağrılmalı.** `client.get_tick_size(token_id)` + `client.get_neg_risk(token_id)`. Hardcoded 0.01/false varsa neg-risk pazarda imza geçersiz olur.

7. **Float arithmetic = ölümcül.** `int(price * size * 1_000_000)` floating-point error → on-chain hash != imzalanan hash → INVALID_SIGNATURE. **Decimal kullanılmalı.**

8. **Heartbeat coroutine 5s zorunlu** GTC orderlar için. Yoksa dead-man-switch 10s sonra her şeyi cancel'lar. Bot'ta var mı? Kritik kontrol.

9. **Reference price source:** Hourly BTC Up/Down → **Binance BTC/USDT 1H candle**. 15m/5m → **Chainlink BTC/USD Data Stream**. CoinGecko/Coinbase kullanan bot resolution price ile sistematik divergence yaşar. **Memory'de heartbeat'te `bnc=$X` Binance var ✅, ama signal tarafında ne kullanıldığı belirsiz.**

10. **0.30-0.35 düşük olasılık girişleri = backtest artifaktı %95.** Fill probability modellemediği için fake. arXiv 2604.24366 — düşük olasılık decile'larında 650-900bps half-spread premium VAR ama maker harvest ediyor; biz taker olarak bunu ÖDÜYORUZ.

**Eleştirilebilir noktalar:**
- Repo'ya erişemediği için varsayımsal — bizim memory verisiyle bazı maddeler zaten kapalı (örn: signature_type=2 + funder = proxy ✅).
- "$1000 spot BTC al, T-bill'e koy" tavsiyesi makul ama bu projenin amacı saf yatırım değil, mühendislik + opsiyonel ürünleştirme.

**Roadmap'e taşıdığımız maddeler (P0 ağırlıklı):** Aşağıdaki Bölüm 5 doğrudan bu PDF'in checklist'ine dayanıyor.

---

## BÖLÜM 3 — POLYMARKET DOCS (28 Nisan 2026 V2) GERÇEKLİK KONTROLÜ

MCP connector ile docs.polymarket.com'dan satır satır okundu. Aşağıda **sertifikalı gerçekler** + **bot durumu farkı**:

### 3.1 SDK Versiyonu

**Doc gerçeği:**
```python
pip install py-clob-client-v2
from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions
```

**Bot durumu:** `requirements.txt`'te tam ne var? Memory'de "py-clob-client 0.18→0.34.6 upgrade" yazıyor. **Bu V1 sürüm numaralandırması.** V2 AYRI BİR PAKET. Bu **P0 kontrol** maddesi.

**Aksiyon:** `requirements.txt` içeriği doğrulanacak. Eğer `py-clob-client` (V1) ise → V2'ye migrate veya V2'ye ihtiyaç olmadığı durumlar dokümante.

### 3.2 Signature Types

**Doc gerçeği:**
| Wallet Type | ID | Funder |
|-------------|-----|--------|
| EOA | 0 | EOA address (= signer) |
| POLY_PROXY (Magic Link) | 1 | Proxy address |
| GNOSIS_SAFE (MetaMask/Rabby/Privy/Turnkey) | 2 | Proxy address |

**Bot durumu (memory):** `signature_type=2 + POLYGON_WALLET=0xA7e758...BAAa proxy` ✅ DOĞRU.

### 3.3 Fee Yapısı

**Doc gerçeği (Crypto kategorisi):**
- Formula: `fee = C × 0.072 × p × (1-p)`
- Peak: $1.80 per 100 shares at p=0.50
- Maker rebate: 20%

**Bot durumu (memory):** `core/fees_v2.py` SINGLE oracle, FAZ 0.1 audit `bit-identical Polymarket docs` ✅ DOĞRU.

### 3.4 Heartbeat (Dead-Man-Switch)

**Doc gerçeği:**
- 5 saniyede bir `POST /heartbeat` zorunlu (GTC/GTD restingorderlar için)
- 10s + 5s buffer içinde gelmezse TÜM açık order'lar cancel edilir
- İlk request'te `heartbeat_id=""`, sonraki her request'te bir önceki response'tan dönen ID kullanılır

**Bot durumu:** Memory'de heartbeat job 1800s (shadow report için). **CLOB heartbeat ayrı — kontrol et:** `engine/live_trader.py` veya `data/polymarket_client.py`'da 5s heartbeat coroutine var mı? **Yoksa P0.**

### 3.5 Order Types

**Doc gerçeği:**
- GTC (default limit, rest)
- GTD (good-til-date, expiration)
- FOK (fill-or-kill, all or nothing)
- FAK (fill-and-kill, partial OK)
- Post-only flag (GTC/GTD only, reject if marketable)

**Bot durumu (memory):** `OrderType.FOK explicit` Phase B audit ✅. Ama post-only flag kullanımı belirsiz — taker fee öderken maker rebate kaçırma riski.

### 3.6 Tick Size & Neg-Risk

**Doc gerçeği:** Her market için per-market sorgulanmalı:
```python
tick_size = client.get_tick_size(token_id)  # "0.01" / "0.001" / vb.
neg_risk = client.get_neg_risk(token_id)
```

**Bot durumu (memory):** `tick_size + neg_risk + builder_code options dict` Phase A ✅ DOĞRU.

### 3.7 Rate Limits (Yeni Ayrıntılar — Memory'de Yok)

**Doc gerçeği:**
| Endpoint | Burst | Sustained |
|----------|-------|-----------|
| POST /order | 3500/10s | 36000/10min |
| DELETE /order | 3000/10s | 30000/10min |
| GET /book | 1500/10s | — |
| GET /price | 1500/10s | — |
| Gamma /markets | 300/10s | — |
| Data API /trades | 200/10s | — |

**Bot durumu:** Memory'de Bulgu 8 (Phase D) `Gamma bulk RL` backlog var. Yukarıdaki tablo bu Phase D maddesini kapatma referansı.

### 3.8 Reference Price (Resolution)

**Doc gerçeği:** Bunu doc explicitly demese de market sayfalarından:
- Hourly BTC Up/Down → Binance BTC/USDT 1H candle
- 15m/5m → Chainlink BTC/USD Data Stream

**Bot durumu (memory):** Heartbeat'te `bnc=$X` Binance var ✅. Ama **signal pipeline'da hangi feed kullanılıyor**? Eğer Coinbase/CoinGecko ise resolution divergence yaşar. **P0 kontrol.**

### 3.9 Sports Markets Özelliği (Bot için Geçerli Değil Ama Önemli)

**Doc gerçeği:** Sports markets'ta:
- Game start time'da TÜM açık order'lar otomatik cancel
- Marketable orders 1 saniye placement delay

**Bot durumu:** Bot crypto markets'ta — relevant değil. Ama **Geopolitics markets %0 fee** (doc'tan) — bot'a yeni bir piyasa kategorisi ekleme fırsatı (P3).

### 3.10 Phase D Backlog (Memory) → Bu Yol Haritasında Kapatılacak

| Bulgu | Konu | Bu yol haritasında nerede |
|-------|------|---------------------------|
| 8 | Gamma bulk rate limits | P1 — Bölüm 5.2 #4 |
| 9 | Allowance pre-flight check | P0 — Bölüm 5.1 #5 |
| 10 | Taker logic clarity | P1 — Bölüm 5.2 #6 |
| 11 | Error code mapping | P2 — Bölüm 5.3 #2 |
| 12 | Status polling refinement | P2 — Bölüm 5.3 #3 |

---

## BÖLÜM 4 — MEVCUT DURUM vs OLMASI GEREKEN (Diff)

| Boyut | Mevcut | Olması gereken | Fark | P |
|-------|--------|----------------|------|---|
| Strateji sayısı | 18+ | 1-3 stabil | -85% | P0 |
| Test coverage | %21.2 | %60+ | +185% | P1 |
| .env değişken | 100+ | <25 | -75% | P1 |
| AI Brain | core/'da | ayrı microservice | refactor | P2 |
| Backtest | hyperopt+replay | walk-forward + slippage sim | model | P0 |
| Fill heuristic | paper×0.66≈live | empirical her hafta yenilenir | drift | P0 |
| OS desteği | Windows | Windows + Linux Docker | +Linux | P1 |
| Multi-user | Tek kullanıcı | Lisans + 3-tier | yeni | P2 |
| Edge kanıtı | yok | 60+ gün live + Sharpe>1 | her şey | P0 |
| Mainnet sermaye | $1.49 shadow | $20→$100→$500 aşamalı | ölçek | P0+ |
| Heartbeat 5s | belirsiz | zorunlu coroutine | doğrula | P0 |
| Reference price feed | belirsiz | Binance hourly + Chainlink m | doğrula | P0 |
| SDK V2 | belirsiz (V1 muhtemel) | py-clob-client-v2 | migrate | P0 |

---

## BÖLÜM 5 — P0 / P1 / P2 ÖNCELİKLİ YOL HARİTASI

### 5.1 P0 — MAINNET'E DOKUNMADAN ÖNCE ZORUNLU (ilk 14 gün)

**P0.1 — SDK Versiyonu Doğrulama**
- `requirements.txt` içinde `py-clob-client` mi `py-clob-client-v2` mi?
- V1 ise: V2'ye migrate gerekli mi yoksa V1 hâlâ çalışıyor mu? (Polymarket V1'i deprecate ettiyse mainnet imzalar reject olur.)
- WebFetch ile docs.polymarket.com/v2-migration sayfasından deprecation timeline çek.
- **Çıktı:** `docs/audits/sdk_v2_migration_check_2026_05.md`

**P0.2 — Heartbeat Coroutine Zorunluluğu**
- `engine/live_trader.py` veya `data/polymarket_client.py` taranır
- 5s heartbeat coroutine var mı? Yoksa eklenir.
- ID rotasyonu doğru mu (her request bir önceki response.heartbeat_id ile)?
- Test: 30 dk GTC order resting, heartbeat akıyor mu, dead-man-switch tetiklemiyor mu?
- **Çıktı:** Heartbeat smoke test artifact + `core/heartbeat.py` eğer yoksa.

**P0.3 — Reference Price Feed Audit**
- Signal pipeline'da BTC fiyatı nereden geliyor? (`data/odds_feed.py`, `indicators/`, `engine_signals.py` taranır.)
- Hourly market'lar için Binance BTC/USDT WebSocket olmalı.
- 15m market'lar için Chainlink Data Stream (Polymarket'in resolution oracle'ı ile uyumlu).
- CoinGecko/Coinbase varsa: signal source ↔ resolution source divergence ÖLÇÜLÜR (geçmiş 30 gün on-chain trade ile karşılaştırma).
- **Çıktı:** `data/price_feed.py` modülü, market type'a göre source seçer; `docs/audits/price_feed_divergence_2026_05.md`.

**P0.4 — Strategy Pruning 18 → 3**
- `_archive/strategies_pre_pruning_2026_05/` klasörüne tüm 18 strateji yedeklenir.
- Past-90 gün performans (Sharpe, max DD, profit factor, expectancy/trade) hesaplanır per strategy.
- Sharpe < 1.2 OR PF < 1.3 olanlar arşive.
- En çok 3 strateji aktif kalır, geri kalanı `config/settings.py`'da `STRATEGY_ENABLED=false`.
- **Çıktı:** `docs/audits/strategy_pruning_2026_05.md` + arşiv klasörü.

**P0.5 — Allowance Pre-Flight Check**
- Phase D Bulgu 9.
- Bot her başlatmada 5 approval'i sırayla doğrular:
  1. pUSD → CTF Exchange
  2. pUSD → NegRiskCtfExchange
  3. pUSD → NegRiskAdapter
  4. CTF → setApprovalForAll(true) for CTF Exchange
  5. CTF → setApprovalForAll(true) for NegRiskCtfExchange
- Eksikse otomatik tamamlar (Telegram'da onay isteyerek).
- SDK `update_balance_allowance(BalanceAllowanceParams(...))` kullanılır.
- **Çıktı:** `core/allowance_preflight.py` + Telegram `/allowance_check` admin command.

**P0.6 — Walk-Forward Backtest + Slippage Modeli**
- Mevcut `backtest/` modülü walk-forward'a evrilir:
  - Train window: rolling 30 gün
  - Test window: forward 7 gün
  - Asla future leak yok
- Slippage modeli orderbook depth ile:
  - `GET /book` derinliğinden gerçek average fill price hesaplanır
  - Min order $5, taker fee `C × 0.072 × p × (1-p)` ekli
- Mevcut hyperopt scriptleri `_archive/hyperopt_disabled_2026_05/`'e (zaten Hyperopt Aşama 1'de büyük temizlik yapılmış, kalan 5 verify/smoke script de oraya).
- **Çıktı:** `backtest/walk_forward.py`, `backtest/slippage_model.py`, `docs/audits/walk_forward_backtest_2026_05.md`.

**P0.7 — Fill Heuristic Empirical Update Job**
- T4.6-B sweep'inde `paper×0.66 ≈ live` bulundu, FILL_SPREAD_COST 0.005→0.023 öneriliyor (T4.7-C backlog).
- Bu yol haritasında **uygulanır:** `config/settings.py` ENV güncellemesi.
- **Haftalık empirical sweep job:** Cron her Cuma 200 trade'lik son hafta + 200 marketleri karşılaştırır, delta>%5 ise alarm.
- **Çıktı:** `core/calibration/fill_heuristic_recalibrate.py`, T4.7-C kapatma.

**P0.8 — Daily Drawdown Kill-Switch**
- Memory'de PNL_PAUSE_THRESHOLD var (-8.0 default). Bu sadece pause.
- Yeni: günlük zarar -10% → HALT (tüm yeni trade kapalı, Telegram alarm).
- Ardışık 5 trade zarar → 1 saat soğuma.
- Haftalık -20% → tam stop, manuel restart.
- ENV: `DAILY_MAX_LOSS_PCT=0.10`, `CONSECUTIVE_LOSS_LIMIT=5`, `WEEKLY_MAX_DD_PCT=0.20`.
- **Çıktı:** `core/risk/circuit_breakers.py` (mevcutsa genişletilir).

**P0.9 — DRY_RUN Default ON**
- `LIVE_ENABLED=false` default. ENV var explicit `LIVE_ENABLED=true` ile + Telegram `/confirm_live <token>` ile aktive olur.
- Bu, yanlışlıkla mainnet sermayeyi yakma riskini sıfırlar.
- **Çıktı:** Bot startup banner: `🔵 PAPER MODE` veya `🔴 LIVE MODE — last toggle by @user at HH:MM`.

**P0.10 — Per-Trade Hard Caps**
- `MAX_ORDER_USD=10` (mainnet ilk hafta), `MIN_PRICE=0.05`, `MAX_PRICE=0.95`.
- Telegram komutu `/buy 100 0.99` parmak kayması = otomatik reject.
- **Çıktı:** `telegram_bot/handlers/order_validator.py`.

### 5.2 P1 — İlk 30 Gün (P0 Geçtikten Sonra)

**P1.1 — Linux/Docker Desteği**
- Dockerfile multi-stage (build + runtime).
- docker-compose.yml: bot + redis (cache) + postgres (gelecekte SQLite'tan migration).
- `systemd` unit dosyası (`PolyPaper-bot.service`, `Restart=always`, `RestartSec=5`).
- Hetzner/DigitalOcean $5/ay VPS deployment guide.

**P1.2 — `core/` Refactor → 3 Modül**
- `core/signal_engine/` (sinyal üretimi)
- `core/execution_engine/` (order placement, fill detection)
- `core/risk_engine/` (kill-switch, position sizing, MC Kelly)
- AI Brain `services/ai_brain/` ayrı microservice.
- Mevcut testlerin import yolları güncellenir, regression testleri PASS.

**P1.3 — Test Coverage 21% → 60%**
- Critical path: signal generation + order construction + signing + fill detection + reconciliation.
- pytest + coverage.py target %60.
- Determinism testleri 3-seed (zaten 42/1337/9001 var).
- **Hedef metrik:** her PR'de coverage düşemez (CI gate).

**P1.4 — Reconciliation Loop (Off-chain ↔ On-chain)**
- Her 5 dakikada Polygon RPC üzerinden CTF balanceOf + pUSD balance çek.
- DB'deki pozisyonlarla karşılaştır.
- Mismatch >$1 ise `/halt` + Telegram alarm.
- Polygon RPC: Alchemy free tier (300k req/ay).
- **Çıktı:** `core/reconciliation/onchain_sync.py`.

**P1.5 — `.env` Cleanup 100+ → 25**
- Audit hangi ENV var aslında runtime kullanılıyor (`grep -r 'os.getenv' .`).
- Kullanılmayan: sil. Whitelist'e: ekle.
- Birleştir: 5 farklı `STRATEGY_X_ENABLED` flag → 1 array `ENABLED_STRATEGIES=ema,vwap,ob_imbalance`.
- Default'ları `config/defaults.py`'a taşı, `.env` sadece secret/override.

**P1.6 — Taker/Maker Logic Clarity (Phase D Bulgu 10)**
- Bot şu an taker mı, maker mı, ya da hibrit? Belirsiz.
- Karar matrisi:
  - Spread >2 tick → post-only GTC limit (maker, %20 rebate kazan)
  - Spread <2 tick → FOK marketable (taker, %1.8 fee öde)
  - Hızla giriş gerekli → FAK partial fill OK
- Telegram'da fee/rebate breakdown göster: "Bu trade $0.34 fee (taker) — paralel GTC olsaydı $0.18 rebate (maker)".

**P1.7 — Structured Logging + Secret Scrubbing**
- `loguru` veya `structlog` JSON format
- Log'lara private_key/api_secret/passphrase girmeyeceğini sağlayan custom formatter
- Pre-commit hook: `detect-secrets` (zaten T10.8 13 regex audit yapıldı, devam)

**P1.8 — Backtest ↔ Live Aynı Kod Path'i**
- `Executor` interface (abstract base class)
- `LiveExecutor` (Polymarket SDK) + `PaperExecutor` (in-memory simülasyon)
- Strateji `executor.place_order(...)` çağırır, hangisi olduğunu bilmez
- Bu, paper-vs-live sapmasını minimize eder (T4.6-B ile öğrenildi).

### 5.3 P2 — 30-90 Gün (SaaS Pivot Hazırlığı)

**P2.1 — Multi-User + Lisans Sistemi**
- DB'ye `users` tablosu (telegram_id, license_key, plan_tier, expires_at)
- `auth/license_check.py` her komutta plan kontrol
- Plan tier'lar: Starter/Trader/Pro (Deepseek önerisi)
- Lisans key Telegram bot komutu `/redeem <key>` ile aktive

**P2.2 — Error Code Mapping (Phase D Bulgu 11)**
- Polymarket'in 15+ error code'u (INVALID_ORDER_MIN_TICK_SIZE, INVALID_POST_ONLY_ORDER, FOK_ORDER_NOT_FILLED, vb.)
- Her birine kullanıcı dostu Türkçe + İngilizce mesaj
- Bot otomatik çözüm önerir: "Tick size yanlış → tick'e snap'liyorum, retry..."
- **Çıktı:** `core/error_handler/polymarket_errors.py`.

**P2.3 — Trade Status Polling Refinement (Phase D Bulgu 12)**
- Mevcut polling interval'ler agresif olabilir (rate limit yer)
- Doc'a göre: matched → mined → confirmed transition takibi
- Status değişene kadar exponential backoff polling (5s → 10s → 30s → 60s)

**P2.4 — Web Dashboard (React veya Streamlit)**
- Live PnL chart, drawdown, win rate, fill rate, slippage histogram
- Read-only public link (her kullanıcı kendi bot'u için)
- Pazarlama materyali: "şeffaf performans = trust signal"

**P2.5 — Ödeme Entegrasyonu**
- Stripe (Avrupa/US) + Coingate/NowPayments (kripto, TR-friendly)
- Telegram bot içi `/subscribe Pro` → ödeme linki → webhook → lisans aktive
- Memory'de Cryptopay/Coingate/Paddle önerileri var (Gemini)

**P2.6 — Affiliate / Referral Program**
- Her kullanıcıya unique referral link
- Yeni abonelik kredisi: %20 lifetime commission
- Pasif gelir mekanizması.

### 5.4 P3 — 90+ Gün (Ölçek + Diversifikasyon)

**P3.1 — Multi-Asset (Polymarket Geopolitics %0 Fee)**
- Crypto'dan başka kategori ekle (Geopolitics fee-free → daha sık trade mümkün)
- Politics/Sports/Finance kategorileri için ayrı fee oracle (zaten fees_v2 hazır)

**P3.2 — Multi-Venue (Kalshi)**
- Aynı tema farklı venue → arbitraj fırsatı
- Kalshi US-only ama API çok benzer
- Risk: regülasyon karmaşası — VPN yasak vb.

**P3.3 — Public API (Geliştirici Erişimi)**
- Pro tier kullanıcılar bot'a programatik erişim
- $99/ay tier; yüksek margin.

**P3.4 — White-Label Lisans**
- $500-2000 setup + %20 monthly revenue share
- Influencer/educator'lara Polymarket Telegram bot lisansla.

---

## BÖLÜM 6 — 30/60/90/180 GÜN TAKVİMİ

### Hafta 1 (1-7 Mayıs 2026)
- [ ] P0.1 SDK versiyon doğrulama → karar
- [ ] P0.2 Heartbeat coroutine eklendi/doğrulandı
- [ ] P0.3 Reference price feed (Binance hourly + Chainlink 15m)
- [ ] P0.5 Allowance pre-flight check
- **Bot durumu sonunda:** PAPER MODE, mainnet kilitli

### Hafta 2 (8-14 Mayıs)
- [ ] P0.4 Strategy pruning 18 → 3 (en iyi performans)
- [ ] P0.6 Walk-forward backtest implementation
- [ ] P0.7 Fill heuristic recalibration (T4.7-C kapatma)
- [ ] P0.8 Drawdown kill-switch
- **Bot durumu sonunda:** 3 strateji aktif, walk-forward gösteriyor

### Hafta 3-4 (15-30 Mayıs) — $20 Live Mikro Test
- [ ] P0.9 + P0.10 hard cap'ler aktif
- [ ] $20 deposit, sadece $5 emirler
- [ ] Amaç: Edge ölçmek DEĞİL — paper P&L vs live P&L sapması <%10 mu doğrulamak
- [ ] Reconciliation loop (P1.4) eklendi
- **Karar noktası:** Sapma <%10 ise Hafta 5'e geç. Değilse simülasyon bozuk → fix önce.

### Ay 2 (Haziran) — Linux + Refactor
- [ ] P1.1 Docker + Linux deployment
- [ ] P1.2 core/ 3'e bölme
- [ ] P1.3 Test coverage 60%+
- [ ] P1.5 .env cleanup
- [ ] P1.6 Taker/maker clarity
- [ ] P1.7 Logging
- **Karar noktası ($100 promotion):** ≥200 live trade, net PnL ≥+5%, Sharpe >1, max DD <%15. Hepsi tutuyorsa $100'e çık. Değilse $20'de kal veya stop.

### Ay 3 (Temmuz) — SaaS Hazırlık
- [ ] P2.1 Multi-user + lisans
- [ ] P2.2/2.3 Error mapping + polling refinement (Phase D kapanış)
- [ ] P2.4 Web dashboard MVP
- **Karar noktası ($500 promotion):** 1000+ trade, Sharpe >1.2, üç ay üst üste pozitif, ops mismatch <%1. Tutuyorsa SaaS lansmana hazırlan.

### Ay 4-6 (Ağustos-Ekim) — SaaS Lansman
- [ ] P2.5 Stripe/Coingate ödeme
- [ ] P2.6 Affiliate program
- [ ] Pazarlama: Reddit, X, Discord, Telegram
- [ ] İlk 10 ödeme yapan müşteri
- **Hedef:** $500-1000 MRR (3-tier ortalama 30 abone)

### Ay 7-12 — Ölçek (P3)
- Multi-asset, multi-venue, public API, white-label
- **Hedef:** $3000+ MRR

---

## BÖLÜM 7 — MAINNET GO/NO-GO KARAR MATRİSİ

Aşağıdaki **TÜM** koşullar sağlanmadan mainnet'e $20'den fazla sermaye yatırılmaz.

### 7.1 Pre-Mainnet Gate (P0 maddelerinin tamamlanması)

| # | Koşul | Kontrol Yöntemi | Status |
|---|-------|-----------------|--------|
| 1 | SDK V2 (gerekiyorsa) | requirements.txt + smoke trade | ⏳ |
| 2 | Heartbeat 5s aktif | 30dk GTC test | ⏳ |
| 3 | Reference price feed Binance/Chainlink | divergence audit raporu | ⏳ |
| 4 | Strategy pruning 18→3 | walk-forward Sharpe>1 | ⏳ |
| 5 | Allowance pre-flight | bot startup test | ⏳ |
| 6 | Walk-forward backtest + slippage | rapor mevcut, profitable | ⏳ |
| 7 | Drawdown kill-switch | unit test triggered | ⏳ |
| 8 | DRY_RUN default | env audit | ⏳ |
| 9 | MAX_ORDER_USD=10 hard cap | integration test | ⏳ |
| 10 | Reconciliation loop (P1.4) | 24h test, 0 mismatch | ⏳ |

### 7.2 $20 → $100 Promotion Gate

| # | Koşul | Threshold |
|---|-------|-----------|
| 1 | Live trade sayısı | ≥200 |
| 2 | Paper vs live PnL sapması | <%10 |
| 3 | Net PnL | ≥+%5 |
| 4 | Sharpe ratio (annualized) | >1.0 |
| 5 | Max drawdown | <%15 |
| 6 | Reconciliation mismatch | 0 |
| 7 | Heartbeat downtime | <0.5% |
| 8 | Order reject rate | <%2 |

### 7.3 $100 → $500 Promotion Gate

| # | Koşul | Threshold |
|---|-------|-----------|
| 1 | Live trade sayısı | ≥1000 |
| 2 | Üç ay üst üste pozitif | yes |
| 3 | Sharpe (90 gün) | >1.2 |
| 4 | Max DD | <%12 |
| 5 | Profit factor | >1.4 |
| 6 | Ops mismatch oranı | <%1 |

### 7.4 SaaS Pivot Gate (Sermaye yerine ürün)

Bu gate, $500'a hiç çıkmadan da geçilebilir. Eğer Hafta 4'te $20 mikro test "para kazandırmıyor ama kararlı çalışıyor" ise, sermaye yerine **ürün** modeline pivot:

| # | Koşul | Threshold |
|---|-------|-----------|
| 1 | Bot uptime | >%99.5 (30 gün) |
| 2 | Telegram UX | tek tıkla kurulum + clear UX |
| 3 | Multi-user lisans | aktif çalışıyor |
| 4 | Error coverage | <%1 unhandled exceptions |
| 5 | Web dashboard | live PnL public link |
| 6 | İlk 3 beta kullanıcı | "kullanışlı" diyor |
| 7 | Yasal kontrol | TR vergi danışmanı, KVKK |

Bu kapı geçilirse → Bölüm 6 Ay 3-6 SaaS plan devreye girer. **Sermaye riski sıfır, gelir potansiyeli pozitif.**

---

## BÖLÜM 8 — RİSKLER & AZALTMA

| Risk | Olasılık | Etki | Azaltma |
|------|----------|------|---------|
| Polymarket V2 sonrası hâlâ V1 SDK (kırık) | DÜŞÜK (sign fix yapılmış) | KRİTİK | P0.1 ilk gün |
| Reference price divergence | ORTA | YÜKSEK | P0.3 audit + Binance/Chainlink |
| Bot edge yok, $1k yanıyor | YÜKSEK | YÜKSEK | $20 mikro-test, gate'ler |
| AI maliyeti (Sonnet 10dk cycle) ölçeklenirse | ORTA | ORTA | Tier'le, opsiyonel kapat, Llama fallback |
| Tedarik zinciri saldırısı (3rd party SDK) | DÜŞÜK | KRİTİK | pip-audit + checksum + isolated wallet |
| Telegram bot token sızıntısı | DÜŞÜK | YÜKSEK | router-level whitelist, BotFather revoke prosedürü |
| TR regülasyon (vergi/KYC) | ORTA | ORTA | hukuk danışmanlığı, Q3 2026 |
| Off-chain ↔ on-chain sync exploit | DÜŞÜK | KRİTİK | reconciliation loop her 5 dk (P1.4) |
| Backtest fake edge → live yanma | ORTA | KRİTİK | walk-forward + paper×live gap ölçümü |
| Anlık $355 PnL = sample size küçük | KESİN | ORTA | 1000+ trade'a kadar conclusion vermeyiz |

---

## BÖLÜM 9 — SON SÖZ

5 farklı AI'nin acımasız analizini Polymarket'in resmi dokümantasyonuyla çapraz doğruladık. Ortak ses çıkardı:

**Bot teknik olarak güzel, ürün olarak kanıtlanmamış, ekonomik olarak henüz pozitif değil.**

Üç olası sonuç:

1. **En olası (~%70):** 30 günlük P0 + walk-forward sonrası strateji edge'i kanıtlanmaz → SaaS pivot. Sermaye yerine ürün satılır. Yıllık $5-15k MRR potansiyeli.

2. **Orta (~%25):** Edge zayıf ama pozitif (Sharpe ~0.8-1.2). Hem bot operate edilir hem SaaS satılır (hibrit). Yıllık $10-30k toplam.

3. **Düşük olasılık (~%5):** Gerçek edge (Sharpe >1.5). Sermaye ölçeklenir, $1k → $10k → $50k. Aynı zamanda SaaS de devreye alınır.

**Tüm üç sonuç senaryosunda, P0 maddelerini hemen kapatmak ve $20 mikro-testi yapmak ZORUNLU.** Bu yol haritası, aceleci hareket etmeden önce gerçeği öğrenmenin en güvenli yoludur.

---

## EKLER

### Ek A — 5 AI'nin Birbirleriyle Çelişen Görüşleri

| Konu | AI A | AI B | Çözüm |
|------|------|------|-------|
| Backtest tutulsun mu? | GPT: komple sil | Audit/Grok: walk-forward'a dönüştür | Walk-forward + slippage modeli ile tut, hyperopt sil |
| AI Brain core'da mı dursun? | Grok: tut, sadece tier'le | GPT: ÇIKAR | core'dan ayır, ayrı microservice (P2.1 zaten 3 modül refactor) |
| 3 strateji mi 5 mi? | Grok: 3 | Deepseek: en iyi 3'e indir | 3 (Sharpe>1.2 + PF>1.3 filtresi) |
| Linux mu öncelik mi sonra mı? | Deepseek: 1.hafta | Grok: orta vade | P1 (P0'dan sonra) — mainnet gate'i Windows'ta da geçilebilir |
| Telegram SaaS mı affiliate mi? | Gemini: önce SaaS | Grok: hibrit | SaaS önce (P2.1-2.5), affiliate P2.6'da ekle |

### Ek B — Polymarket Docs MCP Kanıtları

Bu yol haritasında atıfta bulunulan tüm Polymarket gerçekleri MCP connector ile docs.polymarket.com'dan canlı okundu (2026-04-30). Memory'deki `polymarket_docs_compliance_phase_a_b_c_closure` ile bağlantı korundu.

**Doğrulanmış:**
- Crypto fee = 0.072 × p × (1-p) × C (peak %1.80) ✅
- 3 signature types (0/1/2) ✅
- Heartbeat 5s zorunluluğu ✅
- Tick size + neg_risk per market ✅
- 4 order type (GTC/GTD/FOK/FAK) + post-only flag ✅
- Min order $5 ✅
- Geopolitics %0 fee (gelecek fırsat) ✅
- Rate limits (POST /order 3500/10s burst) ✅

**Doğrulanması gereken (P0 işi):**
- SDK V2 (`py-clob-client-v2`) gerekli mi?
- Heartbeat coroutine kodda var mı?
- Reference price feed Binance/Chainlink mi?

### Ek C — Memory Sync Notları

Bu yol haritası sonrası `MEMORY.md`'ye eklenecek yeni landmark:
- `project_5ai_synthesis_2026_04_30.md` — bu doküman
- P0 her madde tamamlandığında müstakil closure landmark
- Mainnet $20 → $100 → $500 promotion her gate ayrı landmark

### Ek D — Bu Yol Haritasının Yaşam Döngüsü

- Her 30 günde bir review (P0 closed → P1 ilerleme → P2 hazırlık)
- Her promotion gate'te tek tek geçiş kararı
- Eğer SaaS pivot tetiklenirse, bu doküman v2'ye revize edilir.

---

**Hazırlayan:** Claude (PolyPaper Bot Lead Developer)
**Sürüm:** v1.0
**Sonraki Review:** 2026-05-30 (Hafta 4 mikro-test sonu)
