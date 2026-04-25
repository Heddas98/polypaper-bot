@echo off
REM TASKS.md stale marker cleanup -- 5 cosmetic [x] updates.

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === Pre-check lock temizle ===
del /F /Q ".git\HEAD.lock"        2>nul
del /F /Q ".git\index.lock"       2>nul
del /F /Q ".git\maintenance.lock" 2>nul

echo === Staging ===
git add TASKS.md scripts\_commit_tasks_audit.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === Commit ===
git commit -m "docs(tasks): TASKS.md stale marker cleanup audit (5 line update)" -m "" -m "Bastan sona TASKS.md tarama yapildi. 6 marker stale bulundu:" -m "  L154 T4.6 PARTIAL    -> [x] T4.6 CLOSED via T4.6-B (sweep classic)" -m "  L166 T11.8-B parent  -> [x] CLOSED (5 asama 56 dosya 373 site)" -m "  L176 Asama C parent  -> [x] CLOSED (3 batch 30 dosya 206 site)" -m "  L524 T4.6 duplicate  -> [x] CLOSED via T4.6-B (sweep classic)" -m "  L582 T6.3 closure    -> [x] CLOSED 2026-04-21 (Epic 6)" -m "  L758 T9.8-REG        -> [x] CLOSED 2026-04-24 (52/52 PASS)" -m "" -m "Gercek bekleyen is: SADECE 4 satir, hepsi user aksiyon veya bekleme:" -m "  L163 SOL/ETH /strategies ▶ Start (5dk Telegram)" -m "  L183 T4.7-B parent (24h bekleme)" -m "  L185 T4.7-B aktivasyon (/envt REST_TIMING_TELEMETRY true)" -m "  L186 T4.7-B 24h sonrasi (/drt save + compute + settings.py)" -m "" -m "Tum programatik is tamam. Sandbox'ta yapilacak kod isi yok." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
