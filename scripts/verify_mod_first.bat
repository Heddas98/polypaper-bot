@echo off
REM Mod-First Dashboard verification — Heddas 2026-05-06 UX redesign
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Mod-First Dashboard Verification
echo ============================================================

echo.
echo [1/5] data/polymarket_portfolio.py syntax
py -3.11 -m py_compile data\polymarket_portfolio.py
if errorlevel 1 goto :fail

echo [2/5] telegram_bot/handlers/main_dashboard.py syntax
py -3.11 -m py_compile telegram_bot\handlers\main_dashboard.py
if errorlevel 1 goto :fail

echo [3/5] telegram_bot/handlers/live_history_handler.py syntax
py -3.11 -m py_compile telegram_bot\handlers\live_history_handler.py
if errorlevel 1 goto :fail

echo [4/5] telegram_bot/bot.py syntax
py -3.11 -m py_compile telegram_bot\bot.py
if errorlevel 1 goto :fail

echo [5/5] Import test
py -3.11 -c "from telegram_bot.handlers.main_dashboard import main_command, main_callback, paper_dashboard, live_dashboard; print('OK main_dashboard')"
if errorlevel 1 goto :fail
py -3.11 -c "from telegram_bot.handlers.live_history_handler import live_history_callback, live_history_command; print('OK live_history_handler')"
if errorlevel 1 goto :fail
py -3.11 -c "from data.polymarket_portfolio import fetch_activity, fetch_closed_positions, ActivityRow, ClosedPositionRow; print('OK polymarket_portfolio')"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo PASS — Mod-First Dashboard hazir
echo ============================================================
echo.
echo Sonraki adim:
echo   1. Bot durdur (Ctrl+C)
echo   2. Bot baslat
echo   3. Telegram /start  ^(yeni mod-first dashboard^)
echo   4. PAPER MODE veya LIVE MODE sec
echo   5. /lh komutu  ^(live trade history^)
echo.
goto :end

:fail
echo.
echo FAIL — see error above
exit /b 1

:end
pause
