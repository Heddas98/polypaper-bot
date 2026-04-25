@echo off
REM opening_breakout live strategy create -- Windows.
REM Adim 1: dry-run preview
REM Adim 2: gercek INSERT (eger dry-run OK ise)
REM Adim 3: status check

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === 1/3 Dry-run preview ===
py -3.11 scripts\_create_opening_breakout_strategy.py --dry-run
if errorlevel 1 (
  echo DRY-RUN FAIL
  pause
  exit /b 1
)
echo.

echo === 2/3 Devam edilsin mi? ===
echo SQL preview yukarida. Olusturmak icin "y" gir, iptal icin baska tus.
choice /C YN /N /M "Devam? [y/n]: "
if errorlevel 2 (
  echo Iptal edildi.
  popd
  pause
  exit /b 0
)
echo.

echo === 3/3 INSERT calistiriliyor ===
py -3.11 scripts\_create_opening_breakout_strategy.py
if errorlevel 1 (
  echo INSERT FAIL
  pause
  exit /b 1
)
echo.

echo === DB verify ===
py -3.11 -c "import sqlite3; c=sqlite3.connect('data_store/polypaper.db', timeout=10); r=c.execute(\"SELECT id, label, status, asset, timeframe, trade_amount, odds_threshold FROM strategies WHERE strategy_type='opening_breakout'\").fetchall(); [print(' | '.join(str(x) for x in row)) for row in r]; c.close()"
echo.

popd
echo.
echo opening_breakout strategy DB'ye eklendi.
echo Sonraki adim: bot'u restart et veya /reload_strategies komutu (varsa) calistir.
echo Bot 5dk icinde ilk BTC market'inde sinyal aramaya baslar.
pause
