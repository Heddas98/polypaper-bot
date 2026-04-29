@echo off
REM =============================================================
REM  Asama 3.E — son temizlik (3.D residual + LF artifact + 0-byte zombi)
REM  2026-04-29 Heddas: yesilden devam et
REM
REM  Sorunlar:
REM    1. Onceki 3.D'de "echo CRLF -> LF" yanlislikla LF dosyasi olusturdu
REM    2. 0-byte zombi scriptler (eski Becker/Phase47 cleanup'tan)
REM    3. Phantom-restore'lanan Becker .bat'lari (git rm)
REM    4. core/_t11_7_test_mod.py test artifact
REM
REM  Hepsi: rm + git rm + final commit
REM =============================================================

setlocal
cd /d "%~dp0\.."

echo.
echo Asama 3.E: son temizlik
echo Konum: %CD%
echo.

REM Stale lock
if exist .git\index.lock del /f /q .git\index.lock

REM 1. Yanlislik LF dosyasi
if exist "LF" (
    echo [DEL]   LF (3.D batch echo redirect kazasi)
    del /f /q "LF"
)

REM 2. Boyut 0 zombi script'ler (truncated earlier, intent = sil)
for %%F in (scripts\ab_sweep_phase47f8.py scripts\shadow_monitor_47f7.py tests\smoke_phase51.py) do (
    if exist "%%F" (
        for /f %%S in ("%%F") do (
            if %%~zF EQU 0 (
                echo [RM]    %%F (0-byte zombi)
                git rm -f "%%F" 2>nul
            )
        )
    )
)

REM 3. Phantom-restored Becker scripts
if exist "scripts\delete_becker_files_2026_04_28.bat" (
    echo [RM]    scripts\delete_becker_files_2026_04_28.bat (phantom restore)
    git rm -f "scripts\delete_becker_files_2026_04_28.bat" 2>nul
    if exist "scripts\delete_becker_files_2026_04_28.bat" del /f /q "scripts\delete_becker_files_2026_04_28.bat"
)
if exist "scripts\download_becker.bat" (
    echo [RM]    scripts\download_becker.bat (eski)
    git rm -f "scripts\download_becker.bat" 2>nul
    if exist "scripts\download_becker.bat" del /f /q "scripts\download_becker.bat"
)

REM 4. Test artifact
if exist "core\_t11_7_test_mod.py" (
    echo [DEL]   core\_t11_7_test_mod.py (Epic 11 test artifact)
    del /f /q "core\_t11_7_test_mod.py"
)

REM 5. Onceki cleanup batchleri (commit'le zaten arsivlendi)
if exist "scripts\cleanup_repo_hygiene_2026_04_29.bat" (
    echo [DEL]   scripts\cleanup_repo_hygiene_2026_04_29.bat
    del /f /q "scripts\cleanup_repo_hygiene_2026_04_29.bat"
)

echo.
echo Final git status:
git status --short
echo.

echo ========================================
echo OK - 3.E temizlendi.
echo Sonraki commit icin: scripts\commit_fase_a_2026_04_29.bat
echo ========================================
pause
endlocal
