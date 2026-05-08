# Per-Trade Hard Caps — 2026-05 (P0.10 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** YOL_HARITASI §5.1 P0.10 + Heddas direktifi "parmak kayması koruması"

---

## 0 — TL;DR

| Madde | Status |
|---|---|
| `telegram_bot/handlers/order_validator.py` modül | ✅ DONE (240 satır) |
| `validate_order()` 5-kontrol guard | ✅ DONE |
| Polymarket V2 min order $5 enforcement | ✅ DONE |
| Tick size compliance (0.01/0.001/0.0001) | ✅ DONE |
| `parse_buy_command_args()` Telegram parser | ✅ DONE |
| `render_caps_html()` `/caps` display | ✅ DONE |

---

## 1 — 5 Validation Kontrolü

### 1.1 Side (BUY/SELL)
- Geçersiz: `❌ Geçersiz side: <code>SLD</code>`

### 1.2 Token ID Format
- Hex/decimal string, ≥16 char
- Geçersiz: `❌ Geçersiz token_id: <code>abc...</code>`

### 1.3 Amount Range
- Min: `max(ORDER_MIN_USD, POLYMARKET_MIN_ORDER_USD=$5)` — V2 docs floor
- Max: `ORDER_MAX_USD` (default $10, mainnet ilk hafta cap)
- Geçersiz: `❌ Tutar çok büyük: $100 > max $10 (parmak kayması koruması)`

### 1.4 Price Range
- Min: `ORDER_MIN_PRICE` (default 0.05)
- Max: `ORDER_MAX_PRICE` (default 0.95)
- Geçersiz: `❌ Fiyat çok yüksek: 0.99 > max 0.95 (yüksek olasılık asymmetric risk)`

### 1.5 Tick Size Compliance
- Polymarket tick: 0.01 (default), 0.001, 0.0001, 0.1
- Geçersiz: `❌ Fiyat tick uyumlu değil (0.01): 0.555` (0.555 → 0.55 veya 0.56'ya snap)

---

## 2 — ENV (T6.1 hot-tune ready)

```
ORDER_VALIDATOR_ENABLED=true       # bypass için "false" (admin emergency)
ORDER_MIN_USD=5.0                  # Polymarket V2 floor (zorunlu)
ORDER_MAX_USD=10.0                 # mainnet ilk hafta hard cap
ORDER_MIN_PRICE=0.05
ORDER_MAX_PRICE=0.95
```

`/envt ORDER_MAX_USD 50` ile runtime artırma (whitelist eklenmesi gerek `config/env_whitelist.py`).

**Promotion gates:**
- Hafta 1 (mainnet $20): MAX_USD=10
- Hafta 2-4 ($20→$100 promo): MAX_USD=20
- Ay 2 ($100→$500): MAX_USD=50

---

## 3 — Heddas Yerel Apply

### 3.1 Telegram Handler Wire (P1)

Mevcut `/buy` veya `/order` handler'ında validator çağrısı:

```python
from telegram_bot.handlers.order_validator import (
    validate_order, parse_buy_command_args, render_caps_html
)

@admin_only
async def cmd_buy(update, ctx):
    args = ctx.args
    amount, price, parse_errs = parse_buy_command_args(args)
    if parse_errs:
        await update.effective_message.reply_html("\n".join(parse_errs))
        return
    
    token_id = engine.get_active_market_token()
    result = validate_order(
        side="BUY",
        amount_usd=amount,
        price=price,
        token_id=token_id,
    )
    if not result.ok:
        await update.effective_message.reply_html(result.error_html)
        return
    
    # Proceed with order placement using result.sanitized values
    ...

@admin_only
async def cmd_caps(update, ctx):
    await update.effective_message.reply_html(render_caps_html())
```

### 3.2 Whitelist (`config/env_whitelist.py`)

```python
"ORDER_MAX_USD":   {"type": "float", "min": 1.0, "max": 1000.0, ...},
"ORDER_MIN_USD":   {"type": "float", "min": 5.0, "max": 100.0, ...},
"ORDER_MIN_PRICE": {"type": "float", "min": 0.01, "max": 0.5, ...},
"ORDER_MAX_PRICE": {"type": "float", "min": 0.5, "max": 0.99, ...},
"ORDER_VALIDATOR_ENABLED": {"type": "bool", ...},
```

---

## 4 — Memory Landmark

`memory/project_p010_order_hard_caps_closure.md`:
```
P0.10 Per-trade hard caps CLOSED 2026-04-30. telegram_bot/handlers/order_validator.py 240 satır.
5-kontrol guard: side, token_id, amount range (Polymarket V2 floor $5 + ENV cap), price range, tick size.
ENV runtime re-read (T6.1 doctrine). Forward work P1: /buy + /caps handler wire + whitelist.
```

**Sonuç:** P0.10 KAPALI (modül hazır). **P0 listesi tamamen kapatıldı (10/10).**
Sıradaki: **Mainnet Go/No-Go Gate doğrulaması + Mega Audit Phase A-G**.
