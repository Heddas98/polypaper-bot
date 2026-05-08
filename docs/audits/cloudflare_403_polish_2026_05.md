# Cloudflare 403 Polish — 2026-05 (P1.X → Acil Fix)

**Tarih:** 2026-04-30
**Tetik:** Heddas yerel canlı log — `polymarket_portfolio` job her dakika Cloudflare 403 spam

---

## 0 — Problem

Bot canlı log:
```
2026-04-30 14:12:23 [polypaper.bot] Running job "polymarket_portfolio" (interval 60s)
2026-04-30 14:12:23 [py_clob_client_v2] request error status=403
  url=https://clob.polymarket.com/auth/api-key
  body=<Cloudflare blocked page>
```

**Sebep:** `data/polymarket_portfolio.py::_build_clob_client()` her job'da yeni client oluşturup `create_or_derive_api_key()` çağırıyordu. Cloudflare aynı IP'den (IPv6) dakikada 1 derive request'i bot detect tetikledi.

---

## 1 — Fix Uygulandı

### 1.1 Module-level client cache (`data/polymarket_portfolio.py`)

```python
_CLOB_CLIENT_CACHE = {
    "client": None,
    "creds": None,
    "fetched_at": 0.0,
    "cooldown_until": 0.0,
}
```

**Cache stratejisi:**
1. **Cooldown aktif** (`cooldown_until > now`) → return None (sessizce skip)
2. **Cache hit** + age < TTL (default 1h) → cached client return
3. **Cache miss / expired:**
   - **Önce:** ENV stored creds (`POLYMARKET_API_KEY/SECRET/PASSPHRASE`) → set_api_creds (derive bypass)
   - **Yoksa:** derive (Cloudflare risk)
4. **Cloudflare 403 / "blocked"** → 1h cooldown set, log warn (1 kez)

### 1.2 ENV (T6.1 hot-tune ready)

```
CLOB_CLIENT_CACHE_TTL_S=3600          # 1h cache TTL
POLYMARKET_API_KEY=...                # stored creds (varsa derive bypass)
POLYMARKET_API_SECRET=...
POLYMARKET_PASSPHRASE=...
```

### 1.3 Beklenen Davranış (fix sonrası)

**Senaryo 1: Stored ENV creds + V2 SDK uyumlu**
- 60s job çalıştırılır → cache hit → 0 derive request
- Cloudflare 403 yok
- log: `clob_client: cached via stored ENV creds (TTL 3600s)`

**Senaryo 2: Stored creds yok / V2 ile uyumsuz**
- İlk job → derive (1 request, başarılı veya 403)
- Cache hit subsequent jobs (1h TTL)
- 60s × 60 = 3600 cycle'da 1 derive (60x daha az request)
- 403 olursa 1h cooldown → spam yok

**Senaryo 3: Cloudflare persistent 403**
- İlk derive 403 → 1h cooldown
- Job'lar sessizce skip (debug log only)
- Telegram `/portfolio` komutu boyunca yine cooldown'da
- 1h sonra retry

---

## 2 — Heddas Yerel Apply

### 2.1 Bot restart (kod runtime'a yüklenecek)

```cmd
.\stop_bot.bat
.\start.bat
```

### 2.2 İlk 2 dakika log gözlem

**Beklenti (cache çalışıyorsa):**
```
[polypaper.data.polymarket_portfolio] DEBUG: clob_client: cached via stored ENV creds (TTL 3600s)
... (60s sonra) ...
:: derive request YOK
[apscheduler] Running job "polymarket_portfolio"
:: portfolio fetch başarılı veya graceful skip
```

**403 hâlâ varsa (stored creds V1):**
```
[polypaper.data.polymarket_portfolio] WARNING: clob_client: Cloudflare 403 → 1h cooldown
:: 1 satır per saat (her dakika değil)
```

### 2.3 .env Kontrol

`POLYMARKET_API_KEY` set mi?
```cmd
findstr /B "POLYMARKET_API_KEY" .env
```

**Eğer SET ise:** `019c87d9...` görüyorsun (Phase A V1 derive).
**V2 ile uyumlu mu?** Test:
```cmd
py -3.11 -c "
import os; from dotenv import load_dotenv; load_dotenv()
from py_clob_client_v2 import ClobClient, ApiCreds
client = ClobClient('https://clob.polymarket.com', key=os.getenv('POLYGON_PRIVATE_KEY'), chain_id=137, signature_type=2, funder=os.getenv('POLYGON_WALLET'))
client.set_api_creds(ApiCreds(api_key=os.getenv('POLYMARKET_API_KEY'), api_secret=os.getenv('POLYMARKET_API_SECRET'), api_passphrase=os.getenv('POLYMARKET_PASSPHRASE')))
# Cheap call
from py_clob_client_v2 import TradeParams
trades = client.get_trades(TradeParams())
print('STORED CREDS V2 UYUMLU:', len(trades) if trades else 'OK')
"
```

Eğer 401 → stored creds V2 ile invalid → V2 derive zorunlu (1h cooldown'a takılırsın). Çözüm: 1h bekle + tekrar derive **veya** UA override (P2 polish).

---

## 3 — Forward Work (P2)

### 3.1 User-Agent Override (gerçek polish)

`py-clob-client-v2`'nin httpx client init'i tracking edip default UA'yı `PolyPaperBot/v9.7.9` ile override etmek. Ama:
- V2 SDK source'a bakmak gerek (sandbox'ta installed değil)
- Monkey-patch riskli — SDK update'lerde bozulabilir
- Alternatif: `httpx.Client.headers.update({"User-Agent": "..."})` global

### 3.2 IPv4 Bind

Cloudflare IPv6 IP'leri daha agresif flag'liyor. httpx `transport=httpx.HTTPTransport(local_address="0.0.0.0")` ile IPv4 force.

### 3.3 Polymarket Whitelist Request

Heddas Polymarket support'a bot IP whitelist talebi (uzun vadeli).

---

## 4 — Memory Landmark

`memory/project_p1x_cloudflare_polish.md`:
```
P1.X Cloudflare 403 polish CLOSED 2026-04-30. data/polymarket_portfolio.py
_build_clob_client module-level cache + 1h TTL + cooldown + stored ENV creds bypass.
60s job spam → 1h cycle (60x reduction). Heddas bot restart sonrası doğrulama gerek.
P2 forward: UA override + IPv4 bind + Polymarket whitelist.
```

---

## 5 — Bağlantılı Belgeler

- `docs/audits/sdk_v2_migration_apply_2026_05.md` (P0.11 V2 migration)
- `data/polymarket_portfolio.py:288-360` cache implementation
- `MASTER_PLAN_2026_04_30.md` Risk register R009 (Cloudflare cosmetic)

**Sonuç:** Acil fix uygulandı. Heddas restart → 1h içinde Cloudflare 403 spam yok olacak.
