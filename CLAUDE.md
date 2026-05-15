# Memory — PolyPaper Bot

> Working memory. Her oturum başında okunur. Tam liste için `memory/` klasörü.
> Son güncelleme: 2026-05-15 (post-commit-chain konsolide + push'a hazır).
> Audit dosyası: `docs/audits/2026_05_13_ultra_audit.md`.

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

**P1 sprint** (P0 = **6/9 done** — 2026-05-13 audit gerçek sayım; P1-01 + P1-02 wave'lar aktif).
- P1-01: Coverage source genişletme (%42 → %60 ratchet, şu an %44.06).
- P1-02: AI Brain microservice (Wave 1+2a+2b+2c kapalı, Wave 3 approval queue backlog).
- Açık P0: P0-02 (keyring), P0-04 (LIVE_BUDGET 2FA), P0-08 (5m default OFF) — hiçbiri mainnet blocker değil.
- Yeni P0 öneri (audit 2026-05-13): P0-10..P0-15 (precision, AI advisor auth, docs drift guard, PROTECTED_STRATEGIES, log fix, dashboard track).

**Test baseline:** 3,569 PASS / 0 FAIL / 42 skip · Coverage %44.06 · mypy strict 0 hata · ruff 0 violation (memory iddiası — Heddas yerelde son full regression koştursun).

**✅ Commit zinciri konsolide (2026-05-15):** Önceki "39 modified + 18 untracked" CLOSED. 8-commit duplikat zincir (v1 21:39 + v2 21:49) tek temiz 4-thematic + 3 follow-up commit'e indirgendi: `fd79c77` fix(fees) · `4fc5121` feat(p1-02+p2-04) · `e650172` test(p1-01) · `057090c` docs(memory+audit) + drift fix + P0-14 + P0-10. Origin'e push'lanıyor.

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
