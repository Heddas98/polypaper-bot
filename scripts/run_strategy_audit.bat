@echo off
REM ============================================================================
REM PolyPaper - Strategy Audit (P1-04 re-audit helper, 2026-05-11)
REM
REM Calls scripts/audit_strategies.py with sensible defaults for the
REM 2026-05-16 re-audit. Two modes:
REM
REM   1) Plain audit (last 7 days; this is the typical re-audit cadence)
REM        scripts\run_strategy_audit.bat
REM
REM   2) Diff against a previous audit
REM        scripts\run_strategy_audit.bat --since-prev=data_store\audits\strategy_audit_2026XXXXTYYYYYYZ.md
REM
REM   3) Custom lookback
REM        scripts\run_strategy_audit.bat --days=14
REM
REM Output: data_store\audits\strategy_audit_<UTC>.md
REM ============================================================================

setlocal

pushd "%~dp0\.."
if errorlevel 1 goto :fail

set DAYS_ARG=--days=7
set DIFF_ARG=

:parse
if "%~1"=="" goto :run
echo %~1 | findstr /b "--days=" >nul && set DAYS_ARG=%~1
echo %~1 | findstr /b "--since-prev=" >nul && set DIFF_ARG=%~1
shift
goto :parse

:run
echo [audit] running: py -3.11 scripts\audit_strategies.py %DAYS_ARG% %DIFF_ARG%
py -3.11 scripts\audit_strategies.py %DAYS_ARG% %DIFF_ARG%
if errorlevel 1 goto :fail

popd
echo.
echo [audit] OK. Inspect data_store\audits\ for the new report.
pause
exit /b 0

:fail
popd
echo.
echo [audit] FAILED. Check error above.
pause
exit /b 1
