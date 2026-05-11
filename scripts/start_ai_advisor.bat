@echo off
REM P1-02-d (2026-05-11) — Start AI Advisor service in a new window.
REM
REM Usage:
REM   1) Once: install dev deps:
REM        py -3.11 -m pip install -r requirements-dev.txt
REM   2) Double-click this file OR run from PowerShell:
REM        scripts\start_ai_advisor.bat
REM
REM Service runs on http://127.0.0.1:8001 by default.
REM Override port via env: set AI_ADVISOR_PORT=8002 then start.
REM
REM Wave 1: scaffold only — /suggest returns stub HOLD.
REM Stop via:
REM   scripts\stop_ai_advisor.bat
REM   OR close the spawned PowerShell window.

setlocal

set "ADVISOR_PORT=%AI_ADVISOR_PORT%"
if "%ADVISOR_PORT%"=="" set "ADVISOR_PORT=8001"

set "ADVISOR_HOST=%AI_ADVISOR_HOST%"
if "%ADVISOR_HOST%"=="" set "ADVISOR_HOST=127.0.0.1"

echo === Starting AI Advisor (Wave 1 scaffold) ===
echo Host: %ADVISOR_HOST%
echo Port: %ADVISOR_PORT%
echo Health: http://%ADVISOR_HOST%:%ADVISOR_PORT%/health
echo.

pushd "%~dp0\.."

REM Spawn in a new window so closing this one doesn't kill the service.
REM Note: title is "PolyPaper AI Advisor" so stop_ai_advisor.bat can target it.
start "PolyPaper AI Advisor" cmd /k "py -3.11 -m uvicorn services.ai_advisor.app:app --host %ADVISOR_HOST% --port %ADVISOR_PORT% --reload"

echo.
echo Service started in a new window (title: "PolyPaper AI Advisor").
echo Test with:  scripts\ai_advisor_smoke.bat
echo Stop with:  scripts\stop_ai_advisor.bat

popd
endlocal
