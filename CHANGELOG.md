# Changelog

Tüm önemli sürüm değişiklikleri bu dosyada kronolojik olarak listelenir.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) · Versiyonlama: Phase-bazlı (Phase N.M).

## [Unreleased]
Aktif geliştirme branch'i.

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
