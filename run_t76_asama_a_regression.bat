@echo off
REM T7.6 Asama A Windows regression runner
REM 1) 16 dosya import smoke (aiohttp/httpx/telegram/optuna dahil)
REM 2) Full unit suite with stabilized ENV
REM
REM Calistir (repo kokunde):  run_t76_asama_a_regression.bat

setlocal
chcp 65001 >nul

echo ============================================================
echo  T7.6 Asama A Windows Regression — 2 adim
echo ============================================================
echo.

REM Baseline ENV — Memory'deki 323 pass + 6 skip + 2 fail referansi
set SIGNAL_W_WHALE=0.10

echo === Adim 1/2: Import smoke (16 modul) ===
echo.
py -3.11 scripts\smoke_t76_asama_a_imports.py
if errorlevel 1 goto :fail_import

echo.
echo === Adim 2/2: Full unit suite ===
echo.
py -3.11 -m pytest tests\unit -q --tb=short
set PYTEST_EXIT=%errorlevel%

echo.
echo ============================================================
if %PYTEST_EXIT%==0 (
    echo  TUM TESTLER YESIL
) else (
    echo  pytest exit code: %PYTEST_EXIT%
    echo  Beklenen pre-existing: test_phase66.test_no_direction_no_bayesian
    echo                         test_phase82b.TestHyperOptPipelineMutex ^(optuna^)
    echo  Yeni fail varsa paylas.
)
echo ============================================================
pause
exit /b %PYTEST_EXIT%

:fail_import
echo.
echo [HATA] Import smoke basarisiz — asagidaki modul(ler) gercek ortamda import edilemiyor.
echo        Pytest calistirilmadi.
pause
exit /b 1
