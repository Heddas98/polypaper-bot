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

echo [1/3] py_compile + pytest yeni testler...
py -3.11 -m py_compile tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py
if errorlevel 1 goto :fail
py -3.11 -m pytest tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py -q
if errorlevel 1 goto :fail
echo.

echo [2/3] git add (atomic — yeni testler + cleanup batches + status doc + roadmap)...
git add tests\unit\test_circuit_breaker.py tests\unit\test_ev_tracker.py scripts\cleanup_asama_3d_2026_04_29.bat scripts\commit_asama_3c_2026_04_29.bat scripts\commit_fase_a_2026_04_29.bat docs\audits\coverage_status_2026_04_29.md YOL_HARITASI_3AI_SYNTHESIS.md scripts\cleanup_becker_full_2026_04_29.bat
if errorlevel 1 goto :fail
echo.

echo [3/3] git commit...
git commit -m "test(coverage): Fase A — circuit_breaker + ev_tracker + 3.D cleanup batches" -m "" -m "Yeni testler:" -m "* tests/unit/test_circuit_breaker.py: 13 test, core/circuit_breaker.py 0%%->96.1%%" -m "* tests/unit/test_ev_tracker.py: 19 test, core/ev_tracker.py 0%%->31.1%% (DB metodlari Fase B)" -m "" -m "Cleanup batches:" -m "* scripts/cleanup_asama_3d_2026_04_29.bat: CRLF/LF flip onleme + .gitattributes" -m "* scripts/cleanup_becker_full_2026_04_29.bat: orphan .pyc + ~849MB disk" -m "* scripts/commit_asama_3c_2026_04_29.bat: archive (zaten yapildi)" -m "" -m "Docs:" -m "* docs/audits/coverage_status_2026_04_29.md: %%21->%%60 plan, fase A/B/C breakdown" -m "* YOL_HARITASI_3AI_SYNTHESIS.md: v1.1 ilerleme snapshot tablosu (FAZ 0.1 OK, mainnet GREEN)" -m "" -m "Net: +32 test PASS, +75 stmts coverage gained, 0 regression."
if errorlevel 1 goto :fail
echo.

echo ========================================
echo OK — Fase A committed.
echo Sirada: cleanup_asama_3d.bat + cleanup_becker_full.bat
echo ========================================
goto :end

:fail
echo.
echo ========================================
echo FAIL — kontrol et
echo ========================================
exit /b 1

:end
pause
