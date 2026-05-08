# Sprint 3 Plan — P1 Wire + Coverage Boost + Refactor

**Tarih:** 2026-05-05 (Wave 7-8 sonrası güncellendi)
**Süre:** Aktif (Sprint 2 SHADOW paralel devam — 17 Mayıs karar gate)
**Heddas direktifleri (kronolojik):**
1. "Linux erteleyelim sonra tam başla"
2. "Bana bir sonraki testte %50 olalım istiyorum"
3. "Hiç durmadan, paralel güncellemerli de yapalım"
4. "Failleri çöz, en kapsamlı coverage boost yap"

**Mevcut durum (Wave 7 measured):**
- Test sayısı: 1049 → ~3000+ (Wave 8 hedef)
- Coverage TOTAL: %21.2 → **%32.4** (Wave 7) → **%40+ hedef Wave 8**
- Engine wire (5 ENV-gated): TAMAM
- P3.X bulk endpoint: TAMAM (default off, wire Sprint 4)
- Sprint 2 SHADOW ACTIVE: paralel devam

> **Linux/Docker ERTELENDİ:** Sprint 5-6.

---

## 0 — Sprint 3 Hedef Tablosu

| Boyut | Önce | Hedef |
|---|---|---|
| Test coverage | %21.8 | **%60+** |
| P1 modül engine wire | 0/8 entegre | **8/8** ✅ |
| Linux/Docker desteği | Sadece Windows | **Docker + systemd** |
| core/ refactor | 1 büyük klasör | **4 alt-paket** (signal/execution/risk/shared) |
| ENV var | 152 (audit) | **<80** |
| Mainnet | $10 budget | **$100 promotion** (Sprint 2 PASS varsayımıyla) |

---

## 1 — Hafta 1: P1 Engine Wire (4 saat)

Sandbox'taki 8 modülü `core/engine.py` boot sequence'a entegre et.

### Adım 1.1: Allowance Pre-flight (P0.5) — 30dk

`core/engine.py` boot sonu:
```python
from core.allowance_preflight import run_preflight

# Engine __init__ sonu, scheduler başlamadan önce:
async def _boot_post_warmup(self):
    if self.live_trader._auth_verified:
        ok, report = await run_preflight(self.live_trader._client)
        if not ok:
            logger.warning(f"⚠️ Allowance pre-flight FAILED:\n{report}")
            await self.notify_admin(report)
        else:
            logger.info("✅ Allowance pre-flight OK")
```

### Adım 1.2: Portfolio Kill-Switch (P0.8) — 1h

Trade gate:
```python
from core.portfolio_kill_switch import get_kill_switch

class Engine:
    def __init__(self, ...):
        self.kill_switch = get_kill_switch()

    async def _on_trade_closed(self, trade):
        self.kill_switch.record_trade(trade.pnl)

    async def _can_open_trade(self) -> tuple[bool, str]:
        equity = self.risk.state.current_equity
        decision = self.kill_switch.evaluate(equity)
        if decision.halted:
            logger.warning(f"🛑 Kill-switch: {decision.reason} — {decision.detail}")
            return False, decision.reason
        return True, ""
```

`config/env_whitelist.py` ekleme (T6.1 hot-tune):
```python
"KILL_SWITCH_ENABLED": {"type": "bool"},
"KILL_DAILY_MAX_LOSS_PCT": {"type": "float", "min": 0.01, "max": 0.5},
"KILL_WEEKLY_MAX_DD_PCT": {"type": "float", "min": 0.05, "max": 0.5},
"KILL_CONSECUTIVE_LOSS_LIMIT": {"type": "int", "min": 2, "max": 20},
"KILL_CONSECUTIVE_COOLDOWN_S": {"type": "int", "min": 60, "max": 86400},
```

Telegram handler:
```python
@admin_only
async def cmd_kill_switch(update, ctx):
    engine = ctx.application.bot_data["engine"]
    equity = engine.risk.state.current_equity
    html = engine.kill_switch.status_html(equity)
    await update.effective_message.reply_html(html)
```

### Adım 1.3: Order Validator (P0.10) — 30dk

