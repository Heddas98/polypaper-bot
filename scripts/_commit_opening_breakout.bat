@echo off
REM opening_breakout live strategy commit -- Windows.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === Pre-check lock temizle ===
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

echo === Staging ===
git add TASKS.md ^
        scripts\_create_opening_breakout_strategy.py ^
        scripts\_run_opening_breakout_create.bat ^
        scripts\_commit_opening_breakout.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; ast.parse(open('scripts/_create_opening_breakout_strategy.py', encoding='utf-8').read()); print('AST OK')"
if errorlevel 1 (
  echo AST FAIL
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(opening_breakout): backtest-karli strategy live spec + create script" -m "" -m "Backtest probe (T4.6-B keşif, last 50 markets):" -m "  trades=11  wins=8  losses=3  pnl=+$4.26  WR=73%%" -m "" -m "Live strategy zaten kayitli (core/strategy_plugins.py:961" -m "OpeningBreakoutLiveStrategy). Engine btc_move_usd metadata'sini" -m "external_feed (Binance spot momentum) uzerinden sagliyor" -m "(core/engine_signals.py:548)." -m "" -m "Scripts:" -m "  _create_opening_breakout_strategy.py -- DB INSERT (dry-run + live)" -m "  _run_opening_breakout_create.bat     -- 2-asama Windows wrapper" -m "" -m "Spec (Phase 81 doctrine $1/trade canary):" -m "  asset=BTC tf=5m direction=any trade_amount=$1.0" -m "  odds_threshold=0.51 (confidence breakout size'a gore 0.55-0.85)" -m "  SL=0.20 / TP=0.15 (first-minute moves often retrace)" -m "  minutes_after_start=0.5 (opening 30s window)" -m "  status=active deploy_stage=canary" -m "" -m "Kullanim: scripts/_run_opening_breakout_create.bat cift-tikla" -m "(dry-run preview -> y/n confirm -> INSERT -> verify)." -m "Bot restart sonrasi shadow trading baslar; ilk BTC 5m market'te" -m "btc_move_usd >= 10 olunca sinyal." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

if errorlevel 1 (
  echo COMMIT FAILED
  pause
  exit /b 1
)

echo.
echo === git log -1 ===
git log --oneline -1

popd
echo.
echo Commit basarili. Sandbox'a "ok" yaz.
pause
