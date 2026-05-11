@echo off
REM P1-07-followup (2026-05-11) - Auto-fix ruff violations + re-run lint.
REM
REM Calistirma: cift-tikla VEYA
REM     scripts\run_lint_fix.bat
REM
REM Adımlar:
REM   1. ruff check --fix .          (1078 auto-fix uygula)
REM   2. ruff format .               (consistent formatting)
REM   3. ruff check .                (kalan kontrol)
REM   4. mypy core/                  (baseline icin)
REM   5. Yeni transcript yaz
REM
REM Bot durdurulmasina gerek yok - sadece kaynak dosyalari editler.
REM GIT: degisikliklerden once `git status` ile commit/stash yap.

setlocal enabledelayedexpansion

pushd "%~dp0\.."

if not exist data_store\audits mkdir data_store\audits

for /f "delims=" %%a in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')"') do set "DT=%%a"
set "OUT=data_store\audits\lint_fix_%DT%.txt"

echo === P1-07 ruff auto-fix + re-lint ===
echo Output: %OUT%
echo.

REM ── Step 1: ruff check --fix ─────────────────────────────────────────
echo === ruff check --fix === > "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff check --fix . >> "%OUT%" 2>&1
set "FIX_RC=%ERRORLEVEL%"
echo. >> "%OUT%"

echo [Step 1] ruff check --fix complete (exit=!FIX_RC!)

REM ── Step 2: ruff format ──────────────────────────────────────────────
echo === ruff format === >> "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff format . >> "%OUT%" 2>&1
set "FMT_RC=%ERRORLEVEL%"
echo. >> "%OUT%"

echo [Step 2] ruff format complete (exit=!FMT_RC!)

REM ── Step 3: ruff check (verify) ──────────────────────────────────────
echo === ruff check (re-run) === >> "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff check . >> "%OUT%" 2>&1
set "CHECK_RC=%ERRORLEVEL%"
echo. >> "%OUT%"

echo [Step 3] ruff check re-run (exit=!CHECK_RC!)

REM ── Step 4: mypy core/ baseline ──────────────────────────────────────
echo === mypy core/ baseline === >> "%OUT%"
echo. >> "%OUT%"
py -3.11 -m mypy core/ >> "%OUT%" 2>&1
set "MYPY_RC=%ERRORLEVEL%"

echo [Step 4] mypy baseline (exit=!MYPY_RC!)

REM ── Summary ──────────────────────────────────────────────────────────
echo.
echo === Summary ===
echo   ruff fix:    exit=!FIX_RC!
echo   ruff format: exit=!FMT_RC!
echo   ruff check:  exit=!CHECK_RC!
echo   mypy:        exit=!MYPY_RC!
echo.

REM Show kalan violation count
echo === Last 30 lines of transcript ===
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 30"

echo.
echo Full transcript: %OUT%
echo.
echo NEXT: git diff to review changes, then commit if happy.
echo       py -3.11 -m pytest tests/ -m "not integration" to verify
echo       tests still pass after auto-fixes.

popd
endlocal