`telegram_bot/handlers/` mevcut `/buy` (varsa) + `/order` handler'a inject:

```python
from telegram_bot.handlers.order_validator import (
    validate_order, parse_buy_command_args, render_caps_html
)

@admin_only
async def cmd_buy(update, ctx):
    args = ctx.args
    amount, price, errs = parse_buy_command_args(args)
    if errs:
        await update.effective_message.reply_html("\n".join(errs))
        return

    token_id = engine.scanner.get_first_active_token()
    result = validate_order(
        side="BUY", amount_usd=amount, price=price, token_id=token_id,
    )
    if not result.ok:
        await update.effective_message.reply_html(result.error_html)
        return

    # Proceed with engine.live_trader.maybe_mirror(...)
```

### Adım 1.4: Reconciliation Loop (P1.4) — 1h

`core/engine.py` boot:
```python
from core.reconciliation.onchain_sync import ReconciliationTask

# Engine __init__:
self.recon_task = ReconciliationTask(
    db=self.db,
    wallet=os.getenv("POLYGON_WALLET", ""),
    alert_callback=self._notify_admin_html,
)

# Engine boot:
await self.recon_task.start()
```

ENV ekle (.env):
```
RECON_ENABLED=true
RECON_INTERVAL_S=300
RECON_MISMATCH_THRESHOLD_USD=1.0
POLYGON_RPC_URL=https://polygon-rpc.com
```

### Adım 1.5: Heartbeat Coroutine (P1.6.1) — 30dk

P1.6 maker mode aktif olunca zorunlu. Şu an opsiyonel.

`core/engine.py`:
```python
from core.heartbeat import HeartbeatTask

if os.getenv("HEARTBEAT_ENABLED", "false").lower() == "true":
    self.heartbeat_task = HeartbeatTask(client=self.live_trader._client)
    await self.heartbeat_task.start()
```

### Adım 1.6: Maker/Taker Decision (P1.6) — 30dk

`core/live_trader.py::maybe_mirror()`'da spread analizi:

```python
from core.maker_taker_decision import decide_order_type

# Spread + urgency analizi
ob = await self.fetch_orderbook(token_id)
decision = decide_order_type(
    orderbook=ob,
    notional_usd=amount,
    price=odds,
    tick_size="0.01",
    urgency="normal",  # signal_score'a göre değişebilir
)

if decision.role == "maker":
    # post-only GTC
    options["builder_code"] = builder_code
    result = client.post_order(signed, OrderType.GTC, postOnly=True)
else:
    # FOK taker (mevcut akış)
    result = client.post_order(signed, OrderType.FOK)
```

ENV: `MAKER_MODE_ENABLED=true` + `HEARTBEAT_ENABLED=true` (zorunlu).

### Adım 1.7: Executor Abstraction (P1.8) — 30dk

`core/engine.py` strategy'ler executor üzerinden:

```python
from core.executor import get_executor

self.paper_executor = get_executor("paper")
self.paper_executor.set_orderbook_source(self.scanner.get_orderbook)

self.live_executor = get_executor("live", live_trader=self.live_trader)
```

Strategy kodu:
```python
# Önce: strategy → engine.live.maybe_mirror(...)
# Sonra: strategy → executor.place_order(req)
result = await executor.place_order(OrderRequest(
    token_id=tid, side="BUY", amount_usd=amount, price=price,
    order_type="FOK", strategy_label=label, slug=slug,
))
```

### Adım 1.8: Structured Logging (P1.7) — 30dk

`main.py` startup:
```python
from core.structured_logging import setup_structured_logging

# Bot başlangıcı:
setup_structured_logging()
```

ENV:
```
STRUCTURED_LOG_ENABLED=true
LOG_SECRET_SCRUB=true
STRUCTURED_LOG_FILE=data_store/structured.jsonl
```

**Toplam Hafta 1 efor:** ~4 saat.

---

## 2 — Hafta 2: Coverage Boost %22 → %45 (8 saat)

Stratejik test ekleme — büyük dosyalar:

