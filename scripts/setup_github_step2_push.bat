@echo off
REM ============================================================
REM PolyPaper Bot - GitHub Setup Step 2: Push to GitHub
REM ============================================================
REM Bu script private repo olusturur ve push eder.
REM On-kosul: gh kurulu + gh auth login yapilmis olmali.
REM ============================================================

setlocal enabledelayedexpansion
pushd "%~dp0.."

echo.
echo ============================================================
echo  PolyPaper Bot - GitHub Setup (Step 2/2)
echo ============================================================
echo.
echo Calisma dizini: %CD%
echo.

REM --- 1. Pre-flight -----------------------------------------
where gh >nul 2>&1
if errorlevel 1 (
    echo [HATA] gh CLI bulunamadi.
    echo Kurulum: https://cli.github.com/
    echo Kurduktan sonra yeni cmd penceresi ac ve tekrar dene.
    goto :fail
)
echo [OK] gh bulundu.

if not exist .git (
    echo [HATA] .git klasoru yok. Once step 1'i calistir.
    goto :fail
)

REM --- 2. Auth check -----------------------------------------
echo.
echo [1/4] GitHub girisi kontrol ediliyor...
gh auth status 2>nul | findstr /C:"Logged in to github.com" >nul
if errorlevel 1 (
    echo [HATA] GitHub'a giris yapilmamis.
    echo.
    echo Bu komutu calistir:
    echo    gh auth login
    echo.
    echo Sonra bu script'i tekrar calistir.
    goto :fail
)
echo [OK] GitHub'a giris yapilmis.

REM --- 3. Repo ismi ------------------------------------------
echo.
echo [2/4] Repo ismi secimi
set REPO_NAME=polypaper-bot
set /p REPO_NAME_IN="Repo ismi (enter = %REPO_NAME%): "
if not "!REPO_NAME_IN!"=="" set REPO_NAME=!REPO_NAME_IN!

echo.
echo Olusturulacak:
echo    Isim:    !REPO_NAME!
echo    Privacy: PRIVATE
echo.
set /p CONFIRM="Devam? (y/n): "
if /i not "!CONFIRM!"=="y" (
    echo Iptal edildi.
    goto :fail
)

REM --- 4. Commit var mi? -------------------------------------
echo.
echo [3/4] Commit kontrol ediliyor...
git log --oneline -1 >nul 2>&1
if errorlevel 1 (
    echo [HATA] Hic commit yok. Once step 1'i calistir.
    goto :fail
)
git log --oneline -1
echo.

REM --- 5. Create + push --------------------------------------
echo [4/4] Repo olusturuluyor ve push ediliyor...
echo.

gh repo create !REPO_NAME! --private --source=. --remote=origin --push --description "PolyPaper Bot - Polymarket kripto paper trading Telegram botu (Engine v34, Phase 82e)"

if errorlevel 1 (
    echo.
    echo [HATA] Repo olusturma veya push basarisiz.
    echo.
    echo Manuel deneme:
    echo    gh repo create !REPO_NAME! --private --source=. --push
    echo.
    echo Repo zaten varsa:
    echo    git remote add origin https://github.com/USERNAME/!REPO_NAME!.git
    echo    git push -u origin main
    goto :fail
)

echo.
echo ============================================================
echo   BASARILI - Repo GitHub'a yuklendi
echo ============================================================
echo.

for /f "tokens=*" %%i in ('gh repo view !REPO_NAME! --json url -q .url 2^>nul') do set REPO_URL=%%i
if defined REPO_URL (
    echo Repo adresi: !REPO_URL!
    echo Tarayicida acmak: gh repo view !REPO_NAME! --web
)

echo.
echo Gelecekteki degisiklikler icin:
echo    git add .
echo    git commit -m "mesaj"
echo    git push
echo.
popd
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   HATA - Push yarida kaldi
echo ============================================================
echo.
popd
pause
exit /b 1
