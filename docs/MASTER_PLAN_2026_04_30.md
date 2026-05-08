# PolyPaper Bot — MASTER PLAN (Mega Prompt + 5AI Yol Haritası Sentezi)

**Tarih:** 2026-04-30
**Sürüm:** v1.0
**Sahibi:** Claude (Lead Developer/Architect) + Heddas (Operatör)
**Kaynak girdiler:**
1. `uploads/COWORK_POLYMARKET_DOCS_COMPLIANCE_MEGA_PROMPT.md` — 10 katmanlı Polymarket Docs Compliance Audit
2. `YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md` — Grok+Gemini+Deepseek+GPT+Audit sentezi (proje root)
3. Polymarket Docs MCP (canlı) — `docs.polymarket.com` 2026-04-30 snapshot
4. `MEMORY.md` — 60+ landmark (Epic 0-11, Phase A+B+C closure, FAZ 0.1, Becker silme, Hyperopt silme, mode toggle)

**Bu dosyanın amacı:** Mega Prompt'un audit yaklaşımını + Yol Haritası'nın aksiyon planını TEK kaynakta sentezlemek. **Hiçbir madde atlanmayacak.** Tamamlananlar ✅, devam edenler ⏳, yapılmamışlar ❌, planlanmışlar 📋, riskliler ⚠️ ile işaretlenir.

---

## 0 — TL;DR (60 Saniyelik Yönetici Özeti)

**Bot durumu (2026-04-30 itibarıyla):**

| Boyut | Durum | Not |
|---|---|---|
| Polymarket Phase A+B+C compliance | ✅ %95 | Phase D 5 backlog (Bulgu 8/9/10/11/12) |
| Test baseline | ✅ 778-800 PASS / 0 fail | 3-seed deterministic (42/1337/9001) |
| Security baseline | ✅ 13 secret regex / 0 match | pip-audit 0 CVE |
| Mainnet bloklayan epic items | ✅ 0 | Epic 11 T11.1-T11.3 closed |
| **SDK V2 migration** | ✅ **KAPALI 2026-04-30** | V1 0.34.6 mainnet için yeterli (Phase A+B+C smoke PASS). V2 migration P1.9 backlog. Audit: `docs/audits/sdk_v2_migration_check_2026_05.md` |
| **CLOB Heartbeat coroutine** | ✅ **KAPALI 2026-04-30** | Bot FOK-only, Phase C post-order heartbeat ekli, 5s coroutine FOK için gereksiz (P1.6 GTC eklenirse P1.6.1 ZORUNLU). Audit: `docs/audits/heartbeat_audit_2026_05.md` |
| **Reference price feed (Binance/Chainlink)** | ⚠️ **DOĞRULANMAMIŞ** | Heartbeat'te `bnc=$X` Binance var, signal pipeline belirsiz |
| Strategy count | ⚠️ 18+ | 5 AI sentezi: 1-3'e indir |
| Walk-forward backtest | ❌ Yok | Hyperopt silindi, walk-forward eklenmedi |
| Drawdown kill-switch | ⚠️ Kısmi | PNL_PAUSE pause-only, daily/weekly halt eksik |
| Reconciliation loop (off-chain↔on-chain) | ❌ Yok | P1.4 |
| Linux/Docker | ❌ Yok | Windows-only, P1.1 |
| Test coverage | ⚠️ %21.2 | Hedef %60 (P1.3) |
| Multi-user / SaaS | ❌ Yok | P2.1-P2.6 |
| Edge kanıtı | ❌ Yok | Sample 1417 trade, +$355 PnL → istatistiksel anlamlılık zayıf |

**Tek cümle hüküm:** Bot teknik olarak güzel, Polymarket compliance %95, ekonomik olarak kanıtlanmamış. **SDK V2 + Heartbeat + Reference Feed P0** maddelerini kapatmadan mainnet'e $20'den fazla sermaye yatırılmaz.

---

## 1 — KAPSAM (Mega Prompt × 5AI Yol Haritası Birleşimi)

İki belge çakışan ama tamamlayıcı bilgi sağlar:

| Kapsam | Mega Prompt | 5AI Yol Haritası | Sentez |
|---|---|---|---|
| Audit metodolojisi | 10 katman, 40 docs query, bulgu format | — | **Mega Prompt birincil rehber** |
| Kategorize aksiyon | — | P0/P1/P2/P3 + 30/60/90 gün takvim | **Yol Haritası birincil rehber** |
| Polymarket V2 cutover | Bahsetmez | P0.1 SDK V2 doğrulama | **Yol Haritası ekledi** |
| Bulgu→Patch zinciri | unified diff format | P0 ile bağ kurar | **Mega Prompt patches → Yol Haritası uygular** |
| Ekonomi/Edge | — | 1417 trade, $355, mikro marj, SaaS pivot | **Yol Haritası tek kaynak** |
| Pre-Mainnet Gate | DoD checklist (10 madde) | 10-koşul gate matrisi | **İkisi de aynı 10 koşula referans veriyor** |

**Sentez kuralı:** Mega Prompt'un her layer audit'i, Yol Haritası'nın bir veya birden fazla P-maddesini besler. Her bulgu (Layer 1-10) bir P0/P1/P2/P3'e map edilir. Tersi de geçerli: her P-maddesi bir veya birden fazla layer'da kanıtlanır.

---

## 2 — YAPILANLAR (Memory'den ✅)

Bu maddeler **bitmiş** — yeniden yapma. Sadece referans için.

