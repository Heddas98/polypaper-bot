@echo off
REM SELL UI fix verify
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo SELL UI Fix Verification
echo ============================================================

echo.
echo [1/2] Syntax: live_handler.py
py -3.11 -m py_compile telegram_bot\handlers\live_handler.py
if errorlevel 1 goto :fail

echo [2/2] Syntax: bot.py
py -3.11 -m py_compile telegram_bot\bot.py
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo PASS — restart bot + test /live SELL
echo ============================================================
goto :end

:fail
echo FAIL — see error above
exit /b 1

:end
pause
