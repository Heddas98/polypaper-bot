@echo off
REM ============================================================
REM PolyPaper Bot - Step 1B: Eksik dosyalari initial commit'e ekle
REM ============================================================
REM Step 1'de call :add_if_exists subroutine bazi dosyalari
REM atladi (cmd bug veya call limiti). Bu script eksikleri
REM inline git add ile ekler ve --amend ile ayni commit'e katar.
REM ============================================================

setlocal enabledelayedexpansion
pushd "%~dp0.."

echo.
echo ============================================================
echo  PolyPaper Bot - Fix Missing Files (Step 1B)
echo ============================================================
echo.
echo Calisma dizini: %CD%
echo.

REM --- Pre-flight ---------------------------------------------
if not exist .git (
    echo [HATA] .git yok. Once step 1'i calistir.
    goto :fail
)

git log --oneline -1 >nul 2>&1
if errorlevel 1 (
    echo [HATA] Hic commit yok. Once step 1'i calistir.
    goto :fail
)

REM Push edilmis mi? Edildiyse amend tehlikeli.
git log @{upstream}..HEAD >nul 2>&1
if not errorlevel 1 (
    git rev-list --count @{u}..HEAD > "%TEMP%\commit_count.txt"
    set /p COMMIT_COUNT=<"%TEMP%\commit_count.txt"
    del "%TEMP%\commit_count.txt"
    if "!COMMIT_COUNT!"=="0" (
        echo [HATA] Commit zaten push edildi. Amend tehlikeli.
        echo Bu durumda yeni bir commit atmak gerekli.
        goto :fail
    )
)

echo Mevcut commit:
git log --oneline -1
echo.

REM --- Eksik kok dosyalar ------------------------------------
echo [1/3] Eksik kok dosyalar ekleniyor...

if exist .gitignore      ( git add -- .gitignore      && echo    + .gitignore )
if exist .env.example    ( git add -- .env.example    && echo    + .env.example )
if exist requirements.txt ( git add -- requirements.txt && echo    + requirements.txt )
if exist main.py         ( git add -- main.py         && echo    + main.py )
if exist watchdog.bat    ( git add -- watchdog.bat    && echo    + watchdog.bat )
if exist watchdog.vbs    ( git add -- watchdog.vbs    && echo    + watchdog.vbs )
if exist rollback.bat    ( git add -- rollback.bat    && echo    + rollback.bat )
if exist backup.bat      ( git add -- backup.bat      && echo    + backup.bat )
if exist start.bat       ( git add -- start.bat       && echo    + start.bat )
if exist stop_bot.bat    ( git add -- stop_bot.bat    && echo    + stop_bot.bat )
if exist run_tests.bat   ( git add -- run_tests.bat   && echo    + run_tests.bat )
if exist reset_and_start.bat ( git add -- reset_and_start.bat && echo    + reset_and_start.bat )
if exist verify_setup.py ( git add -- verify_setup.py && echo    + verify_setup.py )

echo.
echo [2/3] Eksik kod klasorleri ekleniyor...

if exist core         ( git add -- core         && echo    + core\ )
if exist backtest     ( git add -- backtest     && echo    + backtest\ )
if exist telegram_bot ( git add -- telegram_bot && echo    + telegram_bot\ )
if exist indicators   ( git add -- indicators   && echo    + indicators\ )

REM --- Secret dogrulama --------------------------------------
echo.
echo [Guvenlik] .env staged olmadigini dogrula...
git diff --cached --name-only | findstr /R /C:"^\.env$" >nul
if not errorlevel 1 (
    echo [KRITIK] .env STAGED! Reset ediliyor.
    git reset HEAD -- .env
    goto :fail
)
echo [OK] .env staged degil.

echo [Guvenlik] *.db staged olmadigini dogrula...
git diff --cached --name-only | findstr /R /C:"\.db$" /C:"\.db-wal$" /C:"\.db-shm$" >nul
if not errorlevel 1 (
    echo [KRITIK] *.db STAGED!
    goto :fail
)
echo [OK] *.db staged degil.

REM --- Ozet goster -------------------------------------------
echo.
echo [3/3] Yeni stage'e eklenenler ^(amend edilmek uzere^):
echo ------------------------------------------------------------
git diff --cached --name-only
echo ------------------------------------------------------------
echo.

set /p OK="Bu dosyalari ayni initial commit'e dahil et? (y/n): "
if /i not "!OK!"=="y" (
    echo Iptal edildi. Stage'deki dosyalar kaldi.
    echo Iptal icin: git reset HEAD
    goto :fail
)

echo.
echo Commit --amend yapiliyor...
git commit --amend --no-edit
if errorlevel 1 (
    echo [HATA] Amend basarisiz. Pre-commit hook'u kontrol et.
    goto :fail
)

echo.
echo ============================================================
echo   BASARILI - Eksikler initial commit'e dahil edildi
echo ============================================================
echo.
git log --oneline -1
echo.
echo Toplam dosya sayisi:
git ls-files | find /c /v ""
echo.
echo Sonraki adim:
echo    1. gh CLI kur:  https://cli.github.com/
echo    2. gh auth login
echo    3. scripts\setup_github_step2_push.bat
echo.
popd
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   HATA - Yarida kaldi
echo ============================================================
echo.
popd
pause
exit /b 1
