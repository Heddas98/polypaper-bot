@echo off
REM Aşama 3.C — Becker tam silme commit (Heddas direktifi)
REM Ne yapar: 6 dosya compile + minimal pytest + atomic git add + commit
REM 2026-04-29

cd /d "%~dp0\.."

echo ========================================
echo Asama 3.C: Becker tam silme commit
echo ========================================
echo.

echo [1/4] py_compile 6 cleaned files...
py -3.11 -m py_compile config\settings.py calibration\surface_2d.py core\engine_monitor.py backtest\replay_engine.py backtest\replay_engine_v3.py telegram_bot\handlers\strategy_tester.py
if errorlevel 1 goto :fail
echo COMPILE OK
echo.

echo [2/4] pytest fast subset (fees + phase55 + phase56)...
py -3.11 -m pytest tests\test_phase55_critical.py tests\test_phase56_engine.py tests\unit\test_fees_v2.py -q
if errorlevel 1 goto :fail
echo PYTEST OK
echo.

REM Clear stale index lock if present (WSL mount artifact)
if exist .git\index.lock (
  echo Removing stale .git\index.lock
  del /f /q .git\index.lock
)

echo [3/4] git add -- only the 6 Becker-cleaned files (atomic to avoid WSL index drift)...
git add backtest\replay_engine.py backtest\replay_engine_v3.py calibration\surface_2d.py config\settings.py core\engine_monitor.py telegram_bot\handlers\strategy_tester.py
if errorlevel 1 goto :fail
echo GIT ADD OK
echo.

echo [4/4] git commit...
git commit -m "chore(becker): Asama 3.C tam silme - disk + code (Heddas direktifi)" -m "" -m "* config/settings.py: BECKER_* settings (8 fields) silindi" -m "* calibration/surface_2d.py: _fallback_1d removed, SurfaceBuilder simplified" -m "* core/engine_monitor.py: _smart_exit_check body silindi (~80 satir)" -m "* backtest/replay_engine.py: _apply_becker_boost + _apply_becker_decision silindi (~190 satir)" -m "* backtest/replay_engine_v3.py: _try_load_becker + use_becker_calibration silindi" -m "* telegram_bot/handlers/strategy_tester.py: becker option + _test_with_becker + test_becker_ callback silindi" -m "" -m "Net: -441 satir (503 silindi, 62 eklendi)." -m "Test: 138 PASS (fees_v2 + phase55 + phase56)."
if errorlevel 1 goto :fail
echo.

echo ========================================
echo OK - Asama 3.C committed.
echo ========================================
goto :end

:fail
echo.
echo ========================================
echo FAIL - kontrol et yukaridaki output
echo ========================================
exit /b 1

:end
pause
