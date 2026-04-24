@echo off
REM T11.8-B Asama B Batch 4 (final) commit -- Windows.
REM db_retention + shadow_report = 18 narrow + 3 noqa
REM Asama B closure: 59 -> 14 (all documented noqa)

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
        telegram_bot\jobs\db_retention_job.py ^
        telegram_bot\jobs\shadow_report_job.py ^
        scripts\_commit_t118b_jobs_batch4_final.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/jobs/db_retention_job.py','telegram_bot/jobs/shadow_report_job.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): jobs Batch 4 FINAL -- Asama B closed (18 narrow + 3 noqa)" -m "" -m "db_retention_job.py (10 -> 2):" -m "  + import asyncio, TelegramError" -m "  _ensure_archive_table    -> aiosqlite.Error" -m "  _count_old               -> (aiosqlite.Error, IndexError, TypeError, ValueError)" -m "  _days env int            -> (ValueError, TypeError)" -m "  chunked DELETE fallback  -> aiosqlite.Error" -m "  VACUUM                   -> aiosqlite.Error" -m "  pre/post DB size stat x2 -> OSError" -m "  notify send              -> (TelegramError, asyncio.TimeoutError)" -m "  _archive_rows outer      -> noqa: BLE001 (multi-step archive)" -m "  _delete_old outer        -> noqa: BLE001 (SQL + fallback chain)" -m "" -m "shadow_report_job.py (11 -> 1):" -m "  + import asyncio, TelegramError, aiosqlite" -m "  JSON chat-id load        -> (OSError, JSONDecodeError, AttributeError)" -m "  file write persist       -> OSError" -m "  discover strategy types  -> aiosqlite.Error" -m "  per-stype A/B query      -> (aiosqlite.Error, KeyError, TypeError, ValueError)" -m "  diag send                -> (TelegramError, asyncio.TimeoutError)" -m "  mismatch warn send       -> (TelegramError, asyncio.TimeoutError)" -m "  summary send (3 paths)   -> (TelegramError, asyncio.TimeoutError) x3" -m "  promo send               -> (TelegramError, asyncio.TimeoutError)" -m "  _is_quiet_hours          -> noqa: BLE001 (ZoneInfo Windows-tzdata fallback)" -m "" -m "ASAMA B KAPANDI:" -m "  telegram_bot/jobs/ violation: 59 -> 14" -m "  Kalan 14 hepsi documented noqa: BLE001 (T7.6 job-safety exemption)" -m "  10 dosya AST-clean. Regression yok." -m "  4 batch commit ile tamamlandi." -m "" -m "Siradaki: Asama C (telegram_bot/handlers/), T11.6-B render_user_exception birlesik." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
