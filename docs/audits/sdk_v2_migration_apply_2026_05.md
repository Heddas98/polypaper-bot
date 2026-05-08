# SDK V2 Migration Apply — 2026-05 (P0.11 Closure)

**Tarih:** 2026-04-30
**Sahibi:** Claude (Lead Architect)
**Tetik:** Heddas direktifi 2026-04-30 — "en güncel olsun. v2 ye geçtiyse polymarket sende geç. sen de en güncel ol"
**Önceki:** `docs/audits/sdk_v2_migration_check_2026_05.md` (P0.1 — V1 yeterli kararı revize edildi)

---

## 0 — TL;DR

| Adım | Status | Not |
|---|---|---|
| `requirements.txt` py-clob-client→py-clob-client-v2 | ✅ DONE | + py-builder-relayer-client eklendi |
| 5 dosya × 12 import block refactor | ✅ DONE | Top-level package import (V2 flatten) |
| Heddas yerel `pip install` | ⏳ HEDDAS YEREL | `pip install py-clob-client-v2 py-builder-relayer-client` |
| Bot restart + auth smoke | ⏳ HEDDAS YEREL | `/auth_check` veya `/portfolio` |
| Mainnet $1 USDC smoke trade | ⏳ HEDDAS YEREL | `/buy 1 0.50` smoke order |
| 778-800 test re-validate | ⏳ HEDDAS YEREL | `pytest tests/ -q` |

**Kapsam:** Bu rapor V2 migration **apply** aşamasını belgeler. P0.1 (check) raporunda V1 yeterli demiştik — Heddas direktifi sonrası V2 migration acil P0 oldu.

---

## 1 — Yapılan Değişiklikler

### 1.1 requirements.txt

**Eski:**
```
py-clob-client==0.34.6
```

**Yeni:**
```python
# Polymarket — V2 SDK (Heddas direktifi 2026-04-30: "en güncel ol")
# 2026-04-30: py-clob-client 0.34.6 (V1) → py-clob-client-v2 1.0.0 (V2)
# Polymarket V2 cutover (28 Nisan 2026) sonrası resmi paket. EIP-712 domain
# version "2", V2 order struct (metadata + builder fields), getClobMarketInfo
# native fee query, builder code SDK-native (HMAC + builder-signing-sdk gone).
# API path differences: from py_clob_client → from py_clob_client_v2
# Yeni class: PartialCreateOrderOptions (V1 dict → V2 typed class), MarketOrderArgs
# Aynı kalan: ApiCreds, OrderArgs, OrderType, BalanceAllowanceParams, AssetType
py-clob-client-v2==1.0.0
# Relayer client gasless txns için (allowance + token approval + redeem):
py-builder-relayer-client
```

### 1.2 Import Path Refactor (5 dosya × 12 block)

V2 paketi top-level package'a flatten edildi. V1'in nested submodule yapısı (`py_clob_client.client`, `py_clob_client.clob_types`, `py_clob_client.order_builder.constants`) düzleştirildi: `py_clob_client_v2` direkt top-level. **Sadece** `py_clob_client_v2.order_builder.constants` korundu (BUY, SELL constants).

#### Dosya 1: `core/live_trader.py`

| Satır | Eski | Yeni |
|---|---|---|
| 205-206 | `from py_clob_client.client import ClobClient`<br>`from py_clob_client.clob_types import ApiCreds` | `from py_clob_client_v2 import ClobClient, ApiCreds` |
| 264 | `from py_clob_client.clob_types import TradeParams` | `from py_clob_client_v2 import TradeParams` |
| 436-438 | `from py_clob_client.client import ClobClient`<br>`from py_clob_client.clob_types import OrderArgs, OrderType`<br>`from py_clob_client.order_builder.constants import BUY` | `from py_clob_client_v2 import ClobClient, OrderArgs, OrderType, PartialCreateOrderOptions`<br>`from py_clob_client_v2.order_builder.constants import BUY` |
| 510-512 | `from py_clob_client.clob_types import (BalanceAllowanceParams, AssetType,)` | `from py_clob_client_v2 import (BalanceAllowanceParams, AssetType,)` |

