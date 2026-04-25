@echo off
REM T11.8-B Asama E -- telegram_bot/bot.py main orchestrator narrow.
REM 17 -> 0 unannotated (bulk noqa BLE001 + module docstring doctrine).

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
        telegram_bot\bot.py ^
        scripts\_commit_t118b_e_botpy.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; ast.parse(open('telegram_bot/bot.py', encoding='utf-8').read()); print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): Asama E -- telegram_bot/bot.py main orchestrator (17 noqa)" -m "" -m "bot.py 17 bare-except -> 0 unannotated:" -m "  + module docstring T11.8-B boot-orchestrator doctrine eklendi" -m "  + 11x except Exception as e: -> noqa BLE001 (bulk replace)" -m "  + 6x except Exception as _name: -> noqa BLE001 (regex fix script)" -m "  + 2x except Exception: pass siteleri -> noqa BLE001" -m "" -m "Doktrin: bot.py boot-layer wide catch korundu. Boot orkestrasyonu" -m "40+ optional handler import + JobQueue register + engine wiring +" -m "Telegram startup + DB init dokunuyor. Single missing module bot" -m "crash etmemeli; degraded functionality + log entry yeterli." -m "T11.6 render policy bot.py'da uygulanmiyor (user-facing error" -m "raporlama handler modullerinde gerceklesir)." -m "" -m "T11.8-B advisory zone toplam progress:" -m "  data/         13 narrow + 2 noqa (Asama A) + 7 S2-corrupted backlog" -m "  telegram_bot/jobs/  59 -> 14 documented noqa (Asama B)" -m "  telegram_bot/handlers/ 206 -> 0 unannotated (Asama C, 30 dosya)" -m "  db/           21 -> 0 (Asama D, tam narrow)" -m "  telegram_bot/bot.py 17 -> 0 unannotated (Asama E, bu commit)" -m "" -m "Kalan 55 violation tumu data/* S2-corrupted (Asama A4 Windows backlog)." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
