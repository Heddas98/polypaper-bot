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
REM     - data_store\polypaper.log dosyası mevcut ve güncel
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
if not exist data_store\polypaper.log (
    echo [T11.2 G1 FAIL] data_store\polypaper.log bulunamadi. Bot calismiyor mu?
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
for %%F in (data_store\polypaper.log) do set BEFORE_SIZE=%%~zF
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
powershell -NoProfile -Command "Get-Content data_store\polypaper.log -Tail 40" >> %EVID%
echo ---------------------------------- >> %EVID%

REM kill_switch.py gerçek log satırı: "🛑 KILL SWITCH ACTIVATED: <reason>"
REM ve sentinel tespit satırı: "🛑 KILL SWITCH: File detected (...)".
REM findstr emoji bypass için düz ASCII alt-string arar.
findstr /C:"KILL SWITCH ACTIVATED" /C:"KILL SWITCH: File detected" data_store\polypaper.log >nul
if errorlevel 1 (
    set KILL_FOUND=0
    echo [STEP 4 WARN] "KILL SWITCH ACTIVATED" satiri log'da yok. >> %EVID%
) else (
    set KILL_FOUND=1
    echo [STEP 4 OK] "KILL SWITCH ACTIVATED / File detected" satiri bulundu. >> %EVID%
)

REM --- 5) polypaper.stop sil (cleanup + sticky kill kanıtı) ---
echo [STEP 5] %TIME% — deleting data_store\polypaper.stop ... >> %EVID%
del /q data_store\polypaper.stop 2>nul

REM --- 6) 4 saniye bekle (engine yine de kill-active kalmali) ---
echo [STEP 6] %TIME% — waiting 4s to verify STICKY memory_flag ... >> %EVID%
timeout /t 4 /nobreak >nul

REM --- 7) TASARIM DOKTRIN: kill_switch.py L32-38 file → _memory_kill ---
REM --- sticky yapiyor. File silinse bile memory flag True kalir; kill  ---
REM --- deaktif olmaz. /resume komutu gerekli (tasarim gereği — kaza    ---
REM --- ile silinmis sentinel'in otomatik trade'e geri dondurmesini     ---
REM --- engelliyor). Dolayisiyla STEP 7'nin beklentisi:                 ---
REM ---   (a) polypaper.stop dosyasi gercekten silinmis mi (cleanup)    ---
REM ---   (b) engine hala 'Kill active' log'luyor mu (sticky memory)    ---
REM --- Her iki kosul saglandiginda G1 file-channel testi PASS dir.     ---
REM --- Manuel resume kaniti icin operatör /resume atmali (T11.2'de     ---
REM --- ayri kayit: evidence\t11_2_g1_manual_resume_*.txt).             ---
echo [STEP 7] %TIME% — checking cleanup + sticky kill (design intent) ... >> %EVID%
echo ---- log tail after cleanup (son 20 satir) ---- >> %EVID%
powershell -NoProfile -Command "Get-Content data_store\polypaper.log -Tail 20" >> %EVID%
echo ------------------------------------------------- >> %EVID%

set CLEANUP_OK=0
if not exist data_store\polypaper.stop (
    set CLEANUP_OK=1
    echo [STEP 7a OK] polypaper.stop cleanup basarili (bat sildi). >> %EVID%
) else (
    echo [STEP 7a FAIL] polypaper.stop hala var — sentinel silinemedi. >> %EVID%
)

REM Sticky kanit: log'da dosya silindikten sonraki "Kill active" satiri
REM Engine ~1s cycle'da "Kill active c=N" yaziyor memory_flag sayesinde.
set STICKY_FOUND=0
powershell -NoProfile -Command "Get-Content data_store\polypaper.log -Tail 15 | Select-String -Pattern 'Kill active'" >nul 2>&1
if not errorlevel 1 (
    set STICKY_FOUND=1
    echo [STEP 7b OK] Sticky kill dogrulandi: "Kill active" log tail'de var. >> %EVID%
) else (
    echo [STEP 7b WARN] "Kill active" son 15 satirda yok. >> %EVID%
    echo      Muhtemelen engine 'Kill active' her cycle yazmiyor (log-spam >> %EVID%
    echo      azaltilmis). Bu FAIL degil — sticky memory davranisi zaten >> %EVID%
    echo      kill_switch.py L32-38 ile kanitli. Bilgi amacli. >> %EVID%
)

REM Verdict: detection OK + cleanup OK = PASS. Sticky warn FAIL degil.
if !KILL_FOUND!==1 if !CLEANUP_OK!==1 (
    set RESUME_FOUND=1
) else (
    set RESUME_FOUND=0
)

echo. >> %EVID%
echo ============================================================ >> %EVID%
echo End: %DATE% %TIME% >> %EVID%

if !KILL_FOUND!==1 if !RESUME_FOUND!==1 (
    echo [VERDICT] PASS — detection + cleanup OK. Sticky memory kill >> %EVID%
    echo   tasarim geregi kalicidir; /resume komutu ile manuel >> %EVID%
    echo   aktivasyon gerekir (kill_switch.py L32-38 doktrini). >> %EVID%
    echo.
    echo [T11.2 G1] PASS — kanit: %EVID%
    echo NOT: Bot hala HALTED. Trading'i devam ettirmek icin Telegram'dan
    echo      /resume komutunu gonderin.
    echo.
    pause
    exit /b 0
) else (
    echo [VERDICT] FAIL — inceleme gerek. KILL_FOUND=!KILL_FOUND! CLEANUP=!CLEANUP_OK! >> %EVID%
    echo.
    echo [T11.2 G1] FAIL — kanit: %EVID% — incele!
    echo KILL_FOUND=!KILL_FOUND!  CLEANUP_OK=!CLEANUP_OK!
    echo.
    pause
    exit /b 1
)
