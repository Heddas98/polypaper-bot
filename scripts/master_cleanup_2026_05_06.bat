@echo off
REM ════════════════════════════════════════════════════════════════════════
REM MASTER CLEANUP — Heddas 2026-05-06
REM Tek seferlik root + _archive + scripts cleanup
REM
REM Silinecekler:
REM   - Root: coverage_v*.txt (14), .coverage, 1, .env.example.pre_cleanup_*
REM   - _archive/_commit_msg_*.txt (12), _archive/commit_*.bat (14)
REM   - scripts/cleanup_*_2026_04_29.bat (3) + commit_fase_*.bat
REM         + final_cleanup_*.bat (1) + diğer one-time'lar
REM
REM Korunacaklar:
REM   - _archive/audit_snapshots/ (önemli audit history)
REM   - scripts/coverage_v*.bat (tekrar kullanılabilir)
REM   - scripts/verify_*.bat (tekrar kullanılabilir)
REM   - scripts/install_*.bat (kurulum)
REM   - Tüm core/, data/, telegram_bot/, backtest/, tests/
REM   - .env, .env.example, requirements.txt, README.md
REM ════════════════════════════════════════════════════════════════════════
SETLOCAL EnableDelayedExpansion
cd /d "%~dp0\.."

set DELETED_COUNT=0

echo ============================================================
echo Master Cleanup 2026-05-06 — Heddas direktifi
echo ============================================================

echo.
echo [A1/3] Root cleanup...
echo.
for %%F in (coverage_v11.txt coverage_v12.txt coverage_v13.txt coverage_v14.txt ^
            coverage_v15.txt coverage_v16.txt coverage_v17.txt coverage_v18.txt ^
            coverage_v19.txt coverage_v20.txt coverage_v21.txt coverage_v22.txt ^
            coverage_v23.txt coverage_v24.txt) do (
    if exist "%%F" (
        del /Q "%%F"
        echo   - %%F silindi
        set /a DELETED_COUNT+=1
    )
)

REM .coverage binary cache (pytest)
if exist ".coverage" (
    del /Q ".coverage"
    echo   - .coverage silindi
    set /a DELETED_COUNT+=1
)

REM Boş 1 dosyası
if exist "1" (
    del /Q "1"
    echo   - 1 (empty file) silindi
    set /a DELETED_COUNT+=1
)

REM Eski .env.example backup
if exist ".env.example.pre_cleanup_20260503_123404" (
    del /Q ".env.example.pre_cleanup_20260503_123404"
    echo   - .env.example.pre_cleanup_20260503_123404 silindi
    set /a DELETED_COUNT+=1
)

REM .pytest_cache (regenerate olur)
if exist ".pytest_cache" (
    rmdir /S /Q ".pytest_cache"
    echo   - .pytest_cache klasoru silindi
    set /a DELETED_COUNT+=1
)

echo.
echo [A2/3] _archive cleanup...
echo.

REM Eski commit_msg.txt'ler (git log'da zaten var)
for %%F in (_commit_msg_bulgu_b_fix.txt _commit_msg_epic11_closure.txt ^
            _commit_msg_housekeeping.txt _commit_msg_pnl_div_whitelist.txt ^
            _commit_msg_t11_2_closure.txt _commit_msg_t11_2_g3.txt ^
            _commit_msg_t11_2_g4.txt _commit_msg_t11_3_closure.txt ^
            _commit_msg_t11_3_s1_s2.txt _commit_msg_t11_3_s3.txt ^
            _commit_msg_t11_defense.txt _commit_msg_t4_telemetry.txt) do (
    if exist "_archive\%%F" (
        del /Q "_archive\%%F"
        echo   - _archive/%%F silindi
        set /a DELETED_COUNT+=1
    )
)

REM Eski commit_*.bat'ler (one-time, kullanildi)
for %%F in (commit_bulgu_b_fix.bat commit_epic11_closure_final.bat ^
            commit_housekeeping.bat commit_pnl_div_whitelist.bat ^
            commit_t11_2_closure.bat commit_t11_2_g3.bat commit_t11_2_g4.bat ^
            commit_t11_3_closure.bat commit_t11_3_s1_s2.bat ^
            commit_t11_3_s3.bat commit_t11_defense_batch.bat ^
            commit_t4_10_regime_write.bat commit_t4_6_and_run.bat ^
            commit_t4_telemetry.bat) do (
    if exist "_archive\%%F" (
        del /Q "_archive\%%F"
        echo   - _archive/%%F silindi
        set /a DELETED_COUNT+=1
    )
)

echo.
echo [A3/3] scripts cleanup...
echo.

REM 2026-04-29 one-time cleanup'lar
for %%F in (cleanup_asama_3d_2026_04_29.bat cleanup_asama_3e_2026_04_29.bat ^
            cleanup_becker_full_2026_04_29.bat ^
            commit_fase_a_2026_04_29.bat final_cleanup_and_commit_2026_04_29.bat) do (
    if exist "scripts\%%F" (
        del /Q "scripts\%%F"
        echo   - scripts/%%F silindi
        set /a DELETED_COUNT+=1
    )
)

echo.
echo ============================================================
echo CLEANUP TAMAM — !DELETED_COUNT! dosya/klasor silindi
echo ============================================================
echo.
echo Sonraki adim:
echo   1. git status (degisiklikleri kontrol)
echo   2. .gitignore + README + CHANGELOG guncelle
echo   3. git add + commit + push
echo.
pause
