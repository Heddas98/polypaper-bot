@echo off
REM P1-07-c (2026-05-09) — Local lint + type-check runner.
REM
REM Çalıştırma: bu dosyayı çift-tıkla VEYA
REM     scripts\run_lint.bat
REM
REM İlk kez koşturmadan önce dev deps yüklenmiş olmalı:
REM     py -3.11 -m pip install -r requirements-dev.txt
REM
REM Çıktı:
REM   * data_store\audits\lint_<UTC>.txt — full transcript
REM   * Console — özet (error count)

setlocal enabledelayedexpansion

pushd "%~dp0\.."

if not exist data_store\audits mkdir data_store\audits

REM UTC timestamp via PowerShell (wmic deprecated on newer Windows)
for /f "delims=" %%a in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')"') do set "DT=%%a"
set "OUT=data_store\audits\lint_%DT%.txt"

echo === P1-07 lint + type-check run ===
echo Output: %OUT%
echo.

REM ── ruff check ───────────────────────────────────────────────────────
echo === ruff check ===
py -3.11 -m ruff check . > "%OUT%" 2>&1
set "RUFF_RC=%ERRORLEVEL%"

if !RUFF_RC! equ 0 (
    echo [OK] ruff clean — zero violations.
) else (
    echo [INFO] ruff exit=!RUFF_RC! — see %OUT%
    echo Top 20 violations:
    powershell -NoProfile -Command "Get-Content '%OUT%' -Head 40"
)
echo. >> "%OUT%"

REM ── mypy ─────────────────────────────────────────────────────────────
echo. >> "%OUT%"
echo === mypy core/ === >> "%OUT%"
echo. >> "%OUT%"
echo === mypy core/ ===
py -3.11 -m mypy core/ >> "%OUT%" 2>&1
set "MYPY_RC=%ERRORLEVEL%"

if !MYPY_RC! equ 0 (
    echo [OK] mypy clean — no type errors in core/.
) else (
    echo [INFO] mypy exit=!MYPY_RC! — see %OUT%
    powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 20"
)

REM ── Summary ──────────────────────────────────────────────────────────
echo.
echo === Summary ===
echo   ruff:  exit=!RUFF_RC!
echo   mypy:  exit=!MYPY_RC!
echo.
echo Full transcript: %OUT%

popd
endlocal
