@echo off
REM Simple push - kapanmaz versiyon
cd /d "%~dp0\.."

echo ========================================
echo Polyscout31 - Push to GitHub
echo ========================================
echo.

echo Kapanmasini onlemek icin: bu pencerede 4 kez Enter'a basacaksin.
echo.
pause

echo.
echo [1/4] Mevcut durum
echo ----------------------------------------
git status -sb
echo.
git log --oneline -3
echo.
pause

echo.
echo [2/4] Stale lock varsa sil
echo ----------------------------------------
if exist ".git\index.lock" (
    del /F /Q ".git\index.lock" 2>nul
    if errorlevel 1 (
        echo Lock silinemedi - devam ediyorum, push lock'i kullanmaz
    )
)
echo OK
pause

echo.
echo [3/4] Push to origin/main
echo ----------------------------------------
git push origin main
echo.
echo Push exit code: %ERRORLEVEL%
pause

echo.
echo [4/4] Final state
echo ----------------------------------------
git log --oneline -1
echo.
echo BU PENCEREYI KAPATMA. Output'u oku.
echo Cikmak icin Enter:
pause
