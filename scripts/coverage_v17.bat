@echo off
REM Wave 17 — integration-lite + deep helpers
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 17 Coverage Run (target: 37.6%% -^> 45%%+)
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v17.txt 2>&1

powershell -Command "Get-Content coverage_v17.txt | Select-Object -Last 30"
echo.
echo Full report: coverage_v17.txt
pause
