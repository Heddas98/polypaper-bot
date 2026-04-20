@echo off
REM ============================================================
REM PolyPaper Bot - GitHub Setup Step 1: Git Init + Commit
REM ============================================================
REM Windows cmd'de calisir. Double-click veya cmd'den calistir.
REM ============================================================

setlocal enabledelayedexpansion
pushd "%~dp0.."

echo.
echo ============================================================
echo  PolyPaper Bot - GitHub Setup (Step 1/2)
echo ============================================================
echo.
echo Calisma dizini: %CD%
echo.

REM --- 0. Pre-flight checks -----------------------------------
where git >nul 2>&1
if errorlevel 1 (
    echo [HATA] git bulunamadi!
    echo Kurulum: https://git-scm.com/download/win
    goto :fail
)
echo [OK] git bulundu.

if not exist .gitignore (
    echo [HATA] .gitignore bulunamadi.
    echo Proje kok dizininde olmaniz gerekiyor ama su an %CD%
    goto :fail
)

if exist .env (
    echo [OK] .env mevcut ^(commit edilmeyecek, .gitignore'da^).
) else (
    echo [UYARI] .env yok - key'ler uygulama calismaz.
)

REM --- 1. Bozuk .git temizligi --------------------------------
if exist .git (
    echo.
    echo [UYARI] Var olan .git klasoru bulundu.
    echo         Sandbox'tan yapilan onceki git init bozuk olabilir.
    echo.
    set /p CONFIRM="Bu .git klasorunu silmek istiyor musun? (y/n): "
    if /i not "!CONFIRM!"=="y" (
        echo Iptal edildi.
        goto :fail
    )
    echo [1/6] Bozuk .git klasoru siliniyor...
    rmdir /s /q .git
    if exist .git (
        echo [HATA] .git silinemedi. Manuel sil: rmdir /s /q .git
        goto :fail
    )
    echo [OK] .git silindi.
)

REM --- 2. git init --------------------------------------------
echo.
echo [2/6] git init -b main ...
git init -b main
if errorlevel 1 goto :fail

REM --- 3. Git config ------------------------------------------
echo.
echo [3/6] Git config ayarlaniyor...
git config user.email "vfurkanv@gmail.com"
git config user.name "PolyPaper Bot Owner"
git config core.hooksPath .githooks
git config core.autocrlf true
echo    - user.email: vfurkanv@gmail.com
echo    - user.name:  PolyPaper Bot Owner
echo    - hooksPath:  .githooks
echo    - autocrlf:   true

REM --- 4. Hook kontrol ---------------------------------------
if exist .githooks\pre-commit (
    echo [OK] .githooks\pre-commit mevcut.
) else (
    echo [UYARI] .githooks\pre-commit yok - security hook inaktif.
)

REM --- 5. Staged dosyalar ------------------------------------
echo.
echo [4/6] Onayli dosya/klasorler stage ediliyor...

REM Kok dosyalar
call :add_if_exists README.md
call :add_if_exists LICENSE
call :add_if_exists SECURITY.md
call :add_if_exists CHANGELOG.md
call :add_if_exists .gitignore
call :add_if_exists .env.example
call :add_if_exists requirements.txt
call :add_if_exists main.py
call :add_if_exists watchdog.bat
call :add_if_exists watchdog.vbs
call :add_if_exists rollback.bat
call :add_if_exists backup.bat
call :add_if_exists start.bat
call :add_if_exists stop_bot.bat
call :add_if_exists run_tests.bat
call :add_if_exists reset_and_start.bat
call :add_if_exists verify_setup.py

REM Kod klasorleri
call :add_if_exists core
call :add_if_exists backtest
call :add_if_exists telegram_bot
call :add_if_exists indicators
call :add_if_exists skills
call :add_if_exists data_feeds
call :add_if_exists calibration
call :add_if_exists config
call :add_if_exists db
call :add_if_exists utils
call :add_if_exists scripts
call :add_if_exists tests
call :add_if_exists worker
call :add_if_exists tools
call :add_if_exists docs
call :add_if_exists .githooks
call :add_if_exists .github

REM --- 6. git status ------------------------------------------
echo.
echo [5/6] Stage durumu ^(ilk 40 satir^):
echo ------------------------------------------------------------
git status --short | more +0
echo ------------------------------------------------------------
echo.

REM --- 7. Secret kontrolu ------------------------------------
echo [Guvenlik] .env staged mi kontrol ediliyor...
git ls-files --stage 2>nul | findstr /R /C:"\.env$" >nul
if not errorlevel 1 (
    echo [KRITIK] .env STAGED! Devam etmiyoruz.
    echo Cozum: git rm --cached .env
    goto :fail
)
echo [OK] .env staged degil.

git ls-files --stage 2>nul | findstr /R /C:"\.db$" /C:"\.db-wal$" /C:"\.db-shm$" >nul
if not errorlevel 1 (
    echo [KRITIK] *.db dosyalari STAGED! Devam etmiyoruz.
    goto :fail
)
echo [OK] *.db dosyalari staged degil.

REM --- 8. Commit onay ----------------------------------------
echo.
echo Commit olusturulmak uzere. Yukaridaki stage listesini inceledin mi?
set /p OK="Commit'e devam et? (y/n): "
if /i not "!OK!"=="y" (
    echo Iptal edildi. Dosyalar stage'de kaldi.
    echo Degistirmek icin: git reset veya git rm --cached ^<dosya^>
    goto :fail
)

echo.
echo [6/6] Initial commit olusturuluyor...
git commit -m "Initial commit: PolyPaper Bot Phase 82e Sprint 5 HOTFIX v4"
if errorlevel 1 (
    echo [HATA] Commit basarisiz. Pre-commit hook guvenlik ihlali bildirdi mi?
    echo Yukaridaki hata mesajini incele.
    goto :fail
)

echo.
echo ============================================================
echo   STEP 1 BASARILI - Initial commit olusturuldu
echo ============================================================
echo.
git log --oneline -1
echo.
echo Sonraki adim:
echo   scripts\setup_github_step2_push.bat
echo.
popd
pause
exit /b 0

:add_if_exists
if exist "%~1" (
    git add -- "%~1" >nul 2>&1
    echo    + %~1
)
exit /b 0

:fail
echo.
echo ============================================================
echo   HATA - Setup yarida kaldi
echo ============================================================
echo.
popd
pause
exit /b 1
