@echo off
REM T11.2 read-only kanıt toplayıcı — bot çalışmasa bile koşar.
REM G4 (PnL divergence) + G5 (Rolling WR kill historical) probe'larını
REM sırayla çalıştırır, çıktıları evidence\ klasörüne yazar.
cd /d "%~dp0\.."

if not exist evidence mkdir evidence

REM Windows 11'de wmic kaldırıldı; PowerShell ile timestamp üret.
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set TS=%%a
if "%TS%"=="" set TS=notimestamp

echo.
echo ============================================================
echo  T11.2 Read-Only Probes — %DATE% %TIME%
echo ============================================================
echo.

echo [1/2] G4 PnL Divergence Probe ...
py -3.11 scripts\t11_2_g4_divergence_probe.py > "evidence\t11_2_g4_%TS%.txt" 2>&1
type "evidence\t11_2_g4_%TS%.txt"
py -3.11 scripts\t11_2_g4_divergence_probe.py --json > "evidence\t11_2_g4_%TS%.json" 2>&1

echo.
echo ============================================================
echo.

echo [2/2] G5 Rolling WR Kill Historical ...
py -3.11 scripts\t11_2_g5_wr_kill_historical.py > "evidence\t11_2_g5_%TS%.txt" 2>&1
type "evidence\t11_2_g5_%TS%.txt"
py -3.11 scripts\t11_2_g5_wr_kill_historical.py --json > "evidence\t11_2_g5_%TS%.json" 2>&1

echo.
echo ============================================================
echo  Evidence files:
echo    evidence\t11_2_g4_%TS%.txt  + .json
echo    evidence\t11_2_g5_%TS%.txt  + .json
echo ============================================================
echo.
pause
