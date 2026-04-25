# Geliştirme Phase'leri

PolyPaper Bot'un Phase 47'den Phase 82e Sprint 6'ya kadar olan tüm mühendislik evrimi. Her phase tarih, amaç, değişiklik ve sonuç formatında.

## Phase Özeti

| Phase | Tarih | Ana Değişiklik |
|---|---|---|
| 47f.7+ | 2026-04 | Ops komutları (`/db_health`, `/rs`, `/h`) + RiskState rename |
| 47f.8 | 2026-04-09 | DB retention + risk soft alerts + bot v9.7.3 |
| 47f.9 | 2026-04-09 | fee_v2, dynamic Kelly, maker rebate, `/stats_chart`, realized_slippage |
| 47f.10 | 2026-04-09 | Train/test split, canary, `/stats_hub`, `/risk_hub` |
| 48 | 2026-04-09 | Pytest suite 45/45, .gitignore+.env.example+SECRETS_ROTATION, Sentry, correlation_id, CI workflow |
| 49 | 2026-04-09 | MEGA AUDIT P0: live derive+verify, streak cooldown, auto-resume, is_maker, 31 dead imports removed |
| 50 | 2026-04-09 | 13/13 P0 + Becker replay, verify_db_health, regression checklist, `/alert` komutları |
| 51 | 2026-04-09 | HTML escape sweep 41/41, intent parser `/ai` + `/nl`, NL backtest `/backtest_v2`, MAKER/MAKER_HYBRID fill modes |
| 52 | 2026-04-10 | Full Telegram test 30/30, /trades + /shadow fix, matplotlib fix |
| 53 | 2026-04-10 | `/trades` pagination, DB migrations, `/streak_reset`, Phase 53b forced exit N sec before close |
| 54 | 2026-04-10 | 7 P0 fix — admin auth, div-by-zero, WS logging, risk boundary, settlement lock. Bot v9.7.6 |
| 55 | 2026-04-10 | watchdog.bat+vbs, 39 critical tests, UX Türkçe sweep, rollback.bat |
| 56 | 2026-04-10 | Watchdog VBS fix, BUG-CRIT-01 balance race, 429 retry, fmt_usd, backup.bat. Bot v9.7.7 |
| 56-P1 | 2026-04-10 | Adaptive PnL threshold, fmt_usd propagation. Bot v9.7.8 |
| 57 | 2026-04-11 | WS empty-string guard, WAL busy_timeout+sync, becker warning fix, watchdog v2 single-instance, 69 files archived |
| 58 | 2026-04-11 | 8 fixes — zone 50-65c edge gate, Kelly exploration, Thompson 40%, daily loss race, WS stale 20s |
| 59 | 2026-04-11 | 9 features — mistakes journal, ModelRouter, conviction sizing, prob gap log, reasoning JSON, event calendar, pattern discovery |
| 60 | 2026-04-11 | 10 actions — remaining edge exit, weekend multiplier, round number gravity, capital velocity, cascade detector, optimism tax, sub-25c Becker |
| 61 | 2026-04-11 | Full wipe + 20 new optimized strats (19 active). reset_and_start.bat |
| 62 | 2026-04-11 | CRITICAL — pending orders never filled fix. 3 fill + 5 signal fixes |
| 63 | 2026-04-11 | Mega audit — MAX_OPEN_POSITIONS=5 default blocked 20 active strats. `/diagnose` added |
| 65 | 2026-04-11 | engine_signals split (1034→7 methods), auto-resume=true, WR milestone monitor, Becker validation |
| 66 | 2026-04-12 | Master Roadmap v1.0 (8 phases, 112h/10wk) |
| 67 | 2026-04-12 | Optuna hyperopt pipeline (11 strat spaces, TPE, overfit gate), MC Kelly validation, AI Tournament nightly |
| 68 | 2026-04-12 | Confluence gate (K/4 of 6), RSI+MACD+BB multiplier, BB squeeze, adaptive maker/taker |
| 69 | 2026-04-12 | 2-Agent AI Brain (Optimist+Critic), OpenRouter 4-tier, Fermi decomposition, champion_tracker, reputation |
| 70 | 2026-04-12 | 2D C(K,τ) surface, MCI quality score, EV threshold, PennyContractStrategy |
| 71 | 2026-04-12 | PMXT bridge, whale tracker, event waves, latency monitor, spread signal |
| 72 | 2026-04-12 | Genetic breeding, majority voting, PnL verification |
| 73 | 2026-04-12 | EMA/vol/orderbook skill modules, Sharpe/Sortino metrics, regime-based Kelly decay |
| 74 | 2026-04-12 | Profitability fix — reverted Phase 62b aggressive mode |
| 74b | 2026-04-12 | Per-strategy lifecycle learning (exploration/evaluation/proven) |
| 75 | 2026-04-12 | Ultra Analysis Report — 10 makale+13 repo analiz |
| 76 | 2026-04-12 | Markov Chain estimator, Capital Allocator, BondingYield live plugin |
| 77 | 2026-04-12 | Trade Memory, Decision Explainer (`/why`), Experiment Runner, Module Health |
| 78 | 2026-04-12 | Bug fix sprint — 11 bugs (Brier/breed/optimize HTML esc, hyperopt async, WS 300s force reconnect) |
| 79 | 2026-04-15 | ULTRA COMPLETE — 4 sprints. Zone filter, Brier alarm, 80→20 cmds, `/test_strategy`, `/report` |
| 80 | 2026-04-16 | AI-HyperOpt integration — OPTIMIZE+APPLY_HYPEROPT actions, hyperopt_results table (v14), Blok 9 |
| 81 | 2026-04-16 | Unified Strategy — Live↔Backtest adapter, HyperOpt 9 live params, AI STOP limit, progress display |
| 82a | 2026-04-17 | Phase 82a hotfix |
| 82b.3 | 2026-04-17 | HyperOpt discovery fix — ts_ms SQL bound + pipeline-level cache + STATUS IPC proof |
| 82b.5 | 2026-04-17 | HyperOpt priming — prime_windows_cache() in worker BEFORE trial loop (Score 0.0000 root cause) |
| 82c | 2026-04-17 | Batch 2 — PidFileLock None-safe, Shadow WATCHED env-driven, FUSION 30-40c zone block |
| 82e Sprint 2.1/2.2 | 2026-04-19 | bg_task exception guard (11 files), db/ro_connect RO retry/fallback |
| 82e Sprint 2.3/3.1-3.3/4.1-4.2 | 2026-04-19 | STATUS IPC + cache key norm + atexit + memory guard + parallel N_JOBS + idx_ob_snap_slug_mst |
| 82e Sprint 4.3 | 2026-04-19 | Covering idx — Discovery 222s→7s (32x) |
| 82e Sprint 4.4 | 2026-04-19 | Split-BT idx (5-col covering), TEMP B-TREE GROUP BY eliminated |
| 82e Sprint 4.5 | 2026-04-19 | Apply-filter — `/hyperopt_all` 21→8 types (trial budget -62%) |
| 82e Sprint 4.6 | 2026-04-19 | Classic strategy plugin — no algorithm, direction_filter + threshold + TP/SL |
| 82e Sprint 5 | 2026-04-19 | Unified 5-in-1 — Fusion×29 granular apply, martingale PARAM_SPACES, is_overfit sign-aware |
| 82e Sprint 5 HOTFIX | 2026-04-19 | Classic UNSELLABLE → CLASSIC_BYPASS_ALL_GATES unified 14-gate bypass |
| 82e Sprint 5 HOTFIX v4 | 2026-04-19 | Gamma outcomePrices parse, CLOB unclamped get_resolution_price, TF-aware force_after, `/force_settle` |
| 82e Sprint 5 HOTFIX v5 | 2026-04-20 | Classic FEE_TAIL/TOKEN_CAP/EMA/LOW_VOL/SLIPPAGE/TOO_EARLY bypass + resolution notify |
| 82e Sprint 5 HOTFIX v6 | 2026-04-20 | Classic TAKER limit ceiling (`CLASSIC_TAKER_LIMIT_CEIL`=0.99) + stuck cancel 120s |
| 82e Sprint 6 | 2026-04-20 | `/env_toggle` (`/envt`) admin cmd — 23 whitelisted runtime knob hot-tune, .env patch + audit |
| Epic 1-3 | 2026-04-20 | Ghost modül purge, Root cleanup 100+ → 19, Classic bypass audit |
| T1.4 Faz 1 | 2026-04-20 | 65 blok bare-except narrow + 30 dead code, `core/` 341 → 276 |
| Epic 4 | 2026-04-21 | Simulator audit — Single Fee Oracle (`fees_v2`), ENV slippage heuristics, REST timing telemetry stub |
| Epic 5 | 2026-04-21 | Atomicity/state — T5.6 WS cap prune + deterministic priority + telemetry |
| Epic 6 | 2026-04-21 | UI↔Engine ghost audit — T6.1 PNL_PAUSE + T6.3 brain flags parity + Kelly DB-persist + 5 ghost class doctrine |
| Epic 1-6 Audit | 2026-04-21 | 3 paralel agent comprehensive verification — 126 pass + 2 skip, 0 yeni bug |
| Epic 7 | 2026-04-22 | Dead code & duplicate logic — T7.1-T7.5 + T7.6 Aşama A/B + post-audit. `pearson_like` → `core/stats_utils.py`. `core/` bare = 0 |
| Epic 8 | 2026-04-22 | T8.1 bare-except HIGH (146 blok) + T8.2 LLM rate-limit guard (429 Retry-After + MIN_COST anti-bypass) + LLM_RATELIMIT_* runtime helpers |
| Epic 9 | 2026-04-22 | Test infrastructure — T9.1-T9.10 + post-audit. Coverage 17.5% → 21.2%. 502 → 723 pass + 8 skip. 3-seed deterministic |
| Epic 10 | 2026-04-22 | Security pass — T10.1-T10.10. 13 secret regex pattern, admin gate 3 CRIT fix, hyperopt admin gate, exception leak Batch 2. 723 → **735 pass** |
| Epic 11 T11.2 | 2026-04-22/23 | Live guard validation — 5 canlı + 1 historical = 6/6 PASS. `/live_guards` cmd + 15 yeni test + LIVE_BUDGET runtime read |
| Epic 11 T11.3 | 2026-04-23 | Rollback dry-run 4/4 PASS — git revert + idempotent rollback + envt audit + DB snapshot. **HIGH:** backup atomic write fix (Bulgu B) |
| T4.6-B | 2026-04-24 | Fill heuristic calibration sweep — verdict FAIL, paper×0.66 ≈ live, forward T4.7-C settings.py update |
| T11.8-B | 2026-04-24 | Advisory bare-except sweep — 56 dosya × 373 site (153 narrow + 220 noqa). 6 doktrin sınıfı |
| T9.8-REG | 2026-04-24 | Windows integration smoke 52/52 PASS. Paper-shadow identity 1000 event × 3 seed zero drift. Pre-mainnet gate kapandı |

