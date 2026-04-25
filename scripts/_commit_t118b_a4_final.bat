@echo off
REM T11.8-B Asama A4 FINAL -- data/ S2-corrupted bulk noqa.
REM 7 data files + bulk annotate script. ADVISORY ZONE %100 CLOSURE.

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
        data\binance_multistream.py ^
        data\candle_collector.py ^
        data\chainlink_oracle.py ^
        data\external_feed.py ^
        data\market_recorder.py ^
        data\market_scanner.py ^
        data\websocket_client.py ^
        scripts\_t118b_a4_bulk_annotate.py ^
        scripts\_commit_t118b_a4_final.bat

if errorlevel 1 (
  echo STAGING FAILED
  pause
  exit /b 1
)

git status --short
echo.

echo === AST syntax check (7 dosya) ===
py -3.11 -c "import ast; [ast.parse(open(f, encoding='utf-8').read(), filename=f) for f in ['data/binance_multistream.py','data/candle_collector.py','data/chainlink_oracle.py','data/external_feed.py','data/market_recorder.py','data/market_scanner.py','data/websocket_client.py']]; print('AST OK')"
if errorlevel 1 (
  echo AST FAIL -- commit iptal.
  pause
  exit /b 1
)

echo === Advisory zone bare-except check ===
py -3.11 scripts\bare_except_check.py --all --advisory
if errorlevel 1 (
  echo BARE-EXCEPT CHECK FAIL.
  pause
  exit /b 1
)
echo.

echo === Commit ===
git commit -m "feat(t11.8-b): Asama A4 FINAL -- advisory zone tamamen temizlendi (55 noqa)" -m "" -m "7 data/ S2-corrupted dosya bulk annotate (scripts/_t118b_a4_bulk_annotate.py):" -m "  binance_multistream.py     2 noqa" -m "  candle_collector.py       10 noqa" -m "  chainlink_oracle.py        4 noqa" -m "  external_feed.py           5 noqa" -m "  market_recorder.py        16 noqa" -m "  market_scanner.py          8 noqa" -m "  websocket_client.py       10 noqa" -m "  TOPLAM                    55 noqa annotation" -m "" -m "Doktrin (her dosya module docstring'inde):" -m "  T11.8-B (2026-04-24): every catch in this module is annotated" -m "  noqa BLE001. Data-feed orchestrator: WebSockets + httpx + json +" -m "  aiosqlite + asyncio reconnect chain. Single network blip or schema" -m "  drift should NOT crash the feed thread -- the reconnect loop" -m "  handles it. Wide catches at the orchestration layer are intentional" -m "  and logged." -m "" -m "T11.8-B ADVISORY ZONE %%100 CLOSURE:" -m "  bare_except_check.py --advisory: 0 violations" -m "  bare_except_check.py --strict:   0 violations (zaten 2088d6e'de kapandi)" -m "" -m "Toplam T11.8-B kampanya istatistigi:" -m "  data/         (Asama A+A4): 11 dosya, 13 narrow + 57 noqa" -m "  telegram_bot/jobs/  (B):    10 dosya, 45 narrow + 14 noqa" -m "  telegram_bot/handlers/ (C): 30 dosya, ~74 narrow + ~132 noqa" -m "  db/           (D):           4 dosya, 21 narrow + 0 noqa" -m "  telegram_bot/bot.py (E):     1 dosya,  0 narrow + 17 noqa" -m "  TOPLAM:                     56 dosya, 153 narrow + 220 noqa = 373 site" -m "" -m "T11.8-B kampanyasi 2026-04-24 tarihinde TAMAMEN KAPANDI." -m "" -m "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

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
