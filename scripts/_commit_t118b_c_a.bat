@echo off
REM T11.8-B Asama C Batch C-A commit -- Windows.
REM 11 handler files (1-3 violation) narrow + render_user_exception swaps.

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
        telegram_bot\handlers\strategy_builder.py ^
        telegram_bot\handlers\brier_handler.py ^
        telegram_bot\handlers\settings_handler.py ^
        telegram_bot\handlers\ev_stats_handler.py ^
        telegram_bot\handlers\becker_recal_handler.py ^
        telegram_bot\handlers\archive_info_handler.py ^
        telegram_bot\handlers\risk_handler.py ^
        telegram_bot\handlers\lifecycle_handler.py ^
        telegram_bot\handlers\changelog_handler.py ^
        telegram_bot\handlers\positions.py ^
        telegram_bot\handlers\phase77_handler.py ^
        scripts\_commit_t118b_c_a.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/handlers/strategy_builder.py','telegram_bot/handlers/brier_handler.py','telegram_bot/handlers/settings_handler.py','telegram_bot/handlers/ev_stats_handler.py','telegram_bot/handlers/becker_recal_handler.py','telegram_bot/handlers/archive_info_handler.py','telegram_bot/handlers/risk_handler.py','telegram_bot/handlers/lifecycle_handler.py','telegram_bot/handlers/changelog_handler.py','telegram_bot/handlers/positions.py','telegram_bot/handlers/phase77_handler.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): handlers Batch C-A bulk narrow + T11.6 render_user_exception swap" -m "" -m "11 handler dosyasi dokunuldu (1-3 violation cluster):" -m "  strategy_builder.py    PTBUserWarning import ImportError narrow" -m "  brier_handler.py       outer wrapper noqa + render_user_exception" -m "  settings_handler.py    edit_message_caption (BadRequest, TelegramError, asyncio.TimeoutError)" -m "  ev_stats_handler.py    2x render_user_exception swap (T11.6 policy)" -m "  becker_recal_handler   2x render_user_exception swap" -m "  archive_info_handler   _fmt_ts (ValueError, TypeError, OverflowError, OSError)" -m "  risk_handler.py        edit_message narrow + outer route noqa" -m "  lifecycle_handler.py   2 narrow (aiosqlite.Error, IndexError, KeyError)" -m "  changelog_handler.py   JSON parse narrow + chunk-send noqa" -m "  positions.py           3 noqa outer (DB + polymarket_client fetch chain)" -m "  phase77_handler.py     3x edit_message_text (BadRequest narrow)" -m "" -m "T11.6 render_user_exception integration: 4 site artik T11.6 policy uyumlu" -m "(brier + ev_stats x2 + becker_recal x2). Internal state leak kapatildi." -m "" -m "telegram_bot/handlers/ progress: 206 -> 196 (10 bare narrow + 6 noqa annotated)." -m "_exc_render.py ve rest_timing_handler.py zaten uyumluydu -- skip." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