### 2.1 Polymarket Docs Compliance Phase A+B+C ✅
- **Phase A** (signature/funder fix, commit `polymarket_signature_fix_closure`): `signature_type=0→2` (GNOSIS_SAFE) + `POLYGON_WALLET=Rabby→0xA7e758...BAAa` proxy. AUTH OK + api_key 498bde4b... derive.
- **Phase B** (compliance push, 7 bulgu): tick_size + neg_risk + builder_code options dict, OrderType.FOK explicit, post_heartbeat() post-order, py-clob-client 0.18→0.34.6 upgrade.
- **Phase C** (V2 migration cutover): pUSD-aware, `BalanceAllowanceParams` + `AssetType` enum, EIP-681 deposit, allowance approve via `update_balance_allowance`.
- **Audit dosyaları:** `docs/audits/polymarket_docs_compliance_2026_04.md` (Phase A) + memory `polymarket_signature_fix_closure`.
- **Skor:** 47% → ~95% (Phase D 5 madde açık).

### 2.2 Fee Oracle (FAZ 0.1) ✅
- `core/fees_v2.py` SINGLE oracle, bit-identical Polymarket docs.
- Crypto formula: `fee = C × 0.072 × p × (1-p)`, peak $1.80/100 shares, maker rebate %20.
- F-01..F-04 spec fix (rebate 0.25→0.20, 4 cat exp 0.5/2→1).
- 39/39 fees_v2 + 101/101 broader PASS.
- Audit: `docs/audits/fee_reality_check_2026_04.md`.

### 2.3 Becker Tam Silme (Aşama 1+3.C+3.E) ✅
- 10 dosya rm + engine.py init unwire + bot.py 7 blok + .env.example BECKER_*.
- 800/2/0 test baseline.
- Memory: `becker_aciklamasi_aciklama_1_kapatildi`.

### 2.4 Hyperopt Tam Silme (Aşama 1) ✅
- 699 occurrence × 31 dosya purge. AI Brain refactor (3 fonksiyon + LLM prompt + BLOK 9 ~250 satır) + bot.py 7 blok + 4 handler/job + 7 dosya rm + 2 test cleanup.
- 787/2/0 test PASS.
- Backlog Aşama 2: DB migration v16 drop hyperopt_results + 5 verify/smoke scripts.
- Memory: `hyperopt_asama_1_closure`.

### 2.5 Polymarket Wallet (Aşama 1+2) ✅
- Telegram `/portfolio` (alias `/pf`) async fetch + DB cache + 60s job + 4 inline tab.
- A1 allowance approve (SDK update_balance_allowance), A2 deposit (EIP-681 QR + UI link), A3 withdraw, A4 wallet import rehberi, A5 PK export.
- Smoke: 20 gerçek trade history + derived EOA = Rabby eşleşti.
- Memory: `2026_04_29_mainnet_path_session`.

### 2.6 Mode Toggle (Aşama 3.A+3.B) ✅
- `/mode` (alias `/m`) top-level Paper/Real toggle.
- Banner helper `telegram_bot/templates/mode_banner.py` (📋 PAPER vs 💰 REAL).
- LIVE_ENABLED env mode source-of-truth + runtime toggle (engine.live._enabled + os.environ patch).
- 5 handler banner inject. 3-segment tutarlılık: PAPER sim / POLYMARKET gerçek bakiye / BOT risk limit.

### 2.7 Epic 11 Mainnet Pre-Gate (T11.1-T11.8) ✅
- T11.1 Coverage CI gate (21% baseline, ratchet forward).
- T11.2 Live Guard Validation (G1-G6 = 6/6 PASS, 5 canlı + 1 historical).
- T11.3 Rollback Plan Dry-Run (4/4 PASS) + Bulgu B atomic backup fix.
- T11.4 Coverage CI gate, T11.5 env-leak hygiene, T11.6 exception render policy, T11.7 env_reference AST-gen, T11.8 bare-except pre-commit.
- T9.8-REG Windows integration 52/52 PASS.

### 2.8 Epic 0-10 (Cleanup + Security) ✅
- Epic 0 baseline, Epic 1 ghost modules, Epic 2 root cleanup (100+→19), Epic 3 classic bypass, Epic 4 simulator (T4.1-T4.4 sandbox + T4.5-T4.9 yerel backlog), Epic 5 atomicity/state, Epic 6 UI↔Engine ghost (5 sınıf doktrini), Epic 7 dead code (T7.1-T7.6), Epic 8 bare-except + LLM guard, Epic 9 test infra (17.5%→21.2%), Epic 10 security pass (T10.1-T10.10).

---

## 3 — POLYMARKET V2 GERÇEKLİK KONTROLÜ (Mega Prompt §3 + Docs MCP 2026-04-30)

Polymarket Docs MCP (`mcp__2069108a-96bb-4b48-8db2-11e0d7f6934b__search_polymarket_documentation`) ile **2026-04-30** snapshot:

### 3.1 SDK V2 — ⚠️ KRİTİK BULGU
**Docs:**
```
pip install py-clob-client-v2
from py_clob_client_v2 import ClobClient, OrderArgs, PartialCreateOrderOptions
```
**Bot durumu (`requirements.txt`):**
```
py-clob-client==0.34.6   # V1 sürüm numarası, V2 AYRI PAKET değil
```
**Sonuç:** Bot V1 SDK kullanıyor. Docs V2 öneriyor. **P0.1 ilk iş.** Eğer V1 deprecated ise mainnet imzalar reject olur.

