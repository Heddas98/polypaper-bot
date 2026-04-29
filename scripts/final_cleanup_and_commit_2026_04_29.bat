@echo off
REM =============================================================
REM  Final cleanup + commit (kapanmayacak — self-relaunch)
REM  2026-04-29
REM
REM  Cift-tikla → otomatik olarak kapanmayan yeni cmd penceresi acar
REM  ve isleri orada calistirir. PAUSE'larda Enter'a bas.
REM =============================================================

REM Self-relaunch: ilk calistirmada `start cmd /k` ile yeni pencere ac
REM Boylece cift-tiklamada kapanmasini onleriz.
if "%~1"=="" (
    start "Polyscout Final Cleanup" cmd /k ""%~f0" RUN"
    exit /b
)

cd /d "%~dp0\.."

echo ========================================
echo POLYSCOUT 31 - Final Cleanup ^& Commit
echo Konum: %CD%
echo Tarih: %DATE% %TIME%
echo ========================================
echo.

REM Tum islemler hata yapsa bile devam et, sonunda result goster
set ERR=0

echo [1/5] LF artifact ve _t11_7_test_mod.py temizlik
echo ----------------------------------------
if exist "LF" (
    del /F /Q "LF"
    echo [+] LF silindi
) else (
    echo [=] LF zaten yok
)
if exist "core\_t11_7_test_mod.py" (
    del /F /Q "core\_t11_7_test_mod.py"
    echo [+] core\_t11_7_test_mod.py silindi
) else (
    echo [=] core\_t11_7_test_mod.py zaten yok
)
echo.
echo Devam icin Enter:
pause

echo.
echo [2/5] Stale git lock temizlik
echo ----------------------------------------
if exist ".git\index.lock" (
    del /F /Q ".git\index.lock"
    if errorlevel 1 (
        echo [!] Lock silinemedi (UAC veya ghost file). Devam.
    ) else (
        echo [+] .git\index.lock silindi
    )
) else (
    echo [=] index.lock yok
)
echo.
pause

echo.
echo [3/5] git status (commit ONCESI)
echo ----------------------------------------
git status --short
echo.
pause

echo.
echo [4/5] git add ^& commit (tum kalan degisiklikler)
echo ----------------------------------------
git add .gitattributes
git add docs\env_reference.md
git add scripts\cleanup_asama_3d_2026_04_29.bat
git add scripts\cleanup_becker_full_2026_04_29.bat
git add scripts\commit_fase_a_2026_04_29.bat
git add scripts\cleanup_asama_3e_2026_04_29.bat 2>nul
git add scripts\final_cleanup_and_commit_2026_04_29.bat
git add tests\unit\test_mode_banner.py 2>nul
echo.
echo Stage edildi. Commit:
git commit -m "chore(cleanup): final 3.E + .gitattributes + env_reference + bat fixes" -m "" -m "* .gitattributes: yeni dosya, CRLF/LF normalization rules" -m "* docs/env_reference.md: LIVE_ENABLED telegram_bot/templates/mode_banner.py:25 reference eklendi (Asama 3.B)" -m "* scripts/cleanup_asama_3d_2026_04_29.bat: ^>echo escape fix (LF dosyasi olusturma bug)" -m "* scripts/cleanup_becker_full_2026_04_29.bat: orphan .pyc + download_becker.bat addition" -m "* scripts/commit_fase_a_2026_04_29.bat: 5 test dosyasi listesine genisletildi" -m "* scripts/cleanup_asama_3e_2026_04_29.bat: LF artifact + 0-byte zombi + phantom delete" -m "* scripts/final_cleanup_and_commit_2026_04_29.bat: kapanmayacak step-by-step versiyon" -m "* tests/unit/test_mode_banner.py: Asama 3.B mode banner tests"
if errorlevel 1 (
    echo.
    echo [!] Commit basarisiz. Detay icin yukari bak.
    set ERR=1
) else (
    echo [+] Commit basarili
)
echo.
pause

echo.
echo [5/5] Final git status (commit SONRASI)
echo ----------------------------------------
git status --short
echo.
echo Son commit:
git log -1 --oneline
echo.

echo ========================================
if "%ERR%"=="0" (
    echo OK - Tum isle