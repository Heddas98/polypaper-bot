@echo off
REM T11.8-B Aşama A kapanış commit — Windows'tan çalıştır.
REM TASKS.md + scripts/_unlock_git_head.bat iki dosyayı içerir.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === 1/3 Pre-check: stale locks temizle ===
git maintenance unregister --force 2>nul
git config --local maintenance.auto false
git config --local gc.auto 0
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul
echo locks cleared.
echo.

echo === 2/3 Staging ===
git add TASKS.md scripts\_unlock_git_head.bat scripts\_commit_t118b_asama_a.bat
if errorlevel 1 (
  echo STAGING FAILED — lock hala var olabilir, tekrar dene.
  pause
  exit /b 1
)
git status --short | findstr "^[AM]  TASKS.md ^[AM]  scripts/"
echo.

echo === 3/3 Commit ===
git commit -m "docs(t11.8-b): TASKS.md Aşama A1+A2+A3 kapanış + git unlock recovery bat" -m "" -m "TASKS.md T11.8-B alt-bullet hierarchy:" -m "  A1 odds_feed.py (c743f20)" -m "  A2 event_monitor.py + becker_loader.py (9298e24)" -m "  A3 polymarket_client.py (f3d07be)" -m "  A4 Windows — 7 S2-corrupted data/ dosyası" -m "  B/C/D — telegram_bot/jobs, handlers (T11.6-B birleşik), db/" -m "" -m "scripts/_unlock_git_head.bat v2: git maintenance unregister + gc.auto=0" -m "+ git.exe kill + lock del. Çift-tıkla recovery. Sandbox WSL ghost çözer." -m "" -m "scripts/_commit_t118b_asama_a.bat: bu commit için stable entry (sandbox" -m "tarafı lock nedeniyle commit atamadığında Windows'tan direkt çalışır)." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

if errorlevel 1 (
  echo COMMIT FAILED
  pause
  exit /b 1
)

echo.
echo === git log -1 ===
git log --oneline -1

popd
echo.
echo Commit basarili. Sandbox'a "ok" yaz.
pause
