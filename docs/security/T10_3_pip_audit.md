# Epic 10 T10.3 — Dependency CVE Scan (pip-audit)

**Scope:** `requirements.txt` + full transitive closure (60 packages).
**Tool:** `pip-audit` (PyPA, PyPI Advisory DB + OSV).
**Date:** 2026-04-22.
**Risk:** MED (pre-mainnet hygiene).

## Executive summary

- **24 CVE** bulundu — **3 paket** etkileniyor:
  - `aiohttp 3.10.0` → 20 CVE (fix chain 3.10.2 → 3.13.4)
  - `pillow 11.0.0` → 2 CVE (fix 12.2.0)
  - `python-dotenv 1.0.1` → 1 CVE (fix 1.2.2)
- **57 paket temiz** (python-telegram-bot, httpx, pydantic, websockets,
  pandas, numpy, py-clob-client, pycryptodome, eth-* suite, requests,
  pytest, …).
- **Direkt sömürü yüzeyi düşük** — fix'lerin çoğu "sunucu tarafı
  multipart/static route / FITS/PSD decoder / set_key symlink" senaryolarına
  ait ve PolyPaper'ın kullanım modeline uymuyor (aşağıda per-CVE
  applicability matrisi).
- **Karar:** yine de üçünü de **upgrade et** (defense-in-depth, pre-mainnet
  hygiene, supply-chain aging disiplini).

---

## Paket başına applicability matrisi

### 1. `python-dotenv 1.0.1 → 1.2.2` — CVE-2026-28684 (MED)

**CVE:** Symlink attack via `set_key()` / `unset_key()` during cross-device
rename fallback; attacker symlinki kötü niyetli bir dosyaya yönlendirip
host'taki başka dosyaları bozabilir.

**Applicability:** **N/A (NO).**
- Kod tabanında `set_key` / `unset_key` import'u **0 yer**.
- Sadece `load_dotenv()` kullanılıyor (main.py:26, test + scripts). Bu
  CVE yalnızca write-path API'larını etkiliyor.
- `env_toggle.py` (T10.2 context) runtime `.env` yazımını kendi
  `Path.write_text()` akışıyla yapıyor — dotenv'in `rewrite()` context
  manager'ına hiç girmiyor.

**Karar:** **upgrade for hygiene** (dep version aging zararsız, supply
chain signal pozitif).

### 2. `pillow 11.0.0 → 12.2.0` — CVE-2026-25990, CVE-2026-40192 (LOW-MED)

**CVEs:**
- CVE-2026-25990: PSD image load sırasında OOB write (Pillow ≥ 10.3.0).
  Fix: 12.1.1.
- CVE-2026-40192: FITS decompression bomb — unbounded memory. Fix: 12.2.0.

**Applicability:** **N/A (NO).**
- Pillow kullanım surface:
  - `telegram_bot/banners.py:6`: `Image.new()` + `ImageDraw.Draw()` +
    `img.save(format="PNG")` — **sadece image CREATE** ediyoruz, dış
    dosyayı açmıyoruz. `Image.open()` yok.
  - `backtest/analytics/charts.py:34`: aynı pattern — `Image.new()` +
    `ImageDraw.Draw()` + `img.save()` ile chart banner üretimi.
- PSD ve FITS decoder'ları **hiç tetiklenmiyor**.

**Karar:** **upgrade for hygiene.** 11.0.0'da başka da bir gelişme /
security fix olabilir (12.x memory footprint iyileştirmeleri var).

### 3. `aiohttp 3.10.0 → 3.13.4` — 20 CVE (MED)

**Import surface:**
- `core/keepalive.py` — `aiohttp.web` HTTP sunucusu, port 8080.
  5 GET endpoint: `/`, `/health`, `/status`, `/dashboard`, `/api/data`.
  **GET-only**, POST/PUT/DELETE/multipart yok. Replit/lokal health check
  ve dashboard. `ClientSession`/`static routes`/`follow_symlinks` YOK.
- `data_feeds/news_scanner.py:141,170` — `aiohttp.ClientSession` ile RSS
  feed polling. Public URL'lere GET. Cookie/Auth header set edilmiyor.

**Per-CVE applicability** (20 bulgu):

