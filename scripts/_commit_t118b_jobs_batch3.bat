@echo off
REM T11.8-B Asama B Batch 3 commit -- Windows.
REM maintenance_jobs + db_archive_job = 9 narrow + 6 noqa

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
        telegram_bot\jobs\maintenance_jobs.py ^
        telegram_bot\jobs\db_archive_job.py ^
        scripts\_commit_t118b_jobs_batch3.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/jobs/maintenance_jobs.py','telegram_bot/jobs/db_archive_job.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): jobs Batch 3 bare-except narrow (9 narrow + 6 noqa)" -m "" -m "maintenance_jobs.py (6 -> 3):" -m "  + import asyncio, TelegramError" -m "  prune old backup unlink    -> OSError" -m "  snapshot notify send       -> (TelegramError, asyncio.TimeoutError)" -m "  heartbeat notify send      -> (TelegramError, asyncio.TimeoutError)" -m "  snapshot outer wrapper     -> noqa: BLE001 (OS/aiosqlite/FS mix)" -m "  wal_checkpoint outer       -> noqa: BLE001 (6h scheduler safety)" -m "  heartbeat outer            -> noqa: BLE001 (engine attr class drift)" -m "  NOTE: T11.3 Bulgu B atomic-rename (dest_tmp.replace) bloguna" -m "        dokunulmadi; etrafindaki yollar narrow edildi." -m "" -m "db_archive_job.py (9 -> 3):" -m "  + import TelegramError" -m "  _count_rows              -> (sqlite3.Error, IndexError, TypeError)" -m "  sqlite3.connect + PRAGMA -> sqlite3.Error" -m "  finally conn.close       -> sqlite3.Error" -m "  VACUUM thread            -> (sqlite3.Error, RuntimeError)" -m "  notify send              -> (TelegramError, asyncio.TimeoutError)" -m "  setup time-parse         -> (ValueError, TypeError, AttributeError)" -m "  archive_sync outer       -> noqa: BLE001 (sqlite+pandas+pyarrow mix)" -m "  to_thread wrapper        -> noqa: BLE001 (inner already has result dict)" -m "  admin_command outer      -> noqa: BLE001 (T11.6 render policy compliant)" -m "" -m "telegram_bot/jobs/ progress: 41 -> 32 bare-except (9 more narrow)." -m "2 dosya AST-clean. Regression yok." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