**Aksiyon:** Migration sayfası okunacak (docs.polymarket.com/v2-migration), V1↔V2 imza farkları çıkarılacak, gerekiyorsa requirements.txt + import path migrate edilecek.

### 3.2 Heartbeat — ⚠️ DOĞRULAMA GEREKLİ
**Docs (Q-search "heartbeat 5s GTC"):**
- `POST /heartbeat` 5s'de bir zorunlu (GTC/GTD resting orderlar için).
- 10s + 5s buffer içinde gelmezse TÜM açık order'lar cancel edilir.
- İlk request `heartbeat_id=""`, sonraki her request bir önceki response'tan dönen ID.

**Bot durumu:** `core/` altında 9 dosyada "heartbeat" kelimesi geçiyor (`live_trader`, `engine`, `engine_fills`, `engine_monitor`, `engine_support`, `ai_brain`, `intent_parser`, `risk_manager`, `trade_journal`). Bunlardan hangisi CLOB heartbeat coroutine, hangisi Telegram heartbeat (`bnc=$X` Binance BTC) — taranmamış.

**Aksiyon (P0.2):** Her dosyada heartbeat occurrence'ını dosya:satır + bağlam ile çıkar. CLOB 5s coroutine yoksa ekle.

### 3.3 Reference Price Feed — ⚠️ DOĞRULAMA GEREKLİ
**Docs:**
- Hourly BTC Up/Down → Binance BTC/USDT 1H candle.
- 15m / 5m → Chainlink BTC/USD Data Stream (sponsored, Polymarket'in resolution oracle'ı).

**Bot durumu:** 
- `data/`, `core/`, `indicators/` altında `binance|coinbase|coingecko|chainlink` grep → sadece `data/archive/ob_snapshots_*.parquet` eşleşmesi (eski snapshot dosyaları).
- Memory'de heartbeat banner'da `bnc=$X` Binance gözüküyor.
- **Signal pipeline'ın hangi feed'i kullandığı kod tarafında belirsiz.**

**Aksiyon (P0.3):** `data/odds_feed.py` + `engine_signals.py` + `indicators/` taranır. Eğer CoinGecko/Coinbase/dahili kaynak ise → resolution divergence ölçülür (geçmiş 30 gün on-chain trade ile karşılaştırma).

### 3.4 Diğer Docs Doğruluk Kontrolü
| Docs maddesi | Bot durumu | ✅/⚠️/❌ |
|---|---|---|
| Crypto fee `C × 0.072 × p × (1-p)`, peak $1.80 | `core/fees_v2.py` bit-identical | ✅ |
| 3 signature types (0/1/2) | `signature_type=2` GNOSIS_SAFE | ✅ |
| Tick size + neg_risk per market | Phase A `options_dict` | ✅ |
| GTC/GTD/FOK/FAK + post-only | OrderType.FOK explicit, post-only kullanım belirsiz | ⚠️ |
| Min order $5 | Bot çoğunlukla $1 — config var | ⚠️ |
| Geopolitics %0 fee fırsatı | Henüz kullanılmıyor (P3.1) | 📋 |
| Rate limits POST /order 3500/10s | Phase D Bulgu 8 backlog | ⏳ |
| Allowance 5 approval (CTF + NegRisk) | Aşama 1+2 ile kısmi (SDK update_balance_allowance), 5'in hepsi doğrulanmadı | ⚠️ |
| Float vs Decimal | Belirsiz, bulgu listesinde audit edilecek | ⚠️ |
| HTTP 425 restart window handling | Belirsiz | ⚠️ |

---

## 4 — MEGA PROMPT 10-KATMAN AUDIT'İ (Sentez + Mevcut Durum)

Her layer için: **Hedef** + **Mevcut durum** + **Aksiyon (P-madde haritası)**.

### LAYER 1 — Authentication & API Credentials

**Hedef:** Auth flow `birebir docs uyumlu` mu?

**Mevcut durum:**
- ✅ Phase A: `signature_type=2` GNOSIS_SAFE + `funder=0xA7e758...BAAa` proxy DOĞRU.
- ✅ `client.create_or_derive_api_creds()` çağrısı yapılıyor (idempotent — derive önce, yoksa create).
- ✅ POLY_ADDRESS / POLY_SIGNATURE / POLY_TIMESTAMP / POLY_API_KEY / POLY_PASSPHRASE header'ları SDK içinde yönetiliyor.
- ⚠️ L1/L2/L3 ayrımı kodda explicit değil — SDK içinde abstract.
- ⚠️ API key rotation mekanizması yok (P3 öneri).

**Aksiyon:** Mega Audit Phase C-L1 → bulgu yazılır, P0.1 (V2 SDK upgrade) + P3 (key rotation) listesi.

### LAYER 2 — CLOB Order Lifecycle

**Hedef:** Order create/cancel/replace flow docs ile uyumlu mu?

**Mevcut durum:**
- ✅ Phase B: `OrderType.FOK` explicit kullanım.
- ✅ EIP-712 struct hash SDK delegasyonu (kendi imzalayıcı yazılmamış).
- ✅ `signature_type=2` set ediliyor.
- ⚠️ Post-only flag kullanımı belirsiz (taker fee öderken maker rebate kaçırma riski).
- ⚠️ FOK reject senaryosunda `result['size_matched']` kontrolü belirsiz.
- ⚠️ Self-trade prevention paper engine'de var mı?
- ⚠️ Tick size & min order size strateji guard'ları biliyor mu?

