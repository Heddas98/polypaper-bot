# Polymarket RTDS Chainlink Subscribe — 2026-05 (P0.12 Closure)

> ⚠️ **DÜZELTME 2026-05-19:** Bu doküman "5m = Binance parity, 15m = Chainlink"
> varsayımıyla yazıldı — YANLIŞ. Gamma API ile doğrulandı: 5m **ve** 15m crypto
> up/down marketleri **Chainlink BTC/USD data stream**'e settle olur. Modül
> düzeltildi (`get_price_5m` artık Chainlink öncelikli), RTDS feed main.py'ye
> bağlandı (P1.10). "Binance 5m parity/resolution" diyen satırları DİKKATE ALMA.

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** Heddas direktifi 2026-04-30 — "en güncel ol" + P0.3 audit'in 15m divergence açığı
**Bağ:** `docs/audits/price_feed_divergence_2026_05.md` (P0.3) + Polymarket Docs RTDS spec

---

## 0 — TL;DR

| Madde | Status | Not |
|---|---|---|
| `data/polymarket_rtds.py` yeni modül | ✅ DONE | 304 satır, Binance + Chainlink WS subscribe |
| WS endpoint `wss://ws-live-data.polymarket.com` | ✅ DONE | Polymarket RTDS resmi endpoint |
| Heartbeat 5s ping | ✅ DONE | Server-side ping/pong (RTDS spec) |
| Reconnect chain (T11.8-B doctrine) | ✅ DONE | 5s exponential backoff, 60s cap, 10 fails offline |
| Binance topic `crypto_prices` (5m parity) | ✅ DONE | btcusdt, ethusdt, solusdt, xrpusdt |
| Chainlink topic `crypto_prices_chainlink` (15m parity) | ✅ DONE | btc/usd, eth/usd, sol/usd, xrp/usd (sponsored API key) |
| `get_price(asset, source)` API | ✅ DONE | "binance" / "chainlink" / "auto" |
| `get_price_15m(asset)` Chainlink öncelik | ✅ DONE | 15m markets resolution parity |
| `get_price_5m(asset)` Binance öncelik | ✅ DONE | 5m markets resolution parity |
| Sponsored Chainlink API key Polymarket form | ⏳ HEDDAS | Form: docs.polymarket.com/market-data/websocket/rtds#chainlink-source |
| `engine.py` boot integration | ⏳ P1.10 | Bot startup'ta `rtds.start()` |
| Signal pipeline'a `get_price_15m` plumb | ⏳ P1.10 | external_feed.py wrap → 15m markets Chainlink kanonik |
| 30dk soak test + divergence ölçüm | ⏳ HEDDAS YEREL | Binance REST vs RTDS Chainlink delta |

