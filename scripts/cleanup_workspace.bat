@echo off
REM ============================================================================
REM PolyPaper - Workspace cleanup (2026-05-12)
REM
REM Three cleanup tiers. Each is opt-in via command-line flag.
REM
REM SAFE (always reversible, no production data):
REM   scripts\cleanup_workspace.bat --safe
REM   - __pycache__ (regenerated on next run)
REM   - htmlcov/ (regenerated on next pytest --cov)
REM   - 0-byte placeholder files (admin_chat.json etc. — recreated by bot)
REM   - tmp-journal files (0-byte SQLite artifacts)
REM   - Rotated log .log.1, .log.2 files
REM
REM SNAPSHOT (production DB backups older than 3 days):
REM   scripts\cleanup_workspace.bat --snapshots
REM   - data_store/backups/polypaper_2026-MM-DD.db older than 3 days
REM   - Keeps the 2 most recent backups by date
REM
REM ALL (combines both — interactive prompt):
REM   scripts\cleanup_workspace.bat --all
REM ============================================================================

setlocal EnableDelayedExpansion

pushd "%~dp0\.."
if errorlevel 1 goto :fail

set MODE=
:parse
if "%~1"=="" goto :run
if /i "%~1"=="--safe" set MODE=safe
if /i "%~1"=="--snapshots" set MODE=snapshots
if /i "%~1"=="--all" set MODE=all
shift
goto :parse

:run
if "%MODE%"=="" (
    echo Usage:
    echo   scripts\cleanup_workspace.bat --safe        ^(__pycache__, htmlcov, empty files^)
    echo   scripts\cleanup_workspace.bat --snapshots   ^(DB backups older than 3 days^)
    echo   scripts\cleanup_workspace.bat --all         ^(both, with prompt^)
    goto :end
)

echo === PolyPaper workspace cleanup ===
echo Mode: %MODE%
echo Working dir: %CD%
echo.

if /i "%MODE%"=="all" (
    echo This will run BOTH --safe AND --snapshots.
    set /p CONFIRM=Confirm with Y to proceed:
    if /i not "!CONFIRM!"=="Y" (
        echo Aborted.
        goto :end
    )
)

REM ─── SAFE tier ─────────────────────────────────────────────────────────────
if /i "%MODE%"=="safe" goto :safe
if /i "%MODE%"=="all" goto :safe
goto :snapshots_check

:safe
echo.
echo [SAFE] Removing __pycache__ directories...
for /d /r . %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d"
)

echo [SAFE] Removing htmlcov/ ^(regeneratable^)...
if exist htmlcov rmdir /s /q htmlcov

echo [SAFE] Removing .pytest_cache/ ...
if exist .pytest_cache rmdir /s /q .pytest_cache

echo [SAFE] Removing .mypy_cache/ ...
if exist .mypy_cache rmdir /s /q .mypy_cache

echo [SAFE] Removing .ruff_cache/ ...
if exist .ruff_cache rmdir /s /q .ruff_cache

echo [SAFE] Removing 0-byte tmp-journal files in data_store/backups/...
for %%f in (data_store\backups\*.tmp-journal) do (
    for %%s in ("%%f") do (
        if %%~zs==0 del /q "%%f"
    )
)
for %%f in (data_store\backups\*-journal) do (
    for %%s in ("%%f") do (
        if %%~zs==0 del /q "%%f"
    )
)

echo [SAFE] Removing 0-byte placeholder files in data_store/...
for %%f in (data_store\admin_chat.json data_store\diagnose_result.txt data_store\last50.txt data_store\log600.txt data_store\log_extract.txt data_store\log_trades.txt) do (
    if exist "%%f" (
        for %%s in ("%%f") do (
            if %%~zs==0 del /q "%%f"
        )
    )
)

echo [SAFE] Removing rotated logs .log.1, .log.2 ...
if exist data_store\polypaper.log.1 del /q data_store\polypaper.log.1
if exist data_store\polypaper.log.2 del /q data_store\polypaper.log.2

echo [SAFE] Done.
echo.

if /i "%MODE%"=="safe" goto :end

:snapshots_check
REM ─── SNAPSHOTS tier ────────────────────────────────────────────────────────
if /i "%MODE%"=="snapshots" goto :snapshots
if /i "%MODE%"=="all" goto :snapshots
goto :end

:snapshots
echo.
echo [SNAPSHOTS] Listing backups in data_store/backups/...
dir /B /O:D data_store\backups\polypaper_*.db 2^>nul ^| findstr /R "polypaper_.*\.db$"
echo.
echo Strategy: keep the 2 most recent ^(by date^), remove the rest.
set /p CONFIRM=Confirm with Y to proceed:
if /i not "%CONFIRM%"=="Y" (
    echo Aborted snapshots cleanup.
    goto :end
)

REM Get sorted list of .db backup files; skip last 2.
set COUNT=0
for /f "delims=" %%f in ('dir /B /O:-D data_store\backups\polypaper_*.db 2^>nul') do (
    set /a COUNT+=1
    if !COUNT! GTR 2 (
        echo Removing data_store\backups\%%f
        del /q "data_store\backups\%%f"
    ) else (
        echo Keeping  data_store\backups\%%f
    )
)

echo.
echo [SNAPSHOTS] Done.

:end
echo.
echo === Cleanup complete ===
popd
pause
exit /b 0

:fail
popd
echo FAILED to cd into project root.
pause
exit /b 1