**Aksiyon:** Mega Audit Phase C-L2 → bulgu yazılır, P1.6 (taker/maker karar matrisi) + P0.10 (per-trade hard cap) listesi.

### LAYER 3 — Market Data (Gamma + CLOB REST)

**Hedef:** Market discovery, price polling, orderbook fetching docs uyumlu mu?

**Mevcut durum:**
- ✅ Endpoint URL'leri:
  - Gamma: `https://gamma-api.polymarket.com/markets`, `/events`
  - CLOB: `https://clob.polymarket.com/markets`, `/book`, `/midpoint`, `/price`, `/spread`, `/trades`
  - Data API: `https://data-api.polymarket.com/positions`, `/holdings`
- ⚠️ Pagination cursor-based mı offset-based mı — taranmamış.
- ⚠️ Crypto Up/Down filter logic (`tags`/`category`/`slug`) docs uyumlu mu — taranmamış.
- ⚠️ `closed` / `archived` / `enable_order_book` field parse — taranmamış.

**Aksiyon:** Mega Audit Phase C-L3 → bulgu yazılır, P0.4 (strategy pruning sırasında kullanılır) + P1.6 (executor abstraction) listesi.

### LAYER 4 — WebSocket Feeds

**Hedef:** WS subscription pattern + reconnect/heartbeat doğru mu?

**Mevcut durum:**
- ✅ Endpoint: `wss://ws-subscriptions-clob.polymarket.com/ws/<channel>`.
- ✅ T5.4 + T5.6 reconnect doctrine.
- ✅ T9.8 WS reconnect scenario integration test.
- ⚠️ `tick_size_change` mesaj handler eksik mi?
- ⚠️ Backpressure handling (drop/queue/panic).
- ⚠️ Phase 33 orderbook depth fusion sinyali WS'den mi REST'ten mi besleniyor?

**Aksiyon:** Mega Audit Phase C-L4 → bulgu yazılır, P0.2 (heartbeat) + Phase D Bulgu 8 (rate limits) listesi.

### LAYER 5 — Fee Structure & P&L Accounting

**Hedef:** Post-Jan 2026 fee reform sonrası fee modeli doğru mu?

