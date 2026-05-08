@echo off
REM Wave 21 — Async Context Manager fix + bottom 15 modules
SETLOCAL
cd /d "%~dp0\.."

echo ============================================================
echo Wave 21 Coverage Run (target: 43.1%% -^> 50%%+)
echo Key fix: _AsyncCM helper for async with db.conn.execute() chains
echo Bottom 15 modules:
echo   gamma_hist, polybacktest, binance_hist, fill_model
echo   stats, engine_settlement, bot.py, market_scanner
echo   force_settle, changelog, env_toggle handlers
echo   shadow_report_job, pnl_divergence_job, db_retention_job
echo ============================================================

py -3.11 -m pytest tests/ ^
    --cov=core --cov=data --cov=telegram_bot --cov=backtest ^
    --cov-report=term ^
    --tb=short --no-header ^
    > coverage_v21.txt 2>&1

powershell -Command "Get-Content coverage_v21.txt | Select-Object -Last 35"
echo.
echo Full report: coverage_v21.txt
pause