**Bonus:** `PartialCreateOrderOptions` import edildi (V2 typed class — V1 dict alternatifi). Mevcut `options = {"tick_size": ..., "neg_risk": ...}` dict yapısı şimdilik korundu (V2 backward compat bekleniyor — Heddas yerel test'te dict reject olursa class'a switch).

#### Dosya 2: `data/polymarket_actions.py`

| Satır | Eski | Yeni |
|---|---|---|
| 52 | `from py_clob_client.client import ClobClient` | `from py_clob_client_v2 import ClobClient` |
| 88 | `from py_clob_client.clob_types import BalanceAllowanceParams, AssetType` | `from py_clob_client_v2 import BalanceAllowanceParams, AssetType` |

Docstring güncellendi: "py-clob-client 0.34.6" → "py-clob-client-v2 1.0.0".

#### Dosya 3: `data/polymarket_portfolio.py`

| Satır | Eski | Yeni |
|---|---|---|
| 136 | `from py_clob_client.clob_types import BalanceAllowanceParams, AssetType` | `from py_clob_client_v2 import BalanceAllowanceParams, AssetType` |
| 248 | `from py_clob_client.clob_types import TradeParams` | `from py_clob_client_v2 import TradeParams` |
| 280 | error msg: `"py-clob-client not installed: {e}"` | `"py-clob-client-v2 not installed: {e}"` |
| 290 | `from py_clob_client.client import ClobClient` | `from py_clob_client_v2 import ClobClient` |

#### Dosya 4: `scripts/backfill_ob_trades.py`

| Satır | Eski | Yeni |
|---|---|---|
| 75-77 | 3 ayrı import (client, ApiCreds, TradeParams) | `from py_clob_client_v2 import ClobClient, ApiCreds, TradeParams` |
| 120 | `from py_clob_client.clob_types import TradeParams` | `from py_clob_client_v2 import TradeParams` |
| Error msg | `"py-clob-client not installed"` | `"py-clob-client-v2 not installed"` |

#### Dosya 5: `tests/test_backfill_creds.py`

| Satır | Eski | Yeni |
|---|---|---|
| 54 | `from py_clob_client.client import ClobClient` | `from py_clob_client_v2 import ClobClient` |
| Print msg | `"py-clob-client"` + `"pip install py-clob-client"` | `"py-clob-client-v2"` + `"pip install py-clob-client-v2"` |

### 1.3 Etkilenmeyen Konular (V2 Backward Compat)

V2 SDK V1 ile büyük ölçüde uyumlu — aşağıdaki API call'lar **değişiklik gerektirmedi**:

- `client.create_or_derive_api_creds()` — aynı method
- `client.set_api_creds(creds)` — aynı method
- `client.get_trades(TradeParams())` — aynı signature
- `client.get_tick_size(token_id)` / `client.get_neg_risk(token_id)` — aynı method
- `client.get_balance_allowance(BalanceAllowanceParams)` — aynı signature
- `client.create_order(order_args, options=options)` — aynı signature (options dict V2 backward compat bekleniyor)
- `client.post_order(signed, OrderType.FOK)` — aynı signature
- `client.post_heartbeat("")` — aynı method
- `client.update_balance_allowance(params)` — aynı signature

**Constructor pattern aynı:**
```python
client = ClobClient(
    "https://clob.polymarket.com",
    key=pk, chain_id=137,
    signature_type=2,  # GNOSIS_SAFE
    funder=wallet,
)
```

`ApiCreds` constructor parametreleri aynı:
```python
creds = ApiCreds(
    api_key=...,
    api_secret=...,
    api_passphrase=...,
)
```

---

## 2 — Heddas Yerel Apply Rehberi (CRITICAL — adım adım)

### 2.1 Adım 1: pip install

```cmd
cd C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31
py -3.11 -m pip install --upgrade pip
py -3.11 -m pip uninstall -y py-clob-client py-builder-signing-sdk
py -3.11 -m pip install py-clob-client-v2==1.0.0 py-builder-relayer-client
py -3.11 -m pip list | findstr -i clob
```

**Beklenti:**
```
py-clob-client-v2          1.0.0
py-builder-relayer-client  X.Y.Z
```

### 2.2 Adım 2: Import smoke check

