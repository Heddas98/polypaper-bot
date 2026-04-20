@echo off
REM ============================================================
REM PolyPaper Bot - Becker dataset downloader (Phase 44c)
REM
REM Fetches Jon Becker's prediction-market-analysis archive
REM (Polymarket + Kalshi trades + markets, ~36GB compressed)
REM and extracts it into data_store\becker_raw\.
REM
REM Requires: curl (built into Win10/11), zstd.exe, tar.exe
REM   - tar.exe: built-in on modern Windows
REM   - zstd.exe: choco install zstandard  OR  winget install Facebook.Zstandard
REM ============================================================

setlocal
cd /d "%~dp0\.."

REM Canonical URL verified from Jon-Becker/prediction-market-analysis
REM scripts/download.sh on 2026-04-08. Override via BECKER_DATA_URL env var
REM if Cloudflare R2/S3 host changes.
if "%BECKER_DATA_URL%"=="" (
    set "BECKER_DATA_URL=https://s3.jbecker.dev/data.tar.zst"
)

if not exist "data_store" mkdir data_store
set "ARCHIVE=data_store\becker_data.tar.zst"
set "RAW=data_store\becker_raw"

echo.
echo ============================================================
echo  Becker Dataset Downloader
echo ============================================================
echo  URL    : %BECKER_DATA_URL%
echo  Target : %ARCHIVE%
echo  Extract: %RAW%
echo  ETA    : 30-60 min depending on connection
echo ============================================================
echo.

if exist "%ARCHIVE%" (
    echo [SKIP] Archive already exists. Delete it to re-download.
) else (
    echo [1/3] Downloading via curl with resume support...
    curl -L -C - --retry 5 --retry-delay 10 -o "%ARCHIVE%" "%BECKER_DATA_URL%"
    if errorlevel 1 (
        echo [ERROR] curl download failed. Check %BECKER_DATA_URL% and try again.
        exit /b 1
    )
)

if not exist "%RAW%" mkdir "%RAW%"

echo.
echo [2/3] Decompressing zstd...
where zstd >nul 2>&1
if errorlevel 1 (
    echo [ERROR] zstd.exe not found in PATH.
    echo         Install via:  winget install Facebook.Zstandard
    exit /b 2
)
zstd -d "%ARCHIVE%" -o "data_store\becker_data.tar"
if errorlevel 1 (
    echo [ERROR] zstd decompression failed.
    exit /b 3
)

echo.
echo [3/3] Extracting tar to %RAW% ...
tar -xf "data_store\becker_data.tar" -C "%RAW%"
if errorlevel 1 (
    echo [ERROR] tar extract failed.
    exit /b 4
)

del "data_store\becker_data.tar"

echo.
echo ============================================================
echo  Download + extract complete.
echo  Next: open Telegram and run  /becker_build
echo  (materializes the crypto-only calibration DB via DuckDB)
echo ============================================================
endlocal
