@echo off
REM ═══════════════════════════════════════════════════════════════════════
REM stop_bot.bat — Reliable PolyPaper Bot shutdown
REM ═══════════════════════════════════════════════════════════════════════
REM WHY THIS EXISTS:
REM   Old deploy bats used `taskkill /FI "WINDOWTITLE eq PolyPaper Bot*"`.
REM   That kills the cmd.exe host but Windows sometimes takes 2-5s to
REM   propagate to the python.exe child. If a new bot is started in that
REM   window, main.py's _acquire_instance_lock sees the old PID still
REM   alive and exits with SystemExit(1) ("bot hemen kapanıyor").
REM
REM   This bat reads data_store/polypaper.lock, kills that PID tree (/T),
REM   then falls back to window-title kill for safety, then removes the
REM   lockfile itself. Idempotent — safe to run even if no bot is up.
REM ═══════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion
set ROOT=%~dp0
cd /d "%ROOT%"

set LOCK=data_store\polypaper.lock

REM ── Stage 1: Kill by lockfile PID ──────────────────────────────────────
if exist "%LOCK%" (
    set /p BOT_PID=<"%LOCK%"
    echo [stop_bot] Lockfile PID: !BOT_PID!
    if defined BOT_PID (
        taskkill /F /T /PID !BOT_PID! >nul 2>&1
        if errorlevel 1 (
            echo [stop_bot] PID !BOT_PID! already dead or not found
        ) else (
            echo [stop_bot] Killed PID !BOT_PID! (and children via /T)
        )
    )
) else (
    echo [stop_bot] No lockfile — no running bot expected
)

REM ── Stage 2: Fallback — kill by window title ───────────────────────────
taskkill /F /FI "WINDOWTITLE eq PolyPaper Bot*" >nul 2>&1

REM ── Stage 3: Belt+suspenders — kill any python.exe running main.py ────
REM Uses WMIC to find python.exe processes whose command line contains main.py.
for /f "skip=1 tokens=2" %%p in ('wmic process where "name='python.exe' and commandline like '%%main.py%%'" get ProcessId 2^>nul') do (
    if not "%%p"=="" (
        echo [stop_bot] Stray python.exe PID %%p - killing
        taskkill /F /PID %%p >nul 2>&1
    )
)

REM ── Stage 4: Let OS finish tearing down then remove stale lockfile ─────
timeout /t 2 /nobreak >nul
if exist "%LOCK%" (
    del /f "%LOCK%" >nul 2>&1
    if exist "%LOCK%" (
        echo [stop_bot] WARNING: lockfile still present — manual delete may be needed
    ) else (
        echo [stop_bot] Lockfile removed
    )
)

echo [stop_bot] Done.
endlocal
