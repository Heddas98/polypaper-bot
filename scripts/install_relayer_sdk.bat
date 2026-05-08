@echo off
REM Polymarket Relayer SDK kurulumu
REM Usage: scripts\install_relayer_sdk.bat
SETLOCAL

cd /d "%~dp0\.."

echo ============================================================
echo Polymarket Relayer SDK Installation
echo ============================================================
echo.

echo [1/3] Updating pip...
py -3.11 -m pip install --upgrade pip
if errorlevel 1 echo (pip upgrade warning - continuing)

echo.
echo [2/4] Installing py-builder-relayer-client...
py -3.11 -m pip install py-builder-relayer-client
if errorlevel 1 goto :fail

echo.
echo [3/4] Installing web3 (ABI encoder)...
py -3.11 -m pip install web3
if errorlevel 1 goto :fail

echo.
echo [4/4] Verifying imports...
py -3.11 -c "from py_builder_relayer_client.client import RelayClient; print('OK RelayClient imported')"
if errorlevel 1 goto :fail
py -3.11 -c "from web3 import Web3; w = Web3(); print('OK web3 imported')"
if errorlevel 1 echo (web3 warning - bot manual hex encode kullanir)

echo.
echo ============================================================
echo SUCCESS — Relayer SDK kuruldu
echo ============================================================
echo.
echo Sonraki adim:
echo   1. Botu durdur (Ctrl+C)
echo   2. Botu yeniden baslat
echo   3. Telegram /allowance
echo.
goto :end

:fail
echo.
echo ============================================================
echo FAIL — kurulum hatasi
echo ============================================================
echo.
echo Manuel deneme:
echo   py -3.11 -m pip install py-builder-relayer-client
echo.
echo Olası sebep: Python 3.11 ile uyumsuz - eski versiyon dene:
echo   py -3.11 -m pip install py-builder-relayer-client==0.1.0
echo.
exit /b 1

:end
pause
