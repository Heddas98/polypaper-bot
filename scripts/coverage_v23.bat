@echo off
REM Wave 23 INTEGRATION — real DB + real Engine + real handlers
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 23 INTEGRATION Coverage Run (target: 43.6%% -^> 55%%+)
echo Real aiosqlite Database + run_migrations + real Engine
echo Test count: ~80 yeni integration test
echo ============================================================

py -3.11 -m pip install pytest-asyncio --quiet 2>nul

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest --cov=db ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v23.txt 2>&1

powershell -Command "Get-Content coverage_v23.txt | Select-Object -Last 35"
echo.
echo Full report: coverage_v23.txt
pause
