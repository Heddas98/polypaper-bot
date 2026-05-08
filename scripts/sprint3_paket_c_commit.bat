@echo off
REM Sprint 3 Paket C — Hatasiz + UX commit
SETLOCAL
cd /d "%~dp0\.."

if exist ".git\HEAD.lock" del /Q ".git\HEAD.lock"

git add -A
git commit -m "fix(sprint3-C): 1 FAIL fix + main_dashboard DB schema sync" -m "" -m "1) test_wave22_mega: SystemExit catch eklendi (collector.main argparse)" -m "   Skip CLI entry names (main, run, cli, entry, __main__)" -m "   BaseException catch (SystemExit, KeyboardInterrupt)" -m "" -m "2) main_dashboard._get_paper_summary: gercek DB schema" -m "   Eski: trades.pnl_usd, strategies.active=1 (yanlis column)" -m "   Yeni: executions.pnl status='filled', strategies.status='started'" -m "   Polymarket compliance: bot internal DB schema, no V2 conflict"

git log --oneline -3
echo.
echo === git status ===
git status --short
echo.
pause
