@echo off
REM Wave 15 coverage run — büyük modüller smoke blast
REM Output: coverage_v15.txt
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 15 Coverage Run (target: 37.3%% -^> 45%%+)
echo ============================================================
echo Output: coverage_v15.txt
echo.

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v15.txt 2>&1

echo.
echo ============================================================
echo TOTAL row + last 25 lines:
echo ============================================================
powershell -Command "Get-Content coverage_v15.txt | Select-Object -Last 30"

echo.
echo Full report: coverage_v15.txt
pause
