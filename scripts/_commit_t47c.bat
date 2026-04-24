@echo off
REM T4.7-C fill_model EMPIRICAL defaults commit -- Windows.

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
        backtest\simulation\fill_model.py ^
        tests\unit\test_fill_model_defaults.py ^
        scripts\_commit_t47c.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === Regression test sanity (Windows pytest) ===
py -3.11 -m pytest tests\unit\test_fill_model_defaults.py -v
set TEST_EXIT=%errorlevel%
if not "%TEST_EXIT%"=="0" (
  echo.
  echo TEST FAIL -- commit iptal. Fill defaults regression var.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t4.7-c): fill_model EMPIRICAL defaults -- backtest = live parity" -m "" -m "3 default bumped to match T4.5 calibration + T4.6-B sweep evidence:" -m "  SPREAD_COST                  0.005  -> 0.023  (L73)" -m "  IMPACT_SCALE                 0.01   -> 0.025 (L501)" -m "  LATENCY_DRIFT_BPS_PER_MS     0.08   -> 0.04  (L238)" -m "" -m "Rationale: T4.5 slippage analysis (1082 trades) reported p90 adverse" -m "slippage = +2.3%%; T4.6-B sweep proved backtest HEURISTIC (old defaults)" -m "was ~4.6x too optimistic (classic 199 trades: HEURISTIC -$4.87 vs" -m "EMPIRICAL -$6.51, delta_pnl_pct=-33.68%% FAIL)." -m "" -m "After T4.7-C backtest default run == live reality, no per-call ENV" -m "overrides needed. Legacy heuristic reproducibility preserved via ENV" -m "(e.g. FILL_SPREAD_COST=0.005 reverts pre-T4.7-C behavior)." -m "" -m "Regression guard: tests/unit/test_fill_model_defaults.py (4/4 PASS):" -m "  - SPREAD_COST default locked 0.023" -m "  - IMPACT_SCALE default locked 0.025" -m "  - LATENCY_DRIFT literal locked 0.04 in source" -m "  - ENV override path sanity (legacy reproducibility)" -m "" -m "Every strategy review now gets realistic PnL without mental 0.66x" -m "multiplier. Mainnet go/no-go calibration cleaner." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
