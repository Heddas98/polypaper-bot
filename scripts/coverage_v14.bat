@echo off
REM Wave 14 coverage run — full test suite + report
REM Output: coverage_v14.txt
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 14 Coverage Run (target: 36.2%% -^> 45%%+)
echo ============================================================
echo Output: coverage_v14.txt
echo.

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v14.txt 2>&1

echo.
echo ============================================================
echo Last 25 lines (TOTAL row + summary):
echo ============================================================
powershell -Command "Get-Content coverage_v14.txt | Select-Object -Last 25"

echo.
echo Full report: coverage_v14.txt
pause