## Detaylı Phase Notları

### Phase 48 — Gap Closure (2026-04-09)
İlk professional hygiene sprint. Pytest suite (45/45 pass), `.gitignore` + `.env.example` + `SECRETS_ROTATION.md` ilk kez eklendi. Sentry opt-in, correlation_id filter (`[cid=-]`), config validator, circuit breaker, off-site backup, CI workflow, dead-code audit. `/compare ... split` run_split wiring, auto_promote daily job.

**Kritik sonuç:** 0 errors, bot ilk kez "deployable" hale geldi.

### Phase 49 — MEGA AUDIT Recovery (2026-04-09)
8 P0 fix — A-01 live derive+verify, A-02 streak cooldown, A-03 auto-resume, is_maker migration, 31 dead imports removed, safe_html utility, smoke suite 9/9 pass.

### Phase 55 — Test & Stabilite (2026-04-10)
Watchdog infrastructure (watchdog.bat + watchdog.vbs), 39 critical tests, UX Türkçe sweep (27 string / 9 dosya), emoji palette (6 dosya), rollback.bat, backfill cred check. 19/19 PASS.

### Phase 57 — Bug Fix Sprint (2026-04-11)
**Root cause bulundu:** Hourly crash loop warm-up'ı engelliyor → no trades. WS empty-string guard, WAL `busy_timeout=10000` + `synchronous=NORMAL`, becker warning fix, watchdog v2 single-instance, 69 files archived.

