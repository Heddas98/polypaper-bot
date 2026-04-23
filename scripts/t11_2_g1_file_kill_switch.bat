@echo off
REM =====================================================================
REM T11.2 G1 Kill Switch — File-Channel Runtime Test
REM =====================================================================
REM
REM T11_2_runtime_validation.md G1 için otomatize kanıt: file-channel
REM kill switch'i tetikler, bot'un loglarında "🛑 Kill active" satırını
REM bekler, sonra file'ı siler ve resume log'unu bekler.
REM
REM Kullanım (PolyPaper root'ta):
REM     scripts\t11_2_g1_file_kill_switch.bat
REM
REM Önkoşul:
REM     - Bot AYAKTA olmalı (start_bot.bat koşuyor olmalı)
REM     - logs\polypaper.log dosyası mevcut ve güncel
REM     - data_store\ klasörü yazılabilir
REM
REM Çıktı:
REM     evidence\t11_2_g1_<timestamp>.txt — log satırları + timestamps
REM
REM Exit code:
REM     0: kill active + resume her ikisi de log'da bulundu (guard PASS)
REM     1: kill active veya resume eksik (guard FAIL — inceleme gerek)
REM     2: önkoşul hatası (bot çalışmıyor / log yok / data_store yok)
REM
REM GÜVENLİK: Bu test shadow-live bot'u 5-10 saniye duraklatır. LIVE
REM yok ama shadow trading o pencerede olmayacak. Telegram /stop_all
REM gibi destructive DEĞİL — sadece cycle loop'u bekletir; kill_switch
REM kaldırılınca otomatik devam eder.
REM =====================================================================
setlocal ENABLEDELAYEDEXPANSION

REM --- Working directory: bat'in bulundugu scripts\ degil, bir ust olan ---
REM --- PolyPaper root'a gec. File Explorer'dan cift-tiklandiginda     ---
REM --- %CD% scripts\ olur; logs\ + data_store\ + evidence\ hepsi root ---
REM --- kardesi, bu yuzden cd /d "%~dp0\.." sart.                      ---
cd /d "%~dp0\.."

REM --- Önkoşul doğrulama ---
if not exist logs\polypaper.log (
    echo [T11.2 G1 FAIL] logs\polypaper.log bulunamadi. Bot calismiyor mu?
    echo.
    pause
    exit /b 2
)
if not exist data_store (
    echo [T11.2 G1 FAIL] data_store\ klasoru yok.
    echo.
    pause
    exit /b 2
)
if not exist evidence (
    mkdir evidence
)

REM --- Timestamp + evidence dosyasi (Windows 11: wmic kaldırıldı) ---
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd_HHmmss'"`) do set TS=%%a
if "%TS%"=="" set TS=notimestamp
set EVID=evidence\t11_2_g1_%TS%.txt

echo [T11.2 G1 Kill Switch File-Channel Test] > %EVID%
echo Start: %DATE% %TIME% >> %EVID%
echo ============================================================ >> %EVID%

REM --- 1) log mevcut boyutunu not et (son satirlari farkli bulmak icin) ---
for %%F in (logs\polypaper.log) do set BEFORE_SIZE=%%~zF
echo [STEP 1] Log size before: !BEFORE_SIZE! bytes >> %EVID%

REM --- 2) polypaper.stop olustur ---
echo [STEP 2] %TIME% — touching data_store\polypaper.stop ... >> %EVID%
type nul > data_store\polypaper.stop
echo t11.2-g1-test %DATE% %TIME% > data_store\polypaper.stop

REM --- 3) 6 saniye bekle (engine cycle ~1s, kill check her cycle) ---
echo [STEP 3] %TIME% — waiting 6s for engine to see kill file ... >> %EVID%
timeout /t 6 /nobreak >nul

REM --- 4) log'da "Kill active" arar ---
echo [STEP 4] %TIME% — grepping log for kill-active line ... >> %EVID%
echo ---- log tail (son 40 satir) ---- >> %EVID%
powershell -NoProfile -Command "Get-Content logs\polypaper.log -Tail 40" >> %EVID%
echo ---------------------------------- >> %EVID%

REM kill_switch.py gerçek log satırı: "🛑 KILL SWITCH ACTIVATED: <reason>"
REM ve sentinel tespit satırı: "🛑 KILL SWITCH: File detected (...)".
REM findstr emoji bypass için düz ASCII alt-string arar.
findstr /C:"KILL SWITCH ACTIVATED" /C:"KILL SWITCH: File detected" logs\polypaper.log >nul
if errorlevel 1 (
    set KILL_FOUND=0
    echo [STEP 4 WARN] "KILL SWITCH ACTIVATED" satiri log'da yok. >> %EVID%
) else (
    set KILL_FOUND=1
    echo [STEP 4 OK] "KILL SWITCH ACTIVATED / File detected" satiri bulundu. >> %EVID%
)

REM --- 5) polypaper.stop sil ---
echo [STEP 5] %TIME% — deleting data_store\polypaper.stop ... >> %EVID%
del /q data_store\polypaper.stop 2>nul

REM --- 6) 4 saniye bekle (engine kill-check + resume log) ---
echo [STEP 6] %TIME% — waiting 4s for engine resume ... >> %EVID%
timeout /t 4 /nobreak >nul

REM --- 7) log'da "Kill deactivated" veya cycle devam etme satiri ---
echo [STEP 7] %TIME% — checking for resume signal ... >> %EVID%
echo ---- log tail after resume (son 20 satir) ---- >> %EVID%
powershell -NoProfile -Command "Get-Content logs\polypaper.log -Tail 20" >> %EVID%
echo ----------------------------------------------- >> %EVID%

REM Resume belirteci: "KILL SWITCH DEACTIVATED" VEYA yeni engine cycle satiri
findstr /C:"KILL SWITCH DEACTIVATED" logs\polypaper.log >nul
if errorlevel 1 (
    REM Kill deactivated satiri yoksa, log boyutunun buyudugunu kontrol et
    for %%F in (logs\polypaper.log) do set AFTER_SIZE=%%~zF
    if !AFTER_SIZE! GTR !BEFORE_SIZE! (
        set RESUME_FOUND=1
        echo [STEP 7 OK] Log buyudu (!BEFORE_SIZE! -^> !AFTER_SIZE!); engine cycle devam ediyor. >> %EVID%
    ) else (
        set RESUME_FOUND=0
        echo [STEP 7 WARN] Log boyutu degismedi; engine hala donmus olabilir. >> %EVID%
    )
) else (
    set RESUME_FOUND=1
    echo [STEP 7 OK] "KILL SWITCH DEACTIVATED" satiri bulundu. >> %EVID%
)

echo. >> %EVID%
echo ============================================================ >> %EVID%
echo End: %DATE% %TIME% >> %EVID%

if !KILL_FOUND!==1 if !RESUME_FOUND!==1 (
    echo [VERDICT] PASS — kill active + resume her ikisi de gozlendi. >> %EVID%
    echo.
    echo [T11.2 G1] PASS — kanit: %EVID%
    echo.
    pause
    exit /b 0
) else (
    echo [VERDICT] FAIL — inceleme gerek. KILL_FOUND=!KILL_FOUND! RESUME_FOUND=!RESUME_FOUND! >> %EVID%
    echo.
    echo [T11.2 G1] FAIL — kanit: %EVID% — incele!
    echo KILL_FOUND=!KILL_FOUND!  RESUME_FOUND=!RESUME_FOUND!
    echo.
    pause
    exit /b 1
)
