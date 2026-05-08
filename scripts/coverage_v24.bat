@echo off
REM Wave 24 SAFE — Wave 23 disabled (Windows aiosqlite crash)
REM Bu wave: boundary inputs + edge cases, no real DB
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 24 SAFE Coverage Run
echo Wave 23 DISABLED (Windows access violation crash)
echo Hedef: stable %43.6%% -^> %45-46%%
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    --ignore=tests/unit/test_wave23_integration.py ^
    > coverage_v24.txt 2>&1

powershell -Command "Get-Content coverage_v24.txt | Select-Object -Last 35"
echo.
echo Full report: coverage_v24.txt
pause