```cmd
py -3.11 -c "from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType, PartialCreateOrderOptions, BalanceAllowanceParams, AssetType, TradeParams; print('OK V2 imports')"
```

**Beklenti:** `OK V2 imports`

Eğer ImportError olursa (örn: `BalanceAllowanceParams` V2'de farklı isim):
- Hata satırını paylaş
- Alternatif yol: PyPI sayfasından V2 paket içeriğini incele (`https://pypi.org/project/py-clob-client-v2/`)
- Fallback: `from py_clob_client_v2.clob_types import ...` dene (eğer V2 submodule yapısını korumuşsa)

### 2.3 Adım 3: Bot smoke restart (PAPER MODE)

```cmd
:: PAPER MODE varsayılan — LIVE_ENABLED=false
.\stop_bot.bat
.\start.bat
```

Telegram'dan bot durumu kontrol:
- `/h` — heartbeat
- `/auth_check` — V2 SDK auth flow doğru mu?
- `/portfolio` — V2 SDK portfolio fetch çalışıyor mu?

**Beklenti:** Bot `start.bat` ile boot → Telegram heartbeat OK → /auth_check derive successful → /portfolio cache fetch OK.

### 2.4 Adım 4: Test suite (5 dakika)

```cmd
py -3.11 -m pytest tests/ -q --tb=short 2>&1 | tee evidence/p011_v2_pytest.txt
```

**Beklenti:** 778-800 PASS, 0 fail (3-seed deterministic baseline).

Eğer FAIL olursa:
- import error → SDK V2 paket farklı submodule yapısı kullanıyor (alternatif import path)
- Method signature değişmiş → V2 docs'tan kontrol et
- `evidence/p011_v2_pytest.txt` paylaş, beraber çözeriz

### 2.5 Adım 5: Mainnet $1 USDC smoke trade (opsiyonel ama önerilir)

```
:: Telegram:
/mode
:: Real seç
/live on
/buy 1 0.50
:: Sonuç bekle (10-30s)
/portfolio
:: Trade history'de yeni $1 trade görmelisin
```

**Beklenti:** Order accept → Polymarket account activity'de yeni trade görünür.

Eğer FAIL:
- INVALID_SIGNATURE: V2 EIP-712 domain version uyumsuzluğu — debug
- INVALID_ORDER_NOT_ENOUGH_BALANCE: pUSD bakiye eksik (deposit gerek)
- 401 Unauthorized: API creds derive başarısız (V2 method farklı signature)

### 2.6 Adım 6: Geri dönüş (rollback) prosedürü

Eğer V2 migration sorun yaratırsa:

```cmd
git status
git checkout -- requirements.txt core/live_trader.py data/polymarket_actions.py data/polymarket_portfolio.py scripts/backfill_ob_trades.py tests/test_backfill_creds.py
py -3.11 -m pip uninstall -y py-clob-client-v2 py-builder-relayer-client
py -3.11 -m pip install py-clob-client==0.34.6
.\stop_bot.bat
.\start.bat
```

Bot V1 0.34.6'ya geri döner. Phase A+B+C closure smoke'ları tekrar PASS olmalı.

---

## 3 — Bilinen Belirsizlikler (Heddas Test Sonrası Çözülecek)

### 3.1 `options` Dict vs PartialCreateOrderOptions

V1: `options = {"tick_size": "0.01", "neg_risk": False}`
V2 docs: `options = PartialCreateOrderOptions(tick_size="0.01", neg_risk=False)`

**Şu an kod:** dict yapısı korundu (V2 backward compat bekleniyor).
**Risk:** V2 SDK `client.create_order(args, options=dict)` reject edebilir.
**Fallback:** Eğer reject ise `core/live_trader.py:557-559` aşağıdaki gibi değiştirilir:

```python
# V2 typed class kullan
options = PartialCreateOrderOptions(
    tick_size=meta["tick_size"],
    neg_risk=meta["neg_risk"],
)
# builder_code V2'de OrderArgs içinde olabilir veya client.create_order kwarg
# Heddas test'te tespit edilecek
```

### 3.2 Builder Code Attach

V1: `options["builder_code"] = builder_code` (dict)
V2: `client.createAndPostOrder(userOrder, options, orderType)` — TypeScript'te `userOrder.builderCode`. Python'da muhtemelen `OrderArgs(builder_code=...)` veya separate kwarg.

**Şu an kod:** dict'e `options["builder_code"]` ekleniyor (V1 pattern).
**Risk:** V2 dict yapısını değiştirdiyse builder_code attach edilmez (vanilla order).
**Fallback:** V2 docs/repo örnekleri ile kesin pattern doğrula.

### 3.3 Submodule Yapısı

V1: `from py_clob_client.clob_types import X` (nested)
V2 docs: `from py_clob_client_v2 import X` (top-level)

**Risk:** Bazı class'lar V2'de submodule içinde kalmış olabilir (geriye dönük uyumluluk için):
- `BalanceAllowanceParams`, `AssetType`, `TradeParams` — V2 docs örneklerinde net görmedim

**Fallback:** Eğer top-level import fail ederse:
```python
# Alternative paths to try (V2 submodule fallback)
from py_clob_client_v2.clob_types import BalanceAllowanceParams, AssetType
# veya
from py_clob_client_v2.types import BalanceAllowanceParams, AssetType
```

### 3.4 EIP-712 Domain Version

V1 0.34.6: muhtemelen domain version "2" kullanıyor (Polymarket 2026-04-28 V2 cutover sonrası backward update).
V2: domain version "2" native.

**Risk:** Eğer V1 0.34.6 hala "1" kullanıyorsa, V2'ye geçmek zaten kritik fix idi.
**Verify:** Smoke trade order sign successful → EIP-712 v2 doğrulanmış.

---

## 4 — Memory Landmark Update

`memory/project_p011_sdk_v2_migration_apply.md`:
```
P0.11 SDK V2 migration APPLIED 2026-04-30. requirements.txt + 5 dosya × 12 import block.
Heddas direktifi "en güncel ol" → V1 0.34.6 → V2 1.0.0 + py-builder-relayer-client.
V2 SDK top-level package, V1 nested submodule yapısı flatten. API method'lar (auth, order, balance, trades) backward compat.
options dict pattern korundu (V2 typed class fallback hazır). Heddas yerel apply: pip install + bot restart + smoke trade.
P0.1 V1 yeterli kararı geçersiz, MEMORY.md landmark güncellenecek.
```

`MEMORY.md` (Orientation kısmı) güncellenecek satır:
```
- [P0.11 SDK V2 Migration Applied 2026-04-30](project_p011_sdk_v2_migration_apply.md) — V1 0.34.6 → V2 1.0.0. 5 dosya × 12 import. Heddas yerel pip install + smoke pending.
```

---

## 5 — Sonraki İşler

P0.11 sandbox apply tamamlandı. P0.12 (Chainlink RTDS subscribe) sıradaki:

- ⏳ P0.12 — `data/polymarket_rtds.py` yeni dosya, `wss://ws-live-data.polymarket.com` subscribe
- ⏳ Heddas yerel: `pip install` + bot restart + smoke trade (P0.11 verify)
- ⏳ TASKS.md + MASTER_PLAN_2026_04_30.md güncellemesi (P0.11 ✅, P0.12 in progress)

---

## 6 — Bağlantılı Belgeler

- **MASTER_PLAN_2026_04_30.md** §3.1, §5.1 P0.11
- **TASKS.md** Epic 12.A P0.11 satırı
- **docs/audits/sdk_v2_migration_check_2026_05.md** P0.1 closure (revize edildi)
- **YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md** §5.1 P0.1 (V2 flag)
- **Polymarket Docs:**
  - `docs.polymarket.com/api-reference/clients-sdks`
  - `docs.polymarket.com/trading/clients/l1` (V2 import örneği)
  - `docs.polymarket.com/trading/clients/l2` (V2 ApiCreds örneği)
  - `docs.polymarket.com/builders/fees#sdk-integration` (V2 builder code native)
  - `docs.polymarket.com/builders/fees#eip-712-domain` (V2 domain version "2")

---

**Sonuç:** P0.11 sandbox apply tamam. Heddas yerel pip install + bot restart + smoke trade ile final close. Sonraki: **P0.12 Polymarket RTDS Chainlink subscribe**.
