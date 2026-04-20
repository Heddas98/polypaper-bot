@echo off
echo ============================================
echo  Phase 61: Strategy Reset + Bot Restart
echo ============================================
echo.

echo [1/4] Killing all Python processes...
taskkill /F /IM python.exe 2>nul
taskkill /F /IM python3.exe 2>nul
taskkill /F /IM py.exe 2>nul
taskkill /F /IM wscript.exe 2>nul
del /Q *.lock 2>nul
echo Done.

echo.
echo [2/4] Waiting 5 seconds for DB release...
timeout /t 5 /nobreak >nul

echo.
echo [3/4] Running strategy reset script (20 strategies)...
py -3.11 scripts/reset_strategies_20.py
if errorlevel 1 (
    echo ❌ Strategy reset FAILED!
    pause
    exit /b 1
)

echo.
echo [4/4] Starting bot...
timeout /t 3 /nobreak >nul
start "PolyPaper Bot" py -3.11 main.py

echo.
echo ✅ Bot started with 20 new strategies!
echo    Check Telegram /dashboard for status.
timeout /t 5
