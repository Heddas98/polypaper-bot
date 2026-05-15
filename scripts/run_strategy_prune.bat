@echo off
REM ============================================================================
REM PolyPaper - Strategy Prune (P1-04 re-audit helper, 2026-05-11)
REM
REM Two-step workflow:
REM
REM   1) Dry-run preview (NO DB change):
REM        scripts\run_strategy_prune.bat
REM
REM   2) Apply after reviewing the dry-run output:
REM        scripts\run_strategy_prune.bat --apply
REM
REM   3) Apply without interactive prompt (CI / scheduled):
REM        scripts\run_strategy_prune.bat --apply --yes
REM
REM Side effect (only --apply): UPDATE strategies SET status='stopped' for
REM rows matching the criteria (no_trades / idle 7d+). Idempotent.
REM Audit trail: data_store\audits\prune_<UTC>.md.
REM ============================================================================

setlocal

pushd "%~dp0\.."
if errorlevel 1 goto :fail

set MODE_ARG=--dry-run
set CONFIRM_ARG=

:parse
if "%~1"=="" goto :run
if /i "%~1"=="--apply" set MODE_ARG=--apply
if /i "%~1"=="--yes" set CONFIRM_ARG=--yes
shift
goto :parse

:run
echo [prune] running: py -3.11 scripts\prune_strategies.py %MODE_ARG% %CONFIRM_ARG%
py -3.11 scripts\prune_strategies.py %MODE_ARG% %CONFIRM_ARG%
if errorlevel 1 goto :fail

popd
echo.
echo [prune] OK. Inspect data_store\audits\ for the prune log.
pause
exit /b 0

:fail
popd
echo.
echo [prune] FAILED. Check error above.
pause
exit /b 1
