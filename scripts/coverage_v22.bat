@echo off
REM Wave 22 MEGA — conftest + bot.py + strategy 100-snap lifecycle
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 22 MEGA Coverage Run (target: 43.5%% -^> 50%%+)
echo Strategy: parametric module imports + 100-snap strategy lifecycle
echo Test count: ~150 yeni test
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v22.txt 2>&1

powershell -Command "Get-Content coverage_v22.txt | Select-Object -Last 35"
echo.
echo Full report: coverage_v22.txt
pause
