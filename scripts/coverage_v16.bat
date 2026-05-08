@echo off
REM Wave 16 Mega Path Tests — büyük modüllere derin path coverage
REM Output: coverage_v16.txt
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 16 Coverage Run (target: 37.8%% -^> 50%%+)
echo ============================================================
echo Top 5 modul deep tests:
echo   - engine_signals (1034 stmts)
echo   - ai_brain (991 stmts)
echo   - strategy_plugins (765 stmts)
echo   - engine (699 stmts)
echo   - live_handler (562 stmts)
echo Output: coverage_v16.txt
echo.

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v16.txt 2>&1

echo.
echo ============================================================
echo TOTAL row + last 30 lines:
echo ============================================================
powershell -Command "Get-Content coverage_v16.txt | Select-Object -Last 30"

echo.
echo Full report: coverage_v16.txt
pause
