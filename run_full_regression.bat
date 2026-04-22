@echo off
REM PolyPaper Bot — full test regression runner (Windows).
REM Epic 9 T9.10 closing artifact. Parity with run_full_regression.sh.
REM
REM Kullanım (repo kökünde):
REM   run_full_regression.bat              : full sandbox-compatible suite
REM   run_full_regression.bat unit         : unit only (fast, ~41s)
REM   run_full_regression.bat integration  : integration smoke only (~1.3s)
REM   run_full_regression.bat seed 1337    : full suite with pytest-randomly seed

setlocal
chcp 65001 >nul

REM Stabilize env for deterministic GREEN.
REM NOT: SIGNAL_W_WHALE intentionally NOT set here — test_whale_signal.py
REM expects the Phase 79b default (0.00); setting the override to 0.10
REM breaks test_signal_fusion_includes_whale_weight. T7.6 runner was
REM stale in this regard.
set DATABASE_PATH=:memory:
set TELEGRAM_BOT_TOKEN=test-token
set ADMIN_CHAT_ID=0

echo ============================================================
echo  PolyPaper Bot — Full Regression (Windows)
echo ============================================================
echo.

if "%1"=="unit" goto :unit
if "%1"=="integration" goto :integration
if "%1"=="seed" goto :seed
goto :full

:full
echo === Full suite (unit + integration) ===
py -3.11 -m pytest tests -q
set PYTEST_EXIT=%errorlevel%
goto :done

:unit
echo === Unit only (-m "not integration") ===
py -3.11 -m pytest tests -q -m "not integration"
set PYTEST_EXIT=%errorlevel%
goto :done

:integration
echo === Integration only (-m integration) ===
py -3.11 -m pytest tests -q -m integration
set PYTEST_EXIT=%errorlevel%
goto :done

:seed
if "%2"=="" (
    echo [HATA] Seed verilmedi. Kullanim: run_full_regression.bat seed 1337
    set PYTEST_EXIT=2
    goto :done
)
echo === Full suite with pytest-randomly seed=%2 ===
py -3.11 -m pytest tests -q -p randomly --randomly-seed=%2
set PYTEST_EXIT=%errorlevel%
goto :done

:done
echo.
echo ============================================================
if %PYTEST_EXIT%==0 (
    echo  TUM TESTLER YESIL
    echo  Baseline: 723 pass + 8 skip + 0 fail ^(2026-04-22 T9.10 closure^)
) else (
    echo  pytest exit code: %PYTEST_EXIT%
    echo  Beklenen pre-existing skip ^(8 toplam, sandbox env^):
    echo    - test_brain_flags_parity kelly_sizing ^(intentional^)
    echo    - test_brain_flags_parity drift_monitor ^(intentional^)
    echo    - test_phase82b optuna ^(not installed^)
    echo    - test_phase82b telegram handler x2
    echo    - test_phase77 python-telegram-bot ^(not installed^)
    echo    - test_phase67 telegram x2 ^(not installed^)
    echo  Yeni fail veya farkli skip set varsa paylas.
)
echo ============================================================
pause
exit /b %PYTEST_EXIT%
