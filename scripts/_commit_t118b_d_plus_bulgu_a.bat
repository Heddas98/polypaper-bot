@echo off
REM T11.8-B Asama D + T11.3 Bulgu A bulk closure -- Windows.

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
        db\database.py ^
        db\migrations.py ^
        db\migration_phase79.py ^
        db\ro_connect.py ^
        docs\ARCHITECTURE.md ^
        docs\mainnet\T11_3_rollback_plan.md ^
        scripts\_commit_t118b_d_plus_bulgu_a.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['db/database.py','db/migrations.py','db/migration_phase79.py','db/ro_connect.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): Asama D + T11.3 Bulgu A bulk closure (21 narrow, 0 noqa)" -m "" -m "Asama D -- db/ advisory zone bare-except narrow:" -m "  db/migration_phase79.py (1 -> 0)  migrate runner -> (sqlite3.Error, OSError)" -m "  db/migrations.py        (5 -> 0)  _get_current_version + apply + run + grandfather + rollback" -m "                                   all aiosqlite.Error" -m "  db/database.py          (6 -> 0)  atomic_deduct + exec_retry -> aiosqlite.Error (locked retry)" -m "                                   per_strategy + stats (aiosqlite.Error, IndexError, TypeError)" -m "                                   get_setting + get_settings (aiosqlite.Error, IndexError)" -m "  db/ro_connect.py        (9 -> 0)  _try_connect inner sqlite3.Error" -m "                                   stage-2 immutable sqlite3.Error" -m "                                   sidecar WAL/SHM copy (OSError, SameFileError)" -m "                                   stage-3 tmp-copy (sqlite3.Error, OSError, SameFileError)" -m "                                   open_ro ctx close + tmp unlink -> sqlite3.Error + OSError" -m "                                   async _try aiosqlite.Error (all stages)" -m "" -m "Sonuc: db/ 21 -> 0 bare-except. TAM NARROW, hicbir noqa gerekmedi." -m "4 dosya AST-clean." -m "" -m "T11.3 Bulgu A -- CLOSED (docs fix):" -m "  docs/ARCHITECTURE.md L223 -- rollback.bat satiri silindi + note eklendi" -m "  docs/mainnet/T11_3_rollback_plan.md L340+L347 -- [x] CLOSED" -m "  Referanslar rollback_sprint_2_1.py + git revert HEAD --no-edit'e yonlendirildi." -m "  (DEPLOYMENT.md:115+164 zaten hot fix'liydi.)" -m "" -m "T11.8-B Asama A/B/D tamamen kapandi. Kalan: Asama C (handlers 206 violation)." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
