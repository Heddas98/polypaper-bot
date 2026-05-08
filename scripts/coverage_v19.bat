@echo off
REM Wave 19 — Real async integration to top 7 modules
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 19 Coverage Run (target: 39%% -^> 50%%+)
echo Top 7 deep targets:
echo   engine_signals (1034) ai_brain (991) strategies (781)
echo   engine (699) backtest_v2 (691) live_handler (665) stats (540)
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v19.txt 2>&1

powershell -Command "Get-Content coverage_v19.txt | Select-Object -Last 30"
echo.
echo Full report: coverage_v19.txt
pause