| # | Hedef Modül | Mevcut | Hedef | Test Stratejisi | Efor |
|---|---|---|---|---|---|
| 1 | `core/engine.py` (653 stmt) | 20.6% | 50% | Mock orderbook + scanner, integration test for cycle | 2h |
| 2 | `core/engine_signals.py` (1034 stmt) | 7.1% | 30% | Strategy plugin parameterized test (15 strats × 5 senaryo) | 2h |
| 3 | `core/live_trader.py` (393 stmt) | 28.4% | 60% | Mock SDK responses (auth + post_order + verify) | 1.5h |
| 4 | `core/ai_brain.py` (993 stmt) | 9.2% | 25% | Claude API mock + prompt assertions | 1.5h |
| 5 | `data/polymarket_portfolio.py` (314) | 0% | 50% | Mock CLOB client + cache test | 1h |

**Beklenen artış:** %22 → **%45-50** (Sprint 3 sonu).

---

## 3 — Hafta 3: Polymarket Docs Bulguları (8 saat)

> **Linux/Docker yerine** — 2026-05-03 docs re-audit'ten gelen 4 bulgu.

### 3.1 P2.X — getClobMarketInfo() Dynamic Fee Query (2h)

V2 SDK native method. Statik `core/fees_v2.py` formula yerine per-market real-time fee parametreleri.

`core/fees_v2.py` ekle:
```python
def get_market_fee_params(client, condition_id: str) -> dict | None:
    """V2 SDK ile per-market fee parametreleri runtime fetch.
    Returns {"rate": float, "exp": float, "taker_only": bool} or None on error."""
    try:
        info = client.get_clob_market_info(condition_id)
        fd = info.get("fd", {})
        return {"rate": fd.get("r"), "exp": fd.get("e"), "taker_only": fd.get("to", False)}
    except Exception as e:
        logger.warning(f"get_clob_market_info({condition_id}) failed: {e}")
        return None
```

Kullanım: `polymarket_taker_fee_v2(price, amount, override_rate=params["rate"], override_exp=params["exp"])`.

**Etki:** Geopolitics %0 fee markets otomatik tespit edilir (P3.1 önkoşulu).

### 3.2 P3.X — POST /orders Bulk Endpoint (4h)

15 order tek seferde — 1000/10s burst, 15000/10dk sustained = 15× rate efficiency.

`data/polymarket_client.py` ekle:
```python
async def post_orders_bulk(self, signed_orders: list[dict]) -> list[dict]:
    """V2 SDK bulk order endpoint. Max 15 orders per call."""
    if len(signed_orders) > 15:
        raise ValueError(f"bulk limit 15, got {len(signed_orders)}")
    resp = await self._http.post("/orders", json={"orders": signed_orders})
    return resp.json()["results"]
```

Engine integration (multi-strategy paralel sinyal):
- `core/live_trader.py` 100ms window'da gelen sinyal'leri toplar
- 15'e ulaşınca veya window dolunca bulk submit
- Latency 15× azalır (15 trip → 1 trip)

**Önkoşul:** Sprint 2 PASS (mevcut tek-tek POST /order çalışıyor, optimizasyon sonra).

### 3.3 P3.Y — UMA Dispute Window Awareness (1.5h)

Dispute resolution market settlement zamanını etkiler — bilmek = pozisyon zaman optimizasyonu.

`core/uma_dispute.py` yeni modül:
```python
async def check_dispute_window(client, market_address: str) -> dict:
    """UMA Optimistic Oracle dispute durumunu kontrol et.
    Returns {"in_dispute": bool, "dispute_end_ts": int|None, "settled": bool}"""
    # core/allowance_preflight.py:UMA_OPTIMISTIC_ORACLE address kullan
    # web3.py call → assertionLiveness + currentVotePhase
    ...
```

Engine integration: Settlement yakın market'e yeni pozisyon açma engellensin.

### 3.4 ENV Cleanup Devam (30dk)

