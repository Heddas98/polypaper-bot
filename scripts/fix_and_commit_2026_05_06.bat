@echo off
REM ════════════════════════════════════════════════════════════════════════
REM FIX & COMMIT — Heddas 2026-05-06
REM
REM Önceki bat'lerdeki sorunları düzeltir:
REM 1) .git/HEAD.lock'u temizler
REM 2) Garip "43.7%)" dosyasını siler
REM 3) Master cleanup A2 (_archive) + A3 (scripts) tamamlar
REM 4) Tüm değişiklikleri TEK BÜYÜK commit'te birleştirir
REM 5) push HARIC bırakır (Heddas manuel push edecek)
REM ════════════════════════════════════════════════════════════════════════
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo FIX & COMMIT — 2026-05-06
echo ============================================================

REM ── 1) HEAD.lock temizle ─────────────────────────────────────
echo.
echo [1/5] .git/HEAD.lock temizleniyor...
if exist ".git\HEAD.lock" (
    del /Q ".git\HEAD.lock"
    echo   - .git/HEAD.lock silindi
) else (
    echo   - Lock yok, atlandi
)

REM ── 2) Garip "43.7%)" dosyasi sil ─────────────────────────────
echo.
echo [2/5] Garip "43.7%%)" dosyasi siliniyor...
if exist "43.7%%)" (
    del /Q "43.7%%)"
    echo   - "43.7%%)" silindi
) else (
    echo   - "43.7%%)" yok
)

REM ── 3) _archive cleanup (A2 manuel) ─────────────────────────
echo.
echo [3/5] _archive eski commit_msg + bat siliniyor...
del /Q "_archive\_commit_msg_bulgu_b_fix.txt" 2>nul
del /Q "_archive\_commit_msg_epic11_closure.txt" 2>nul
del /Q "_archive\_commit_msg_housekeeping.txt" 2>nul
del /Q "_archive\_commit_msg_pnl_div_whitelist.txt" 2>nul
del /Q "_archive\_commit_msg_t11_2_closure.txt" 2>nul
del /Q "_archive\_commit_msg_t11_2_g3.txt" 2>nul
del /Q "_archive\_commit_msg_t11_2_g4.txt" 2>nul
del /Q "_archive\_commit_msg_t11_3_closure.txt" 2>nul
del /Q "_archive\_commit_msg_t11_3_s1_s2.txt" 2>nul
del /Q "_archive\_commit_msg_t11_3_s3.txt" 2>nul
del /Q "_archive\_commit_msg_t11_defense.txt" 2>nul
del /Q "_archive\_commit_msg_t4_telemetry.txt" 2>nul
del /Q "_archive\commit_bulgu_b_fix.bat" 2>nul
del /Q "_archive\commit_epic11_closure_final.bat" 2>nul
del /Q "_archive\commit_housekeeping.bat" 2>nul
del /Q "_archive\commit_pnl_div_whitelist.bat" 2>nul
del /Q "_archive\commit_t11_2_closure.bat" 2>nul
del /Q "_archive\commit_t11_2_g3.bat" 2>nul
del /Q "_archive\commit_t11_2_g4.bat" 2>nul
del /Q "_archive\commit_t11_3_closure.bat" 2>nul
del /Q "_archive\commit_t11_3_s1_s2.bat" 2>nul
del /Q "_archive\commit_t11_3_s3.bat" 2>nul
del /Q "_archive\commit_t11_defense_batch.bat" 2>nul
del /Q "_archive\commit_t4_10_regime_write.bat" 2>nul
del /Q "_archive\commit_t4_6_and_run.bat" 2>nul
del /Q "_archive\commit_t4_telemetry.bat" 2>nul
echo   - _archive cleanup tamam

REM ── 4) scripts cleanup (A3 manuel) ──────────────────────────
echo.
echo [4/5] scripts one-time bat siliniyor...
del /Q "scripts\cleanup_asama_3d_2026_04_29.bat" 2>nul
del /Q "scripts\cleanup_asama_3e_2026_04_29.bat" 2>nul
del /Q "scripts\cleanup_becker_full_2026_04_29.bat" 2>nul
del /Q "scripts\commit_fase_a_2026_04_29.bat" 2>nul
del /Q "scripts\final_cleanup_and_commit_2026_04_29.bat" 2>nul
echo   - scripts cleanup tamam

REM ── 5) Single comprehensive commit ──────────────────────────
echo.
echo [5/5] Tum degisiklikler tek commit'te birlestirily...
echo.

REM Reset staging area (clean slate)
git reset HEAD 2>nul

REM Add everything (modifications + new files)
git add -A

REM Show what will be committed
echo === Stage edilenler (ozet) ===
git diff --cached --stat | tail -30
echo.

