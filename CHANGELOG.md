# Changelog

Tüm önemli sürüm değişiklikleri bu dosyada kronolojik olarak listelenir.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versiyonlama: Phase-bazlı (Phase N.M).

## [Unreleased]
Aktif geliştirme branch'i.

## [Mod-First UX + Live Trading Stack] — 2026-05-05/06

### Added — Mod-First Dashboard (Heddas direktifi UX redesign)
- `/start` artık **mod seçim ekranı** (PAPER vs LIVE)
- `telegram_bot/handlers/main_dashboard.py` (yeni) — paper/live menü ayrımı
- Cross-mod geçiş butonları (her menüde "diğer mod'a geç")
- `_get_paper_summary()` + `_get_live_summary()` — overview kartları

### Added — Live Trade History + CSV Export
- `telegram_bot/handlers/live_history_handler.py` (yeni)
- `/lh`, `/livehistory` komutu — paginated trade list (5/sayfa)
- Per-trade detay ekran: tarih, market, outcome, USDC, TX hash, Polygonscan link
- CSV export — **15 zengin alan** (`timestamp_iso`, `usdc_size`, `condition_id`,
  `transaction_hash`, `polygonscan_url`, vs.)
- PnL detay paneli — bugün+7gün, win rate, best/worst trade

### Added — Polymarket Live Trading Stack
- `data/polymarket_actions.py::approve_allowance()` — **3-contract approve**
  via Polymarket Relayer (gasless, $0 gas)
- `data/polymarket_actions.py::redeem_position()` — winning shares → pUSD
  (gasless via CTF.redeemPositions)
- `telegram_bot/jobs/auto_redeem_job.py` (yeni) — opsiyonel periodic redeem
- Live BUY/SELL UI (Heddas direktifi, mod-first içinde)
  - 4-ekran flow: Timeframe → Asset → Amount → Confirm
  - SELL panel PnL ile pozisyon listesi + 25/50/75/100% sat butonları
  - Settled detection: 🏆 winner → Redeem button | ⚰️ loser → "değersiz" mesaj

### Added — Polymarket Data API Coverage
- `data/polymarket_portfolio.py::fetch_activity()` — `/activity` endpoint
  (TRADE/REDEEM/SPLIT/MERGE)
