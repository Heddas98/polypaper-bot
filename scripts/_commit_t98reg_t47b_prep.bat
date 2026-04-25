@echo off
REM T9.8-REG runner + T4.7-B telemetry prep commit -- Windows.

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
git add TASKS.md ^
        scripts\_run_t98_reg_windows.bat ^
        scripts\_t47b_telemetry_check.bat ^
        scripts\_t47b_compute_p50.py ^
        scripts\_commit_t98reg_t47b_prep.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; ast.parse(open('scripts/_t47b_compute_p50.py', encoding='utf-8').read()); print('AST OK')"
if errorlevel 1 (
  echo AST FAIL
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "chore(t9.8-reg + t4.7-b): integration regression runner + telemetry prep" -m "" -m "T9.8-REG hazirlik:" -m "  scripts/_run_t98_reg_windows.bat -- 3-phase pytest run" -m "    Phase 1: tests/integration/ tam koşum (-v --tb=short)" -m "    Phase 2: hizli ozetsayim (passed/failed/skipped)" -m "    Phase 3: 3-seed paper-shadow identity determinism" -m "" -m "T4.7-B hazirlik (24h bekleme gerektiriyor):" -m "  scripts/_t47b_telemetry_check.bat -- status + activate guide" -m "  scripts/_t47b_compute_p50.py        -- JSON dump -> percentile compute" -m "    Reads /drt save JSON or in-process get_summary()" -m "    Outputs recommended REST_LATENCY_MS + JITTER_MS" -m "    Insight diff vs 200ms heuristic baseline" -m "" -m "Aktivasyon: /envt REST_TIMING_TELEMETRY true (Telegram), 24h calistir," -m "sonra /drt save + compute script + config/settings.py guncelle." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
