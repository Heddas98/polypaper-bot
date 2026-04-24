@echo off
REM Recovery v2: Windows-side stale .git lock kaldır + auto-maintenance disable.
REM Sandbox WSL ghost — bu .bat'ı yönetici olmadan da çalışır.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.
echo === 1/4 Disabling git background maintenance (lock kaynagi) ===
git maintenance unregister --force 2>nul
git config --local maintenance.auto false
git config --local gc.auto 0
echo gc.auto = 0  ^| maintenance.auto = false
echo.

echo === 2/4 Killing background git processes ===
tasklist /FI "IMAGENAME eq git.exe" 2>nul | find /I "git.exe" >nul
if not errorlevel 1 (
  taskkill /F /IM git.exe 2>nul
  echo background git.exe killed
) else (
  echo no background git.exe
)
echo.

echo === 3/4 Lock dosyaları siliniyor ===
if exist ".git\HEAD.lock"        echo HEAD.lock VAR
if exist ".git\index.lock"       echo index.lock VAR
if exist ".git\maintenance.lock" echo maintenance.lock VAR

del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

if exist ".git\HEAD.lock"  (echo HEAD.lock HALA VAR)  else (echo HEAD.lock TEMIZ)
if exist ".git\index.lock" (echo index.lock HALA VAR) else (echo index.lock TEMIZ)
echo.

echo === 4/4 git status ===
git status --short
popd
echo.
echo Hazir. Sandbox tarafindan tekrar commit denenebilir.
pause
