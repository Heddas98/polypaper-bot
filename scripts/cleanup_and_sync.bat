@echo off
REM ============================================================
REM PolyPaper Bot - Cleanup + Sync Bot State + Push to GitHub
REM ============================================================
REM Bu script su islemleri sirasiyla yapar:
REM   1) Kok dizindeki shell-typo ve log cop dosyalarini sil
REM   2) Tracked ama .gitignore'a ait olan dosyalari untrack et
REM   3) README/CHANGELOG/PHASES guncellemelerini commit et
REM   4) GitHub'a push et
REM ============================================================
REM On-kosullar:
REM   - scripts\setup_github_step1_init.bat tamamlandi
REM   - scripts\setup_github_step1b_fix_missing.bat tamamlandi
REM   - scripts\setup_github_step2_push.bat tamamlandi
REM   - Remote "origin" ayarli (https://github.com/Heddas98/polypaper-bot)
REM ============================================================

setlocal enabledelayedexpansion
pushd "%~dp0.."

echo.
echo ============================================================
echo  PolyPaper Bot - Cleanup + Sync
echo ============================================================
echo.
echo Calisma dizini: %CD%
echo.

REM --- Pre-flight ---------------------------------------------
where git >nul 2>&1
if errorlevel 1 (
    echo [HATA] git bulunamadi.
    goto :fail
)
if not exist .git (
    echo [HATA] .git klasoru yok. Once setup_github_step1_init.bat calistir.
    goto :fail
)
git log --oneline -1 >nul 2>&1
if errorlevel 1 (
    echo [HATA] Hic commit yok. Once initial commit yap.
    goto :fail
)

echo Mevcut commit:
git log --oneline -1
echo.

REM --- [1/4] Kok dizin cop temizligi --------------------------
echo [1/4] Kok dizindeki cop dosyalar temizleniyor...
echo.

REM Shell-typo junk (Windows cmd dosyalari)
set JUNK=0.80 1 2 Zstd `os.` PLUGIN_ERROR fiyat "is_overfit()" martingale opt-out regress sifir worker ~80
for %%F in (%JUNK%) do (
    if exist "%%~F" (
        del /f /q "%%~F" 2>nul
        if not exist "%%~F" echo    - silindi: %%~F
    )
)

REM Deploy log ve analiz output dosyalari
echo.
echo    Deploy/analiz log dosyalari:
for %%F in (
    deploy_*_log.txt
    analyze_output.txt
    covering_index_output.txt
    split_backtest_index_output.txt
    clean_overfit_log.txt
    diagnose_hot_indexes_output.txt
    diagnose_phase82b_log.txt
    diag_ai_f_loss_log.txt
    fix_classic_threshold_log.txt
    restart_classic_bypass_pin_log.txt
    verify_82b2_log.txt
    verify_bot_82c_batch2_log.txt
    deploy_sprint6_env_toggle_log.txt
    deploy_hotfix_v5_log.txt
    deploy_hotfix_v6_log.txt
    hotfix_resolution_path_v4_log.txt
) do (
    if exist "%%~F" (
        del /f /q "%%~F" 2>nul
        if not exist "%%~F" echo    - silindi: %%~F
    )
)

REM Test DB'leri (gitignore'da var ama local'de duruyor)
if exist test_edge_analysis.db (
    del /f /q test_edge_analysis.db 2>nul
    echo    - silindi: test_edge_analysis.db
)
if exist test_edge_analysis.db-journal (
    del /f /q test_edge_analysis.db-journal 2>nul
    echo    - silindi: test_edge_analysis.db-journal
)

REM pytest cache
for /d %%D in (pytest-cache-files-*) do (
    rmdir /s /q "%%~D" 2>nul
    if not exist "%%~D" echo    - silindi: %%~D\
)

REM Kok __pycache__
if exist __pycache__ (
    rmdir /s /q __pycache__ 2>nul
    if not exist __pycache__ echo    - silindi: __pycache__\
)

echo [OK] Lokal temizlik tamam.
echo.

REM --- [2/4] Tracked ama gitignore'da olan dosyalari untrack --
echo [2/4] Tracked ama .gitignore'a ait dosyalari untrack ediliyor...
echo.

REM git'te olup silinenleri stage et (tracked dosya silindigi icin)
git add -A --update >nul 2>&1
echo    [OK] Silinen dosyalar staged.

REM --- [3/4] Guncellenmis docs'u stage'le ---------------------
echo.
echo [3/4] Guncel dokuman degisiklikleri stage ediliyor...
echo.

if exist README.md         ( git add -- README.md         && echo    + README.md )
if exist CHANGELOG.md      ( git add -- CHANGELOG.md      && echo    + CHANGELOG.md )
if exist docs\PHASES.md    ( git add -- docs\PHASES.md    && echo    + docs\PHASES.md )
if exist docs\GITHUB_SETUP.md ( git add -- docs\GITHUB_SETUP.md && echo    + docs\GITHUB_SETUP.md )
if exist .gitignore        ( git add -- .gitignore        && echo    + .gitignore )
if exist scripts\cleanup_and_sync.bat ( git add -- scripts\cleanup_and_sync.bat && echo    + scripts\cleanup_and_sync.bat )
if exist scripts\setup_github_step1_init.bat ( git add -- scripts\setup_github_step1_init.bat && echo    + scripts\setup_github_step1_init.bat )
if exist scripts\setup_github_step1b_fix_missing.bat ( git add -- scripts\setup_github_step1b_fix_missing.bat && echo    + scripts\setup_github_step1b_fix_missing.bat )
if exist scripts\setup_github_step2_push.bat ( git add -- scripts\setup_github_step2_push.bat && echo    + scripts\setup_github_step2_push.bat )

REM Bot v9.7.9 + Sprint 6 icin kaynak dosyalar (eger degismisse)
if exist telegram_bot\version.py   ( git add -- telegram_bot\version.py   && echo    + telegram_bot\version.py )
if exist telegram_bot\bot.py       ( git add -- telegram_bot\bot.py       && echo    + telegram_bot\bot.py )
if exist config\env_whitelist.py   ( git add -- config\env_whitelist.py   && echo    + config\env_whitelist.py )
if exist telegram_bot\handlers\env_toggle.py ( git add -- telegram_bot\handlers\env_toggle.py && echo    + telegram_bot\handlers\env_toggle.py )
if exist scripts\smoke_sprint6_env_toggle.py ( git add -- scripts\smoke_sprint6_env_toggle.py && echo    + scripts\smoke_sprint6_env_toggle.py )
if exist core\engine_signals.py    ( git add -- core\engine_signals.py    && echo    + core\engine_signals.py )
if exist core\engine_fills.py      ( git add -- core\engine_fills.py      && echo    + core\engine_fills.py )

REM --- Guvenlik dogrulamasi -----------------------------------
echo.
echo [Guvenlik] .env staged olmadigini dogrula...
git diff --cached --name-only | findstr /R /C:"^\.env$" >nul
if not errorlevel 1 (
    echo [KRITIK] .env STAGED! Reset ediliyor.
    git reset HEAD -- .env
    goto :fail
)
echo [OK] .env staged degil.

git diff --cached --name-only | findstr /R /C:"\.db$" /C:"\.db-wal$" /C:"\.db-shm$" >nul
if not errorlevel 1 (
    echo [KRITIK] *.db STAGED!
    goto :fail
)
echo [OK] *.db staged degil.

echo.
echo Stage durumu:
echo ------------------------------------------------------------
git diff --cached --name-only
echo ------------------------------------------------------------
echo.

REM Stage bos mu?
for /f %%c in ('git diff --cached --name-only 2^>nul ^| find /c /v ""') do set STAGE_COUNT=%%c
if "!STAGE_COUNT!"=="0" (
    echo [BILGI] Stage bos - degisiklik yok, commit atlaniyor.
    goto :push_check
)

set /p OK="Bu degisiklikleri commit et? (y/n): "
if /i not "!OK!"=="y" (
    echo Iptal edildi. Stage'deki dosyalar kaldi.
    goto :fail
)

echo.
echo Commit olusturuluyor...
git commit -m "docs: sync to v9.7.9 + Phase 82e Sprint 6 + cleanup root clutter"
if errorlevel 1 (
    echo [HATA] Commit basarisiz. Pre-commit hook'u kontrol et.
    goto :fail
)
echo [OK] Commit atildi.
echo.

:push_check
REM --- [4/4] GitHub'a push -----------------------------------
echo [4/4] GitHub'a push ediliyor...
echo.

REM Remote var mi?
git remote -v | findstr /C:"origin" >nul
if errorlevel 1 (
    echo [HATA] Remote "origin" yok. Once step 2 push scripti calistir.
    goto :fail
)

REM Push edilecek commit var mi?
git log @{upstream}..HEAD --oneline 2>nul | find /c /v "" > "%TEMP%\push_count.txt"
set /p PUSH_COUNT=<"%TEMP%\push_count.txt"
del "%TEMP%\push_count.txt"

if "!PUSH_COUNT!"=="0" (
    echo [BILGI] Push edilecek commit yok - her sey senkron.
    goto :done
)

echo !PUSH_COUNT! commit push edilecek:
git log @{upstream}..HEAD --oneline
echo.
set /p OK="Push et? (y/n): "
if /i not "!OK!"=="y" (
    echo Iptal edildi. Lokal commit(ler) var ama push edilmedi.
    goto :fail
)

git push origin main
if errorlevel 1 (
    echo [HATA] Push basarisiz.
    goto :fail
)

:done
echo.
echo ============================================================
echo   BASARILI - Cleanup + Sync tamam
echo ============================================================
echo.
git log --oneline -3
echo.
echo Repo adresi: https://github.com/Heddas98/polypaper-bot
echo Tarayicida acmak: gh repo view --web
echo.
popd
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   HATA - Cleanup + Sync yarida kaldi
echo ============================================================
echo.
popd
pause
exit /b 1
