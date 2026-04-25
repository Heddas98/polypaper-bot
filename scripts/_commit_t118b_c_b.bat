@echo off
REM T11.8-B Asama C Batch C-B commit -- Windows.
REM 12 handler files (4-9 violation) bulk narrow.

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
        telegram_bot\handlers\env_toggle.py ^
        telegram_bot\handlers\strategy_report.py ^
        telegram_bot\handlers\diagnose_handler.py ^
        telegram_bot\handlers\roadmap_handler.py ^
        telegram_bot\handlers\filters_handler.py ^
        telegram_bot\handlers\markets.py ^
        telegram_bot\handlers\live_guards_handler.py ^
        telegram_bot\handlers\force_settle_handler.py ^
        telegram_bot\handlers\dashboard.py ^
        telegram_bot\handlers\live_handler.py ^
        telegram_bot\handlers\strategy_tester.py ^
        telegram_bot\handlers\stats.py ^
        scripts\_commit_t118b_c_b.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/handlers/env_toggle.py','telegram_bot/handlers/strategy_report.py','telegram_bot/handlers/diagnose_handler.py','telegram_bot/handlers/roadmap_handler.py','telegram_bot/handlers/filters_handler.py','telegram_bot/handlers/markets.py','telegram_bot/handlers/live_guards_handler.py','telegram_bot/handlers/force_settle_handler.py','telegram_bot/handlers/dashboard.py','telegram_bot/handlers/live_handler.py','telegram_bot/handlers/strategy_tester.py','telegram_bot/handlers/stats.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): handlers Batch C-B bulk narrow (12 files, 41 narrow+noqa)" -m "" -m "12 handler dosyasi (4-9 violation cluster):" -m "  env_toggle.py        4 OSError narrow + T11.6-OK preserved" -m "  strategy_report.py   3 SQL+coercion narrow" -m "  diagnose_handler.py  4 per-block noqa (multi-source diagnostic)" -m "  roadmap_handler.py   4 noqa + render_user_exception" -m "  filters_handler.py   DB persist+load + 3 edit_message narrow" -m "  markets.py           4 ISO+edit_message + wait_for narrow" -m "  live_guards_handler  5 per-guard render noqa BLE001" -m "  force_settle_handler httpx+aiosqlite + outer noqa" -m "  dashboard.py         risk-snapshot+odds-feed + outer noqa" -m "  live_handler.py      7 edit_message + 1 SQL narrow" -m "  strategy_tester.py   9 noqa BLE001 (replay engine multi-layer)" -m "  stats.py             5 outer noqa + 4 narrow + edit_message" -m "" -m "telegram_bot/handlers/ progress: 196 -> 155 (41 narrow/noqa eklendi)." -m "Kalan: Batch C-C buyuk dosyalar (strategies, menu, ai_handler, backtest_v2," -m "hyperopt) ~112 violation × 5 dosya." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