| CVE | Summary | Fix | Applicability |
| --- | --- | --- | --- |
| CVE-2024-42367 | Static compressed-variant symlink traversal | 3.10.2 | **NO** — static route yok |
| CVE-2024-52304 | Chunk extension newline request smuggling (pure-Py) | 3.10.11 | **NO** — C ext enabled |
| CVE-2025-53643 | Trailer section parse smuggling (pure-Py) | 3.12.14 | **NO** — C ext enabled |
| CVE-2025-69223 | Zip bomb decompression DoS | 3.13.3 | **LOW** — zip/gzip request body yok |
| CVE-2025-69224 | Non-ASCII header smuggling (pure-Py) | 3.13.3 | **NO** — C ext |
| CVE-2025-69225 | Non-ASCII decimals in Range header | 3.13.3 | **LOW** — Range işlemiyoruz |
| CVE-2025-69226 | Static path existence enumeration | 3.13.3 | **NO** — static yok |
| CVE-2025-69227 | POST assert-bypass infinite loop (-O mode) | 3.13.3 | **NO** — `-O` yok, POST yok |
| CVE-2025-69228 | `Request.post()` memory exhaustion | 3.13.3 | **NO** — POST handler yok |
| CVE-2025-69229 | Chunked read CPU blocking | 3.13.3 | **NO** — `request.read()` yok |
| CVE-2025-69230 | Cookie parse logging storm | 3.13.3 | **LOW** — `request.cookies` erişmiyoruz |
| CVE-2026-22815 | Header/trailer uncapped memory | 3.13.4 | **LOW** — GET-only, küçük headers |
| CVE-2026-34513 | DNS cache unbounded growth | 3.13.4 | **LOW** — client 5 RSS host (sabit) |
| CVE-2026-34514 | `content_type` header injection (client) | 3.13.4 | **NO** — untrusted content_type yok |
| CVE-2026-34515 | Windows NTLM static resource leak | 3.13.4 | **NO** — Windows değil + static yok |
| CVE-2026-34516 | Multipart header memory overflow | 3.13.4 | **NO** — multipart yok |
| CVE-2026-34517 | Multipart field pre-check OOM | 3.13.4 | **NO** — multipart yok |
| CVE-2026-34518 | Cookie/Proxy-Auth retained on redirect | 3.13.4 | **LOW-THEORETICAL** — news_scanner redirect'te cookie leak; ama cookie/auth SET etmiyoruz |
| CVE-2026-34519 | `reason` param header injection | 3.13.4 | **NO** — untrusted reason yok |
| CVE-2026-34520 | C parser null byte in headers | 3.13.4 | **LOW** — dashboard public sayılabilir (Replit), ama downstream trust surface'i dar |
| CVE-2026-34525 | Multiple Host header proxy bypass | 3.13.4 | **NO** — `add_domain` yok, reverse proxy yok |

**Agregat risk:** **LOW-MED.** Direkt exploit path yok. Dashboard
Replit/public expose edilirse header handling CVE'leri (34520/22815)
teorik olarak tetiklenebilir ama sadece DoS / log spam sınıfı etki.
Pre-mainnet hygiene şart.

**Karar:** **upgrade 3.10.0 → 3.13.4** (latest safe).

---

## Upgrade plan

**`requirements.txt` patch** (3 satır):
```diff
-Pillow==11.0.0
+Pillow==12.2.0

-python-dotenv==1.0.1
+python-dotenv==1.2.2

-aiohttp==3.10.0
+aiohttp==3.13.4
```

Diğer tüm pin'ler aynı kalıyor.

**Doğrulama adımları:**
1. `pip install -r requirements.txt --upgrade --break-system-packages`
   — sandbox testi.
2. `pip-audit -r requirements.txt` — 0 vuln olmalı.
3. `bash run_full_regression.sh` — 731 pass + 8 skip + 0 fail sabit
   kalmalı. Özellikle:
   - `test_keepalive_*.py` (aiohttp.web API sözleşmesi)
   - `test_news_scanner_*.py` (aiohttp ClientSession)
   - `test_banners.py` (Pillow Image.new API)
4. Syntax check tüm import path'lerde import error yok olmalı.
5. Windows `venv` üstünde de aynı `pip install --upgrade` + smoke çalıştırma
   gerekli (runtime prod ortamı Windows).

**Rollback:** git revert upgrade commit'i — pin'ler eski haline döner.
Hiçbir kod değişikliği upgrade'e bağlı değil (API compat bekleniyor:
3.10.x → 3.13.x non-breaking, Pillow 11 → 12 deprecations yok, dotenv
1.0 → 1.2 `load_dotenv` unchanged).

---

## Forward work

- **T11.4** pre-commit hook: `pip-audit` quarterly + CI gate.
- **T11.x** `pip list --outdated` disiplini: aiohttp-stability-track yerine
  `aiohttp>=3.13.4,<4.0.0` floor-pin kullanmak (supply chain sustain).
- **T10.2 Batch 3 (Epic 11)**: dashboard'u sadece localhost bind
  (`127.0.0.1:8080`) + Replit'te REPLIT reverse proxy authn'in önünde
  tutmak — header-injection CVE'lerini iyi-niyetli DoS riskinden tamamen
  çıkarır.

---

## Audit metadata

- **Tool version:** pip-audit (latest PyPI 2026-04-22)
- **Advisory DBs:** PyPA + OSV.dev
- **Scan command:** `pip-audit -r requirements.txt --desc`
- **JSON artifact:** production'da `scripts/run_pip_audit.sh` ile
  `logs/pip_audit_YYYYMMDD.json` — _(T11.4'te automate)_
- **Commit:** see `git log` for T10.3 upgrade commit
