@echo off
REM ═══════════════════════════════════════════════════════════════════
REM Sprint 2 Daily Monitor — Heddas Windows cron
REM 2026-05-03 → 2026-05-17 (14 gün shadow live)
REM ═══════════════════════════════════════════════════════════════════
REM
REM Çalıştırma:
REM   1. Manuel: scripts\sprint2_daily_monitor.bat
REM   2. Cron (Task Scheduler):
REM      - Trigger: Daily 09:00 (Türkiye sabah)
REM      - Action: Start program → C:\...\sprint2_daily_monitor.bat
REM      - Working dir: C:\Users\heddas\Desktop\Heddas\Dersnotu2\Polyscout31
REM
REM Çıktı:
REM   data_store\sprint2_daily_YYYYMMDD.txt
REM   data_store\sprint2_gate_YYYYMMDD.txt (gate günü 17 May)
REM ═══════════════════════════════════════════════════════════════════

cd /d "%~dp0\.."
echo === Sprint 2 Daily Monitor === > data_store\sprint2_daily_log.tmp
echo Tarih: %DATE% %TIME% >> data_store\sprint2_daily_log.tmp
echo. >> data_store\sprint2_daily_log.tmp

REM 1) Daily check (1 gün özet)
py -3.11 scripts\sprint2_daily_check.py --days 1 >> data_store\sprint2_daily_log.tmp 2>&1
if errorlevel 1 goto :fail

echo. >> data_store\sprint2_daily_log.tmp
echo === Decision Gate Preview (14 gün) === >> data_store\sprint2_daily_log.tmp
py -3.11 scripts\sprint2_decision_gate.py >> data_store\sprint2_daily_log.tmp 2>&1

REM 2) Save dated copy
set DT=%DATE:~6,4%%DATE:~3,2%%DATE:~0,2%
copy data_store\sprint2_daily_log.tmp "data_store\sprint2_daily_%DT%.txt" > nul
echo OK: data_store\sprint2_daily_%DT%.txt
exit /b 0

:fail
echo FAIL: daily check error
type data_store\sprint2_daily_log.tmp
exit /b 1
