@echo off
REM ════════════════════════════════════════════════════════════════════════
REM GIT COMMIT CHAIN — Heddas 2026-05-06
REM
REM Önkoşul: scripts\master_cleanup_2026_05_06.bat ÇALIŞTIRILMIŞ olmalı.
REM Bu bat: 7 atomic commit zinciri. PUSH ETMEZ — Heddas son aşamada
REM `git push origin main` ile manuel push edecek.
REM ════════════════════════════════════════════════════════════════════════
SETLOCAL EnableDelayedExpansion
cd /d "%~dp0\.."

echo ============================================================
echo Git Commit Chain — 2026-05-06
echo ============================================================

echo.
echo === Pre-flight: git status ===
git status --short
echo.
pause

REM ── Commit 1: Cleanup root + _archive + scripts ─────────────────────
echo.
echo [1/7] chore(cleanup): root + _archive + scripts temp files
git add -A .gitignore
git add -u
REM Untracked yeni dosyalar bir sonraki commit'lerde eklenecek
git diff --cached --stat
git commit -m "chore(cleanup): root + _archive + scripts temp files (2026-05-06)" ^
           -m "- 14 coverage_v*.txt (runtime reports, gitignore'a eklendi)" ^
           -m "- _archive/_commit_msg_*.txt (12 dosya, git log'da mevcut)" ^
           -m "- _archive/commit_*.bat (14 one-time bat)" ^
           -m "- scripts/cleanup_*_2026_04_29 (3 one-time bat)" ^
           -m "- scripts/commit_fase_a_* + final_cleanup_* (one-time)" ^
           -m "- .gitignore: coverage_v*.txt eklendi"
if errorlevel 1 echo (commit 1: nothing to commit veya hata - devam)

REM ── Commit 2: Allowance V2 + 3-contract Relayer fix ────────────────
echo.
echo [2/7] fix(allowance): V2 dict + 3-contract approve via Relayer
git add data/polymarket_actions.py data/polymarket_portfolio.py
git add core/live_trader.py core/allowance_preflight.py
git add .env.example requirements.txt
git commit -m "fix(allowance): V2 dict + 3-contract approve via Polymarket Relayer" ^
           -m "Polymarket V2 cutover (2026-04-28) breaking changes:" ^
           -m "- bal['allowance'] -> bal['allowances'] dict per-spender" ^
           -m "- 3-contract approve: pUSD -> CTF + CTF Exchange + Neg Risk" ^
           -m "- Relayer SDK gasless approve (RELAYER_API_KEY headers)" ^
           -m "- redeem_position() gasless via CTF.redeemPositions" ^
           -m "- 3-adres sistem dokumante: Profile/Deposit/Rabby"
if errorlevel 1 echo (commit 2: nothing to commit veya hata - devam)

REM ── Commit 3: V2 SDK MarketOrderArgs + PartialCreateOrderOptions ───
echo.
echo [3/7] fix(sdk): V2 OrderArgs + MarketOrderArgs + PartialCreateOrderOptions
git add core/live_trader.py
git commit -m "fix(sdk): V2 MarketOrderArgs + PartialCreateOrderOptions dataclass" ^
           -m "- create_and_post_market_order auto decimal precision" ^
           -m "- OrderArgs.builder_code field (V2'de OrderArgs icinde)" ^
           -m "- PartialCreateOrderOptions(tick_size, neg_risk) typed" ^
           -m "- Fallback path: eski OrderArgs+create_order eski SDK icin"
if errorlevel 1 echo (commit 3: nothing - devam)

REM ── Commit 4: Mod-first dashboard + Live UX redesign ───────────────
echo.
echo [4/7] feat(ux): mod-first dashboard + live UX redesign
git add telegram_bot/handlers/main_dashboard.py
git add telegram_bot/handlers/live_history_handler.py
git add telegram_bot/handlers/live_handler.py
git add telegram_bot/bot.py
git commit -m "feat(ux): mod-first dashboard + live history + redeem UI" ^
           -m "- /start = PAPER vs LIVE mod secim ekrani" ^
           -m "- main_dashboard.py: paper-only / live-only menu ayrimi" ^
           -m "- live_history_handler.py: per-trade detay + CSV export" ^
           -m "- CSV export 15 alan (TX hash, polygonscan_url, etc.)" ^
           -m "- PnL detay panel (bugun/7gun, win rate, best/worst)" ^
           -m "- SELL panel: PnL ile pozisyon listesi + %% satis" ^
           -m "- Settled detection: redeem button winner / dead loser" ^
           -m "- live_redeem callback: gasless redeem on-chain"
