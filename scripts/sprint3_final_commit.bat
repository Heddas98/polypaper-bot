@echo off
REM Sprint 3 Final Commit — Paket B + C + Sprint 3 closure
SETLOCAL
cd /d "%~dp0\.."

if exist ".git\HEAD.lock" del /Q ".git\HEAD.lock"

git add -A
git commit -m "feat(sprint3): Paket B + C — V2 WSS meta events + 1 FAIL fix" -m "" -m "Sprint 3 Paket C (Hatasiz + UX):" -m "- test_wave22_mega: SystemExit catch (collector.main argparse)" -m "- main_dashboard._get_paper_summary: bot DB schema fix" -m "  (executions.pnl + strategies.status='started')" -m "" -m "Sprint 3 Paket B (Hizlandirma + Hafifletme):" -m "- WSS V2 meta events handler (data/websocket_client.py)" -m "  * tick_size_change: tick degisikliginde callback fire" -m "  * new_market: yeni market discovery" -m "  * market_resolved: UMA report event" -m "- WSS subscribe: custom_feature_enabled=true flag" -m "  (Polymarket V2 spec /api-reference/wss/market)" -m "" -m "Polymarket compliance:" -m "- V2 SDK: py-clob-client-v2 1.0.0 (resmi en guncel)" -m "- WSS endpoint: ws-subscriptions-clob.polymarket.com/ws/market" -m "- 5/5 contract address dogru (pUSD, CTF, CTF Exchange, Neg Risk)" -m "- Allowance V2 dict, MarketOrderArgs decimal, Relayer gasless" -m "" -m "Sprint 3 sonu: bot mainnet-ready, 17 May decision gate hazir."

git log --oneline -5
echo.
git push origin main
echo.
pause
