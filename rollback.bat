@echo off
cd /d "%~dp0"
echo ============================================
echo  PolyPaper Bot — Rollback Tool v1.0
echo ============================================
echo.

:: Parametresiz calisirsa menu goster
if "%~1"=="" goto :MENU

:: Parametre varsa direkt calistir
if /i "%~1"=="last" goto :ROLLBACK_LAST
if /i "%~1"=="list" goto :LIST_COMMITS
if /i "%~1"=="status" goto :GIT_STATUS
echo  ❌ Bilinmeyen komut: %~1
echo  Kullanim: rollback.bat [last^|list^|status]
goto :EOF

:MENU
echo  Secenekler:
echo    1) Son commit'e geri don (git checkout)
echo    2) Son 10 commit'i goster
echo    3) Git durumu goster
echo    4) Iptal
echo.
set /p CHOICE="  Seciminiz (1-4): "
if "%CHOICE%"=="1" goto :ROLLBACK_LAST
if "%CHOICE%"=="2" goto :LIST_COMMITS
if "%CHOICE%"=="3" goto :GIT_STATUS
if "%CHOICE%"=="4" goto :EOF
echo  Gecersiz secim.
goto :MENU

:LIST_COMMITS
echo.
echo  Son 10 commit:
echo  ─────────────────────────────────────
git log --oneline -10 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  ⚠️ Git repo bulunamadi. Henuz git init yapilmamis olabilir.
    goto :EOF
)
echo  ─────────────────────────────────────
echo.
goto :EOF

:GIT_STATUS
echo.
git status --short 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  ⚠️ Git repo bulunamadi.
)
echo.
goto :EOF

:ROLLBACK_LAST
echo.
echo  ⚠️ DIKKAT: Kaydedilmemis degisiklikler kaybolacak!
echo.

:: Mevcut durumu goster
git status --short 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo  ❌ Git repo bulunamadi. Once git init yapin.
    goto :EOF
)
echo.

:: Onay al
set /p CONFIRM="  Devam etmek istiyor musunuz? (E/H): "
if /i not "%CONFIRM%"=="E" (
    echo  Iptal edildi.
    goto :EOF
)

:: Oncelikle stash et (geri donulebilir)
echo.
echo  [1/4] Mevcut degisiklikler stash'e aliniyor...
git stash push -m "rollback-backup-%DATE:~-4%%DATE:~3,2%%DATE:~0,2%-%TIME:~0,2%%TIME:~3,2%" 2>nul
echo    OK

:: Bot'u durdur
echo  [2/4] Bot durduruluyor...
taskkill /F /IM python.exe /T 2>nul
taskkill /F /IM py.exe /T 2>nul
timeout /t 2 /nobreak >nul
echo    OK

:: Son commit'e don
echo  [3/4] Son commit'e donuluyor...
git checkout -- . 2>nul
echo    OK

:: Bot'u yeniden baslat
echo  [4/4] Bot yeniden baslatiliyor...
start "PolyPaper Bot" cmd /c "py -3.11 main.py"
timeout /t 5 /nobreak >nul
echo    OK

echo.
echo  ============================================
echo  ✅ Rollback tamamlandi!
echo  Stash'teki yedek: git stash list
echo  Geri almak icin: git stash pop
echo  ============================================

:EOF
