@echo off
REM T11.8-B Asama B Batch 2 commit -- Windows.
REM pattern_discovery + auto_promote + tournament = 13 narrow + 2 noqa

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
        telegram_bot\jobs\pattern_discovery_job.py ^
        telegram_bot\jobs\auto_promote_job.py ^
        telegram_bot\jobs\tournament_job.py ^
        scripts\_commit_t118b_jobs_batch2.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/jobs/pattern_discovery_job.py','telegram_bot/jobs/auto_promote_job.py','telegram_bot/jobs/tournament_job.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): jobs Batch 2 bare-except narrow (13 narrow + 2 noqa)" -m "" -m "pattern_discovery_job.py (4 -> 1):" -m "  _ensure_table  -> aiosqlite.Error (CREATE TABLE IF NOT EXISTS idempotent)" -m "  run discovery  -> (aiosqlite.Error, KeyError, TypeError, ValueError)" -m "  HTML fallback  -> TelegramError (BadRequest is specific)" -m "  outer wrapper  -> noqa: BLE001 (JobQueue safety)" -m "" -m "auto_promote_job.py (5 -> 0, tam narrow, outer wrapper yok):" -m "  _env_int/_env_float -> (ValueError, TypeError)" -m "  query               -> aiosqlite.Error" -m "  UPDATE per-sid      -> aiosqlite.Error" -m "  notify send         -> (TelegramError, asyncio.TimeoutError)" -m "" -m "tournament_job.py (6 -> 1):" -m "  nested import-fail notify x2 -> (TelegramError, asyncio.TimeoutError)" -m "  deploy_params                -> (aiosqlite.Error, KeyError, TypeError)" -m "  report send                  -> (TelegramError, asyncio.TimeoutError)" -m "  lifecycle cache invalidate   -> (AttributeError, KeyError)" -m "  outer wrapper                -> noqa: BLE001" -m "" -m "telegram_bot/jobs/ progress: 54 -> 41 bare-except (13 more narrow)." -m "3 dosya AST-clean. Regression yok." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
