@echo off
REM Push to GitHub -- Heddas98/polypaper-bot
REM URL hardcoded ki delayed-expansion bug yememezi.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

set "GH_URL=https://github.com/Heddas98/polypaper-bot.git"

echo === Repo: %CD%
echo === Target: %GH_URL%
echo.

echo === Lock temizle ===
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

echo === 1/4 Mevcut remote'lari listele ===
git remote -v
echo.

echo === 2/4 Origin remote ekle (varsa override) ===
git remote remove origin 2>nul
git remote add origin "%GH_URL%"
if errorlevel 1 (
  echo REMOTE ADD FAILED
  pause
  exit /b 1
)
git remote -v
echo.

echo === 3/4 Branch main olarak ayarla ===
git branch -M main
echo.

echo === 4/4 Push ===
echo Eger ilk pushsa GitHub kullanici adi + Personal Access Token sorabilir.
echo Token: https://github.com/settings/tokens (scope: repo)
echo.
git push -u origin main
set PUSH_EXIT=%errorlevel%
echo.

if "%PUSH_EXIT%"=="0" (
  echo ===========================================
  echo  ✓ PUSH BASARILI -- GitHub guncel
  echo ===========================================
  echo.
  echo  Repo URL: %GH_URL%
  echo  Branch:   main
  echo.
  git log --oneline -5
) else (
  echo ===========================================
  echo  ✗ PUSH FAILED -- exit code %PUSH_EXIT%
  echo ===========================================
  echo.
  echo Sik gorulen sorunlar:
  echo  1. Authentication: GitHub artik password kabul etmiyor.
  echo     - https://github.com/settings/tokens uzerinden Personal Access Token
  echo       (classic) olustur, scope: repo. Token'i sakla.
  echo     - Tekrar push yap, kullanici adi sor, sifre yerine token'i yapistir.
  echo  2. Repo bos olmasi gerekiyor (yeni olusturulmussa README/license eklenmemis olmali).
  echo     - Eger GitHub'da otomatik README varsa once cek:
  echo       git pull origin main --allow-unrelated-histories
  echo     - Sonra: git push origin main
  echo  3. Repo private + token sadece public repo izniyle olusturulduyse:
  echo     - Token'i regenerate et, scope: repo (full).
)

popd
echo.
pause