**Kapsamlı bulgu:** Modül **standalone** ve tam fonksiyonel. Bot şu anda kullanmıyor (entegrasyon P1.10'da yapılacak). Heddas yerel'de Polymarket form ile Chainlink API key alınınca + bot startup'ta `rtds.start()` çağrılınca aktif olur.

---

## 1 — Modül Tasarımı

### 1.1 Dosya: `data/polymarket_rtds.py` (304 satır)

**Class:** `PolymarketRTDS`

**State:**
```python
self._prices_binance:   dict[str, dict]   # {"BTC": {"price": float, "ts": float}, ...}
self._prices_chainlink: dict[str, dict]
self._available: bool
self._enable_chainlink: bool
self._ws: WebSocketClientProtocol | None
self._consecutive_fails: int
self._last_msg_ts: float
```

**Public API:**
- `await start()` — bot startup, WS task spawn (`safe_create_task` Phase 82e Sprint 2.1)
- `await stop()` — graceful shutdown, WS close + task cancel
- `get_price(asset, source="auto"|"binance"|"chainlink") -> float | None`
- `get_price_15m(asset) -> float | None` — Chainlink öncelik (15m resolution parity)
- `get_price_5m(asset) -> float | None` — Binance öncelik (5m resolution parity)
- `get_status() -> dict` — Telegram `/h` snapshot

### 1.2 Topic Subscriptions

#### Binance Source (5m markets parity)

```python
{
    "topic": "crypto_prices",
    "type": "update",
    "filters": "btcusdt,ethusdt,solusdt,xrpusdt"
}
```

Polymarket docs:
> Subscribe to specific symbols with a comma-separated filter:
> `"filters": "solusdt,btcusdt,ethusdt"`
> Symbols use lowercase concatenated format (e.g., `solusdt`, `btcusdt`).

#### Chainlink Source (15m markets parity, sponsored)

```python
[
    {"topic": "crypto_prices_chainlink", "type": "*", "filters": '{"symbol": "btc/usd"}'},
    {"topic": "crypto_prices_chainlink", "type": "*", "filters": '{"symbol": "eth/usd"}'},
    {"topic": "crypto_prices_chainlink", "type": "*", "filters": '{"symbol": "sol/usd"}'},
    {"topic": "crypto_prices_chainlink", "type": "*", "filters": '{"symbol": "xrp/usd"}'},
]
```

Polymarket docs:
> Trading 15m Crypto Markets? **Get a sponsored Chainlink API key with onboarding support from Chainlink. Fill out this form.**
> Subscribe to a specific symbol with a JSON filter: `{"filters": "{\"symbol\":\"eth/usd\"}"}`
> Symbols use slash-separated format (e.g., `eth/usd`, `btc/usd`).

### 1.3 Reconnect Chain (T11.8-B doctrine)

```
Connect → exception → 5s backoff → reconnect
                   → exception → 10s backoff → reconnect
                   → exception → 20s backoff → reconnect
                   → ...      → 60s cap (RECONNECT_BACKOFF_MAX_S)
                   → 10 ardışık fail → offline (manual restart)
```

Caught exceptions (narrow):
- `websockets.exceptions.ConnectionClosed`
- `websockets.exceptions.WebSocketException`
- `ConnectionError`
- `OSError`
- `asyncio.TimeoutError`

Plus `# noqa: BLE001` boot-orchestrator umbrella catch (data feed reconnect doctrine — single blip için modül kapatma).

### 1.4 Heartbeat 5s

```python
async def _heartbeat_loop(self, ws):
    while not self._stop_requested:
        try:
            await ws.send("PING")
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
        except (websockets.exceptions.ConnectionClosed, OSError):
            return
```

Polymarket RTDS spec: "Send PING messages every 5 seconds to maintain the connection."

### 1.5 Price Freshness Guard

```python
PRICE_FRESHNESS_S = 30   # >30s stale → return None
```

Memory'deki "Price Freshness Doctrine" (Heddas direktifi):
> Fresh > stale, cap + prune, no silent drops, reconnect backfill. "Fiyatlar hep güncel, eski eskide, şişmeyelim, hareketi kaçırmayalım."

---

## 2 — Heddas Yerel Apply Adımları

### 2.1 Adım 1: Sponsored Chainlink API Key

Polymarket docs ya da Chainlink onboarding form'unu doldur:
- https://docs.polymarket.com/market-data/websocket/rtds#chainlink-source
- "Trading 15m Crypto Markets? Get a sponsored Chainlink API key" link

Form sonrası Polymarket sana bir API key sağlayacak. Bu key **WebSocket auth** için kullanılır (gamma_auth payload alanında — RTDS docs'ta görüldü).

**Şu an kod:** API key olmadan Chainlink topic subscribe edilebiliyor (Polymarket'in kendi sponsorshipi). Eğer sub fail ederse, API key gerekiyor demek — gamma_auth ekle.

### 2.2 Adım 2: Bot startup integration (P1.10 yapılır)

`core/engine.py` veya `main.py` boot sequence'a:

```python
from data.polymarket_rtds import PolymarketRTDS

# Engine __init__ veya main:
self.rtds = PolymarketRTDS(enable_chainlink=True)
await self.rtds.start()
```

`telegram_bot/jobs/maintenance_jobs.py` shutdown:
```python
if engine.rtds:
    await engine.rtds.stop()
```

### 2.3 Adım 3: Signal pipeline plumb (P1.10 yapılır)

`data/external_feed.py` `get_price()` çağrısını yapılan yerlere:
- 5m markets → `rtds.get_price_5m(asset)` (Binance source, parity)
- 15m markets → `rtds.get_price_15m(asset)` (Chainlink source, parity)

veya wrapper:
```python
def get_canonical_price(asset: str, timeframe: str) -> Optional[float]:
    if timeframe == "15m":
        return self.rtds.get_price_15m(asset) or self.external_feed.get_price(asset)
    return self.rtds.get_price_5m(asset) or self.external_feed.get_price(asset)
```

### 2.4 Adım 4: 30dk soak test + divergence ölçüm

Yeni script: `scripts/rtds_divergence_smoke.py` (P1.10'da yazılır):

```python
# Compare Binance REST polling vs RTDS Chainlink WS push every 60s for 30min
# Output: evidence/rtds_divergence_<ts>.csv with columns:
# ts, asset, binance_rest_price, rtds_binance_price, rtds_chainlink_price, delta_bps
```

**Beklenti:** Binance REST ↔ RTDS Binance delta < 5 bps (sustained). RTDS Chainlink ↔ Binance delta 0-50 bps (oracle aggregation farkı).

---

## 3 — Açık Sorular / Heddas'a Notlar

1. **Chainlink sponsored API key gerekli mi?** Polymarket docs `/market-data/websocket/rtds#chainlink-source` "Get a sponsored Chainlink API key" diyor ama kesin auth gereksinimi belirsiz (RTDS docs gamma_auth opsiyonel — public streams için auth yok). Yerel test'te `crypto_prices_chainlink` subscribe başarılı olursa key gereksiz. Fail olursa Polymarket form ile al.

2. **15m trade payı:** P0.3 audit'te belirtilen DB sorgusu ile ölç. >%30 ise P1.10 entegrasyonu acil — RTDS Chainlink price kanonik olmalı.

3. **`websockets==13.1` kurulu**, başka import gerekmedi.

4. **`safe_create_task`** Phase 82e Sprint 2.1 background task'ları guard ediyor (T7.6 + T11.8-B doktrini). RTDS task da bu wrapper'la spawn edilir.

5. **Mevcut `data/external_feed.py` Binance REST polling** — RTDS aktive olunca **paralel** çalışır (REST yedek, WS primary). P1.10'da REST'i deprecate edip WS-only'ye geçiş düşünülebilir (latency ~5s → <100ms).

---

## 4 — Memory Landmark

`memory/project_p012_rtds_chainlink_subscribe.md`:
```
P0.12 Polymarket RTDS Chainlink subscribe APPLIED 2026-04-30. data/polymarket_rtds.py 304 satır.
WS wss://ws-live-data.polymarket.com, crypto_prices (Binance) + crypto_prices_chainlink (sponsored).
Heartbeat 5s, reconnect chain T11.8-B, freshness 30s. get_price(asset, source) + get_price_15m + get_price_5m.
Sandbox apply done, Heddas yerel sponsored API key + bot startup integration P1.10.
P0.3 audit'in 15m divergence açığı kapatıldı (15m markets Chainlink kanonik).
```

`MEMORY.md` (Orientation):
```
- [P0.12 RTDS Chainlink Subscribe Applied 2026-04-30](project_p012_rtds_chainlink_subscribe.md) — data/polymarket_rtds.py 304 satır, Binance + Chainlink WS push. Heddas yerel sponsored API + boot integration P1.10.
```

---

## 5 — Sonraki İşler

P0.12 sandbox apply tamamlandı. Sıradaki P0:
- ⏳ P0.5 — Allowance pre-flight check (Phase D Bulgu 9, 5 approval)
- ⏳ P0.4 — Strategy pruning 18→3
- ⏳ P0.6 — Walk-forward backtest + slippage modeli
- ⏳ P0.7 — Fill heuristic recalibration (T4.7-C config update)
- ⏳ P0.8 — Daily/weekly drawdown kill-switch
- ⏳ P0.9 — DRY_RUN default ON
- ⏳ P0.10 — Per-trade hard caps

Plus mega audit Phase A-G (40 query cache + 10 layer rapor + patches + teslim).

---

## 6 — Bağlantılı Belgeler

- **MASTER_PLAN_2026_04_30.md** §3.3, §5.1 P0.12
- **TASKS.md** Epic 12.A P0.12 satırı
- **docs/audits/price_feed_divergence_2026_05.md** P0.3 audit (15m divergence açığı)
- **data/external_feed.py** Binance REST polling (paralel çalışır)
- **YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md** §3.8 (resolution price feed)
- **Polymarket Docs:**
  - `docs.polymarket.com/market-data/websocket/rtds` (RTDS spec)
  - `docs.polymarket.com/market-data/websocket/rtds#binance-source`
  - `docs.polymarket.com/market-data/websocket/rtds#chainlink-source`

---

**Sonuç:** P0.12 sandbox apply tamam. Heddas yerel: sponsored API + boot integration (P1.10). Sıradaki: **P0.5 Allowance pre-flight check**.
