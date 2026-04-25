@echo off
REM T4.7-B telemetry status + activate guide.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === T4.7-B REST telemetry status check ===
echo.
echo 1. Mevcut .env'de REST_TIMING_TELEMETRY ayari:
findstr /B "REST_TIMING_TELEMETRY" .env 2>nul
if errorlevel 1 echo    (.env'de yok -- default false)
echo.

echo 2. Mevcut REST_LATENCY_MS (config/settings.py default):
findstr "REST_LATENCY_MS" config\settings.py | findstr "default"
echo.

echo === Telemetry ACMA (bot calistirken) ===
echo Bu adimi sen Telegram'dan yap:
echo.
echo   /envt REST_TIMING_TELEMETRY true
echo.
echo Bot 24h calisirsa (yaklasik 1500-3000 sample) yeterli veri toplar.
echo.
echo === Telemetry OKUMA (24h sonra) ===
echo Telegram'dan:
echo.
echo   /drt save                    -- JSON dosyaya yazsin
echo   veya scripts\_t47b_compute_p50.bat (asagida) calistir
echo.

popd
echo.
pause
