@echo off
REM T11.8-B Asama B Batch 1 commit -- Windows.
REM 3 jobs dosyasi narrow: pnl_divergence + shadow_vs_paper + becker_rolling_recal

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
        telegram_bot\jobs\pnl_divergence_job.py ^
        telegram_bot\jobs\shadow_vs_paper_job.py ^
        telegram_bot\jobs\becker_rolling_recal_job.py ^
        scripts\_commit_t118b_jobs_batch1.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/jobs/pnl_divergence_job.py','telegram_bot/jobs/shadow_vs_paper_job.py','telegram_bot/jobs/becker_rolling_recal_job.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): jobs Batch 1 bare-except narrow (5 narrow + 3 noqa)" -m "" -m "telegram_bot/jobs/pnl_divergence_job.py:" -m "  + import asyncio, TelegramError" -m "  send_message  -> (TelegramError, asyncio.TimeoutError)" -m "  outer wrapper -> noqa: BLE001 (JobQueue scheduler safety)" -m "" -m "telegram_bot/jobs/shadow_vs_paper_job.py:" -m "  + import asyncio, TelegramError" -m "  row-access    -> (KeyError, IndexError, TypeError)" -m "  send_message  -> (TelegramError, asyncio.TimeoutError)" -m "  outer wrapper -> noqa: BLE001" -m "" -m "telegram_bot/jobs/becker_rolling_recal_job.py:" -m "  + import asyncio, TelegramError" -m "  send_message  -> (TelegramError, asyncio.TimeoutError)" -m "  outer wrapper -> noqa: BLE001" -m "  schedule fn   -> (AttributeError, TypeError, ValueError)" -m "" -m "T7.6 job-safety exemption: JobQueue kullanan callback'lerin" -m "outermost wrapper'i wide catch edecek (unhandled exception" -m "scheduler thread'ini oldurur). noqa: BLE001 + reasoning doc." -m "" -m "T11.8-B Asama B ilerleme: 59 -> 54 bare-except (5 narrow)." -m "3 dosya AST-clean. Regression yok." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