**Mevcut durum:**
- ✅ `core/fees_v2.py` SINGLE oracle bit-identical docs.
- ✅ FAZ 0.1 audit closure.
- ✅ Maker rebate %20.
- ⚠️ Paper P&L vs Real P&L delta — T4.6-B sweep paper×0.66≈live → P0.7 fill heuristic recalibration.
- ⚠️ Zone analysis fee-aware mı fee-naive mi (memory'de "Zone 35-50c karlı, Zone 50-65c kayıp" — fee dahil tekrar hesaplandığında değişebilir, P2 flag).

**Aksiyon:** Mega Audit Phase C-L5 → bulgu yazılır, P0.7 + P2.x flag listesi.

### LAYER 6 — Oracle, Resolution, UMA

**Hedef:** Market resolution mekanizması bot'un anladığı şekilde mi?

**Mevcut durum:**
- ⚠️ `endDate` vs `gameStartTime` vs `closedTime` field anlamları kodda explicit değil.
- ⚠️ UMA dispute window crypto Up/Down için kaç saat? Bot pozisyon açmamalı/kapatmamalı mı?
- ⚠️ Resolved market YES=$1, NO=$0 redemption PnL hesabında doğru mu?
- ⚠️ `enable_order_book = false` veya `closed = true` markete bot order atıyor mu?

**Aksiyon:** Mega Audit Phase C-L6 → bulgu yazılır. P0.3 (reference price feed) ile bağlantılı (resolution oracle = Binance/Chainlink).

### LAYER 7 — Rate Limits & Throttling

**Hedef:** Bot Polymarket rate limitlerini aşıyor mu?

**Mevcut durum:**
- ⏳ Phase D Bulgu 8 backlog (Gamma bulk RL).
- Docs (Q-search):
  - POST /order 3500/10s burst, 36000/10min sustained
  - DELETE /order 3000/10s, 30000/10min
  - GET /book 1500/10s
  - GET /price 1500/10s
  - Gamma /markets 300/10s
  - Data API /trades 200/10s
- ⚠️ Per-endpoint limit kodda biliniyor mu yoksa generic mi?
- ⚠️ 429 backoff exponential mi?
- ⚠️ Multi-asset paralel scan limit aşıyor mu?

**Aksiyon:** Mega Audit Phase C-L7 → bulgu yazılır, Phase D Bulgu 8 kapatılır.

### LAYER 8 — Error Codes & Exception Handling

**Hedef:** Polymarket error code'lar gracefully handle ediliyor mu?

**Mevcut durum:**
- ✅ T7.6 Aşama A+B+C bare-except narrow (37+23+146 narrow).
- ✅ T11.6 exception render policy.
- ⏳ Phase D Bulgu 11 backlog (error code mapping).
- ⚠️ Specific Polymarket errors: INVALID_ORDER_MIN_TICK_SIZE / INVALID_POST_ONLY_ORDER / FOK_ORDER_NOT_FILLED / 15+ kod.
- ⚠️ Idempotency: Aynı order iki kere gönderilirse Polymarket ne döner?
- ⚠️ HTTP 425 (Too Early) restart window handling.

**Aksiyon:** Mega Audit Phase C-L8 → bulgu yazılır, P2.2 error mapping kapatılır.

### LAYER 9 — Token / Currency / Contract Addresses

**Hedef:** Smart contract address'leri ve currency handling doğru mu?

**Mevcut durum:**
- ✅ Phase C: pUSD-aware (V2 collateral migration).
- ⚠️ V1'de USDC.e bulunan kod izleri kalmış olabilir → grep kontrol.
- ⚠️ Exchange / NegRiskExchange / NegRiskAdapter / CTF contract address'leri kodda hardcoded mı yoksa SDK içinde mi?
- ⚠️ USDC = 6 decimal, condition tokens = 6 decimal. Float vs Decimal kullanımı (docs: "float = INVALID_SIGNATURE riski").
- ⚠️ Polygon RPC endpoint (free public mı Alchemy/Infura mı?).

**Aksiyon:** Mega Audit Phase C-L9 → bulgu yazılır, P0.5 (allowance pre-flight 5 approval audit) + Float→Decimal narrow.

### LAYER 10 — Paper Trading Fidelity (BONUS — KRİTİK)

**Hedef:** Paper engine'in real CLOB davranışına ne kadar yakın? Live shadow ile delta nedir?

**Mevcut durum:**
- ✅ T4.6-B sweep: paper×0.66≈live, FILL_SPREAD_COST 0.005→0.023 öneriliyor.
- ⏳ T4.7-C config update backlog (P0.7 ile kapatılacak).
- ⚠️ Fill probability model orderbook depth + price aggressiveness'a bağlı mı?
- ⚠️ Latency modeling (200ms-2s)?
- ⚠️ Adverse selection (sniped maker)?
- ⚠️ Queue position approximation?
- ⚠️ Spread crossing (midpoint dolma yanılsaması)?

**Aksiyon:** Mega Audit Phase C-L10 → bulgu yazılır, P0.7 (fill heuristic recalibration) + P0.6 (walk-forward backtest slippage model) + P1.8 (executor abstraction = paper-live aynı path).

---

## 5 — 5AI YOL HARİTASI P0/P1/P2/P3 (Sentez + Layer Map)

### 5.1 P0 — MAINNET'E DOKUNMADAN ÖNCE ZORUNLU (ilk 14 gün)

| ID | Madde | Layer | Status | Notlar |
|---|---|---|---|---|
| P0.1 | SDK V2 doğrulama | L1, L2 | ✅ KAPALI 2026-04-30 | V1 0.34.6 mainnet için yeterli (Phase A+B+C smoke PASS, EIP-712 SDK delegasyonu, builder code native attach). V2 migration P1.9 backlog. Audit: `docs/audits/sdk_v2_migration_check_2026_05.md` |
| P0.2 | Heartbeat coroutine 5s | L4 | ✅ KAPALI 2026-04-30 | Bot FOK-only (`core/live_trader.py:568`), Phase C Bulgu 5 post-order heartbeat ekli (`live_trader.py:579`) — 5s coroutine FOK için gereksiz. P1.6 (post-only GTC) eklenirse P1.6.1 (coroutine) ZORUNLU. Audit: `docs/audits/heartbeat_audit_2026_05.md` |
| P0.3 | Reference price feed Binance/Chainlink | L3, L6 | ✅ KAPALI 2026-04-30 | Bot Binance REST kullanıyor (5m markets ✅ Polymarket Binance source ile uyumlu). 15m markets Chainlink Data Stream eksikliği → divergence riski (5-50 bps). Heddas direktifi "en güncel ol" → P0.12 (Chainlink RTDS subscribe) yeni P0. Audit: `docs/audits/price_feed_divergence_2026_05.md` |
| P0.4 | Strategy pruning 18 → 3 | L10 | ⏳ AÇIK | _archive/strategies_pre_pruning_2026_05/, Sharpe>1.2 + PF>1.3 filter |
| P0.5 | Allowance pre-flight 5 approval | L9 | ⚠️ KISMİ | Aşama 1+2 SDK update_balance_allowance ile kısmen, 5 approval doğrulaması açık |
| P0.6 | Walk-forward backtest + slippage | L10 | ❌ YOK | Hyperopt silindi, walk-forward eklenmedi |
| P0.7 | Fill heuristic recalibration (T4.7-C) | L5, L10 | ⏳ AÇIK | T4.6-B sweep'in çıkardığı config update + haftalık cron job |
| P0.8 | Daily/weekly drawdown kill-switch | L8 | ⚠️ KISMİ | PNL_PAUSE pause-only, halt eksik |
| P0.9 | DRY_RUN default ON | L1 | ⚠️ KISMİ | Aşama 3.A+3.B mode toggle ✅, default ON doğrulanmalı |
| P0.10 | Per-trade hard caps | L2 | ❌ YOK | telegram_bot/handlers/order_validator.py |
| **P0.11** | **py-clob-client V1 → V2 migration** | **L1, L2** | ✅ **TAMAMLANDI 2026-04-30 13:28 UTC** | requirements.txt + 5 dosya × 12 import block + 5 method rename (`_creds` → `_key`). **Canlı doğrulama:** `Live Trader STANDBY \| auth=✅ \| Budget $1.49`. V2 SDK auth verify PASS, EIP-712 v2 domain çalışıyor, Phase A creds (498bde4b...) V2 backward compat. Cloudflare 403 (initial derive only) cosmetic, fallback stored creds geçti. P1 backlog: UA override polish. Audit: `docs/audits/sdk_v2_migration_apply_2026_05.md` |
| **P0.12** | **Polymarket RTDS Chainlink subscribe (15m parity)** | **L3, L4, L6** | ✅ SANDBOX APPLY DONE 2026-04-30 + ⏳ Heddas yerel | `data/polymarket_rtds.py` 304 satır yazıldı: WS wss://ws-live-data.polymarket.com, Binance topic (5m parity) + Chainlink topic (15m sponsored), heartbeat 5s, reconnect chain T11.8-B doctrine, freshness 30s. `get_price(asset, source)` + `get_price_15m` (Chainlink öncelik) + `get_price_5m` (Binance öncelik). Audit: `docs/audits/rtds_chainlink_subscribe_2026_05.md`. Heddas: sponsored Chainlink API key Polymarket form + P1.10 boot integration. |

### 5.2 P1 — İlk 30 Gün (P0 Geçtikten Sonra)

| ID | Madde | Layer | Status | Notlar |
|---|---|---|---|---|
| P1.1 | Linux/Docker desteği | — | ❌ YOK | Dockerfile multi-stage + docker-compose + systemd unit |
| P1.2 | core/ → 3 modül refactor | — | ❌ YOK | signal_engine + execution_engine + risk_engine, AI Brain ayrı microservice |
| P1.3 | Test coverage 21% → 60% | — | ⏳ AÇIK | Critical path + 3-seed + CI gate |
| P1.4 | Reconciliation loop | L9 | ❌ YOK | 5dk Polygon RPC CTF balanceOf vs DB, mismatch>$1 halt |
| P1.5 | .env 100+ → 25 | — | ❌ YOK | Audit + whitelist + array consolidation |
| P1.6 | Taker/maker karar matrisi | L2 | ⏳ AÇIK | Phase D Bulgu 10. Spread>2tick post-only GTC, <2tick FOK |
| P1.7 | Structured logging + secret scrubbing | L8 | ⏳ KISMİ | T10.8 13 regex audit ✅, loguru/structlog migrate aç |
| P1.8 | Executor abstraction (paper=live aynı path) | L10 | ❌ YOK | LiveExecutor + PaperExecutor common interface |
| ~~P1.9~~ | ~~py-clob-client V1 → V2 migration~~ | L1, L2 | 🔄 **P0.11'e yükseltildi** (Heddas direktifi 2026-04-30 "en güncel ol") | — |
| P1.6.1 | Heartbeat 5s coroutine (P1.6 öncesi ZORUNLU) | L4 | 📋 BACKLOG | core/heartbeat.py yeni dosya, async loop 5s + ID rotation + 400 retry + graceful shutdown. P0.2 closure'da açıldı. ETA: 2h. P1.6 (post-only GTC) öncesi implement |

### 5.3 P2 — 30-90 Gün (SaaS Pivot Hazırlığı)

| ID | Madde | Layer | Status | Notlar |
|---|---|---|---|---|
| P2.1 | Multi-user + lisans | — | ❌ YOK | DB users, /redeem <key>, 3 tier (Starter $9 / Trader $29 / Pro $79) |
| P2.2 | Polymarket error code mapping | L8 | ⏳ AÇIK | Phase D Bulgu 11. 15+ error code TR+EN message + auto-resolution suggestion |
| P2.3 | Status polling refinement | L8 | ⏳ AÇIK | Phase D Bulgu 12. Exp backoff 5→10→30→60s |
| P2.4 | Web dashboard MVP | — | ❌ YOK | Streamlit/React, public read-only PnL link |
| P2.5 | Stripe + Coingate ödeme | — | ❌ YOK | TR-friendly kripto entegrasyonu |
| P2.6 | Affiliate program | — | ❌ YOK | %20 lifetime commission |

### 5.4 P3 — 90+ Gün (Ölçek + Diversifikasyon)

| ID | Madde | Layer | Status | Notlar |
|---|---|---|---|---|
| P3.1 | Multi-asset (Geopolitics %0 fee) | L5 | ❌ YOK | Politics/Sports/Finance kategorileri |
| P3.2 | Multi-venue (Kalshi) | — | ❌ YOK | US-only, regülasyon karmaşası |
| P3.3 | Public API (Pro tier) | — | ❌ YOK | $99/ay tier, programatik erişim |
| P3.4 | White-label lisans | — | ❌ YOK | $500-2000 setup + %20 monthly revshare |

---

## 6 — 30/60/90/180 GÜN TAKVİMİ (Yol Haritası §6)

### Hafta 1 (1-7 Mayıs 2026) — P0 Sprint A
- [ ] P0.1 SDK V2 doğrulama → karar (V1 ya da V2 migrate)
- [ ] P0.2 Heartbeat coroutine eklendi/doğrulandı
- [ ] P0.3 Reference price feed (Binance hourly + Chainlink 15m)
- [ ] P0.5 Allowance pre-flight check
- **Bot durumu sonunda:** PAPER MODE, mainnet kilitli

### Hafta 2 (8-14 Mayıs) — P0 Sprint B
- [ ] P0.4 Strategy pruning 18 → 3
- [ ] P0.6 Walk-forward backtest implementation
- [ ] P0.7 Fill heuristic recalibration (T4.7-C kapatma)
- [ ] P0.8 Drawdown kill-switch
- **Bot durumu sonunda:** 3 strateji aktif, walk-forward gösteriyor

### Hafta 3-4 (15-30 Mayıs) — $20 Live Mikro Test
- [ ] P0.9 + P0.10 hard cap'ler aktif
- [ ] $20 deposit, sadece $5 emirler
- [ ] Amaç: paper P&L vs live P&L sapması <%10
- [ ] Reconciliation loop (P1.4) eklendi
- **Karar noktası:** Sapma <%10 → Hafta 5'e geç. Değilse simülasyon bozuk → fix önce.

### Ay 2 (Haziran) — Linux + Refactor
- [ ] P1.1 Docker + Linux deployment
- [ ] P1.2 core/ 3'e bölme
- [ ] P1.3 Test coverage 60%+
- [ ] P1.5 .env cleanup
- [ ] P1.6 Taker/maker clarity
- [ ] P1.7 Logging
- **$100 Promotion Karar:** ≥200 trade, PnL≥+%5, Sharpe>1, DD<%15 hepsi tutuyorsa $100'e çık.

### Ay 3 (Temmuz) — SaaS Hazırlık
- [ ] P2.1 Multi-user + lisans
- [ ] P2.2/2.3 Phase D Bulgu 11+12
- [ ] P2.4 Web dashboard MVP
- **$500 Promotion Karar:** 1000+ trade, Sharpe>1.2, 3 ay üst üste pozitif, mismatch<%1.

### Ay 4-6 (Ağustos-Ekim) — SaaS Lansman
- [ ] P2.5 Stripe/Coingate ödeme
- [ ] P2.6 Affiliate program
- [ ] Pazarlama (Reddit, X, Discord, Telegram)
- [ ] İlk 10 ödeme yapan müşteri
- **Hedef:** $500-1000 MRR

### Ay 7-12 — Ölçek (P3)
- Multi-asset, multi-venue, public API, white-label
- **Hedef:** $3000+ MRR

---

## 7 — MAINNET GO/NO-GO GATE'LERİ (Yol Haritası §7)

### 7.1 Pre-Mainnet Gate (P0 maddelerinin tamamlanması) — 10 Koşul

| # | Koşul | Status |
|---|---|---|
| 1 | SDK V2 (gerekiyorsa) | ⏳ |
| 2 | Heartbeat 5s aktif | ⏳ |
| 3 | Reference price feed Binance/Chainlink | ⏳ |
| 4 | Strategy pruning 18→3 | ⏳ |
| 5 | Allowance pre-flight | ⏳ |
| 6 | Walk-forward backtest + slippage | ⏳ |
| 7 | Drawdown kill-switch | ⏳ |
| 8 | DRY_RUN default | ⏳ |
| 9 | MAX_ORDER_USD=10 hard cap | ⏳ |
| 10 | Reconciliation loop (P1.4) | ⏳ |

### 7.2 $20 → $100 Promotion Gate

| # | Koşul | Threshold |
|---|---|---|
| 1 | Live trade sayısı | ≥200 |
| 2 | Paper vs live PnL sapması | <%10 |
| 3 | Net PnL | ≥+%5 |
| 4 | Sharpe | >1.0 |
| 5 | Max DD | <%15 |
| 6 | Reconciliation mismatch | 0 |
| 7 | Heartbeat downtime | <0.5% |
| 8 | Order reject rate | <%2 |

### 7.3 $100 → $500 Promotion Gate

| # | Koşul | Threshold |
|---|---|---|
| 1 | Live trade sayısı | ≥1000 |
| 2 | Üç ay üst üste pozitif | yes |
| 3 | Sharpe (90 gün) | >1.2 |
| 4 | Max DD | <%12 |
| 5 | Profit factor | >1.4 |
| 6 | Ops mismatch oranı | <%1 |

### 7.4 SaaS Pivot Gate (Sermaye yerine ürün) — Alternatif

Hafta 4'te $20 mikro test "para kazandırmıyor ama kararlı çalışıyor" ise, sermaye yerine ürün modeline pivot:

| # | Koşul | Threshold |
|---|---|---|
| 1 | Bot uptime | >%99.5 (30 gün) |
| 2 | Telegram UX | tek tıkla kurulum + clear UX |
| 3 | Multi-user lisans | aktif |
| 4 | Error coverage | <%1 unhandled exceptions |
| 5 | Web dashboard | live PnL public link |
| 6 | İlk 3 beta kullanıcı | "kullanışlı" |
| 7 | Yasal kontrol | TR vergi danışmanı, KVKK |

---

## 8 — RİSK REGİSTER (Yol Haritası §8)

| Risk | Olasılık | Etki | Skor | Azaltma |
|---|---|---|---|---|
| Polymarket V2 sonrası V1 SDK kırık | DÜŞÜK (sign fix yapılmış) | KRİTİK | 8/10 | P0.1 ilk gün |
| Reference price divergence | ORTA | YÜKSEK | 7/10 | P0.3 audit + Binance/Chainlink migrate |
| Bot edge yok, $1k yanıyor | YÜKSEK | YÜKSEK | 9/10 | $20 mikro-test, gate'ler |
| AI Brain Sonnet 10dk maliyeti ölçeklenirse | ORTA | ORTA | 5/10 | Tier'le, opsiyonel kapat, Llama fallback |
| Tedarik zinciri saldırısı (3rd party SDK) | DÜŞÜK | KRİTİK | 7/10 | pip-audit + checksum + isolated wallet |
| Telegram bot token sızıntısı | DÜŞÜK | YÜKSEK | 6/10 | router-level whitelist, BotFather revoke prosedürü |
| TR regülasyon (vergi/KYC) | ORTA | ORTA | 5/10 | hukuk danışmanlığı Q3 2026 |
| Off-chain ↔ on-chain sync exploit | DÜŞÜK | KRİTİK | 7/10 | reconciliation loop her 5 dk (P1.4) |
| Backtest fake edge → live yanma | ORTA | KRİTİK | 8/10 | walk-forward + paper×live gap ölçümü |
| Anlık $355 PnL = sample size küçük | KESİN | ORTA | 6/10 | 1000+ trade'a kadar conclusion verme |

---

## 9 — MEGA AUDIT DELIVERABLES (Mega Prompt §4)

Audit fazlarının çıktıları `audit_phase_polymarket_compliance/` altında üretilecek:

```
audit_phase_polymarket_compliance/
├── 00_EXECUTIVE_SUMMARY.md              # 2 sayfa, yönetici özeti
├── 01_POLYMARKET_COMPLIANCE_AUDIT.md    # Ana rapor (~3000 satır)
├── 02_DOCS_DELTA_REPORT.md              # Her bulgu detaylı
├── 03_REFACTOR_ROADMAP.md               # Sprint planı (bu dosya ile bağ)
├── 04_RISK_REGISTER.md                  # P0-P3 risk listesi (bu dosya §8 ile bağ)
├── 05_API_SURFACE_INVENTORY.md          # Hangi endpoint hangi dosyada
├── 06_CONTRACT_ADDRESS_AUDIT.md         # Smart contract address doğrulama
├── 07_FEE_MODEL_VALIDATION.md           # Fee math, real vs paper
├── 08_PAPER_VS_LIVE_FIDELITY_GAP.md     # Layer 10 deep dive
├── 09_TEST_PLAN.md                      # Refactor sonrası test stratejisi
├── 10_MIGRATION_NOTES.md                # Backward compat
├── docs_cache/                          # 40 mandatory query cache
│   ├── INDEX.md
│   ├── Q01_clob_auth.md
│   └── ... (Q01-Q40)
│   └── full_pages/
├── code_patches/
│   ├── analysis/
│   │   ├── file_inventory.csv
│   │   ├── api_surface.csv
│   │   ├── grep_results.txt
│   │   └── dependency_graph.dot
│   ├── proposed/                        # Hazır .patch dosyaları
│   ├── snippets/
│   └── MANUAL_REVIEW_REQUIRED.md
└── self_check/
    ├── CHECKLIST.md                     # 50+ madde
    └── COMPLETENESS_SCORE.md            # ≥80/100 hedef
```

**Teslim raporu:** `_TESLIM_RAPORU_TR.md` proje root'unda Türkçe.

---

## 10 — EXECUTION ORDER (TASK SIRASI)

İn-conversation TaskList'te (cowork widget) görünen sıra:

1. **#1 Master TASKS.md sentezi** ⏳ (bu doküman + TASKS.md Epic 12)
2. **#2 P0.1 SDK V2 doğrulama** — kritik, ilk iş
3. **#3 P0.2 Heartbeat coroutine**
4. **#4 P0.3 Reference price feed**
5. **#5 P0.5 Allowance pre-flight**
6. **#6 P0.4 Strategy pruning**
7. **#7 P0.6 Walk-forward + slippage**
8. **#8 P0.8 Kill-switch**
9. **#9 Mega Audit Phase C — 10 layer analiz**
10. **#10 Mega Audit Phase D-G — synthesis + patches + teslim**
11. **#11 P0.7 Fill heuristic recalibration**
12. **#12 Mega Audit Phase A — Recon**
13. **#13 P0.9 DRY_RUN default**
14. **#14 Mega Audit Phase B — Docs cache 40 query**
15. **#15 P0.10 Per-trade hard caps**
16. **#16 P1.1-P1.8 30-gün paketi**
17. **#17 P2.1-P2.6 SaaS hazırlık**
18. **#18 Mainnet Gate doğrulaması**
19. **#19 Final verification (pytest + lint + docs sync)**

**Her task tamamlandığında:**
1. `TaskUpdate` ile in_progress → completed
2. `TASKS.md` Epic 12 altındaki ilgili madde ✅ + commit hash + tarih
3. Bu dosya (MASTER_PLAN_2026_04_30.md) §3 (V2 gerçeklik) ve §5 (P0/P1/P2/P3 tablosu) güncellenir
4. Memory landmark eklenir (yeni `project_*.md`)
5. Git commit (atomic, conventional commits)

---

## 11 — KAPANIŞ NOTU

5 farklı AI'nin ortak hükmü: **bot teknik olarak güzel, ürün olarak kanıtlanmamış, ekonomik olarak henüz pozitif değil.**

Üç olası sonuç:
1. **En olası (~%70):** 30 gün P0 + walk-forward sonrası edge kanıtlanmaz → SaaS pivot. Yıllık $5-15k MRR potansiyeli.
2. **Orta (~%25):** Edge zayıf ama pozitif (Sharpe ~0.8-1.2). Hibrit (bot + SaaS). Yıllık $10-30k.
3. **Düşük (~%5):** Gerçek edge (Sharpe>1.5). Sermaye ölçeklenir + SaaS. $1k → $10k → $50k.

**Tüm üç senaryoda P0 maddelerini hemen kapatmak ve $20 mikro-testi yapmak ZORUNLU.**

---

**Sonraki Review:** 2026-05-30 (Hafta 4 mikro-test sonu)
**Son güncelleme:** 2026-04-30 (initial sentez)
