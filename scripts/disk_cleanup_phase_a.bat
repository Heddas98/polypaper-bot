@echo off
:: Disk Cleanup — Phase A (Safe Deletes)
:: 2026-04-30 Heddas direktifi (108 GB → ~75 GB beklenen)
::
:: ÖNCE BOTU DURDUR! Bu script DB'ye dokunmaz ama precaution.
::
:: Silinenler:
::   - data_store\polypaper_pre77.db        (8.0 GB)
::   - data_store\polypaper_pre_phase80.db  (9.7 GB)
::   - data_store\backup_phase82e\          (8.8 GB)
::   - data_store\backups\*.db-wal (orphan) (~27.5 GB)
::   - htmlcov\                             (5.8 MB)
::   - __pycache__\ recursive               (~10 MB)
::   - polypaper.db   (kök, eski 64K dosya)
::   - data\polypaper.db (boş, eski path)
::
:: Beklenen kazanım: ~34 GB
::
:: Korunan:
::   - data_store\polypaper.db (canlı, 8.8 GB)
::   - data_store\backups\ içindeki .db dosyaları (Phase B'de prune)
::   - data\archive\*.parquet (saniyelik veri, backtest için)

echo ============================================
echo   Disk Cleanup Phase A - Safe Deletes
echo ============================================
echo.

:: Bot lockfile kontrol — sadece dosya varsa VE boyut > 0 ise warn
:: (PID 0 byte lockfile false positive olabilir)
setlocal enabledelayedexpansion
set "LOCK_PATH=data_store\polypaper.lock"
set "LOCK_SIZE=0"
if exist "%LOCK_PATH%" (
    for %%A in ("%LOCK_PATH%") do set "LOCK_SIZE=%%~zA"
)
if !LOCK_SIZE! GTR 0 (
    echo [WARN] Bot calisir gibi gozukuyor: %LOCK_PATH% boyut=!LOCK_SIZE!
    echo [WARN] Lutfen once .\stop_bot.bat calistir!
    pause
    exit /b 1
)
endlocal
echo [OK] Bot kapali, devam ediyor.
echo.

echo [1/8] data_store\polypaper_pre77.db (8.0 GB)
if exist data_store\polypaper_pre77.db (
    del /F /Q data_store\polypaper_pre77.db
    echo   - silindi
) else (
    echo   - bulunamadi (atlandi)
)

echo [2/8] data_store\polypaper_pre_phase80.db (9.7 GB)
if exist data_store\polypaper_pre_phase80.db (
    del /F /Q data_store\polypaper_pre_phase80.db
    echo   - silindi
) else (
    echo   - bulunamadi
)

echo [3/8] data_store\backup_phase82e\ (8.8 GB)
if exist data_store\backup_phase82e (
    rmdir /S /Q data_store\backup_phase82e
    echo   - silindi
) else (
    echo   - bulunamadi
)

echo [4/8] orphan .db-wal in data_store\backups\
if exist data_store\backups (
    for %%F in (data_store\backups\*.db-wal) do (
        set "WAL=%%F"
        set "DB=%%~dpnF"
        call :check_orphan "%%F"
    )
)

echo [5/8] htmlcov\ (5.8 MB)
if exist htmlcov (
    rmdir /S /Q htmlcov
    echo   - silindi
)

echo [6/8] __pycache__\ (recursive)
for /D /R %%d in (__pycache__) do (
    if exist "%%d" rmdir /S /Q "%%d"
)
echo   - silindi (recursive)

echo [7/8] root polypaper.db (eski/boş, 64K)
if exist polypaper.db (
    del /F /Q polypaper.db
    echo   - silindi
) else (
    echo   - bulunamadi
)

echo [8/8] data\polypaper.db (boş, yanlış path)
if exist data\polypaper.db (
    del /F /Q data\polypaper.db
    echo   - silindi (boş eski file)
) else (
    echo   - bulunamadi
)

echo.
echo ============================================
echo   Phase A complete!
echo   Tahmini disk geri kazanim: ~34 GB
echo   Sirada: scripts\disk_cleanup_phase_b.bat
echo ============================================
pause
exit /b 0

:check_orphan
:: %~1 = WAL path; check if matching .db exists
set "WAL_PATH=%~1"
set "DB_PATH=%~dpn1"
if not exist "%DB_PATH%" (
    echo   - orphan: %WAL_PATH%
    del /F /Q "%WAL_PATH%"
)
exit /b 0
