@echo off
REM Sade tek commit - hicbir fancy char yok
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Just Commit - 2026-05-06
echo ============================================================
echo.

REM HEAD.lock varsa sil
if exist ".git\HEAD.lock" del /Q ".git\HEAD.lock"

REM Stage everything
git add -A

REM Show status before commit
echo === git status ===
git status --short
echo.

REM Single comprehensive commit
git commit -m "feat: mod-first UX + V2 SDK + live trading stack + cleanup (2026-05-06)" -m "" -m "Heddas direktifi: GitHub guncel hale getir, sadelestir." -m "" -m "Mod-first dashboard: /start = PAPER vs LIVE secim ekrani." -m "Live history + CSV export 15 alan + PnL detay panel." -m "Polymarket V2 SDK fixes (allowances dict, MarketOrderArgs)." -m "Live trading stack: gasless approve + redeem via Relayer." -m "Auto-redeem job (5dk interval, idempotent)." -m "Test coverage Wave 13-24: 502 -> 3474 pass, 21.2 -> 43.7." -m "New core modules: heartbeat, executor, maker_taker_decision," -m "  reconciliation, structured_logging, uma_dispute, etc." -m "Cleanup: 14 coverage_v*.txt, 26 _archive temp, 5 scripts."

echo.
echo === git log son 5 ===
git log --oneline -5
echo.
echo === git status (kalanlar) ===
git status --short
echo.
echo Sonraki adim: git push origin main
echo.
pause