if errorlevel 1 echo (commit 4: nothing - devam)

REM ── Commit 5: Auto-redeem job + portfolio data-api endpoints ───────
echo.
echo [5/7] feat(jobs): auto-redeem job + activity/closed-positions fetch
git add telegram_bot/jobs/auto_redeem_job.py
git add data/polymarket_portfolio.py
git commit -m "feat(jobs): auto-redeem periodic job + Polymarket data-api" ^
           -m "- auto_redeem_job.py: 5dk interval, idempotent, admin notif" ^
           -m "- ENV AUTO_REDEEM_ENABLED=false default" ^
           -m "- fetch_activity (TRADE/REDEEM/SPLIT/MERGE)" ^
           -m "- fetch_closed_positions (settled history + realized_pnl)" ^
           -m "- ActivityRow + ClosedPositionRow dataclasses"
if errorlevel 1 echo (commit 5: nothing - devam)

REM ── Commit 6: Test coverage push Wave 13-24 ────────────────────────
echo.
echo [6/7] test(coverage): Wave 13-24 push (21%% -> 43.7%%)
git add tests/unit/conftest.py
git add tests/unit/test_p0_p1_extra_coverage.py
git add tests/unit/test_wave22_mega.py
git add tests/unit/test_wave23_integration.py
git add tests/unit/test_wave24_safe.py
git add scripts/coverage_v*.bat scripts/verify_*.bat scripts/install_*.bat
git add scripts/master_cleanup_2026_05_06.bat scripts/git_commit_chain_2026_05_06.bat
git commit -m "test(coverage): Wave 13-24 push 21.2%% -> 43.7%% (+22.5 pt)" ^
           -m "- 502 -> 3,474 tests pass (+591%%)" ^
           -m "- conftest.py shared fixtures (_AsyncCM, db_stub)" ^
           -m "- Wave 22 mega: 130-modul parametrik import + 240 strategy lifecycle" ^
           -m "- Wave 23 integration: real DB (Windows aiosqlite crash, env-gated DISABLED)" ^
           -m "- Wave 24 safe: boundary inputs + edge cases" ^
           -m "- Coverage helper bat'leri (coverage_v*.bat, verify_*.bat)"
if errorlevel 1 echo (commit 6: nothing - devam)

REM ── Commit 7: Docs (README + CHANGELOG + new core modules) ─────────
echo.
echo [7/7] docs: README + CHANGELOG + new core modules
git add README.md CHANGELOG.md
git add core/__init__.py core/heartbeat.py core/executor.py
git add core/maker_taker_decision.py core/portfolio_kill_switch.py
git add core/status_poller.py core/structured_logging.py core/uma_dispute.py
git add core/allowance_preflight.py
git add core/calibration/ core/error_handler/ core/reconciliation/
git add core/engine.py core/ai_brain.py core/fees_v2.py
git add backtest/slippage_model.py backtest/walk_forward.py
git add docs/env_reference.md
git add audit_phase_polymarket_compliance/
git add YOL_HARITASI_5AI_SYNTHESIS_2026_04_30.md
git add _TESLIM_RAPORU_TR.md
git add tests/test_backfill_creds.py
git add scripts/backfill_ob_trades.py
git add TASKS.md
git add -A
git commit -m "docs: README + CHANGELOG + core P1 modules + audit" ^
           -m "- README.md: mod-first komut tablosu, V2 SDK, 3,474 test, 43.7%% cov" ^
           -m "- CHANGELOG.md: Mod-first UX + Live trading stack 2026-05-05/06" ^
           -m "- core/heartbeat.py P1.6.1 (Polymarket post-only GTC oncesi)" ^
           -m "- core/executor.py P1.8 (paper=live ayni path)" ^
           -m "- core/maker_taker_decision.py P1.6 (Phase D Bulgu 10)" ^
           -m "- core/reconciliation/onchain_sync.py P1.4" ^
           -m "- core/structured_logging.py P1.7 + secret scrubbing" ^
           -m "- core/uma_dispute.py + core/allowance_preflight.py" ^
           -m "- core/__init__.py P1.2 refactor shim" ^
           -m "- audit_phase_polymarket_compliance: docs uyum raporu" ^
           -m "- TASKS.md: backlog guncel"
if errorlevel 1 echo (commit 7: nothing - devam)

echo.
echo ============================================================
echo COMMIT CHAIN TAMAM
echo ============================================================
echo.
echo === git log (son 10) ===
git log --oneline -10
echo.
echo === git status (kalanlar) ===
git status --short
echo.
echo Sonraki adim:
echo   git push origin main    ^(MANUEL — Heddas onay verdiginde^)
echo.
pause