- `data/polymarket_portfolio.py::fetch_closed_positions()` — `/closed-positions`
- `ActivityRow` + `ClosedPositionRow` dataclasses
- `PortfolioSnapshot` 6 paralel fetch (önceki 4'ten +2)

### Fixed — V2 SDK Breaking Changes (Polymarket V2 cutover 2026-04-28)
- `bal["allowance"]` (V1) → `bal["allowances"]` dict (V2) — 4 dosyada fix
- `OrderArgs(builder_code=...)` — V2'de field, V1'de options dict
- `PartialCreateOrderOptions` typed dataclass — V1 plain dict yerine
- `MarketOrderArgs + create_and_post_market_order` — decimal precision auto
- 3-adres sistemi netleştirildi: Profile/Safe Proxy (POLYGON_WALLET) ≠ Deposit ≠ Rabby EOA

### Added — Test Coverage Push (Wave 13-24)
- **3,474 tests pass** (önceki 2,999), **0 fail**
- Coverage **%21.2 → %43.7** (+22.5 pt, 24 wave)
- `tests/unit/conftest.py` shared fixtures (`db_stub`, `_AsyncCM`)
- `tests/unit/test_wave22_mega.py` — 130 modül parametrik import test
- Wave 13-24 sırasıyla: market UI, handler smoke, big modules, AI/engine real path,
  V2 fix verify, async CM mock, integration-lite, redeem flow

### Removed — Cleanup (Heddas direktifi)
- 14 `coverage_v*.txt` runtime raporları (artık gitignore'da)
- `_archive/_commit_msg_*.txt` (12 dosya, git log'da var)
- `_archive/commit_*.bat` (14 dosya, one-time kullanıldı)
- `scripts/cleanup_*_2026_04_29.bat` (3 dosya)
- `scripts/commit_fase_a_*.bat` + `scripts/final_cleanup_*.bat`

### Changed — Documentation
- `README.md` — yeni komut tablosu (mod-first, live, paper, ops kategorileri)
- `.gitignore` — `coverage_v*.txt` regex eklendi
- `.env.example` — Polymarket 3-adres sistemi açıklaması, `RELAYER_API_KEY`,
  `AUTO_REDEEM_ENABLED` flag'leri

## [T9.8-REG — Windows Integration Smoke] — 2026-04-24

### Verified
- Windows tarafı `pytest tests/integration/` **52/52 PASS** (15 test class)
- Paper-shadow identity 1000 event × 3 seed (42/1337/9001) — **zero drift**
- Fee oracle bit-identical, WS reconnect doctrine GREEN
- Pre-mainnet gate kapandı: T11.1 + T11.2 + T11.3 + T9.8-REG hepsi ✅

## [T11.8-B — Advisory Bare-Except Closure] — 2026-04-24

### Refactored
- 56 dosya × **373 bare-except site** (153 narrow + 220 documented `noqa`)
- 5 aşama: A data + B jobs + C handlers + D db + E `bot.py` + A4 data S2
- 6 doktrin sınıfı zımbalandı (full narrow / job-safety / router-dispatch / multi-source / boot orchestrator / data-feed reconnect)
- T11.6 render policy 4+ handler'a entegre

### Tooling
- `scripts/_t118b_a4_bulk_annotate.py` — bulk annotation script
- `bare_except_check.py --advisory` → 0 violation

## [T4.6-B — Fill Heuristic Calibration Sweep] — 2026-04-24

### Discovery
- Classic 199 trade × 200 markets sweep
- HEURISTIC -$4.87 vs EMPIRICAL -$6.51, delta_pnl_pct = -33.68%, **verdict FAIL**
- Sinyal üretimi etkilenmedi (WR 52.26% her ikisi), tüm sapma fill tarafında
- **Zihin çarpanı:** paper × 0.66 ≈ live beklentisi
- Forward work T4.7-C: `config/settings.py` FILL_SPREAD_COST 0.005→0.023, IMPACT 0.01→0.025, LATENCY_DRIFT 0.08→0.04

### Artifact
- `backtest/calibration/sweep_fill_heuristic_20260424_193711.json`

## [Epic 11 T11.3 — Rollback Dry-Run] — 2026-04-23

### Verified
- 4/4 senaryo PASS: S1 git revert + S2 rollback_sprint_2_1.py idempotent + S3 `/envt` audit log + S4 DB snapshot restore (Apr 19 backup)

### Fixed (HIGH severity)
- **Bulgu B:** backup atomic write eksikti — 2026-04-20 + 2026-04-23 backup dosyaları corrupt (729-780 MB, header null)
- Fix: `dest.tmp` → atomic rename pattern

### Status
- Pre-mainnet gate **3/3 ✅** (T11.1 + T11.2 + T11.3)
- Mainnet Go/No-Go karar aşamasında

## [Epic 11 T11.2 — Live Guard Validation Full Closure] — 2026-04-23

### Verified
- 5 canlı + G5 historical = **6/6 PASS**
- G3 commit `60a5efc`, whitelist fix `da11c2f` (PNL_DIVERGENCE_* 4 entry, G2 paraleli), G4 commit `59c68d2`
- Whitelist 33 → 37 knob

### Backlog
- Canlı ALERT branch (G2/G3/G4) 48h shadow run — Windows backlog

## [Epic 11 T11.2 — Ek İş Batch] — 2026-04-22

### Added
- `/live_guards` (alias `/lg`) admin Telegram cmd — 6 guard single snapshot
- LIVE_BUDGET runtime read (T6.1 parity)
- 15 yeni regression test
- Changelog `pnl` / `trades` kolonları NULL → dolu

### Commits
- `98d8f71`, `f3ffa04`, `bc87f42` — 3 atomic commit

## [Epic 10 — Security Pass + Post-Audit] — 2026-04-22

### Closed (T10.1-T10.5)
- T10.1: secret leak scan CLEAN (6 regex × 4 scope = 0 match)
- T10.2: 3 CRIT callback auth gap fixed (`filters_callback`, `brain_toggle_callback`, 5 strategy callbacks) via `_is_admin_call()` helper + 8 AST regression test
- T10.3: pip-audit 0 CVE
- T10.4: `.env.example` ↔ `settings.py` sync
- T10.5: `get_live_price` fresh-over-stale

### Post-Audit (T10.6-T10.10)
- `6998f6f` T10.6 hyperopt_apply_callback admin gate (CRIT — T10.2 kapsam kaçağı)
- `a9cbc89` T10.7 Batch 2 exception leak (2 site)
- `bdff7ff` T10.8 secret regex 6→**13** (+AKIA / hf_ / sk-proj- / sk_live_ / sk_test_ / bare-64hex / BIP-39)
- `0cf35b3` T10.9 eth-* doc precision
- `9006853` T10.10 F4 reproducible grep

### Test Baseline
- 498 → **735 pass + 8 skip + 0 fail**, 3-seed deterministic
- 0 admin-gate eksik, 13 secret pattern × 3 scope, 0 CVE, 0 eval/shell

## [Epic 9 — Test Infrastructure Closure] — 2026-04-22

### Coverage
- 17.5% → **21.2%** (502 → 723 pass + 8 skip + 0 fail)

### Added
- T9.6: 8 commit × 160 test × 8 critical-path modül (502 → 662 pass)
- T9.7: market_recorder UI↔engine explicit parity (5 ghost class doctrine tamamlandı)
- T9.8: 3 integration dosyası × 50 test (Engine boot + Single Fee Oracle PnL identity + WS reconnect)
- T9.10: WS fixture isolation hardening (microsecond ISO roundtrip race + WS_STALE_SEC env-pin)

### Doctrine
- DI pattern + autouse fixture + asyncio.run
- Mixin harness pattern
- ENV runtime re-read

### Post-Audit
- `d2cb442` Batch A (5 test correctness)
- `25bbd4f` Batch B+C (doc accuracy + T10.5/T11.4/T11.5 forward work)

## [Epic 8 — Bare-Except HIGH + LLM Guard] — 2026-04-22

### Refactored
- T8.1: 4 commit (auto_opt + engine + engine_signals + ai_brain) × 146 blok
- T8.2: `c967726` LLM rate-limit guard (429 Retry-After + cooldown + MIN_COST anti-bypass)

### Genel Tarama Closure
- `99844b9` B7 RiskLimits doc
- `990d234` engine `noqa` × 3 + `/diagnose` live count
- `69553c4` LLM_RATELIMIT_* runtime helpers (T6.1 pattern + whitelist `llm` group)

### Test
- 489 → 498 pass + 0 new regression

## [Epic 7 — Dead Code & Duplicate Logic] — 2026-04-22

### Closed
- T7.1-T7.5: 10 superseded smoke → `_archive/smoke_superseded_2026_04_21/`
- replay_engine v1 + v3 keep both

### T7.6 — Bare-Except Faz 3
- Aşama A (16 dosya, 16/16 ✅): 37 blok narrow + 4 unused import + observability shadow-bug rescue + 4 noqa gerekçe
- Aşama B (7 dosya × 7 atomic commit): 23 narrow + 21 noqa Faz 3 audit + 2 dead import. `core/` bare = 0
- Post-audit: 23 modül × 14 bulgu, 5 critical + 4 smell + 1 style = 10 atomic commit
- `pearson_like` triplicate → `core/stats_utils.py`
- `live_trader` ENV-override + whitelist
- `trade_journal` GC-safe
- `auto_optimizer` ROLLING_WR runtime

### B6 + B3 Closure
- `4fdc781` bg_task `_BG_TASK_OBJECTS` strong-ref set + 7 yeni test
- `9de66ef` ARCHITECTURE.md stats_utils + B6 cross-refs
- `9f1b8cd` TASKS.md B3 AUDIT-CLOSED

### Test
- 496 pass + 0 new regression

## [Epic 1-6 — Comprehensive Audit] — 2026-04-21

### Verified
- 3 paralel agent full verification
- **126 pass + 2 skip**, 0 yeni bug
- 1 cosmetic TASKS.md sayı hatası fix (`341→237` gerçek vs `341→276` iddia)

## [Epic 6 — UI↔Engine Ghost Audit] — 2026-04-21

### Closed (T6.1-T6.5)
- T6.1 PNL_PAUSE runtime fix: `/env_toggle` silent ghost toggle düzeltildi. Module-top constant → `_get_pnl_pause_threshold()` helper. Whitelist default drift -3.0 → -8.0. 82/82 test
- T6.3 Brain Flags Parity: AI Brain panel 4 ghost + boot-sync defect kapatıldı. Canonical 6-flag set + `kelly_sizing` virtual. 10+9 test GREEN
- Kelly DB-persist + whitelist runtime-readiness guard
- 5 ghost sınıfı doktrini
- 57 test toplam

## [Epic 5 — Atomicity / State] — 2026-04-21

### Fixed (T5.6)
- WS cap fix: prune wiring + deterministic priority_first + telemetry
- 14/14 test, cap skiplerinin state drift'i çözüldü

## [Epic 4 — Simulator Audit] — 2026-04-21

### Closed (T4.1-T4.4)
- Single fee oracle (`core/fees_v2.py` ONLY) — pre-Mart 2026 v1 + legacy category arşivlendi
- ENV-overridable slippage heuristics
- REST timing telemetry stub (`core/observability/rest_timing.py`)
- T4.5/T4.6/T4.7 yerel Windows backlog

## [Epic 2 — Root Cleanup] — 2026-04-20

### Closed
- Kök 100+ → 19 dosya
- 97 → `_archive/` 11 nested subfolder
- Mainnet-ready

## [T1.4 Faz 1 — Bare Except Narrowing] — 2026-04-20

### Refactored
- 65 blok CRITICAL path narrow + 30 satır dead code
- `core/`: 341 → 276 bare-except site

## [Phase 82e Sprint 6 — `/env_toggle` hot-tune] — 2026-04-20

### Added
- `/env_toggle` (alias `/envt`) admin Telegram komutu — bot restart olmadan runtime ENV knob'larını değiştir
- 23 whitelisted runtime knob (classic/fills/gates/sizing/ws/logging/risk grupları)
- `config/env_whitelist.py` — whitelist tek kaynak, yeni knob eklemek için tek satır
- `telegram_bot/handlers/env_toggle.py` — group list, detail view, set, reset aksiyonları
- `logs/env_toggle_audit.log` — tab-separated audit trail (ts, admin, action, key, old, new)
- `scripts/smoke_sprint6_env_toggle.py` — 25 kontrol smoke test
- `deploy_sprint6_env_toggle.bat` — deploy otomasyonu

### Changed
- `telegram_bot/bot.py` — +2 handler registration (`/env_toggle`, `/envt`)

### Guardrails
- Module-level ENV'ler (import-time okunan, örn. `MIN_ORDER_SHARES`, `ALLOWED_ZONES`, `SIGNAL_W_*`) **whitelist'e dahil edilmedi** — bu knob'lar restart gerektirir, yalan söylemiyoruz
- Tip + range validasyonu (bilinmeyen key reject, out-of-range reject)
- Admin-only (`settings.is_admin`), whitelist-only

### Verified
- Smoke: 25/25 PASS

## [Phase 82e Sprint 5 HOTFIX v6 — Classic TAKER Fill] — 2026-04-20

### Added
- `CLASSIC_TAKER_LIMIT_CEIL` env (default `0.99`) — classic TAKER emirleri için üst limit ceiling
- `TAKER_STUCK_TIMEOUT_SEC` env (default `120`) — stuck TAKER auto-cancel

### Fixed
- HOTFIX v5 sonrası GATE geçtiği halde fill'e gitmeyen (`cur>limit`) classic emirler — ceiling ile limit adaptif
- Stuck TAKER'ların pending'te sonsuza kadar kalması

### Verified
- Smoke: 9/9 PASS
- Live: `pend` düştü, `open` arttı

## [Phase 82e Sprint 5 HOTFIX v5 — Classic FREE-MODE] — 2026-04-20

### Added
- Classic plugin için FEE_TAIL / TOKEN_CAP / EMA / LOW_VOL / SLIPPAGE / TOO_EARLY gate bypass'ları
- `CLASSIC_RESPECT_FEE_TAIL` env (opt-in, default false)
- `CLASSIC_NOTIFY_RESOLUTION` env (default true) — Telegram resolution + exit bildirimi
- Resolution + exit notify template'leri

### Verified
- Smoke: 7/7 PASS

## [Phase 82e Sprint 5 HOTFIX v4] — 2026-04-19

### Added
- `/force_settle <market_slug>` admin komutu — manuel resolution
- Gamma API `outcomePrices` parse (unclamped resolution price)
- CLOB unclamped `get_resolution_price` fallback
- TF-aware `force_after 900s` settlement

### Fixed
- Resolution price 0/1 clamp sorunu

## [Phase 82e Sprint 5 FINAL] — 2026-04-19

### Added
- Classic strategy plugin — algoritmasız, direction_filter + threshold + TP/SL
- Fusion×29 granular apply (v15 asset/tf granularity)
- Martingale PARAM_SPACES
- `CLASSIC_BYPASS_ALL_GATES` unified 14-gate bypass
- `CLASSIC_RESPECT_UNSELLABLE`, `CLASSIC_RESPECT_ZONES` opt-in flags

### Changed
- `is_overfit()` sign-aware (önceki versiyon negative improvements ile yanlış sonuç veriyordu)
- `engine_signals.py` ENV override'ları eklendi

## [Phase 82e Sprint 4.6] — 2026-04-19

### Added
- Yeni "classic" strategy_type plugin sistemi
- `core/strategy_plugins.py` yeni modül

## [Phase 82e Sprint 4.3/4.4/4.5] — 2026-04-19

### Added
- `idx_ob_snap_slug_mst_ts` covering index (Sprint 4.3)
- `idx_ob_snap_atf_slug_mst_ts` 5-col covering index (Sprint 4.4)
- Apply-filter `/hyperopt_all` 21→8 types (Sprint 4.5)

### Performance
- **Discovery: 222s → 7s (32x)** covering index ile
- Split-backtest TEMP B-TREE GROUP BY kaldırıldı
- Trial budget %62 azaldı

### Fixed
- HyperOpt worker Windows cp1252 encoding crash → UTF-8 force

## [Phase 82e Sprint 2.1/2.2/2.3/3.1-3.3/4.1-4.2] — 2026-04-19

### Added
- `core/bg_task.py` — background task exception guard (11 files)
- `db/ro_connect.py` — RO retry/fallback logic
- STATUS IPC proof mechanism
- Cache key normalization
- PidFileLock `atexit` handler
- Memory guard (HYPEROPT_MEMORY_*)
- Parallel `N_JOBS` support
- `idx_ob_snap_slug_mst` index

## [Phase 82b.5] — 2026-04-17

### Fixed
- **Score 0.0000 root cause**: `HyperOptPipeline.prime_windows_cache()` artık worker'da trial loop ÖNCESİ çağrılıyor
- `buffer_hours`: `last_n*2` → `last_n//20` (400h → 10h for last_n=200)
- AVG cols dropped

## [Phase 82b.3] — 2026-04-17

### Fixed
- HyperOpt discovery `ts_ms` SQL bound + pipeline-level cache + STATUS IPC proof

## [Phase 82c + 82c Batch 2] — 2026-04-17

### Added
- PidFileLock None-safe
- Shadow WATCHED env-driven
- FUSION 30-40c zone block (AI_F_* loss bucket)
- Deploy bat `/T+8s+recheck` pattern

## [Phase 80/81] — 2026-04-16

### Added
- OPTIMIZE + APPLY_HYPEROPT actions
- `hyperopt_results` table v14 (asset/tf granular)
- `save_to_db` mekanizması
- Blok 9 action group
- Live↔Backtest adapter
- HyperOpt 9 live params
- AI STOP limit (2/cycle)
- `--last/--from/--random` flags
- ReplayEngine backtest in suggester

## [Phase 79] — 2026-04-15

### Added
- Zone filter, Brier alarm
- `/test_strategy`, `/report` komutları

### Changed
- Telegram komut listesi 80 → 20
- 35 files modified, 4 new

## [Phase 77] — 2026-04-12

### Added
- Trade Memory (persistent pattern öğrenme)
- Decision Explainer (`/why`)
- Experiment Runner (`/experiment`)
- Module Health (`/health`)

## [Phase 76] — 2026-04-12

### Added
- Markov Chain estimator
- Capital Allocator per-strategy buckets
- BondingYield live plugin (11th strategy)
- `/markov`, `/capital` komutları

## [Phase 74/74b] — 2026-04-12

### Changed
- Phase 62b aggressive mode geri alındı
- `MIN_COMPOSITE` 0.18→0.35, `CONVICTION` 0.15→0.30
- `EDGE` 0.30→0.45, `PARITY` 200→80bps

### Added
- Per-strategy lifecycle learning (exploration/evaluation/proven)
- `/lifecycle` komutu

## [Phase 73] — 2026-04-12

### Added
- **ROADMAP v1.0 COMPLETE**
- EMA/vol/orderbook skill modules
- Sharpe/Sortino metrics
- Regime-based Kelly decay

## [Phase 69-72] — 2026-04-12

- Phase 69: 2-Agent AI Brain (Optimist+Critic)
- Phase 70: 2D C(K,τ) surface, MCI, EV threshold, PennyContract
- Phase 71: PMXT bridge, whale tracker, event waves, latency monitor
- Phase 72: Evolutionary breeding, majority voting, PnL verification

## [Phase 67/68] — 2026-04-12

### Added
- Optuna hyperopt pipeline (11 spaces, TPE, overfit gate)
- MC Kelly validation (10K paths)
- AI Tournament nightly job
- Confluence gate (K/4 of 6)
- RSI+MACD+BB confidence multiplier
- BB squeeze, adaptive maker/taker

## [Phase 66] — 2026-04-12

### Added
- Master Roadmap v1.0 (8 phases, 112h/10wk)

## [Phase 65] — 2026-04-11

### Changed
- `engine_signals.py` split (1034→7 methods)
- `auto-resume=true` default
- `wal_autocheckpoint=5000`

### Added
- WR milestone monitor (50/100/200/500)
- Becker validation script

### Removed
- Fees v1, keepalive

## [Phase 63] — 2026-04-11

### Fixed
- **ROOT CAUSE:** `MAX_OPEN_POSITIONS=5` default 20 aktif stratejiyi bloke ediyordu
- `engine.py` 3 missing env overrides
- Verdict log masking

### Added
- `/diagnose` komutu

## [Phase 61/62] — 2026-04-11

- Phase 61: Full wipe + 20 new optimized strats (19 active, `HT-ETH` auto-paused)
- Phase 62: **CRITICAL** — pending orders never filled fix. 3 fill + 5 signal fixes

## [Phase 58-60] — 2026-04-11

- Phase 58: Zone 50-65c edge gate 0.80, Kelly exploration WR>52%, Thompson 40%
- Phase 59: Mistakes journal, ModelRouter, conviction sizing, prob gap log, reasoning JSON
- Phase 60: Remaining edge exit, weekend multiplier, round number gravity, cascade detector

## [Phase 55-57] — 2026-04-10/11

- Phase 55: Watchdog infrastructure, 39 critical tests, Türkçe UX sweep
- Phase 56: Watchdog VBS fix, balance race, 429 retry, `fmt_usd`. **Bot v9.7.7**
- Phase 57: WS empty-string guard, WAL `busy_timeout=10000`, watchdog v2 single-instance, 69 files archived

## [Phase 48-54] — 2026-04-09/10

- Phase 48: Pytest 45/45, CI workflow, `.gitignore`, correlation_id, circuit breaker
- Phase 49: MEGA AUDIT — 8 P0 fixes (live derive+verify, streak cooldown, auto-resume)
- Phase 50: 13/13 P0 fixes, Becker replay, regression checklist, `/alert` komutları
- Phase 51: HTML escape sweep 41/41, `/ai` + `/nl` intent parser, `/backtest_v2`, MAKER fill modes
- Phase 52: Full Telegram test 30/30 PASS
- Phase 53: `/trades` pagination, forced exit N sec before close (Phase 53b)
- Phase 54: 7 P0 critical — admin auth, div-by-zero, WS logging, risk boundary. **Bot v9.7.6**

## [Phase 47f.7-47f.10] — 2026-04-09

- 47f.7: Ops komutları (`/db_health`, `/rs`, `/h`), RiskState rename
- 47f.8: DB retention, heartbeat 80% pnl warning, **Bot v9.7.3**
- 47f.9: `fee_v2`, dynamic Kelly, maker rebate, `/stats_chart`
- 47f.10: Train/test split, canary deploy, `/stats_hub`, `/risk_hub`

## Eski Sürümler (Pre-Phase 47)

Detaylar: [docs/PHASES.md](docs/PHASES.md)
