@echo off
REM ============================================================
REM PolyPaper Bot - Repo Polish 2026-04-25
REM ============================================================
REM Bu script repo'yu profesyonel hale getirir:
REM   1. Lock temizle
REM   2. Kok cop dosyalari sil
REM   3. Eski phase dokumanlarini docs/archive'a tasi
REM   4. coverage.json + slippage JSON tracked olanlari untrack et
REM   5. 5 atomic commit at:
REM      - docs(changelog+phases): Epic 7-11 girdileri
REM      - docs(readme+arch+strategies): 2026-04-25 sync + opening_breakout + ghost doctrine
REM      - chore(cleanup): kok cop dosyalari sil + phase50 docs arsivle
REM      - chore(gitignore): coverage + scratch + calibration patterns
REM      - feat(tasks): TASKS.md repo'ya ekle
REM   6. GitHub'a push
REM ============================================================

setlocal enabledelayedexpansion
pushd "%~dp0.."

echo.
echo ============================================================
echo  PolyPaper Bot - Repo Polish 2026-04-25
echo ============================================================
echo.
echo Calisma dizini: %CD%
echo.

REM --- Pre-flight ---------------------------------------------
where git >nul 2>&1
if errorlevel 1 ( echo [HATA] git bulunamadi. & goto :fail )
if not exist .git ( echo [HATA] .git yok. & goto :fail )
git log --oneline -1 >nul 2>&1
if errorlevel 1 ( echo [HATA] Hic commit yok. & goto :fail )

echo Mevcut commit:
git log --oneline -1
echo.

REM --- [0/6] Lock temizle ------------------------------------
echo [0/6] Git lock dosyalari temizleniyor...
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul
echo [OK] Lock temiz.
echo.

REM --- [1/6] FAZ 1+2 commit (CHANGELOG + PHASES + README + ARCH + STRATEGIES) ---
echo [1/6] Doc updates commit (CHANGELOG + PHASES + README + ARCH + STRATEGIES)...
echo.

git add CHANGELOG.md docs/PHASES.md README.md docs/ARCHITECTURE.md docs/STRATEGIES.md

REM Stage bos mu kontrol
for /f %%c in ('git diff --cached --name-only 2^>nul ^| find /c /v ""') do set STAGE_COUNT=%%c
if "!STAGE_COUNT!"=="0" (
    echo    [skip] Doc updates zaten commit'lenmis.
) else (
    echo Staged dosyalar:
    git diff --cached --name-only
    echo.
    git commit -m "docs: Epic 7-11 + T9.8-REG + T11.8-B + T4.6-B sync + opening_breakout + ghost doctrine"
    if errorlevel 1 ( echo [HATA] Commit basarisiz. & goto :fail )
    echo [OK] Commit atildi.
)
echo.

REM --- [2/6] FAZ 3 cleanup: cop dosyalari sil ---------------
echo [2/6] Kok cop dosyalari siliniyor...
echo.

REM Once docs/archive klasorlerini olustur
if not exist docs\archive          mkdir docs\archive
if not exist docs\archive\handoffs mkdir docs\archive\handoffs
if not exist docs\archive\phase50  mkdir docs\archive\phase50

REM Eski handoff'u tasi (varsa)
if exist HANDOFF_PROMPT_2026-04-20.md (
    move /Y HANDOFF_PROMPT_2026-04-20.md docs\archive\handoffs\ >nul
    echo    + tasindi: HANDOFF_PROMPT_2026-04-20.md -> docs/archive/handoffs/
)

REM Eski phase50 dokumanlarini tasi
if exist docs\MANUAL_STEPS_PHASE50.md (
    move /Y docs\MANUAL_STEPS_PHASE50.md docs\archive\phase50\ >nul
    echo    + tasindi: docs/MANUAL_STEPS_PHASE50.md -> docs/archive/phase50/
)
if exist docs\REGRESSION_CHECKLIST_PHASE50.md (
    move /Y docs\REGRESSION_CHECKLIST_PHASE50.md docs\archive\phase50\ >nul
    echo    + tasindi: docs/REGRESSION_CHECKLIST_PHASE50.md -> docs/archive/phase50/
)

REM EDGE_DISCOVERY_GUIDE arsivle
if exist EDGE_DISCOVERY_GUIDE.md (
    move /Y EDGE_DISCOVERY_GUIDE.md docs\archive\ >nul
    echo    + tasindi: EDGE_DISCOVERY_GUIDE.md -> docs/archive/
)

