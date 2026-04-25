@echo off
REM Final cleanup + GitHub push wrapper -- Windows.
REM
REM Adim 1: T9.8-REG closure commit (var olan _commit_t98reg_closure.bat zincirlemesi)
REM Adim 2: Untracked artifact + deletion commit (sentinel + sweep JSON eskileri + activate_strategies.py)
REM Adim 3: GitHub remote check + push (user URL vermesi gerekirse durur)

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo ========================================
echo  ADIM 1/3: T9.8-REG closure commit
echo ========================================
echo.

REM Lock temizle
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

git add scripts\_commit_t98reg_closure.bat scripts\_run_t98_reg_windows.bat
git diff --cached --stat | findstr "_t98reg\|_t98_reg" >nul
if errorlevel 1 (
  echo  [skip] T9.8-REG zaten commit'lenmis veya degisiklik yok.
) else (
  git commit -m "feat(t9.8-reg): closure commit bat + Phase 3 path fix" -m "" -m "scripts/_commit_t98reg_closure.bat -- standalone closure commit script" -m "scripts/_run_t98_reg_windows.bat -- Phase 3 path duzeltildi" -m "(TestPaperShadowIdentity -> TestRandomReplay)" -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
  if errorlevel 1 (
    echo COMMIT FAILED Adim 1
    pause
    exit /b 1
  )
  git log --oneline -1
)
echo.

echo ========================================
echo  ADIM 2/3: Untracked artifact cleanup
echo ========================================
echo.

del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul

REM activate_strategies.py deletion + sentinel + eski sweep JSON'lar
git add backtest\calibration\_probe_best_strategy.txt
git add backtest\calibration\sweep_fill_heuristic_20260424_105927.json
git add backtest\calibration\sweep_fill_heuristic_20260424_110429.json
git rm scripts\activate_strategies.py 2>nul

REM BUGUN_NE_YAPACAGIM.md kullanici notu -- gitignore'a ekle, commit etme
findstr "BUGUN_NE_YAPACAGIM" .gitignore >nul 2>&1
if errorlevel 1 (
  echo BUGUN_NE_YAPACAGIM.md >> .gitignore
  git add .gitignore
)

git status --short
echo.

git diff --cached --stat
git diff --cached --stat | findstr /R "." >nul
if errorlevel 1 (
  echo  [skip] Adim 2 stage edilecek dosya yok.
) else (
  git commit -m "chore(cleanup): T4.6-B sweep artifacts + activate_strategies.py removal" -m "" -m "backtest/calibration/_probe_best_strategy.txt -- T4.6-B probe sentinel" -m "backtest/calibration/sweep_fill_heuristic_2026042X_*.json -- erken probe runs" -m "scripts/activate_strategies.py -- Epic 2 cleanup deletion" -m ".gitignore -- BUGUN_NE_YAPACAGIM.md eklendi (kullanici personal note)" -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
  if errorlevel 1 (
    echo COMMIT FAILED Adim 2
    pause
    exit /b 1
  )
  git log --oneline -1
)
echo.

echo ========================================
echo  ADIM 3/3: GitHub remote check
echo ========================================
echo.

git remote -v
echo.
git remote 2>&1 | findstr "origin" >nul
if errorlevel 1 (
  echo.
  echo  [!] GitHub remote tanimli DEGIL.
  echo.
  echo  GitHub'a push etmek icin once remote ekle:
  echo    git remote add origin https://github.com/KULLANICIADI/REPOADI.git
  echo    git branch -M main
  echo    git push -u origin main
  echo.
  echo  Veya GitHub'da yeni bir repo olustur:
  echo    https://github.com/new
  echo  Sonra yukaridaki 3 komutu calistir.
  echo.
  echo  Repo URL'in elinde varsa simdi gir [bos birak iptal]:
  set /p REPO_URL=Repo URL:
  if not "%REPO_URL%"=="" (
    echo.
    git remote add origin "%REPO_URL%"
    git branch -M main
    echo.
    echo Push deniyor...
    git push -u origin main
    if errorlevel 1 (
      echo.
      echo PUSH FAILED -- muhtemelen authentication veya repo izni sorunu.
      echo Cozum:
      echo   1. https://github.com/settings/tokens -- Personal Access Token (classic)
      echo      olustur, scope: repo. Token'i sakla.
      echo   2. git config --global credential.helper manager
      echo   3. Tekrar dene: git push -u origin main
      echo      (kullanici adi sor, sifre yerine token yapistir)
    ) else (
      echo.
      echo  ✓ Push BASARILI. GitHub guncel.
    )
  ) else (
    echo  Iptal edildi. Remote eklenmedi.
  )
) else (
  echo  [ok] Remote tanimli. Push yapiliyor...
  echo.
  git push origin main
  if errorlevel 1 (
    echo.
    echo PUSH FAILED -- conflict veya authentication problemi olabilir.
    echo  git pull --rebase origin main  &  git push origin main
  ) else (
    echo.
    echo  ✓ Push BASARILI. GitHub guncel.
  )
)
echo.

echo ========================================
echo  FINAL STATUS
echo ========================================
echo.
git status --short
echo.
git log --oneline -5
echo.

popd
pause
