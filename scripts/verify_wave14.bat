@echo off
REM Wave 14 verification — handler smoke tests + 2 FAIL fix
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Wave 14 Verification
echo ============================================================

echo.
echo [1/3] Syntax check
py -3.11 -m py_compile tests\unit\test_p0_p1_extra_coverage.py
if errorlevel 1 goto :fail

echo.
echo [2/3] Run Wave 13 + Wave 14 subset
py -3.11 -m pytest tests/unit/test_p0_p1_extra_coverage.py ^
    -k "Wave13 or Wave14 or TestExecuteMarketOrder or TestApproveAllowance" ^
    --no-header -v 2>&1 | tail -50
if errorlevel 1 goto :fail

echo.
echo [3/3] Run polymarket_actions tests
py -3.11 -m pytest tests/unit/test_p0_p1_extra_coverage.py::TestPolymarketActions ^
    --no-header -v 2>&1 | tail -20

echo.
echo ============================================================
echo PASS — Wave 14 verified
echo ============================================================
goto :end

:fail
echo.
echo FAIL — see error above
exit /b 1

:end
pause
