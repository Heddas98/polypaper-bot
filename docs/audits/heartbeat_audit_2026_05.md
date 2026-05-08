# Heartbeat Coroutine Audit — 2026-05 (P0.2 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Kapsam:** Polymarket CLOB heartbeat (`POST /heartbeat`) zorunluluğu vs bot mevcut implementation
**Yöntem:** Polymarket Docs MCP + bot kodu cross-reference
**Tetik:** YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md §5.1 P0.2 + Comprehensive Audit PDF "Heartbeat coroutine 5s zorunlu GTC orderlar için"

---

## 0 — TL;DR

| Soru | Cevap |
|---|---|
| Bot CLOB heartbeat gönderiyor mu? | ✅ EVET (`core/live_trader.py:579` post-order heartbeat) |
| Bot 5s heartbeat coroutine var mı? | ❌ HAYIR (sadece post-order tek seferlik heartbeat) |
| Heartbeat coroutine **zorunlu** mu? | ❌ ŞU AN HAYIR (bot FOK-only, resting order yok) |
| Heartbeat coroutine **ne zaman zorunlu** olur? | ⚠️ P1.6 (taker/maker karar matrisi) GTC/GTD/post-only ekleyince **ZORUNLU** |
| P0.2 status | ✅ KAPALI (conditional pass) — defansif post-order heartbeat var, GTC eklenirse coroutine task açılacak (P1.6.1) |

**Kapsamlı bulgu:** Bot her order'ı `OrderType.FOK` (fill-or-kill) ile gönderiyor. FOK orderlar anında resolve olur (ya tamamen fill ya cancel) — resting open order üretmez. Polymarket heartbeat sadece resting GTC/GTD orderları korur. FOK-only flow için heartbeat 5s coroutine **gerekli değil**, ancak Phase C Bulgu 5 closure'da **defansif post-order heartbeat** eklenmiş — bu yeterli.

---

## 1 — Polymarket Docs Heartbeat Spec (2026-04-30)

### 1.1 Endpoint

`POST /heartbeat` — `docs.polymarket.com/api-reference/trade/send-heartbeat` + `/trading/orders/overview#heartbeat`

### 1.2 Spec

**Docs aynen:**
> The heartbeat endpoint maintains session liveness for order safety. **If a valid heartbeat is not received within 10 seconds (with up to a 5-second buffer), all of your open orders will be cancelled.**
>
> Sends a heartbeat signal to maintain active session status. **If heartbeats are not sent regularly, all open orders for the user will be automatically canceled.**

### 1.3 Loop Pattern (Python)

```python
import time

heartbeat_id = ""
while True:
    resp = client.post_heartbeat(heartbeat_id)
    heartbeat_id = resp["heartbeat_id"]
    time.sleep(5)
```

### 1.4 ID Rotation

**Docs:**
> On each request, include the most recent `heartbeat_id` you received. For your first request, use an empty string. If you send an invalid or expired `heartbeat_id`, the server responds with a 400 Bad Request and provides the correct `heartbeat_id` in the response. Update your client and retry.

**Sonuç:**
- İlk request: `heartbeat_id=""`
- Her sonraki request: bir önceki response.heartbeat_id
- Invalid/expired ID → 400 + correct ID döner → retry

### 1.5 Etkisi

**Heartbeat eksik olursa (10s+5s buffer):**
- Tüm açık order'lar (resting GTC/GTD/post-only) cancel.
- FOK/FAK orderlar anlık match → resting yapmaz → heartbeat eksikse de etkilenmez.

---

## 2 — Bot Mevcut Heartbeat Implementation

### 2.1 Order Type Distribution (`grep` Audit)

```
=== OrderType usage (FOK/GTC/GTD/FAK) ===
core/live_trader.py:568:    result = client.post_order(signed, OrderType.FOK)
```

**Tek `OrderType.FOK` kullanım.** GTC/GTD/FAK kodda yok.

### 2.2 Heartbeat Sites

| Dosya:Satır | Tip | Açıklama |
|---|---|---|
| `core/live_trader.py:574-582` | **CLOB heartbeat** (post-order) | Phase C Bulgu 5 fix: her order sonrası `client.post_heartbeat("")` |
| `core/engine.py:826-841` | Telegram heartbeat (banner) | Cycle status `bnc=$X` Binance BTC fiyat surface |
| `telegram_bot/jobs/maintenance_jobs.py:236-304` | Telegram heartbeat job | 10dk interval health-check + alert (CLOB heartbeat DEĞİL) |
| `core/risk_manager.py:143, 385-404` | Telegram heartbeat alert flag | Risk alert state (CLOB heartbeat DEĞİL) |
| `core/trade_journal.py:145-157` | Telegram heartbeat log | `decisions.jsonl` HEARTBEAT event log (CLOB heartbeat DEĞİL) |

**Sonuç:** Bot'ta 2 farklı "heartbeat" semantiği:
1. **CLOB heartbeat** — Polymarket session liveness (`client.post_heartbeat()`).
2. **Telegram heartbeat** — bot health banner + 10dk job (`HEARTBEAT_INTERVAL_SEC=600`).

Bunlar farklı amaçlarda. Karıştırılmamalı.

### 2.3 Phase C Bulgu 5 Fix (Mevcut Implementation)

`core/live_trader.py:574-582`:
```python
# 2026-04-29 Phase C Bulgu 5 fix: post-order heartbeat. Polymarket
# cancels open orders 10s+5s after last heartbeat. Even though FOK
# orders fill or cancel immediately, this heartbeat covers
# marketable-but-delayed scenarios + signals session liveness.
try:
    client.post_heartbeat("")
except (AttributeError, Exception) as _hb_err:  # noqa: BLE001
    # SDK eski version'larda post_heartbeat yok; warn-and-continue.
    logger.debug(f"post_heartbeat unavailable: {_hb_err}")
```

