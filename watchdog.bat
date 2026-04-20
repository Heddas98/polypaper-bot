@echo off
setlocal enabledelayedexpansion

REM ========================================
REM PolyPaper Bot Watchdog v2.0 (Phase 57)
REM Single-instance guard + robust restart
REM ========================================

cd /d "%~dp0"

REM ── Single-instance guard ──────────────────
set "WD_LOCK=logs\watchdog.lock"
if not exist logs mkdir logs

REM Check if another watchdog is already running
if exist "%WD_LOCK%" (
    set /p WD_PID=<"%WD_LOCK%"
    tasklist /FI "PID eq !WD_PID!" 2>NUL | find "!WD_PID!" >NUL
    if not errorlevel 1 (
        echo [%date% %time%] Another watchdog is running (PID !WD_PID!). Exiting.
        echo [%date% %time%] Another watchdog is running ^(PID !WD_PID!^). Exiting. >> logs\watchdog.log
        goto :EOF
    )
    echo [%date% %time%] Stale watchdog lock (PID !WD_PID! dead). Overwriting.
)

REM Write our PID to lockfile
for /f "tokens=2" %%a in ('tasklist /FI "WINDOWTITLE eq %~nx0" /NH 2^>NUL ^| findstr /i "cmd.exe"') do set "MY_PID=%%a"
REM Fallback: use parent PID approximation
if not defined MY_PID (
    wmic process where "name='cmd.exe' and commandline like '%%watchdog%%'" get processid /format:value 2>NUL | find "=" > "%WD_LOCK%.tmp" && for /f "tokens=2 delims==" %%p in (%WD_LOCK%.tmp) do set "MY_PID=%%p"
    del /q "%WD_LOCK%.tmp" 2>NUL
)
if not defined MY_PID set "MY_PID=unknown"
echo !MY_PID! > "%WD_LOCK%"

REM ── Variables ──────────────────────────────
set "LOG_FILE=logs\watchdog.log"
set "RESTART_COUNT=0"
set "HOUR_STARTED=%time:~0,2%"
set "MAX_RESTARTS_PER_HOUR=5"
set "PAUSE_DURATION=600"
set "CHECK_INTERVAL=30"
set "BOT_PID_FILE=data_store\polypaper.lock"

REM Startup banner
cls
echo.
echo ========================================
echo PolyPaper Bot Watchdog v2.0 (Phase 57)
echo Single-instance, robust restart
echo ========================================
echo.
call :LOG_MESSAGE "Watchdog v2.0 started (PID=!MY_PID!)"

:MAIN_LOOP
REM ── Hour reset ─────────────────────────────
set "CURRENT_HOUR=%time:~0,2%"
if not "!CURRENT_HOUR!"=="!HOUR_STARTED!" (
    set "RESTART_COUNT=0"
    set "HOUR_STARTED=!CURRENT_HOUR!"
)

REM ── Check if bot is running via its own lockfile ──
if exist "%BOT_PID_FILE%" (
    set /p BOT_PID=<"%BOT_PID_FILE%"
    REM Check if that specific PID is alive AND is python
    tasklist /FI "PID eq !BOT_PID!" /NH 2>NUL | find /i "python" >NUL
    if not errorlevel 1 (
        REM Bot is alive — sleep and recheck
        echo [%date% %time%] Bot running (PID !BOT_PID!)
        timeout /t %CHECK_INTERVAL% /nobreak >NUL
        goto MAIN_LOOP
    )
    REM PID file exists but process is dead — stale lock
    call :LOG_MESSAGE "Stale bot lock (PID !BOT_PID! dead). Cleaning up."
    del /q "%BOT_PID_FILE%" 2>NUL
)

REM ── Also check for ANY main.py python process ─────
wmic process where "name='python.exe' and commandline like '%%main.py%%'" get processid /format:value 2>NUL | find "ProcessId" >NUL
if not errorlevel 1 (
    call :LOG_MESSAGE "main.py python process found but no lockfile. Waiting."
    timeout /t %CHECK_INTERVAL% /nobreak >NUL
    goto MAIN_LOOP
)

REM ── Bot is NOT running — restart ───────────────
call :LOG_MESSAGE "Bot not running. Checking restart limit..."

if %RESTART_COUNT% geq %MAX_RESTARTS_PER_HOUR% (
    call :LOG_MESSAGE "Restart limit reached (%MAX_RESTARTS_PER_HOUR%/hr). Pausing %PAUSE_DURATION%s..."
    timeout /t %PAUSE_DURATION% /nobreak >NUL
    set "RESTART_COUNT=0"
    set "HOUR_STARTED=%time:~0,2%"
    goto MAIN_LOOP
)

set /a RESTART_COUNT+=1
call :LOG_MESSAGE "Starting bot (restart #%RESTART_COUNT%/%MAX_RESTARTS_PER_HOUR%)"

REM ── Kill any orphan python main.py processes first ─
taskkill /F /FI "WINDOWTITLE eq PolyPaper*" >NUL 2>&1
wmic process where "name='python.exe' and commandline like '%%main.py%%'" call terminate >NUL 2>&1
timeout /t 2 /nobreak >NUL

REM ── Start bot (blocking — watchdog waits) ──────
py -3.11 main.py

REM Bot exited
call :LOG_MESSAGE "Bot process exited (code=%ERRORLEVEL%). Will restart..."
timeout /t 5 /nobreak >NUL

goto MAIN_LOOP

REM ========================================
:LOG_MESSAGE
setlocal enabledelayedexpansion
set "MSG=%~1"
echo [%date% %time%] !MSG! >> "%LOG_FILE%"
echo [%date% %time%] !MSG!
endlocal
goto :EOF

:CLEANUP
del /q "%WD_LOCK%" 2>NUL
endlocal
