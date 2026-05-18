# Memory — PolyPaper Bot

> Working memory. Her oturum başında okunur. Tam liste için `memory/` klasörü.
> Son güncelleme: 2026-05-15 (post-ultra-audit Wave 1 closure).
> Audit dosyaları: `docs/audits/2026_05_13_ultra_audit.md` + `docs/audits/2026_05_15_ultra_audit.md` (yeni, 22 bulgu).

## Me

**Heddas** (vfurkanv@gmail.com) — solo developer + operator. PolyPaper Bot'un tek geliştiricisi, denetçisi ve oncall'i. Türkçe konuşur, kod İngilizce yorum + Türkçe progress log karışık.

## Proje

**PolyPaper Bot** — Polymarket binary prediction market'ler için otonom trading botu.
- **Mainnet LIVE: 2026-05-09'dan beri** (6 gün) — gerçek pUSD ile.
- Shadow trading: 2026-05-03'ten beri (12 gün).
- 2 ay solo geliştirme, day 1'den production.
- Tek Telegram chat'ten kontrol (paper + live aynı kod tabanı).
- Klasör: `C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31`
- GitHub: `Heddas98/polypaper-bot`

## Mevcut Faz

**P1 sprint** + 2026-05-15 ultra-audit Wave 1 closure.
- P1-01: Coverage source genişletme (%42 → %60 ratchet, şu an %44.06 toplam ama kritik path <%30).
- P1-02: AI Brain microservice (Wave 1+2a+2b+2c kapalı, Wave 3 approval queue backlog).
- Açık P0 eski: P0-02 (keyring), P0-04 (LIVE_BUDGET 2FA), P0-08 (5m default OFF) — mainnet blocker değil.
- Audit 2026-05-13: P0-10 (fees precision) ✅, P0-14 (1h cycle log) ✅ kapalı; P0-11..P0-13, P0-15 backlog.
- **Audit 2026-05-15 Wave 1 ✅** (4 fix): C-01 is_admin backdoor, C-02 slug prompt-injection sanitize, H-03 /buy inf/nan reject, L-01 ruff F401.
- **REG-01 ✅** (`8b13226`): `services/ai_advisor/app.py` 2026-05-13 v2 zincirinde truncate (412→333 satır). v1 `ca6ff41`'den restore. test 29/29 PASS.
- **Audit 2026-05-15 Wave 2 ✅** (6 fix): H-01 AI Advisor cost-tiered auth (`fc04d1c`), H-02 budget race lock (`4820636`), H-04 deduct rowcount false-positive (`8959905`), H-05 live_handler admin gate + M-01 exception leak (`88573b9`), H-06 zaten C-01'de kapanmış.
- **REG-02 ✅** (2026-05-17): Kök neden — Heddas ana dizini (`Polyscout31`) origin/main'in 12+ commit gerisindeydi + bozuk working tree (`config/env_whitelist.py` `list_groups`'suz). `main.py` ImportError. Test/kod bug'ı DEĞİLDİ — working environment desync. Çözüm: stale `.git/index.lock` sil → `git stash` → `git reset --hard origin/main`. Ana dizin → `6b8e670`, bot import OK, 41/41 test PASS.
- **Log audit 2026-05-18 ✅** (bot ilk temiz boot sonrası): M-09 per_market_exposure unbounded growth/827 stale entry (`39e5bf3`), H-07 reconciliation log mesajı dinamik (`35ccb7b`), L-07 chainlink CHAINLINK_RPC_URL env override (`a9deec4`).
- **`.env` ✅ (2026-05-18)**: OP-01 duplikat `LIVE_ENABLED` satır 46 silindi (tek kaynak `true`), L-07 `CHAINLINK_RPC_URL=ethereum.publicnode.com` eklendi. OP-02 (stale Polymarket creds) açık — yeni API key Heddas almalı.
- **Wave 3 ✅ (2026-05-18, C-03)**: `test_live_trader_e2e.py` 9 test (`maybe_mirror` success path, `b1f219a`) + `test_ai_brain_cycle_e2e.py` 5 test (`run_brain_cycle` P0-01 invariant, `7ea5674`). Kritik-path e2e boşluğu kapandı.
- **Wave 3 kalan ✅ (2026-05-18)**: M-07 mypy_baseline UTF-16→UTF-8 regen (`f8c23bd`, mypy 0 hata doğrulandı), M-03 ai_brain `_BrainResponse` Pydantic LLM schema (`0ca08cb`, +2 test). M-08 (555 class / 24.5k satır monolith) — bölme planı audit ADDENDUM 5'te, tam uygulama backlog (test-kaybı riski, ayrı oturum).
- **OP-02 ✅ (2026-05-18)**: `.env` Polymarket creds stale değil artık. API key sorunlu DEĞİLDİ (Polymarket'te duruyordu) — sadece `.env` kopyaları eskiydi. `scripts/refresh_polymarket_creds.py` ile `POLYGON_PRIVATE_KEY`'den geçerli L2 creds türetilip `.env` güncellendi. Bot restart → PATH 1 stored creds PASS.
- Wave 4 backlog: M-02/M-04/M-05/M-06, L-02..L-06 + M-08 uygulama (test monolith böl).

**Test baseline (2026-05-15 full regression):** 3,572 pass / 12 fail / 63 skip. 12 fail = 9 REG-01 (app.py truncation, ÇÖZÜLDÜ) + 3 REG-02 (reproduce edilemedi). Ruff 0 violation. Coverage %44.06 toplam (kritik path <%30, C-03 açık). mypy strict baseline corrupted (M-07).

**✅ Commit zinciri (2026-05-15):** Önceki "39 modified + 18 untracked" CLOSED. 8 duplikat → 4 thematic + 3 follow-up commit (push: `1de180c..3289636`). Ultra-audit + Wave 1: `6e4b9ba` docs(audit) · `93136c6` fix(c-01) · `bda186a` fix(c-02) · `9c01bca` fix(h-03) · `47c7e25` fix(l-01) · `0a8acbd` chore(memory). REG-01: `8b13226` fix(regression) app.py truncation restore.

## Tech Stack

- **Python 3.11** (Windows 10/11 local, Docker P1-05 roadmap)
- **Polymarket V2 SDK**: `py-clob-client-v2==1.0.0` + `py-builder-relayer-client` (gasless)
- **AI**: Claude Sonnet 4.6 (Critic) + Groq Llama 70B (Optimist), 2-agent loop, $15 budget cap
- **DB**: SQLite + WAL (PostgreSQL migration = P1-08)
- **Bot**: Telegram (python-telegram-bot), 40+ komut, inline keyboards
- **Observability**: Sentry custom transactions (env-gated), reality_gap_job
- **Test**: pytest + pytest-cov, 3-seed determinism (42/1337/9001)

## Kişiler

Solo proje — sadece **Heddas**. Dış ekip yok.

## Çalışma Tarzı (Doktrin)

- **STRICT CLEANUP mod**: Spekülasyon yok. Her iddia → dosya + satır numarası.
- **"Para kazanana kadar para harcamayacağız"** — $0 cost ilkesi, her batch budget-aware.
- **Mainnet protected**: `core/ai_brain.py::PROTECTED_STRATEGIES` ve `PROTECTED_STRATEGY_TYPES={"classic"}` dokunulmaz.
- **Her commit + her PR**: Türkçe `feat/fix/docs/chore/test/deps` prefix.
- **Memory landmarks**: Büyük closure'larda `data_store/.auto-memory/project_*.md` doss çıkarılır.
- **Yapı**: Roadmap (`02_POLYPAPER_YOL_HARITASI.md`) → progress log (`03_POLYPAPER_PROGRESS_LOG.md`) + cleanup TASKS (`TASKS.md`) → memory landmark.
- **Memory drift kontrolü (yeni 2026-05-13)**: Her oturum açılışında `CLAUDE.md` / `memory/status.md` / `TASKS.md` üçü ile gerçek kod kanıtını karşılaştır. Tutarsızsa **koda güven, memory'yi güncelle**. 2026-05-13 audit: 4 P0 closed ama memory open gösteriyordu.

## Anahtar Komutlar (Telegram)

`/dashboard` `/d` · `/strategies` `/s` · `/buy` `/sell` · `/envt` `/env_toggle` (37 whitelist param) · `/lg` `/live_guards` (6 guard snapshot) · `/rg` `/reality_gap` (paper×0.66 vs live) · `/ra` `/ref_audit` (reference price audit) · `/recon` `/rc` (pUSD on-chain vs DB) · `/drt` (REST timing).

## Kritik Açık İşler (P0 — 2026-05-13 audit gerçek)

### Gerçek açık (kod-kanıtlı)

- **P0-02** POLYGON_PRIVATE_KEY plaintext → Windows DPAPI / keyring (`config/settings.py:94-96` hâlâ plaintext)
- **P0-04** LIVE_BUDGET 2-faktör + 24h cooldown (`core/live_trader.py:107-116` tek faktör)
- **P0-08** 5m binary'ler default OFF (`config/settings.py:32` BTC default ENABLED — direktif değişti mi onayla)

### Yeni öneri (2026-05-13 audit, detay: `docs/audits/2026_05_13_ultra_audit.md`)

- **P0-10** `fees_v2.py` precision 4 → 5 decimal (docs: smallest 0.00001 USDC)
- **P0-11** AI Advisor service auth (X-Internal-Key, `services/ai_advisor/app.py` hiç auth yok)
- **P0-12** Polymarket constant drift CI guard (haftalık docs MCP karşılaştır)
- **P0-13** `PROTECTED_STRATEGIES` audit (`core/ai_brain.py:102` sadece 2 entry)
- **P0-14** AI Brain "10min cycle" log → "1h cycle" (`core/ai_brain.py:163` vs `:105`)
- **P0-15** `dashboard.html` git'e ekle (97KB untracked)

### KAPALI (kod-kanıtlı, 2026-05-13 audit ile doğrulandı)

- **P0-01** ✅ 2026-05-08 (`ai_brain.py:319-326,1993-2002,2011-2017` "NO auto-execute fallback")
- **P0-03** ✅ (grep `export_private_key` → 0 hit; `portfolio_handler.py:113` "PK access now via OS keychain")
- **P0-05** ✅ 2026-05-09
- **P0-06** ✅ 2026-05-08 (`requirements.txt:39`)
- **P0-07** ✅ 2026-05-09
- **P0-09** ✅ 2026-05-08 (`core/kelly.py:38-52`)

## Son Closure'lar (referans)

- **P0-05** Atomic backup + SHA256 + manifest + restore CLI ✅ 2026-05-09
- **P0-07** Reference price audit (Binance kline ground-truth) ✅ 2026-05-09
- **Crypto fee fix** 0.072 → 0.07 (Polymarket docs cross-check) ✅ 2026-05-11
- **P1-01 Wave 1+1b+2+3+3b** Coverage tests ✅ 2026-05-11
- **P1-02 Wave 1** AI Advisor microservice scaffold (FastAPI + 6 test) ✅ 2026-05-11
- **P1-07** mypy strict — 0 hata, baseline regen ✅ 2026-05-11
- **Epic 11 FULL CLOSURE** Mainnet-ready (T11.1-T11.8 + 3 kritik pre-mainnet bug fix) ✅ 2026-04-24

## Preferences

- Türkçe konuş (kod İngilizce, log/karar Türkçe karışık)
- Bullet list yerine progress log entry stili (tarihli, dosya:satır referanslı)
- Her closure'da `memory/landmarks/` altına özet bırak
- Mainnet blocker varsa BÜYÜK BÜYÜK uyar; defense-in-depth'i mainnet blocker'dan ayrı tut
- `Plan` mode → küçük tek soru sor → büyük blok atma, küçük adım küçük commit
