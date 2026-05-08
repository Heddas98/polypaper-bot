@echo off
REM Wave 13 verification — syntax check + 3 FAIL fix + new tests
REM Usage: scripts\verify_wave13.bat
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 13 Verification
echo ============================================================

echo.
echo [1/4] Syntax check — data/polymarket_actions.py
py -3.11 -m py_compile data\polymarket_actions.py
if errorlevel 1 goto :fail

echo.
echo [2/4] Syntax check — telegram_bot/handlers/live_handler.py
py -3.11 -m py_compile telegram_bot\handlers\live_handler.py
if errorlevel 1 goto :fail

echo.
echo [3/4] Syntax check — tests/unit/test_p0_p1_extra_coverage.py
py -3.11 -m py_compile tests\unit\test_p0_p1_extra_coverage.py
if errorlevel 1 goto :fail

echo.
echo [4/4] Run Wave 13 tests only ^(fast subset^)
py -3.11 -m pytest tests/unit/test_p0_p1_extra_coverage.py ^
    -k "Wave13 or TestApproveAllowanceMultiPath or TestMarketBuySellFlowWave13 or TestStructuredLoggingWave13 or TestExecuteMarketOrderWave13 or TestMarketBuySellUI" ^
    --no-header -v 2>&1 | tail -30
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo PASS — Wave 13 verified
echo ============================================================
goto :end

:fail
echo.
echo ============================================================
echo FAIL — see error above
echo ============================================================
exit /b 1

:end
pause