### Phase 63 — Mega Audit (2026-04-11)
**Root cause:** `MAX_OPEN_POSITIONS=5` default 20 aktif stratejiyi blokluyordu. Engine.py'de 3 missing env override fixed. `/diagnose` komutu eklendi. 142 files, 0 errors. $10,386 bakiye ~5-10% inflated çıktı.

### Phase 67 — Parameter Optimization (2026-04-12)
**Optuna hyperopt pipeline:** 11 strategy spaces, TPE, overfit gate. MC Kelly validation (10K paths, numpy). AI Tournament nightly job. `/hyperopt` `/mc_kelly` komutları. 36 tests.

### Phase 69 — AI Brain Upgrade (2026-04-12)
**2-Agent AI:** Optimist+Critic→synthesis. OpenRouter SDK 4-tier fallback. Fermi decomposition. `champion_tracker.py`, `reputation.py`. 20 tests.

### Phase 73 — Skill Modules & Kelly Decay (2026-04-12)
**ROADMAP v1.0 COMPLETE.** EMA/vol/orderbook skills, Sharpe/Sortino metrics, regime-based Kelly decay (`KELLY_DECAY_TRENDING=0.25`, `KELLY_DECAY_RANGING=0.167`, `KELLY_DECAY_VOLATILE=0.125`). 45 tests.

