@echo off
:: Sprint 2 — Mainnet Activation Script
:: 2026-05-03 Heddas direktifi: para yatırıldı, mikro test başlat
::
:: ÖNCELİKLE TÜM SAFETY ENV'lerin .env'de olduğundan EMİN OL!
:: Bu script .env'i kontrol etmez — .env'i sen yaz.
::
:: Beklenen .env satırları (en sonuna ekle):
::
::   # Sprint 2 Mainnet Mikro Test
::   LIVE_ENABLED=true
::   LIVE_MAX_TRADE=1.00
::   LIVE_BUDGET=10.0
::   ORDER_VALIDATOR_ENABLED=true
::   ORDER_MAX_USD=10
::   ORDER_MIN_USD=5
::   ORDER_MIN_PRICE=0.05
::   ORDER_MAX_PRICE=0.95
::   KILL_SWITCH_ENABLED=true
::   KILL_DAILY_MAX_LOSS_PCT=0.10
::   KILL_CONSECUTIVE_LOSS_LIMIT=5
::   KILL_CONSECUTIVE_COOLDOWN_S=3600
::   KILL_WEEKLY_MAX_DD_PCT=0.20
::   FILL_SPREAD_COST=0.023
::   FILL_IMPACT=0.025
::   LATENCY_DRIFT=0.04

echo ============================================
echo   Sprint 2 - Mainnet Activation
echo ============================================
echo.

:: Pre-flight checks
echo [1/4] Bot kontrol...
if exist data_store\polypaper.lock (
    for %%A in (data_store\polypaper.lock) do (
        if %%~zA GTR 0 (
            echo [WARN] Bot calisir gibi gozukuyor - .\stop_bot.bat calistir
            pause
            exit /b 1
        )
    )
)

echo [2/4] V2 SDK import smoke...
py -3.11 -c "from py_clob_client_v2 import ClobClient, ApiCreds, OrderArgs, OrderType; print('  OK')" || (
    echo [FAIL] V2 SDK yok - py -3.11 -m pip install py-clob-client-v2==1.0.0
    pause
    exit /b 1
)

echo [3/4] Yeni moduller smoke...
py -3.11 -c "from core.heartbeat import HeartbeatTask; from core.maker_taker_decision import decide_order_type; from core.portfolio_kill_switch import get_kill_switch; from telegram_bot.handlers.order_validator import validate_order; print('  OK')" || (
    echo [FAIL] Modul import hatasi
    pause
    exit /b 1
)

echo [4/4] .env safety vars kontrol...
findstr /B "LIVE_ENABLED=true" .env >nul 2>&1
if errorlevel 1 (
    echo [WARN] .env'de LIVE_ENABLED=true yok!
    echo        Lutfen .env'e safety env'leri ekle ve tekrar calistir.
    echo        Detay: scripts\sprint2_activate_mainnet.bat dosyasinin basinda.
    pause
    exit /b 1
)
findstr /B "ORDER_MAX_USD=10" .env >nul 2>&1
if errorlevel 1 (
    echo [WARN] .env'de ORDER_MAX_USD=10 yok! Hard cap aktif degil.
    pause
    exit /b 1
)
findstr /B "KILL_SWITCH_ENABLED=true" .env >nul 2>&1
if errorlevel 1 (
    echo [WARN] .env'de KILL_SWITCH_ENABLED=true yok! Drawdown koruma yok.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Pre-flight checks PASSED
echo   Bot baslatiliyor (LIVE MODE)
echo ============================================
echo.
.\start.bat

echo.
echo Bot LIVE mode'da baslatildi.
echo Beklenen:
echo   - Telegram banner: "LIVE MODE" (kirmizi)
echo   - Live Trader: STANDBY -> ACTIVE
echo   - Polymarket budget gercek balance gosterir
echo.
echo Monitoring: scripts\sprint2_daily_check.py
echo.
pause
