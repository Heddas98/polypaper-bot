@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM ============================================
REM PolyPaper Bot Backup Script v1.0
REM Phase 56D: Off-site backup to OneDrive/USB
REM ============================================

echo.
echo ============================================
echo  PolyPaper Bot Yedekleme
echo ============================================
echo.

REM Tarih damgasi
for /f "tokens=1-3 delims=/ " %%a in ("%date%") do set "DSTAMP=%%c%%a%%b"
for /f "tokens=1-2 delims=:." %%a in ("%time: =0%") do set "TSTAMP=%%a%%b"
set "BACKUP_NAME=polypaper_backup_%DSTAMP%_%TSTAMP%"

REM Hedef secimi
echo  Yedekleme hedefi:
echo  1) OneDrive (varsayilan)
echo  2) Belgelerim (Documents)
echo  3) Ozel konum
echo.
set /p CHOICE="  Seciniz (1/2/3): "

if "%CHOICE%"=="2" (
    set "DEST=%USERPROFILE%\Documents\PolyPaper_Backups"
) else if "%CHOICE%"=="3" (
    set /p DEST="  Tam yol girin: "
) else (
    set "DEST=%USERPROFILE%\OneDrive\PolyPaper_Backups"
)

REM Hedef klasor olustur
if not exist "%DEST%" mkdir "%DEST%"
set "TARGET=%DEST%\%BACKUP_NAME%"
mkdir "%TARGET%" 2>NUL

echo.
echo  Hedef: %TARGET%
echo  Yedekleniyor...

REM Kritik dosyalari yedekle
echo  [1/5] Veritabani...
if exist "data\polypaper.db" copy /Y "data\polypaper.db" "%TARGET%\" >NUL
if exist "data\polypaper.db-wal" copy /Y "data\polypaper.db-wal" "%TARGET%\" >NUL

echo  [2/5] Konfigurasyon...
if exist ".env" copy /Y ".env" "%TARGET%\.env.backup" >NUL
if exist "config" xcopy /E /I /Q /Y "config" "%TARGET%\config" >NUL

echo  [3/5] Strateji verileri...
if exist "data\strategies" xcopy /E /I /Q /Y "data\strategies" "%TARGET%\strategies" >NUL
if exist "data_store" (
    REM Sadece kucuk dosyalari yedekle (calibration DB cok buyuk)
    for %%f in (data_store\*.json data_store\*.csv) do (
        copy /Y "%%f" "%TARGET%\" >NUL 2>NUL
    )
)

echo  [4/5] Loglar (son 7 gun)...
if exist "logs" (
    mkdir "%TARGET%\logs" 2>NUL
    forfiles /p "logs" /m "*.log" /d -7 /c "cmd /c copy @path \"%TARGET%\logs\\\" >NUL" 2>NUL
    REM Son log her zaman
    if exist "logs\bot.log" copy /Y "logs\bot.log" "%TARGET%\logs\" >NUL
)

echo  [5/5] Dokumanlar...
if exist "docs" xcopy /E /I /Q /Y "docs" "%TARGET%\docs" >NUL

REM Boyut hesapla
set "SIZE=0"
for /f "tokens=3" %%a in ('dir "%TARGET%" /s 2^>NUL ^| findstr "File(s)"') do set "SIZE=%%a"

echo.
echo  ============================================
echo  ✅ Yedekleme tamamlandi!
echo  Konum: %TARGET%
echo  Boyut: %SIZE% bytes
echo  Zaman: %date% %time%
echo  ============================================
echo.

REM Eski yedekleri temizle (30 gunden eski)
echo  Eski yedekler temizleniyor (30+ gun)...
forfiles /p "%DEST%" /d -30 /c "cmd /c if @isdir==TRUE rd /s /q @path" 2>NUL
echo  Temizlik tamamlandi.
echo.
pause
