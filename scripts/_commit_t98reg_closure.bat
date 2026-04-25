@echo off
REM T9.8-REG closure commit -- Windows.
REM 52/52 PASS Windows integration regression + bat path fix.

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
        scripts\_commit_t98reg_closure.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === Commit ===
git commit -m "feat(t9.8-reg): Windows integration smoke 52/52 PASS + bat path fix" -m "" -m "Windows pytest tests/integration/ 3-phase run sonucu:" -m "  Phase 1 (verbose): 52 passed in 27.85s" -m "  Phase 2 (quick):   52 passed in 24.76s" -m "  Phase 3 (3-seed):  3/3 PASS (TestRandomReplay 42/1337/9001)" -m "" -m "15 test class coverage:" -m "  Boot smoke (3) + brain flags (3) + siblings (9) + collections (4) +" -m "  stop no-op (2) + fees v2 (3) + oracle identity (6) + stream det (4) +" -m "  closed PnL (4) + RandomReplay 3-seed (3) + WS baseline (2) +" -m "  drop legacy (1) + reconnect invalidation (2) + double reconnect (1) +" -m "  freshness doctrine (4)." -m "" -m "Anlami:" -m "  - Engine boot path sandbox + Windows ikisinde de aynı." -m "  - Single fee oracle (fees_v2) bit-identical taker/maker math." -m "  - Paper-shadow 1000 trade x 3 seed ZERO drift." -m "  - WS reconnect doctrine (Sprint 5+) GREEN." -m "" -m "Cosmetic fix: scripts/_run_t98_reg_windows.bat Phase 3 path duzeltildi" -m "(TestPaperShadowIdentity -> TestRandomReplay, gercek class adi)." -m "" -m "Pre-mainnet gate KAPANDI: T11.1 audit + T11.2 live guard 6/6 +" -m "T11.3 rollback 4/4 + T9.8-REG 52/52 integration. Mainnet Go/No-Go" -m "karari icin defensive baseline tam." -m "" -m "Memory: project_t98_reg_closure.md" -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