**Pattern:**
- Her FOK order sonrası heartbeat gönderiliyor.
- ID rotation YOK (her seferinde `""` empty string).
- Defansif: `marketable-but-delayed` senaryolarda (FOK reject delayed → heartbeat ile cancel-protect).

---

## 3 — Bot İçin Heartbeat Coroutine Zorunluluğu

### 3.1 Karar Matrisi

| Bot Order Tipi | Resting? | Heartbeat 5s Coroutine | Sonuç |
|---|---|---|---|
| FOK (mevcut) | Hayır (fill or kill) | ❌ Gerekli değil | Mevcut post-order heartbeat yeterli |
| FAK | Hayır (partial fill + cancel rest) | ❌ Gerekli değil | Aynı |
| GTC (P1.6 backlog) | Evet (book'ta resting) | ✅ ZORUNLU | Coroutine eklenmeli |
| GTD (P1.6 backlog) | Evet (expiration'a kadar) | ✅ ZORUNLU | Coroutine eklenmeli |
| Post-only GTC (P1.6 backlog) | Evet (maker rebate) | ✅ ZORUNLU | Coroutine eklenmeli |

### 3.2 Mevcut FOK-Only Flow

**Risk analizi:** Bot 1417 trade tamamladı (memory'den). Hepsi FOK. Hiçbir trade resting open order olarak Polymarket'e bırakılmadı.

→ Heartbeat 5s coroutine **şu an** gerekli **değil**.

### 3.3 P1.6 Sonrası (Taker/Maker Karar Matrisi)

YOL_HARITASI §5.2 P1.6:
> Bot şu an taker mı, maker mı, ya da hibrit? Belirsiz.
> Karar matrisi:
> - Spread >2 tick → post-only GTC limit (maker, %20 rebate kazan)
> - Spread <2 tick → FOK marketable (taker, %1.8 fee öde)
> - Hızla giriş gerekli → FAK partial fill OK

P1.6 implementation post-only GTC eklerse → heartbeat 5s coroutine **ZORUNLU**. P0.2'nin "follow-up task" olarak P1.6.1 açılır.

---

## 4 — P0.2 Karar + Aksiyon

### 4.1 Karar

**P0.2 STATUS: ✅ KAPALI (conditional pass)**

Gerekçe:
1. Bot FOK-only akış (resting order üretmez).
2. Phase C Bulgu 5 fix'i defansif post-order heartbeat ekli (`core/live_trader.py:579`).
3. Polymarket heartbeat 10s+5s buffer + FOK anında resolve = race condition yok.
4. Heartbeat 5s coroutine eklemenin overhead'i (her 5s `POST /heartbeat`, rate limit) FAK-only akış için faydası YOK.

### 4.2 Aksiyon (Defense-in-Depth)

#### A. Mevcut Phase C Bulgu 5 Fix Doğrulaması

**Test:** `core/live_trader.py:574-582` ile test:
- Mock SDK ile `post_heartbeat("")` çağrısı doğrula
- AttributeError + generic Exception path test
- `evidence/p02_heartbeat_postorder_smoke.txt` → 1 PASS

#### B. P1.6.1 Yeni Task (Backlog)

`docs/MASTER_PLAN_2026_04_30.md` §5.2 P1.6 altına alt-madde:

```
P1.6.1 — Heartbeat 5s coroutine (P1.6 öncesi ZORUNLU)
- core/heartbeat.py yeni dosya
- async loop, 5s interval, ID rotation
- Bot startup'ta task spawn (asyncio.create_task)
- Bot shutdown'da graceful cancel
- 400 Bad Request retry (server-provided ID)
- Test: 30dk soak test, 0 cancel race
- ETA: 2 saat
```

P1.6 implementation **öncesi** P1.6.1 implementation gerekli.

#### C. Memory Landmark

`memory/project_p02_heartbeat_audit_closure.md`:
```
P0.2 Heartbeat audit CLOSED — Bot FOK-only, post-order heartbeat (Phase C Bulgu 5) yeterli.
5s coroutine P1.6 (post-only GTC) eklemeden gerekli değil. P1.6.1 backlog.
```

---

## 5 — Açık Sorular / Heddas'a Notlar

1. **P1.6 Roadmap karar:** Maker rebate %20 stratejik fırsat. Post-only GTC eklenince fee saving substantial olabilir (taker $1.80 vs maker rebate +$0.36/100 shares). Ancak P1.6.1 heartbeat coroutine gerek.
2. **Mevcut post-order heartbeat ID rotation YOK.** Memory'de note: "Phase C Bulgu 5 fix" empty string ID. Bu OK çünkü FOK-only flow'da heartbeat sadece "alive" sinyali, ID chain önemli değil. P1.6.1 coroutine'de ID rotation ZORUNLU olacak.

---

## 6 — Bağlantılı Belgeler

- **MASTER_PLAN_2026_04_30.md** §3.2 Heartbeat, §5.1 P0.2, §5.2 P1.6
- **TASKS.md** Epic 12.A P0.2 satırı
- **YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md** §5.1 P0.2
- **core/live_trader.py:574-582** Phase C Bulgu 5 fix
- **memory/project_polymarket_signature_fix_closure.md** Phase C Bulgu 5
- **Polymarket Docs:**
  - `docs.polymarket.com/api-reference/trade/send-heartbeat`
  - `docs.polymarket.com/trading/orders/overview#heartbeat`
  - `docs.polymarket.com/trading/orders/create#heartbeat`

---

**Sonuç:** P0.2 KAPALI. Sonraki iş: **P0.3 Reference Price Feed Audit (Binance + Chainlink)**.