REM Single comprehensive commit
git commit -m "feat: mod-first UX + V2 SDK + live trading stack + cleanup (2026-05-06)" ^
           -m "" ^
           -m "Heddas direktifi: GitHub guncel hale getir, sadelestir, hatasiz." ^
           -m "" ^
           -m "=== UX REDESIGN (Mod-First Dashboard) ===" ^
           -m "- /start = PAPER vs LIVE mod secim ekrani" ^
           -m "- main_dashboard.py: paper/live menu ayrimi (cross-mod butonlar)" ^
           -m "- live_history_handler.py: per-trade detay + CSV export 15 alan" ^
           -m "- PnL detay panel (bugun/7gun, win rate, best/worst)" ^
           -m "- SELL panel: PnL ile pozisyon listesi + 25/50/75/100%% sat" ^
           -m "- Settled detection: redeem button winner / dead loser" ^
           -m "" ^
           -m "=== POLYMARKET V2 SDK FIXES (Breaking changes 2026-04-28) ===" ^
           -m "- bal['allowance'] -> bal['allowances'] dict (4 dosyada fix)" ^
           -m "- OrderArgs.builder_code field (V2'de OrderArgs icinde)" ^
           -m "- PartialCreateOrderOptions(tick_size, neg_risk) typed" ^
           -m "- MarketOrderArgs + create_and_post_market_order auto decimal" ^
           -m "- 3-adres sistem netlestirildi: Profile/Deposit/Rabby" ^
           -m "" ^
           -m "=== LIVE TRADING STACK (Gasless via Relayer) ===" ^
           -m "- approve_allowance: 3-contract (CTF + CTF Exchange + Neg Risk)" ^
           -m "- redeem_position: gasless via CTF.redeemPositions" ^
           -m "- auto_redeem_job: 5dk interval, idempotent, admin notif" ^
           -m "- fetch_activity + fetch_closed_positions data-api endpoints" ^
           -m "- ActivityRow + ClosedPositionRow dataclasses" ^
           -m "" ^
           -m "=== TEST COVERAGE PUSH (Wave 13-24) ===" ^
           -m "- 502 -> 3,474 tests pass (+591%%), 0 fail" ^
           -m "- Coverage 21.2%% -> 43.7%% (+22.5 pt, 24 wave)" ^
           -m "- conftest.py shared fixtures (_AsyncCM, db_stub)" ^
           -m "- Wave 22 mega: 130 modul parametrik + 240 strategy lifecycle" ^
           -m "- Wave 23 integration env-gated DISABLED (Windows aiosqlite crash)" ^
           -m "" ^
           -m "=== NEW CORE MODULES (P1 Backlog) ===" ^
           -m "- core/heartbeat.py P1.6.1 (post-only GTC oncesi zorunlu)" ^
           -m "- core/executor.py P1.8 (paper=live ayni path)" ^
           -m "- core/maker_taker_decision.py P1.6 (Phase D Bulgu 10)" ^
           -m "- core/reconciliation/onchain_sync.py P1.4" ^
           -m "- core/structured_logging.py P1.7 (secret scrubbing)" ^
           -m "- core/uma_dispute.py P3.Y (UMA dispute window)" ^
           -m "- core/allowance_preflight.py" ^
           -m "- core/portfolio_kill_switch.py" ^
           -m "- core/__init__.py P1.2 refactor shim" ^
           -m "" ^
           -m "=== CLEANUP ===" ^
           -m "- 14 coverage_v*.txt (gitignore'a eklendi)" ^
           -m "- _archive/_commit_msg_*.txt (12 dosya)" ^
           -m "- _archive/commit_*.bat (14 one-time)" ^
           -m "- scripts/cleanup_*_2026_04_29 (5 one-time)" ^
           -m "- .gitignore: coverage_v*.txt regex eklendi" ^
           -m "" ^
           -m "=== DOCS ===" ^
           -m "- README.md: mod-first komutlar, V2 SDK, 3,474 test, 43.7%% cov" ^
           -m "- CHANGELOG.md: 2026-05-05/06 detayli release notes" ^
           -m "- 14 yeni audit doc (docs/audits/)" ^
           -m "- docs/SPRINT3_PLAN.md, SPRINT2_MAINNET_GUIDE.md" ^
           -m "- audit_phase_polymarket_compliance/ (uyum raporu)"

if errorlevel 1 (
    echo.
    echo ⚠️  Commit FAIL — git status'a bak
    git status --short | head -20
    pause
    exit /b 1
)

echo.
echo ============================================================
echo COMMIT TAMAM ✅
echo ============================================================
echo.
echo === Son 5 commit ===
git log --oneline -5
echo.
echo === Kalan untracked (varsa) ===
git status --short
echo.
echo Sonraki adim:
echo   git push origin main
echo.
pause
