@echo off
REM Repo state diagnose + GitHub sync.
REM 1. Local commit log
REM 2. Add remote (idempotent)
REM 3. Fetch from GitHub (read what's there)
REM 4. Compare local vs remote
REM 5. Push (with rebase fallback)

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

set "GH_URL=https://github.com/Heddas98/polypaper-bot.git"

echo ===========================================
echo  REPO DIAGNOSE + GITHUB SYNC
echo  Target: %GH_URL%
echo ===========================================
echo.

del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

echo === 1/6 LOCAL git log (son 10) ===
git log --oneline -10
echo.

echo === 2/6 LOCAL git status ===
git status --short
echo.

echo === 3/6 Remote ekle (idempotent) ===
git remote remove origin 2>nul
git remote add origin "%GH_URL%"
git remote -v
echo.

echo === 4/6 GitHub'dan fetch (uzaktaki commit'leri oku) ===
git fetch origin
set FETCH_EXIT=%errorlevel%
if not "%FETCH_EXIT%"=="0" (
  echo.
  echo  [!] Fetch FAILED -- exit %FETCH_EXIT%
  echo  Olasi sebep:
  echo    a^) Repo yok / yanlis URL
  echo    b^) Authentication gerekiyor (token gir)
  echo    c^) Repo private + token scope yetersiz
  echo.
  echo  Cozum: https://github.com/settings/tokens classic, scope=repo
  echo  Sonra bu bati tekrar calistir.
  pause
  exit /b 1
)
echo.

echo === 5/6 LOCAL vs REMOTE karsilastirma ===
echo.
echo --- Remote (origin/main) son 10 commit ---
git log --oneline origin/main -10 2>nul
if errorlevel 1 (
  echo  [info] origin/main yok -- repo bos veya farkli branch.
  git branch -r
)
echo.
echo --- Local main'de var ama origin/main'de yok ---
git log origin/main..main --oneline 2>nul
echo.
echo --- origin/main'de var ama local main'de yok (eger varsa) ---
git log main..origin/main --oneline 2>nul
echo.

echo === 6/6 Push ===
git branch -M main
git push -u origin main
set PUSH_EXIT=%errorlevel%
echo.

if "%PUSH_EXIT%"=="0" (
  echo ===========================================
  echo  ✓ PUSH BASARILI -- GitHub guncel
  echo ===========================================
  echo.
  git log --oneline -5
) else (
  echo ===========================================
  echo  ✗ PUSH FAILED -- exit %PUSH_EXIT%
  echo ===========================================
  echo.
  echo  Eger 'rejected non-fast-forward' hatasi alirsan:
  echo    - GitHub'da farkli/eski commit history var.
  echo    - Cozum 1 (uzakta hosgelmedik commit yok ise):
  echo        git push -u origin main --force-with-lease
  echo    - Cozum 2 (uzakta korunmasi gereken commit'ler var ise):
  echo        git pull origin main --rebase --allow-unrelated-histories
  echo        git push -u origin main
  echo.
  echo  Eger 'authentication failed' alirsan:
  echo    - Token expired / scope eksik.
  echo    - https://github.com/settings/tokens regenerate, scope=repo (full).
  echo    - Sonra: git push -u origin main (kullanici=Heddas98, sifre=token)
)
echo.

popd
pause
