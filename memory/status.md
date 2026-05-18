# Project Status — 2026-05-15 Snapshot (post-ultra-audit Wave 1)

> Bu dosya canlı durumun özetidir. Detay için `02_POLYPAPER_YOL_HARITASI.md` + `03_POLYPAPER_PROGRESS_LOG.md`.
> **2026-05-13 ultra-audit**: `docs/audits/2026_05_13_ultra_audit.md` — memory stale fix.
> **2026-05-15 commit zinciri**: 8 duplikat commit (v1+v2) → 4 thematic + 3 follow-up commit + push.
> **2026-05-15 ultra-audit Wave 1**: `docs/audits/2026_05_15_ultra_audit.md` — 22 bulgu (3 Critical, 6 High, 8 Medium, 5 Low); Wave 1 (C-01 + C-02 + H-03 + L-01) kapalı, Wave 2-3 backlog.
> **2026-05-15 full regression**: 3,572 pass / 12 fail. REG-01 (app.py truncation, 9 fail) ✅ `8b13226`.
> **2026-05-17 REG-02 ✅**: kök neden ana dizin desync — Heddas working tree origin/main'in 12+ commit gerisindeydi (`config/env_whitelist.py` `list_groups`'suz, bot ImportError). stash+reset --hard ile senkronlandi → 6b8e670. Test/kod bug'ı değildi.
> **2026-05-18 log audit ✅**: bot ilk temiz boot. M-09 per_market_exposure 827 stale entry leak (`39e5bf3`), H-07 reconciliation log dinamik (`35ccb7b`), L-07 chainlink RPC env (`a9deec4`). Bot restart'ta doğrulandı: per_market 827→3, log dinamik.
> **2026-05-18 `.env` + Wave 3 ✅**: OP-01 duplikat LIVE_ENABLED silindi + L-07 CHAINLINK_RPC_URL eklendi. Wave 3 C-03: `test_live_trader_e2e.py` 9 test (`b1f219a`) + `test_ai_brain_cycle_e2e.py` 7 test (`7ea5674`+`0ca08cb`).
> **2026-05-18 Wave 3 kalan ✅**: M-07 mypy_baseline UTF-16→UTF-8 (`f8c23bd`, mypy 0 hata), M-03 ai_brain Pydantic LLM schema (`0ca08cb`). M-08 (555 class monolith) bölme planı audit ADDENDUM 5'te — uygulama backlog.
> **2026-05-18 OP-02 ✅**: `.env` Polymarket creds refresh. API key sorunlu değildi (Polymarket'te geçerli) — `.env` kopyaları eskiydi. `scripts/refresh_polymarket_creds.py` ile `POLYGON_PRIVATE_KEY`'den türetilip güncellendi. Bot restart 21:16 → PATH 1 PASS doğrulandı (401/400/derive satırları kayboldu, Chainlink `oracle smoke OK`).
> **2026-05-18 Wave 4 ✅**: M-04 httpx verify, M-05 `_place` notify, M-06 Sentry PII doktrin (`9b0b346`). M-02 & L-05 FALSE POSITIVE. L-02 risk>değer. Audit'in tüm Critical/High + anlamlı Medium'ları kapandı. Backlog: M-08, L-03/L-04/L-06.
> **2026-05-18 `/live` redesign**: Faz 1 kokpit ✅ (`e39042f`), Budget Reset butonu ✅ (`1b2f506`), Faz 2A veri katmanı ✅ (`9803037` live_trades INSERT bug, `d574924` allowance ♾️, `f99b0b0` streak PAPER etiketi). Açık: manuel trade settle eşleştirme (güvenilir kaynak yok — backlog), Faz 2B paneller, Faz 3.
> **2026-05-15 Wave 2 ✅**: H-01 AI Advisor auth (`fc04d1c`), H-02 budget race lock (`4820636`), H-04 deduct false-positive (`8959905`), H-05 admin gate + M-01 exception leak (`88573b9`), H-06 C-01'de kapanmış.

## TL;DR

**Mainnet LIVE 6 gündür** (2026-05-09'dan beri). Epic 11 + T11.x pre-mainnet gate'ler hepsi kapalı. Şu an **P1 sprint**'inde — coverage genişletme (P1-01) + AI Brain microservice ayrımı (P1-02). P0'da **3 gerçek açık** + 6 yeni P0 öneri var (P0-10..P0-15, audit'ten geliyor); hiçbiri **acil mainnet stop** seviyesinde değil.

### ⚠️ Memory drift düzeltmesi 2026-05-13

Önceki snapshot 7 P0 açık gösteriyordu — **kod kanıtı 4 tanesinin KAPALI olduğunu doğruladı** (P0-01, P0-03, P0-06, P0-09). Detay aşağıda + `docs/audits/2026_05_13_ultra_audit.md` §3.

### ✅ Commit zinciri konsolide 2026-05-15

Önceki "39 modified + 18 untracked" CLOSED. Local main, origin/main'in 8 duplikat commit önündeydi (v1 21:39 + v2 21:49 — aynı 4 subject iki kere atılmış); soft-reset + 4 thematic + 3 follow-up commit zincirine indirildi: `fd79c77` fix(fees) · `4fc5121` feat(p1-02+p2-04) · `e650172` test(p1-01) · `057090c` docs(memory+audit) + drift fix + P0-14 + P0-10. Origin'e push'a hazır.

## Yapı Sağlığı

| Metrik | Değer |
|--------|-------|
| Tests | **3,569 PASS / 0 FAIL / 42 skip** |
| Coverage | **%44.06** (ratchet: 42 → 43 → 45 → 50 → 55 → 60) |
| mypy strict | **0 hata** (55 source file) |
| ruff | **0 violation** |
| Bare-except | **0 strict / 0 advisory** |
| Secret leak regex | **13 × 3 scope = 0 match** |
| Mainnet blocker | **0** |

## P0 Açık (gerçek, kod-kanıtlı 2026-05-13 audit)

| # | Item | Effort | Durum | Kanıt |
|---|------|--------|-------|-------|
| P0-02 | POLYGON_PRIVATE_KEY plaintext → DPAPI/keyring | L | **AÇIK** | `config/settings.py:94-96` hâlâ plaintext |
| P0-04 | LIVE_BUDGET 2-faktör + 24h cooldown | M | **AÇIK** | `core/live_trader.py:107-116` tek faktör |
| P0-08 | 5m binary'ler default OFF (env-opt-in) | S | **AÇIK** | `config/settings.py:32` BTC default ENABLED |

## P0 Yeni Öneriler (2026-05-13 audit)

| # | Item | Effort | Kaynak |
|---|------|--------|--------|
| P0-10 | `fees_v2.py` precision 4 → 5 decimal (docs: 0.00001 USDC smallest) | XS | audit §2.4 |
| P0-11 | AI Advisor service auth (X-Internal-Key header) | S | audit §4.1 S-01 |
| P0-12 | Polymarket constant drift CI guard (haftalık docs MCP karşılaştır) | M | audit §4.4 D-05 |
| P0-13 | `PROTECTED_STRATEGIES` audit — top kazananları ekle | S | audit §4.3 C-02 |
| P0-14 | AI Brain log/comment "10min cycle" → "1h cycle" | XS | audit §4.3 C-03 |
| P0-15 | `dashboard.html` git'e ekle (untracked) | XS | audit §4.4 D-02 |

## P0 Kapanmış (kod kanıtlı)

| # | Item | Tarih | Kanıt |
|---|------|-------|-------|
| P0-01 | AI Brain auto-execute → approval queue | 2026-05-08 | `core/ai_brain.py:319-326,1993-2002,2011-2017` "NO auto-execute fallback" |
| P0-03 | Telegram `/export_private_key` sil | <2026-05-13 | grep "export_private_key" → 0 hit; `portfolio_handler.py:113` "PK access now via OS keychain" |
| P0-05 | Atomic backup + SHA256 + manifest + restore CLI | 2026-05-09 | — |
| P0-06 | `py-builder-relayer-client==0.0.1` pin | 2026-05-08 | `requirements.txt:39` |
| P0-07 | Reference price audit (Binance ground-truth + /ra panel) | 2026-05-09 | — |
| P0-09 | Kelly MAX_BET_PCT tek kaynağa | 2026-05-08 | `core/kelly.py:38-52` "P0-09 single-source-of-truth" |

## P1 Aktif

### P1-01 Coverage Source Genişlet (XL, partial)

- Baseline %42 → %44.06 (Wave 1 + 1b + 2 + 3 + 3b sonrası)
- Wave hedefleri: handler smoke, slug/fees helpers, engine_signals/fills pure helpers
- Sonraki: Wave 4 (backtest data_sources) + CI workflow + ratchet 43 → 45

### P1-02 AI Brain Microservice (XL, partial)

- Wave 1 + 2a + 2b + 2c kapalı (FastAPI service, /health /suggest /stats, 6 integration test PASS)
- Default: `AI_ADVISOR_ENABLED=false` — sıfır cost, opt-in
- Sonraki: Wave 3 approval queue flow (P0-01 ile birlikte)

### P1-07 mypy strict (CLOSED 2026-05-11)

- 0 hata, baseline regen, `--unsafe-fixes` clean, F601/B023/F821 fix

## Son 7 Gün Major Commit

```
9aeaa6d P1-02 Wave 1: AI Advisor scaffold (FastAPI + 6 TestClient tests)
304ffd5 P1-07 final: 2 last violations + mypy_baseline.txt regenerated
2d900bf P1-07 hard-close: 14 violations cleaned
b44e6fb P1-07 hard-close: F601 + B023 manuel fix
efae07e P1-07 followup: ruff --unsafe-fixes sweep
5ddc921 P1-07 followup: F821 critical bug fix (Becker dead code)
a265378 docs: GitHub full refresh (README + CHANGELOG modernize)
300870b feat(sprint3): Paket B + C V2 WSS meta events
ad906b9 feat: mod-first UX + V2 SDK + live trading stack
```

## Bekleyen Heddas Kararları

- (yok şu an — tüm bekleyen item'lar fix yapıldı, son crypto fee 0.072 → 0.07 dahil)

## Pending Veri / Telemetri Bekleyişi

- **P0-07 reference price audit** — 7 günlük production rapor **2026-05-15 itibarıyla** anlamlı olacak (external_prices 2026-05-08'de dolmaya başladı, audit infra hazır)
- **T4.7-B REST timing** — 24h `REST_TIMING_TELEMETRY=true` sonrası empirical p50 update (~80ms beklenir, heuristic 200ms ile 3.5x fazla)

## Cowork / Memory

- Bu memory sistemi bugün (2026-05-13) bootstrap edildi
- `CLAUDE.md` + `memory/` + `dashboard.html` kuruldu
- TASKS.md zaten mevcuttu (Epic-tarzı cleanup backlog), bozulmadı

## Bir Sonraki Oturumda Atılacak Adım

**1. Acil (1 oturum)**: 39+18 dosyalık uncommitted work'i 3-4 thematic commit'e böl ve push'la (audit §0 KRİTİK).

**2. Hızlı kazanımlar** (3×XS + 2×S effort, ~1 günlük iş):
   - P0-10 fees_v2 precision (4→5 decimal)
   - P0-14 AI Brain "10min cycle" log fix
   - P0-15 dashboard.html git ekle
   - P0-11 AI Advisor X-Internal-Key auth
   - P0-13 PROTECTED_STRATEGIES audit

**3. Orta vade** (M effort):
   - P0-12 Polymarket docs drift CI guard
   - P1-09 memory drift pre-commit hook
   - P0-04 LIVE_BUDGET 2-faktör

**4. Uzun vade** (L+ effort, mainnet stabil olduktan sonra):
   - P0-02 keyring/DPAPI migration
   - P0-08 5m default OFF (veya direktif değişti onayla)
   - P1-08 PostgreSQL, P1-05 Docker

Heddas tercihine bağlı — küçük adım küçük commit doktrinine sadık kal.
