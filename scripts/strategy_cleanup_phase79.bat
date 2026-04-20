@echo off
REM Phase 79: Strategy Cleanup — Stop Losing Strategies
REM Keep only the 4 profitable strategies active
REM
REM Usage: strategy_cleanup_phase79.bat

echo.
echo ============================================================
echo Phase 79: Strategy Cleanup — Profitability Optimization
echo ============================================================
echo.

py -3.11 scripts\strategy_cleanup_phase79.py

echo.
pause
