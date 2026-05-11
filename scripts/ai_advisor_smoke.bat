@echo off
REM P1-02-d (2026-05-11) — Smoke test the running AI Advisor service.
REM
REM Hits /health and POST /suggest with a sample payload.
REM Requires curl (Windows 10+ has it built in).

setlocal

set "ADVISOR_PORT=%AI_ADVISOR_PORT%"
if "%ADVISOR_PORT%"=="" set "ADVISOR_PORT=8001"
set "BASE=http://127.0.0.1:%ADVISOR_PORT%"

echo === /health ===
curl -s -o - -w "\nstatus=%%{http_code}\n" %BASE%/health
echo.

echo === /suggest (stub Wave 1) ===
curl -s -X POST -H "Content-Type: application/json" ^
    -d "{\"market\": {\"slug\": \"btc-up-or-down-on-may-11-2026\", \"asset\": \"BTC\", \"timeframe\": \"24h\"}, \"strategy\": {\"label\": \"M_BTC_5m_any_0.92\"}, \"correlation_id\": \"smoke-001\"}" ^
    -w "\nstatus=%%{http_code}\n" ^
    %BASE%/suggest
echo.

echo === /stats ===
curl -s -o - -w "\nstatus=%%{http_code}\n" %BASE%/stats
echo.

echo Done. Expected: 200 200 200 with stub_mode=true on /suggest.

endlocal
