@echo off
REM Phase 79 DB Migration — Windows Runner
REM Adds missing columns: executions.signal_score, executions.conviction, whale_trades table
REM Usage: migration_phase79.bat [optional-db-path]

setlocal enabledelayedexpansion

if "%1"=="" (
    set DB_PATH=polypaper.db
) else (
    set DB_PATH=%1
)

echo [Phase 79] Running migration on: !DB_PATH!
py -3.11 db/migration_phase79.py !DB_PATH!

if %errorlevel% equ 0 (
    echo.
    echo [Phase 79] Migration successful
) else (
    echo.
    echo [Phase 79] Migration FAILED with error code %errorlevel%
)

pause
endlocal
