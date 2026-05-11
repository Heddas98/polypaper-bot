@echo off
REM P1-02-d (2026-05-11) — Stop AI Advisor service.
REM
REM Identifies the uvicorn process by port (default 8001) and terminates it.
REM Falls back to window-title match if the port lookup fails.

setlocal

set "ADVISOR_PORT=%AI_ADVISOR_PORT%"
if "%ADVISOR_PORT%"=="" set "ADVISOR_PORT=8001"

echo === Stopping AI Advisor on port %ADVISOR_PORT% ===

REM Find the PID listening on the port.
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":%ADVISOR_PORT% " ^| findstr "LISTENING"') do (
    echo Killing PID %%P (uvicorn on port %ADVISOR_PORT%)
    taskkill /F /PID %%P 2>nul
)

REM Belt-and-braces: also try to close the window by title.
taskkill /F /FI "WINDOWTITLE eq PolyPaper AI Advisor" 2>nul

echo Done.
endlocal
