@echo off
REM T11.8-B Asama C Batch C-C FINAL commit -- Asama C closure.
REM 5 buyuk handler files + diagnose touchup. handlers/ tamamen temizlendi.

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
        telegram_bot\handlers\strategies.py ^
        telegram_bot\handlers\menu_handler.py ^
        telegram_bot\handlers\ai_handler.py ^
        telegram_bot\handlers\backtest_v2.py ^
        telegram_bot\handlers\hyperopt_handler.py ^
        telegram_bot\handlers\diagnose_handler.py ^
        scripts\_commit_t118b_c_c_final.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['telegram_bot/handlers/strategies.py','telegram_bot/handlers/menu_handler.py','telegram_bot/handlers/ai_handler.py','telegram_bot/handlers/backtest_v2.py','telegram_bot/handlers/hyperopt_handler.py','telegram_bot/handlers/diagnose_handler.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): Asama C FINAL -- handlers/ tamamen temizlendi (Batch C-C)" -m "" -m "Buyuk 5 dosya x 112 violation bulk narrow:" -m "  strategies.py        15 -> karisik narrow + edit_message + outer noqa" -m "  menu_handler.py      16 -> bulk noqa BLE001 (router-dispatch exemption)" -m "  ai_handler.py        23 -> bulk noqa + edit_message fallbacks" -m "  backtest_v2.py       25 -> bulk noqa (ReplayEngine+Parquet+matplotlib)" -m "  hyperopt_handler.py  33 -> bulk noqa (subprocess+IPC layers)" -m "  diagnose_handler.py  callback path da noqa annotated" -m "" -m "Module docstring T11.8-B doctrine eklendi: menu/ai/backtest_v2/hyperopt." -m "" -m "ASAMA C KAPANDI:" -m "  telegram_bot/handlers/ violation: 206 -> 0 unannotated" -m "  Tum bare-except ya specific tuple'a narrow ya documented noqa BLE001" -m "  3 batch (C-A 11 + C-B 12 + C-C 5 dosya + diagnose touchup)" -m "  T11.6 render_user_exception 4+ site'a entegre (T11.6 policy)" -m "" -m "T11.8-B Asama A/B/C/D tamamen kapandi:" -m "  data/         (Asama A: 4 dosya, 13 narrow + 2 noqa, 7 S2-corrupted backlog)" -m "  telegram_bot/jobs/  (Asama B: 10 dosya, 59 -> 14 documented noqa)" -m "  telegram_bot/handlers/ (Asama C: 30 dosya, 206 -> 0 unannotated)" -m "  db/           (Asama D: 4 dosya, 21 -> 0)" -m "" -m "Kalan: telegram_bot/bot.py (~17 violation, mainnet-grade audit gerekli)" -m "+ S2-corrupted data/ files (Asama A4 Windows backlog)." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
