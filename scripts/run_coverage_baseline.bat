@echo off
REM P1-01-b (2026-05-09): Baseline coverage measurement.
REM
REM Genişletilmiş source (core + data + telegram_bot + backtest) ile mevcut
REM coverage yüzdesini ölçer. Sonuç P1-01-c eşik kararına input olur.
REM
REM Çalıştırma: bu dosyayı çift-tıkla VEYA shell'den:
REM    scripts\run_coverage_baseline.bat
REM
REM Çıktı:
REM   * stdout — modül modül coverage özeti
REM   * data_store\audits\coverage_baseline_<UTC>.txt — full transcript
REM   * htmlcov\index.html — interactive HTML rapor

setlocal enabledelayedexpansion

REM Ensure we run from repo root (script's parent)
pushd "%~dp0\.."

if not exist data_store\audits mkdir data_store\audits

REM UTC timestamp
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value 2^>nul') do set "DT=%%a"
if "%DT%"=="" (
    REM Fallback for newer Windows where wmic is removed
    for /f "delims=" %%a in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')"') do set "DT=%%a"
)
set "OUT=data_store\audits\coverage_baseline_%DT:~0,15%.txt"

echo === P1-01-b coverage baseline run ===
echo Output: %OUT%
echo.

REM Run pytest with coverage. Skip integration (long, often Windows-only deps).
REM --cov-config picks up .coveragerc; --cov-report=term-missing prints uncovered lines.
py -3.11 -m pytest tests/ -m "not integration" ^
    --cov --cov-config=.coveragerc ^
    --cov-report=term-missing ^
    --cov-report=html ^
    -q --tb=short ^
    > "%OUT%" 2>&1

set "RC=%ERRORLEVEL%"

echo.
echo === Last 60 lines of output ===
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 60"
echo.
echo === Per-module coverage summary ===
findstr /C:"core/" /C:"data/" /C:"telegram_bot/" /C:"backtest/" /C:"TOTAL" "%OUT%"

echo.
if %RC% equ 0 (
    echo [OK] pytest passed
) else (
    echo [INFO] pytest exit=%RC% — see %OUT% for details
)
echo.
echo Full transcript: %OUT%
echo HTML report: htmlcov\index.html

popd
endlocal