### Phase 77 — Learning Brain (2026-04-12)
Trade Memory (persistent pattern öğrenme), Decision Explainer (`/why`), Experiment Runner (`/experiment`), Module Health (`/health`). 4 new + 5 modified files. 6/6 smoke PASS.

### Phase 80 — AI-HyperOpt Integration (2026-04-16)
OPTIMIZE + APPLY_HYPEROPT actions, `hyperopt_results` table (v14 — asset/tf granular), `save_to_db`, Blok 9, `_ALLOWED_PARAMS` expanded, `engine→self.engine` fix. `deploy_phase80.bat` ready.

### Phase 82b.5 — HyperOpt Priming (2026-04-17)
**Score 0.0000 root cause:** Discovery ran INSIDE per-trial `wait_for(300s)`, got cancelled before cache populated, every trial MISS-ed.

**Fix:** `HyperOptPipeline.prime_windows_cache()` called in worker BEFORE trial loop, bounded by `STUDY_TIMEOUT_SEC`. `buffer_hours last_n*2 → last_n//20` (400h → 10h for last_n=200). AVG cols dropped.

### Phase 82e Sprint 4.3 — Covering Index (2026-04-19)
**Discovery 222s → 7s (32x).** Yeni `idx_ob_snap_slug_mst_ts` covering index. `hyperopt_worker` UTF-8 stdout fix (Windows cp1252 crash giderildi).

### Phase 82e Sprint 4.6 — Classic Plugin (2026-04-19)
Yeni **"classic" strategy_type** — algoritma yok, sadece `direction_filter + threshold + TP/SL`. PARAM_SPACES'ta yok (hyperopt auto-skip). Smoke 10/10 PASS.

### Phase 82e Sprint 5 FINAL + HOTFIX'ler (2026-04-19)
**Unified 5-in-1 deploy:**
- Fusion×29 granular apply (v15 asset/tf)
- martingale PARAM_SPACES
- is_overfit sign-aware
- engine_signals ENV overrides
- Classic ALLOWED_ZONES bypass bugfix

**HOTFIX v3:** Classic için tek ENV ile 14-gate bypass (`CLASSIC_BYPASS_ALL_GATES=true`).

**HOTFIX v4 (Resolution Path):** Gamma `outcomePrices` parse + CLOB unclamped `get_resolution_price` + TF-aware `force_after 900s` + `/force_settle` admin cmd.

**HOTFIX v5 (2026-04-20):** Classic plugin için 6 ek gate bypass (FEE_TAIL / TOKEN_CAP / EMA / LOW_VOL / SLIPPAGE / TOO_EARLY). Opt-in flag: `CLASSIC_RESPECT_FEE_TAIL`. Telegram resolution + exit notify (`CLASSIC_NOTIFY_RESOLUTION=true`).

**HOTFIX v6 (2026-04-20):** v5 sonrası GATE geçip fill'e gitmeyen classic emirler (`cur>limit`) için TAKER limit ceiling eklendi. `CLASSIC_TAKER_LIMIT_CEIL=0.99` + `TAKER_STUCK_TIMEOUT_SEC=120` stuck auto-cancel. Smoke 9/9.

