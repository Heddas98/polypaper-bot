@echo off
REM P1-07-followup (2026-05-11) - Aggressive auto-fix.
REM
REM `ruff check --fix` ile uygulanan 1078 fix sonrası kalan 158 violation:
REM   UP035 typing.Dict/List/Tuple/Set deprecated
REM   UP038 isinstance(x, (A, B)) -> isinstance(x, A | B)
REM   F841 unused local variable
REM   B007 loop variable unused
REM   F811 redefinition
REM   F601 duplicate dict key
REM
REM Bu bat --unsafe-fixes ile 99 ek auto-fix uygular (Ruff "unsafe" diyor cunku
REM ekstrem nadir edge case'lerde behavior degisikligi olabilir; pratikte
REM safe).
REM
REM ONCE F821 critical bug'lari elle duzeltildi:
REM   * backtest_v2.py: 4 dead Becker function silindi
REM   * force_settle_handler.py: 2 slug undefined fix
REM   * test_p0_08_multi_tf.py: duplicate __main__ block silindi

setlocal enabledelayedexpansion

pushd "%~dp0\.."

if not exist data_store\audits mkdir data_store\audits

for /f "delims=" %%a in ('powershell -NoProfile -Command "[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')"') do set "DT=%%a"
set "OUT=data_store\audits\lint_unsafe_fix_%DT%.txt"

echo === P1-07-followup unsafe-fixes sweep ===
echo Output: %OUT%
echo.

REM Step 1: --unsafe-fixes (UP*, F*, B*)
echo === ruff check --fix --unsafe-fixes === > "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff check --fix --unsafe-fixes . >> "%OUT%" 2>&1
set "RC=%ERRORLEVEL%"
echo. >> "%OUT%"

REM Step 2: ruff format (consistent formatting after fixes)
echo === ruff format === >> "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff format . >> "%OUT%" 2>&1
echo. >> "%OUT%"

REM Step 3: re-run check (verify)
echo === ruff check (verify) === >> "%OUT%"
echo. >> "%OUT%"
py -3.11 -m ruff check . >> "%OUT%" 2>&1
set "FINAL_RC=%ERRORLEVEL%"

echo.
echo === Summary ===
echo   unsafe-fix exit:  !RC!
echo   final check exit: !FINAL_RC!
echo.

REM Show top of final check (remaining violations)
echo === Remaining violations (top) ===
powershell -NoProfile -Command "Get-Content '%OUT%' -Tail 50 | Select-String -Pattern '^\w.*\.py:' | Select-Object -First 30"

echo.
echo Full transcript: %OUT%
echo.
echo NEXT: git diff --shortstat ; pytest tests/ -m "not integration" -q

popd
endlocal