Mevcut: 70 env (Sprint 2'de 100→70 yapıldı).
Hedef: <50 (sadece secret + sıkça override).

`scripts/env_audit.py` tekrar koş, kalan dead'leri sil.

**Toplam Hafta 3 efor:** ~8 saat.

---

## 4 — Hafta 4: P1.2 Refactor + ENV Cleanup (4 saat)

### 4.1 P1.2 core/ Refactor (2 saat)

`docs/refactor/core_refactor_plan_2026_05.md` plan'ı uygula:
- 4 paket: `signal_engine/`, `execution_engine/`, `risk_engine/`, `shared/`
- AI Brain: `services/ai_brain/`
- 30+ `git mv` (history korunur)
- `core/__init__.py` shim (backward compat → mevcut import'lar bozulmaz)
- pytest 1012+ baseline run (regression check)

### 4.2 ENV Cleanup (1.5 saat)

Mevcut env_audit.py bulgu: **210 forgotten + 46 dead = 256 var**.
Hedef: **<80 var** (sadece secret + override).

Adımlar:
1. `config/defaults.py` yeni dosya — kullanılmayan ENV'lerin default değerleri
2. Code refactor: `os.getenv("X", default)` → `from config.defaults import X` (basit constant)
3. `.env.example` minimal versiyon yaz (sadece secret + sıkça override)

### 4.3 P2.X getClobMarketInfo() Adoption (30dk — yeni bulgu)

`core/fees_v2.py` opsiyonel:
```python
def get_market_fee_params(client, condition_id: str) -> dict:
    """V2 SDK ile per-market fee parametreleri runtime fetch."""
    info = client.get_clob_market_info(condition_id)
    return {
        "rate": info["fd"]["r"],
        "exp": info["fd"]["e"],
        "taker_only": info["fd"]["to"],
    }
```

`polymarket_taker_fee_v2()` çağrılarında `override_rate` + `override_exp` ile kullan.

---

## 5 — Sprint 3 Karar Gate (Sonu)

| Kriter | Threshold | Karar |
|---|---|---|
| Coverage | ≥%45 | ✅ Sprint 4'e |
| P1 wire | 8/8 ✅ | ✅ |
| Docker test | local + VPS smoke | ✅ |
| Mainnet PnL | ≥+%5 (Sprint 2'den itibaren) | ✅ → $100 promotion |
| Edge zayıf | <+%5 (60+ gün) | 🔄 SaaS pivot |

---

## 6 — Sprint 3 Toplam Eforu

| Hafta | İş | Saat | Sprint 2 paralel? |
|---|---|---|---|
| 1 | P1 wire (8 modül) | 4 | ❌ 17 May sonrası |
| 2 | Coverage boost (5 hedefli) | 8 | ✅ paralel OK |
| 3 | Polymarket docs bulguları (P2.X+P3.X+P3.Y+ENV) | 8 | ✅ paralel OK (modül yazımı) |
| 4 | core/ refactor + ENV cleanup | 4 | ✅ refactor planı paralel |
| **Toplam** | | **24 saat** (Heddas yerel) | |

> Linux/Docker erteendi → Sprint 5-6 (SaaS lansman öncesi).

---

## 7 — Sprint 4 (Sonra) — SaaS Hazırlığı

Sprint 3 PASS → Sprint 4 (Ay 3 — Temmuz):
- P2.1 Multi-user + lisans (3-tier $9/$29/$79)
- P2.4 Web dashboard MVP (Streamlit)
- P2.5 Stripe + Coingate ödeme
- P2.6 Affiliate program (%20 lifetime)

Sprint 4 sonu **SaaS lansman** (Ağustos-Ekim).

---

## 8 — Sprint 3 Önkoşullar

✅ Sprint 2 mainnet $20 mikro test PASS (drift <%10, PnL ≥+%5, ≥200 trade)
✅ Bot 14 gün stabil (0 critical bug, kill-switch tetiklemedi)
✅ Cloudflare 403 kalıcı çözüldü (cross-module shared cache doğrulandı)

---

**Sonuç:** Sprint 3 detaylı plan hazır. Sprint 2 bittiğinde (17 Mayıs civarı) Heddas bu plan'ı sırasıyla uygular. ~24 saat efor, ~4 hafta süre.
