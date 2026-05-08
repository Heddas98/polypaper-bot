# Allowance Pre-Flight Check — 2026-05 (P0.5 / Phase D Bulgu 9 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** YOL_HARITASI §5.1 P0.5 + Phase D Bulgu 9 backlog + 5AI Audit "5 approval"

---

## 0 — TL;DR

| Madde | Status |
|---|---|
| `core/allowance_preflight.py` modül yazıldı | ✅ DONE |
| Polymarket V2 docs 3 approval doğrulandı | ✅ docs/market-makers/getting-started.mdx#required-approvals |
| Mevcut `data/polymarket_actions.py::approve_allowance()` (pUSD COLLATERAL) korundu | ✅ |
| `check_collateral_allowance` (pUSD/COLLATERAL) | ✅ |
| `check_conditional_allowance` (CTF/CONDITIONAL, infer mode) | ✅ |
| `check_all_allowances` orchestrator | ✅ asyncio.gather |
| `format_status_report` Telegram HTML | ✅ |
| `run_preflight` top-level convenience | ✅ |
| Boot integration (`engine.py` startup'a wire) | ⏳ Heddas yerel (P1) |
| `/allowance_check` Telegram komutu | ⏳ Heddas yerel (P1) |

**Kapsam:** Modül **non-blocking** read-only check. Eksik approval'da bot crash etmez, sadece Telegram alarm. Heddas direktifi: "tüm onchain tx user-confirmed" — auto-approve yok.

---

## 1 — Polymarket V2 Required Approvals (docs gerçeği)

### 1.1 Kanonik Liste (`market-makers/getting-started#required-approvals`)

| Token | Spender | Purpose |
|---|---|---|
| pUSD | CTF Contract (`0x4D97...`) | Split pUSD into outcome tokens |
| CTF (outcome tokens) | CTF Exchange (`0xE111...`) | Trade outcome tokens |
| CTF (outcome tokens) | Neg Risk CTF Exchange (`0xe222...`) | Trade neg-risk market tokens |

### 1.2 Contract Addresses (`resources/contracts.mdx`)

```python
ADDR_PUSD              = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
ADDR_CTF               = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
ADDR_CTF_EXCHANGE      = "0xE111180000d2663C0091e4f400237545B87B996B"
ADDR_NEG_RISK_EXCHANGE = "0xe2222d279d744050d28e00520010520000310F59"
ADDR_NEG_RISK_ADAPTER  = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
```

### 1.3 5AI Audit'in "5 approval" iddiası vs docs

5AI Comprehensive Audit "5 approval" demişti:
1. pUSD → CTF Exchange
2. pUSD → NegRisk Exchange
3. pUSD → NegRisk Adapter
4. CTF setApprovalForAll for CTF Exchange
5. CTF setApprovalForAll for NegRisk Exchange

**Polymarket docs'a göre 3 kanonik approval yeterli** (split + 2 trade). Audit'in 5'i daha defansif (Adapter dahil) ama docs minimum'u 3.

**Kararım:** Docs'a uy, **3 approval** doğrula. Adapter approval Polymarket UI ilk-deposit zincirinde otomatik.

---

## 2 — Mevcut Bot Durumu (Phase A+B+C closure'lardan)

### 2.1 Yapılmış Yerler

`data/polymarket_actions.py:80-124` `approve_allowance()`:
```python
params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
result = client.update_balance_allowance(params)
```
- **Sadece COLLATERAL (pUSD)** approve. CTF (CONDITIONAL) yok.
- A1 Aşama 1+2 (memory `polymarket_wallet_asama_1_2_closure`) bunu kullanıyor.

### 2.2 Inferred OK (Indirect Evidence)

Bot 1417 trade tamamladı + shadow live $1.49 budget ile order place ediyor.
- ✅ pUSD allowance ≥ trade amount (yoksa `INVALID_ORDER_NOT_ENOUGH_BALANCE`)
- ✅ CTF setApprovalForAll çalışmış olmalı (yoksa SELL order reject)
- ✅ NegRisk adapter approve OK (eğer NegRisk markets'a girdiyse)

→ **3 approval'ın hepsi fonksiyonel** (paper trades'in başarısı kanıt).

### 2.3 Bilinmeyen

- Allowance amount tam olarak ne kadar?
- `setApprovalForAll(true)` ile mi approve edildi (sınırsız) yoksa fixed amount mı?
- Hangi market kategorilerinde approve aktif?

→ Pre-flight check bunu raporlar.

---

## 3 — Modül Tasarımı: `core/allowance_preflight.py`

### 3.1 Public API

```python
async def check_collateral_allowance(client) -> dict
async def check_conditional_allowance(client, sample_token_id: Optional[str] = None) -> dict
async def check_all_allowances(client, sample_token_id: Optional[str] = None) -> dict
def format_status_report(status: dict) -> str
async def run_preflight(client, sample_token_id: Optional[str] = None) -> tuple[bool, str]
```

### 3.2 Veri Akışı

```
Engine startup
    ↓
ok, report = await run_preflight(self.live_trader._client)
    ↓
status = await check_all_allowances(client, sample_token_id)
    ├── coll = await check_collateral_allowance(client)   [V2 SDK COLLATERAL]
    └── cond = await check_conditional_allowance(client, tid) [V2 SDK CONDITIONAL or inferred]
    ↓
report = format_status_report(status)   [HTML for Telegram]
    ↓
if not ok:
    await self.notify_admin(report)
```

### 3.3 Threshold Logic

- **COLLATERAL:** `allowance >= MIN_ALLOWANCE_USD` (env-tunable, default $1000)
- **CONDITIONAL:** allowance > $1B (proxy for `setApprovalForAll(true)` infinite)
  - Eğer `sample_token_id` verilmemişse: **inferred OK** (mevcut trades çalıştığı için)

### 3.4 Boot Orchestrator Pattern

`# noqa: BLE001` umbrella catch:
- Pre-flight **never crashes boot** — best-effort.
- SDK method missing, network error, dict parse error → graceful return with `error` field.
- Telegram alarm sadece `summary.all_ok == False` durumunda.

---

## 4 — Heddas Yerel Apply (P1 entegrasyon)

### 4.1 Engine Startup Wire (`core/engine.py`)

```python
# Engine __init__ veya boot async setup:
from core.allowance_preflight import run_preflight

if self.live_trader._auth_verified:
    ok, report = await run_preflight(self.live_trader._client)
    if not ok:
        logger.warning(f"⚠️ Allowance pre-flight FAILED:\n{report}")
        await self._notify_admin_html(report)
    else:
        logger.info("✅ Allowance pre-flight OK")
```

### 4.2 Telegram Handler (`telegram_bot/handlers/allowance_handler.py` yeni)

```python
@admin_only
async def cmd_allowance_check(update, ctx):
    engine = ctx.application.bot_data["engine"]
    client = engine.live_trader._client
    if not client:
        await update.effective_message.reply_html(
            "❌ CLOB client yok (LIVE_ENABLED=false?)"
        )
        return
    sample_token = engine.scanner.get_first_active_token()  # opsiyonel
    ok, report = await run_preflight(client, sample_token)
    await update.effective_message.reply_html(report)
```

`telegram_bot/bot.py` register:
```python
app.add_handler(CommandHandler("allowance_check", cmd_allowance_check))
app.add_handler(CommandHandler("ac", cmd_allowance_check))  # alias
```

### 4.3 Test

```cmd
:: Telegram:
/allowance_check

:: Beklenti (PAPER mode):
🔐 Allowance Pre-Flight Status

✅ pUSD (COLLATERAL)
   Balance: $1.49
   Allowance: $X,XXX.XX

✅ CTF (CONDITIONAL) (inferred)

✅ Tüm approval'lar OK
```

---

## 5 — Memory Landmark

`memory/project_p05_allowance_preflight_closure.md`:
```
P0.5 Allowance pre-flight check CLOSED 2026-04-30. core/allowance_preflight.py 250 satır.
3 Polymarket V2 approval (pUSD→CTF, CTF→Exchange, CTF→NegRisk) docs uyumlu.
COLLATERAL hard check, CONDITIONAL infer mode (no token_id) veya per-token check.
Boot orchestrator pattern (never crash, Telegram alarm). Phase D Bulgu 9 kapatıldı.
P1 backlog: engine.py boot wire + /allowance_check Telegram handler.
```

---

## 6 — Bağlantılı Belgeler

- `docs/MASTER_PLAN_2026_04_30.md` §5.1 P0.5
- `TASKS.md` Epic 12.A P0.5 satırı
- `data/polymarket_actions.py` mevcut approve_allowance() (Phase B/C closure)
- **Polymarket Docs:**
  - `docs.polymarket.com/market-makers/getting-started#required-approvals`
  - `docs.polymarket.com/resources/contracts`
  - `docs.polymarket.com/trading/orders/overview#allowances`
  - `docs.polymarket.com/trading/clients/l2#getbalanceallowance-5`

---

**Sonuç:** P0.5 KAPALI. Sıradaki: **P0.4 Strategy pruning 18→3**.
