@echo off
REM ============================================================
REM Becker calibration files — final delete (Heddas direktifi)
REM 2026-04-28 — Aşama 1.5: 10 boşaltılmış (0-byte) Becker dosyasını
REM Windows filesystem'inden gerçekten siler.
REM
REM Çift tıkla veya cmd'den çalıştır. py_compile + pytest sonrası
REM çalıştırılmalı. Geri alınamaz — git history'de kalır.
REM ============================================================

setlocal
cd /d "%~dp0\.."

echo.
echo Polyscout31 Becker dosya silme islemi...
echo Konum: %CD%
echo.

set "FILES=core\becker_calibration.py core\becker_weight_tracker.py core\becker_rolling_recal.py data\becker_loader.py backtest\becker_replay.py scripts\becker_zone_analysis.py scripts\becker_deep_analysis.py scripts\becker_validate.py telegram_bot\jobs\becker_rolling_recal_job.py telegram_bot\handlers\becker_recal_handler.py"

for %%F in (%FILES%) do (
    if exist "%%F" (
        del /F /Q "%%F"
        if exist "%%F" (
            echo [FAIL] "%%F" silinemedi
        ) else (
            echo [OK]   "%%F"
        )
    ) else (
        echo [SKIP] "%%F" zaten yok
    )
)

echo.
echo Bitti. Sonraki adim: py -3.11 -m py_compile core/engine.py
echo                       py -3.11 -m pytest tests/unit/test_fees_v2.py -v
echo.
pause
endlocal