### Phase 82e Sprint 6 — `/env_toggle` Hot-Tune (2026-04-20)
**Amaç:** Her hotfix sonrası bot restart + `.env` elle edit döngüsünü kır.

**Ne yapar:** Admin Telegram komutu ile 23 whitelisted runtime knob'u (`CLASSIC_BYPASS_ALL_GATES`, `TAKER_STUCK_TIMEOUT_SEC`, `MIN_COMPOSITE`, `CONVICTION_MIN`, `ADAPTIVE_MAKER_ENABLED` vb.) bot kapatmadan değiştir. `os.environ` + `.env` patch + `logs/env_toggle_audit.log`.

**Komutlar:**
- `/env_toggle` — gruplu liste (◆ = default'tan sapan)
- `/env_toggle KEY` — detay (type, default, min/max, current)
- `/env_toggle KEY VALUE` — set (tip + range valide)
- `/env_toggle reset KEY` — default'a dön (`.env` line silinir)

**Guardrails:** Module-level import-time ENV'ler whitelist'e DAHİL EDİLMEDİ (`MIN_ORDER_SHARES`, `ALLOWED_ZONES`, `SIGNAL_W_*`) — bunlar restart gerektirir, yalan söylemeyiz.

**Yeni dosyalar:** `config/env_whitelist.py`, `telegram_bot/handlers/env_toggle.py`, `scripts/smoke_sprint6_env_toggle.py`, `deploy_sprint6_env_toggle.bat`.

Smoke: 25/25 PASS.

### Epic 7 — Dead Code & Duplicate Logic (2026-04-22)
**T7.1-T7.5:** 10 superseded smoke → `_archive/smoke_superseded_2026_04_21/`. `replay_engine` v1+v3 her ikisi keep.

**T7.6 Faz 3 bare-except (113 blok × 26 dosya):** 3 aşamaya bölündü:
- **Aşama A (16 dosya):** 37 narrow + 4 unused import + observability shadow-bug rescue + 4 noqa
- **Aşama B (7 dosya, 7 atomic commit):** 23 narrow + 21 noqa + 2 dead import. **`core/` bare = 0**
- **Post-audit:** 23 modül × 14 bulgu, 5 critical + 4 smell + 1 style. `pearson_like` triplicate → **`core/stats_utils.py`**. `live_trader` ENV-override + whitelist. `trade_journal` GC-safe. `auto_optimizer` ROLLING_WR runtime. **B6:** bg_task `_BG_TASK_OBJECTS` strong-ref set + 7 yeni test.

### Epic 8 — Bare-Except HIGH + LLM Guard (2026-04-22)
**T8.1:** ai_brain (47) + auto_optimizer (22) + engine (35) + engine_signals (42) = **146 HIGH-risk blok** narrow.

**T8.2 LLM Rate-Limit Guard:** Anthropic 429 Retry-After + cooldown + MIN_COST anti-bypass. `LLM_RATELIMIT_*` runtime helpers (T6.1 pattern + whitelist `llm` group).

498 pass + 0 new regression.

### Epic 9 — Test Infrastructure (2026-04-22)
**Coverage 17.5% → 21.2%, 502 → 723 pass + 8 skip + 0 fail.**

- **T9.6:** 8 commit × 160 test × 8 critical-path modül. Mixin harness pattern + ENV runtime re-read doktrini zımbalandı.
- **T9.7 ghost guards:** `market_recorder` UI↔engine explicit parity, 11 test × 5 class. **5 ghost sınıfı doktrini tamamlandı.**
- **T9.8 integration:** 3 dosya × 50 test. Engine boot + Single Fee Oracle PnL identity + WS reconnect.
- **T9.10 WS fixture isolation:** microsecond ISO roundtrip race + WS_STALE_SEC env-pin. 3-seed determinism GREEN (42/1337/9001).

**Doktrin:** DI pattern + autouse fixture + asyncio.run.

### Epic 10 — Security Pass (2026-04-22)
**T10.1-T10.5 closed:** secret leak scan CLEAN (6 regex × 4 scope = 0 match). 3 CRIT callback auth gap fix (`filters_callback`, `brain_toggle_callback`, 5 strategy callbacks) via `_is_admin_call()` + 8 AST regression test. pip-audit 0 CVE. `.env.example` ↔ `settings.py` sync. `get_live_price` fresh-over-stale.

**Post-audit T10.6-T10.10:**
- T10.6: `hyperopt_apply_callback` admin gate (CRIT — T10.2 kapsam kaçağı)
- T10.7: Batch 2 exception leak (2 site)
- T10.8: secret regex 6 → **13** (+AKIA / hf_ / sk-proj- / sk_live_ / sk_test_ / bare-64hex / BIP-39)
- T10.9: eth-* doc precision
- T10.10: F4 reproducible grep

**Test:** 498 → **735 pass + 8 skip + 0 fail**.

### Epic 11 — Mainnet Go/No-Go Pre-Gate

**T11.2 Live Guard Validation (2026-04-22/23):** 5 canlı + 1 historical = **6/6 PASS**. Guards: G1 Kill Switch (file-channel 96ms + sticky memory) / G2 Live Budget (1.49→0.5→1.49 runtime + whitelist fix) / G3 PNL Divergence / G4 Rolling WR Kill / G5 ROLLING_WR_KILL changelog historical / G6 24h staleness. `/live_guards` (`/lg`) admin cmd + 15 yeni regression test + LIVE_BUDGET runtime read (T6.1 parity). Whitelist 33 → 37 knob.

**T11.3 Rollback Dry-Run (2026-04-23):** **4/4 senaryo PASS.** S1 git revert + S2 `rollback_sprint_2_1.py` idempotent + S3 `/envt` audit log + S4 DB snapshot restore (Apr 19 backup). **HIGH-severity Bulgu B:** backup atomic write eksikti — 2026-04-20 + 2026-04-23 backup'ları corrupt (729-780 MB, header null). Fix: `dest.tmp` → atomic rename pattern. **Pre-mainnet gate 3/3 ✅.**

**T9.8-REG Windows Integration (2026-04-24):** `pytest tests/integration/` **52/52 PASS** (15 test class). Paper-shadow identity 1000 event × 3 seed zero drift. Fee oracle bit-identical, WS reconnect doctrine GREEN.

**T4.6-B Fill Heuristic Sweep (2026-04-24):** classic 199 trade × 200 markets. HEURISTIC -$4.87 vs EMPIRICAL -$6.51, delta_pnl_pct = **-33.68%**, verdict FAIL. Sinyal üretimi etkilenmedi (WR 52.26% her ikisi), tüm sapma fill'de. **Zihin çarpanı: paper × 0.66 ≈ live.** Forward T4.7-C: FILL_SPREAD_COST 0.005→0.023, IMPACT 0.01→0.025, LATENCY_DRIFT 0.08→0.04.

**T11.8-B Advisory Bare-Except (2026-04-24):** **56 dosya × 373 site** (153 narrow + 220 documented `noqa`). 5 aşama (A data + B jobs + C handlers + D db + E `bot.py` + A4 data S2). 6 doktrin sınıfı. T11.6 render policy 4+ handler entegre. `bare_except_check.py --advisory` 0 violation.

## Phase Numaralandırma Notları

- Phase 64 ve Phase 66 arası Phase 65'e sığdırıldı (Phase 66 Roadmap document).
- Phase 75 ayrı bir phase değil, analiz raporu.
- Phase 82a/b/c/d/e parallel fix stream'ler — her biri bağımsız deploy'lar.

## Mevcut (2026-04-25)

**Aktif milestone:** Epic 11 (Mainnet Go/No-Go) — T11.1 + T11.2 + T11.3 + T9.8-REG **hepsi ✅** kapandı, mainnet bloklayıcı yok. T11.8-B advisory zone temizliği bitti, T4.6-B fill heuristic kalibrasyon sweep'i `paper×0.66 ≈ live` zihin çarpanını ortaya koydu. Bot v9.7.9, 18+ engine + Classic plugin + AI Brain (Claude Sonnet 10dk cycle). Shadow live aktif ($1.49 USDC, $1/trade, 3 strateji). Bakiye ~$10,386, toplam PnL +$355, WR %57, 1,417+ trade.

**Test baseline:** 735 pass + 8 skip + 0 fail, 3-seed deterministic (42/1337/9001).
**Security baseline:** 13 secret regex × 3 scope = 0 match, pip-audit 0 CVE, 0 admin-gate eksik, 0 eval/shell.
**Bare-except:** `core/` = 0, advisory zone kampanyası 56 dosya × 373 site closure.
