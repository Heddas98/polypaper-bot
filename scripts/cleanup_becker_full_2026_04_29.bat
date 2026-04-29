@echo off
REM =============================================================
REM  Becker Full Cleanup — disk + zombie code (Aşama 3 cosmetic)
REM  2026-04-29 Heddas direktifi: "Becker'i de PC den silelim"
REM
REM  Siler:
REM    - data_store/becker_calibration.db (~849MB DuckDB)
REM    - data_store/becker_data.tar.zst (archive)
REM    - data_store/becker_raw/ (raw parquets)
REM    - data_store/becker_rolling/ (rolling recal data)
REM    - data_store/becker_weight_state.json
REM    - reports/becker_deep_analysis.html
REM    - htmlcov/z_*becker*.html (3 coverage reports)
REM    - 4 zombie 0-byte scripts (Hyperopt cleanup'tan kalan)
REM
REM  Geri alınamaz. git history zaten arşiv (Becker code rm: be98af8).
REM =============================================================

setlocal
cd /d "%~dp0\.."

echo.
echo Polyscout31 — Becker Full Disk Cleanup
echo Konum: %CD%
echo.

REM Disk usage öncesi
echo [BEFORE] data_store boyutu:
dir data_store /s /-c | find "File(s)"
echo.

REM 1. Becker DuckDB
if exist "data_store\becker_calibration.db" (
    echo [DEL]   data_store\becker_calibration.db (DuckDB)
    del /F /Q "data_store\becker_calibration.db"
)

REM 2. Becker tarball
if exist "data_store\becker_data.tar.zst" (
    echo [DEL]   data_store\becker_data.tar.zst
    del /F /Q "data_store\becker_data.tar.zst"
)

REM 3. Becker raw klasör
if exist "data_store\becker_raw" (
    echo [DEL]   data_store\becker_raw\ (klasör)
    rmdir /S /Q "data_store\becker_raw"
)

REM 4. Becker rolling klasör
if exist "data_store\becker_rolling" (
    echo [DEL]   data_store\becker_rolling\ (klasör)
    rmdir /S /Q "data_store\becker_rolling"
)

REM 5. Becker weight state
if exist "data_store\becker_weight_state.json" (
    echo [DEL]   data_store\becker_weight_state.json
    del /F /Q "data_store\becker_weight_state.json"
)

REM 6. Becker analysis report
if exist "reports\becker_deep_analysis.html" (
    echo [DEL]   reports\becker_deep_analysis.html
    del /F /Q "reports\becker_deep_analysis.html"
)

REM 7. htmlcov Becker reports
if exist "htmlcov\z_57760688d1f824db_becker_calibration_py.html" (
    echo [DEL]   htmlcov\z_*becker*.html (3 coverage reports)
    del /F /Q "htmlcov\z_*becker*.html"
)

REM 8. 0-byte zombie scripts (Hyperopt cleanup'tan kalan)
if exist "scripts\bench_discovery_plan.py" (
    echo [DEL]   scripts\bench_discovery_plan.py (0 byte zombie)
    del /F /Q "scripts\bench_discovery_plan.py"
)
if exist "scripts\smoke_unified_phase82e_final.py" (
    echo [DEL]   scripts\smoke_unified_phase82e_final.py (0 byte)
    del /F /Q "scripts\smoke_unified_phase82e_final.py"
)
if exist "scripts\verify_migration_v15.py" (
    echo [DEL]   scripts\verify_migration_v15.py (0 byte)
    del /F /Q "scripts\verify_migration_v15.py"
)
if exist "scripts\verify_phase82e_markers.py" (
    echo [DEL]   scripts\verify_phase82e_markers.py (0 byte)
    del /F /Q "scripts\verify_phase82e_markers.py"
)

REM 9. Becker disk cleanup batch (eski sürüm, bunun yerine bu çalıştı)
if exist "scripts\cleanup_becker_disk_2026_04_29.bat" (
    echo [DEL]   scripts\cleanup_becker_disk_2026_04_29.bat (eski versiyon)
    del /F /Q "scripts\cleanup_becker_disk_2026_04_29.bat"
)

REM 10. Becker delete files batch (Aşama 1'den)
if exist "scripts\delete_becker_files_2026_04_28.bat" (
    echo [DEL]   scripts\delete_becker_files_2026_04_28.bat (eski)
    del /F /Q "scripts\delete_becker_files_2026_04_28.bat"
)

REM 11. Orphan .pyc bytecode (Becker modülleri silindi ama __pycache__ kaldı)
echo [DEL]   __pycache__\*becker*.pyc orphan bytecode
del /F /Q /S "__pycache__\*becker*.pyc" >nul 2>&1
del /F /Q "backtest\__pycache__\*becker*.pyc" >nul 2>&1
del /F /Q "core\__pycache__\*becker*.pyc" >nul 2>&1
del /F /Q "data\__pycache__\*becker*.pyc" >nul 2>&1
del /F /Q "scripts\__pycache__\*becker*.pyc" >nul 2>&1
del /F /Q "telegram_bot\handlers\__pycache__\*becker*.pyc" >nul 2>&1

REM 12. Eski Becker download batch
if exist "scripts\download_becker.bat" (
    echo [DEL]   scripts\download_becker.bat (eski)
    del /F /Q "scripts\download_becker.bat"
)

echo.
echo [AFTER] data_store boyutu:
dir data_store /s /-c | find "File(s)"
echo.
echo Bitti. Disk alanı temizlendi.
echo Sonraki adım: git add -A; git commit -m "chore: Becker full disk cleanup"
echo.
pause
endlocal
