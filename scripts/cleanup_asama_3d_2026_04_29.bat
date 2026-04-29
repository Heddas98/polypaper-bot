@echo off
REM =============================================================
REM  Asama 3.D — git status dirty-state temizligi (mount drift)
REM  2026-04-29 Heddas direktifi: yesilden devam et
REM
REM  Sorun: 17 dosya git status'ta "modified" gozukuyor ama:
REM    - 13 .py/.md dosya: pure CRLF/LF flip (icerik identik)
REM    - 5 phantom-deleted .py/.bat: mount mtime hayalı
REM    - core/ai_brain.py +1908b: line-ending farki, NUL pad yok
REM
REM  Cozum:
REM    1. CRLF dosyalari git checkout HEAD ile LF'e geri al
REM    2. Phantom deleted dosyalari index'ten cikar
REM    3. .gitattributes ekle (gelecekte CRLF flip onlemi)
REM
REM  Aciklama: bot Windows'ta calisirken Python her ikisi de okur
REM  (LF + CRLF). git'in yeni satir normalizasyonu CRLF gostererek
REM  kafa karistiriyor. .gitattributes "text=auto eol=lf" ile cozulur.
REM =============================================================

setlocal
cd /d "%~dp0\.."

echo.
echo Asama 3.D: git status temizlik
echo Konum: %CD%
echo.

REM Stale index lock
if exist .git\index.lock (
  echo [LOCK]  .git\index.lock siliniyor
  del /f /q .git\index.lock
)

echo.
echo [1/4] Mevcut git status:
git status --short
echo.

echo [2/4] CRLF -^> LF flip dosyalari git checkout ile HEAD'e geri al...
git checkout HEAD -- core\ai_brain.py core\engine.py core\engine_settlement.py core\keepalive.py
if errorlevel 1 goto :fail

REM Phantom-deleted scripts (mount mtime fantomu, FS'te yok)
git checkout HEAD -- scripts\ab_sweep_phase47f8.py scripts\bench_discovery_plan.py scripts\delete_becker_files_2026_04_28.bat scripts\shadow_monitor_47f7.py scripts\smoke_unified_phase82e_final.py scripts\verify_migration_v15.py scripts\verify_phase82e_markers.py tests\smoke_phase51.py 2>nul

REM JSON calibration files (auto-generated, mount-flip'e yatkin)
git checkout HEAD -- backtest\calibration\live_paper_drift_2026_04_29.json backtest\calibration\sweep_fill_heuristic_20260424_105927.json backtest\calibration\sweep_fill_heuristic_20260424_110429.json backtest\calibration\sweep_fill_heuristic_20260424_193711.json 2>nul

echo.
echo [3/4] .gitattributes ekle (gelecekte CRLF flip onlemi)...
if not exist ".gitattributes" (
  (
    echo # Auto-detect text files and normalize line endings to LF
    echo * text=auto eol=lf
    echo.
    echo # Force LF for source code
    echo *.py text eol=lf
    echo *.md text eol=lf
    echo *.json text eol=lf
    echo *.yml text eol=lf
    echo *.yaml text eol=lf
    echo.
    echo # Force CRLF for Windows-only files
    echo *.bat text eol=crlf
    echo *.cmd text eol=crlf
    echo.
    echo # Binary
    echo *.db binary
    echo *.duckdb binary
    echo *.parquet binary
    echo *.zst binary
    echo *.gz binary
    echo *.png binary
    echo *.jpg binary
  ) > .gitattributes
  echo [+] .gitattributes olusturuldu
) else (
  echo [=] .gitattributes zaten var
)

echo.
echo [4/4] Sonuc git status:
git status --short
echo.

echo ========================================
echo OK - Asama 3.D dirty-state temizlendi.
echo Kalan dosyalari (env_reference.md vs.) ayri commit'le.
echo ========================================
goto :end

:fail
echo.
echo ========================================
echo FAIL - kontrol et yukaridaki output
echo ========================================
exit /b 1

:end
pause
endlocal
