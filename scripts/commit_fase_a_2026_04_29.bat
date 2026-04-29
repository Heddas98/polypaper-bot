@echo off
REM Fase A coverage tests + 3.D cleanup + roadmap status commit (Heddas direktifi)
REM 2026-04-29

cd /d "%~dp0\.."

echo ========================================
echo Fase A coverage + 3.D cleanup commit
echo ========================================
echo.

REM Stale lock
if exist .git\index.lock del /f /q .git\index.lock

echo [1/3] py_compile + pytest yeni testler (130 test 6 modul)...
py -3.11 -m py_compile tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py tests\unit\test_indicators_and_stats.py tests\unit\test_regime.py tests\unit\test_strategy_selector.py
if errorlevel 1 goto :fail
py -3.11 -m pytest tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py tests\unit\test_indicators_and_stats.py tests\unit\test_regime.py tests\unit\test_strategy_selector.py -q
if errorlevel 1 goto :fail
echo.

echo [2/3] git add (atomic — yeni testler + cleanup batches + status doc + roadmap)...
git add tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py tests\unit\test_indicators_and_stats.py tests\unit\test_regime.py tests\unit\test_strategy_selector.py scripts\cleanup_asama_3d_2026_04_29.bat scripts\cleanup_asama_3e_2026_04_29.bat scripts\commit_asama_3c_2026_04_29.bat scripts\commit_fase_a_2026_04_29.bat docs\audits\coverage_status_2026_04_29.md YOL_HARITASI_3AI_SYNTHESIS.md scripts\cleanup_becker_full_2026_04_29.bat
if errorlevel 1 goto :fail
echo.

echo [3/3] git commit...
git commit -m "test(coverage): Fase A - 6 modul + 3.D/3.E cleanup batches" -m "" -m "Yeni testler (130 test PASS, 0 regression):" -m "* tests/unit/test_circuit_breaker.py: 13 test, core/circuit_breaker.py 0%%->96.1%%" -m "* tests/unit/test_ev_tracker.py: 19 test, core/ev_tracker.py 0%%->31.1%% (DB metodlari Fase B)" -m "* tests/unit/test_indicators_and_stats.py: 37 test, indicators 10.5%%->100%%, stats_utils 0%%->100%%" -m "* tests/unit/test_regime.py: 28 test, core/regime.py 0%%->100%%" -m "* tests/unit/test_strategy_selector.py: 21 test, core/strategy_selector.py 0%%->64.9%% (load_from_db Fase B)" -m "* core/kill_switch.py: zaten 93.8%% (mevcut testler dogrulandi)" -m "" -m "Cleanup batches:" -m "* scripts/cleanup_asama_3d_2026_04_29.bat: CRLF/LF flip onleme + .gitattributes" -m "* scripts/cleanup_asama_3e_2026_04_29.bat: LF artifact + 0-