REM Sil: gecici/duplikat
for %%F in (
    BUGUN_NE_YAPACAGIM.md
    TEMIZLEME_PLANI_2026-04-20.md
    DEPLOY_INSTRUCTIONS.md
    WATCHDOG_SETUP.md
    coverage.json
    run_t76_asama_a_regression.bat
) do (
    if exist "%%~F" (
        del /F /Q "%%~F"
        if not exist "%%~F" echo    - silindi: %%~F
    )
)

REM Stage silinen ve tasinan dosyalari
git add -A --update >nul 2>&1
git add docs/archive >nul 2>&1

for /f %%c in ('git diff --cached --name-only 2^>nul ^| find /c /v ""') do set STAGE_COUNT=%%c
if "!STAGE_COUNT!"=="0" (
    echo    [skip] Cleanup zaten commit'lenmis.
) else (
    echo.
    echo Staged dosyalar (cleanup):
    git diff --cached --name-only
    echo.
    git commit -m "chore(cleanup): root junk removal + phase50 docs to docs/archive/"
    if errorlevel 1 ( echo [HATA] Cleanup commit basarisiz. & goto :fail )
    echo [OK] Cleanup commit atildi.
)
echo.

REM --- [3/6] .gitignore commit + tracked calibration untrack ---
echo [3/6] gitignore + slippage JSON untrack...
echo.

REM slippage JSON'u zaten gitignore'da olabilir ama tracked
git rm --cached backtest/calibration/slippage_2026q2.json 2>nul && echo    - untracked: backtest/calibration/slippage_2026q2.json
git rm --cached coverage.json 2>nul

git add .gitignore

for /f %%c in ('git diff --cached --name-only 2^>nul ^| find /c /v ""') do set STAGE_COUNT=%%c
if "!STAGE_COUNT!"=="0" (
    echo    [skip] gitignore zaten guncel.
) else (
    git diff --cached --name-only
    git commit -m "chore(gitignore): coverage + scratch + calibration runtime patterns"
    if errorlevel 1 ( echo [HATA] gitignore commit basarisiz. & goto :fail )
    echo [OK] gitignore commit atildi.
)
echo.

REM --- [4/6] TASKS.md repo'ya ekle ---------------------------
echo [4/6] TASKS.md repo'ya ekleniyor...
echo.

if exist TASKS.md (
    git add TASKS.md
    for /f %%c in ('git diff --cached --name-only 2^>nul ^| find /c /v ""') do set STAGE_COUNT=%%c
    if "!STAGE_COUNT!"=="0" (
        echo    [skip] TASKS.md zaten tracked ve unchanged.
    ) else (
        git commit -m "feat(tasks): add TASKS.md to repo for AI handoff context"
        if errorlevel 1 ( echo [HATA] TASKS commit basarisiz. & goto :fail )
        echo [OK] TASKS.md commit atildi.
    )
) else (
    echo    [skip] TASKS.md yok.
)
echo.

REM --- [5/6] Guvenlik dogrulamasi ----------------------------
echo [5/6] Guvenlik dogrulamasi...
git ls-files | findstr /R /C:"^\.env$" >nul
if not errorlevel 1 ( echo [KRITIK] .env tracked! & goto :fail )
echo [OK] .env tracked degil.

git ls-files | findstr /R /C:"\.db$" >nul
if not errorlevel 1 ( echo [UYARI] *.db tracked, ama gitignore'da. ileri inceleme gerekir. )

REM --- [6/6] Push --------------------------------------------
echo.
echo [6/6] GitHub'a push...
echo.

REM Push edilecek commit var mi?
git fetch origin main 2>nul
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
    echo Iptal. Lokal commitler var ama push edilmedi.
    goto :fail
)

git push origin main
if errorlevel 1 ( echo [HATA] Push basarisiz. & goto :fail )

:done
echo.
echo ============================================================
echo   BASARILI - Repo polish tamam
echo ============================================================
echo.
echo Son 5 commit:
git log --oneline -5
echo.
echo Repo: https://github.com/Heddas98/polypaper-bot
echo Acmak: gh repo view --web
echo.
popd
pause
exit /b 0

:fail
echo.
echo ============================================================
echo   HATA - Repo polish yarida kaldi
echo ============================================================
echo.
echo Mevcut durum:
git status --short 2>nul | head -20
echo.
popd
pause
exit /b 1
