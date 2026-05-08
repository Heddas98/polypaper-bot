@echo off
REM Wave 20 — MEGA boost top 10 modules
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 20 MEGA Coverage Run (target: 41.5%% -^> 55%%+)
echo Top 10 deep targets:
echo   engine_signals (1034) ai_brain (991) strategies (781)
echo   live_handler (694) backtest_v2 (691) engine (699)
echo   stats (540) bot.py (448) polymarket_portfolio (425)
echo   auto_optimizer (384)
echo Plus new Wave 19+ feature tests:
echo   main_dashboard, live_history_handler, redeem_position
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v20.txt 2>&1

powershell -Command "Get-Content coverage_v20.txt | Select-Object -Last 35"
echo.
echo Full report: coverage_v20.txt
pause
