@echo off
REM Wave 13 coverage run — full test suite + report
REM Usage: scripts\coverage_wave13.bat
REM Output: coverage_v13.txt
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 13 Coverage Run
echo ============================================================
echo Output: coverage_v13.txt
echo.

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v13.txt 2>&1

echo.
echo ============================================================
echo Last 30 lines of coverage_v13.txt:
echo ============================================================
type coverage_v13.txt | more +%COVLEN%
echo.
echo Full report: coverage_v13.txt
pause
