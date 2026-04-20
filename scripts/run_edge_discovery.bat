@echo off
REM Phase 79: Edge Discovery Runner
REM Analyzes profitable trade zones and generates edge report
REM
REM Usage: run_edge_discovery.bat

echo.
echo ============================================================
echo Running PolyPaper Edge Discovery Analysis...
echo ============================================================
echo.

cd /d "%~dp0\.."

py -3.11 analysis/edge_discovery.py --output analysis/edge_report.md

echo.
echo ============================================================
echo Report saved to: analysis\edge_report.md
echo ============================================================
echo.

pause
