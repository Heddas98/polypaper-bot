@echo off
REM T4.6-B sweep closure commit -- Windows.

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
        scripts\_probe_trade_producing_strategies.py ^
        scripts\_run_t46b_sweep_chain.bat ^
        scripts\_commit_t118b_asama_a.bat ^
        scripts\_commit_t46b_closure.bat ^
        backtest\calibration\sweep_fill_heuristic_20260424_193711.json

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === Commit ===
git commit -m "feat(t4.6-b): sweep closure -- classic 199 trades, delta_pnl_pct=-33.68%% FAIL" -m "" -m "Probe (9 aday x last_n=50) sonrasi classic strategy secildi (50 trade)." -m "Full sweep 200 markets:" -m "  HEURISTIC total_pnl = -$4.87 (spread 0.5%%)" -m "  EMPIRICAL total_pnl = -$6.51 (spread 2.3%%)" -m "  delta_pnl_pct = -33.68%% | direction consistent | verdict FAIL" -m "" -m "Kanit: T4.5 calibration (1082 trade) tahmin ettigi 4.6x carpan" -m "replay engine'de confirmed. Signal uretimi etkilenmiyor (WR 52.26%%" -m "both runs identical), sapma tamamen fill fiyatlarinda." -m "" -m "Zihin carpani (T4.7-C uygulanana kadar):" -m "  Paper +$X -> Live ~+$X * 0.66" -m "  Paper -$X -> Live ~-$X * 1.34" -m "" -m "Sweep artifact: backtest/calibration/sweep_fill_heuristic_20260424_193711.json" -m "Memory landmark: project_t46b_sweep_closure.md" -m "" -m "Forward work T4.7-C: config/settings.py EMPIRICAL defaults update" -m "(FILL_SPREAD_COST 0.005->0.023 + IMPACT 0.01->0.025 + LATENCY 0.08->0.04)." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
