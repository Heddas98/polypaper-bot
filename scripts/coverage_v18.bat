@echo off
REM Wave 18 — REAL path execution (mixin + DB stub + strategy loops)
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 18 Coverage Run (target: 39.1%% -^> 50%%+)
echo Real mixin call paths, no module-blast
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v18.txt 2>&1

powershell -Command "Get-Content coverage_v18.txt | Select-Object -Last 30"
echo.
echo Full report: coverage_v18.txt
pause
