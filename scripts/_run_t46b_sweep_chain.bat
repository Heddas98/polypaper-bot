@echo off
REM T4.6-B sweep retry chain -- Windows.
REM Adim 1: probe (hangi registered strategy trade uretiyor)
REM Adim 2: o strategy ile sweep_fill_heuristic.py (HEURISTIC vs EMPIRICAL)

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === 1/3 Probe: hangi strategy trade uretiyor? ===
py -3.11 scripts\_probe_trade_producing_strategies.py --markets 50
if errorlevel 1 (
  echo [fatal] probe basarisiz -- hic trade ureten strategy yok.
  echo --markets degeri dusuk olabilir veya DB bos.
  pause
  exit /b 1
)
echo.

echo === 2/3 Sentinel okuma ===
set BEST_STRAT=
for /f "usebackq delims=" %%s in (`type backtest\calibration\_probe_best_strategy.txt 2^>nul`) do set BEST_STRAT=%%s
if "%BEST_STRAT%"=="" (
  echo [fatal] sentinel okunamadi
  pause
  exit /b 1
)
echo Secilen strategy: %BEST_STRAT%
echo.

echo === 3/3 Sweep: HEURISTIC vs EMPIRICAL ===
py -3.11 scripts\sweep_fill_heuristic.py --strategy %BEST_STRAT% --markets 200
set SWEEP_EXIT=%errorlevel%

echo.
echo === Sweep exit code: %SWEEP_EXIT% ===
echo (0=PASS/INVESTIGATE, 1=FAIL)
echo.
echo Cikti JSON: backtest\calibration\sweep_fill_heuristic_*.json
powershell -NoProfile -Command "Get-ChildItem backtest\calibration\sweep_fill_heuristic_*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 3 | Format-Table Name, LastWriteTime"

popd
pause
