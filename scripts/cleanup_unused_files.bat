@echo off
REM ═══════════════════════════════════════════════════════════════════
REM Polyscout31 Unused File Cleanup — 2026-05-05
REM Heddas direktifi: "gereksiz dosyaları silelim, projeyi sadeleştirelim"
REM ═══════════════════════════════════════════════════════════════════
REM
REM SAFE: Bu .bat sadece açıkça temp/log/eski test çıktısı dosyaları siler.
REM Production kodu ASLA silmez. İlk koşumda DRY-RUN gibi davranır
REM (sadece listele). Onay verirsen --apply ile koş.
REM
REM Kullanım:
REM   scripts\cleanup_unused_files.bat        ← LİSTELE (dry-run)
REM   scripts\cleanup_unused_files.bat APPLY  ← SİLE (onaylandı)
REM ═══════════════════════════════════════════════════════════════════

cd /d "%~dp0\.."

set MODE=%1
if "%MODE%"=="" set MODE=DRY

echo ═══════════════════════════════════════════════
echo Polyscout31 Cleanup — Mode: %MODE%
echo ═══════════════════════════════════════════════
echo.

echo [1/5] Eski coverage_v*.txt + final_results.txt:
for %%f in (coverage_v3.txt coverage_v4.txt coverage_v5.txt coverage_v6.txt coverage_v7.txt coverage_v8.txt coverage_v9.txt coverage_v10.txt coverage_v11.txt coverage_full.txt final_results.txt) do (
    if exist "%%f" (
        echo   - %%f
        if "%MODE%"=="APPLY" del "%%f"
    )
)
echo.

echo [2/5] Eski htmlcov\ klasörleri:
if exist htmlcov\ (
    echo   - htmlcov\
    if "%MODE%"=="APPLY" rmdir /s /q htmlcov
)
echo.

echo [3/5] Python __pycache__ klasörleri:
if "%MODE%"=="APPLY" (
    for /r %%d in (__pycache__) do (
        if exist "%%d" rmdir /s /q "%%d"
    )
    echo   ✓ __pycache__ temizlendi
) else (
    echo   (DRY: __pycache__ klasörleri var)
)
echo.

echo [4/5] .pytest_cache + .coverage:
for %%f in (.coverage .pytest_cache) do (
    if exist "%%f" (
        echo   - %%f
        if "%MODE%"=="APPLY" (
            if exist .pytest_cache rmdir /s /q .pytest_cache
            if exist .coverage del .coverage
        )
    )
)
echo.

echo [5/5] _archive\ dizininde 30+ gün eski dosyalar (manual review önerilir):
if exist _archive\ (
    echo   - _archive\ var (boyut kontrol etmek için: scripts\analyze_archive.bat)
    echo   ! Manual review gerekli, .bat otomatik silmez
)
echo.

if "%MODE%"=="DRY" (
    echo ═══════════════════════════════════════════════
    echo DRY-RUN TAMAMLANDI. Silmek için:
    echo   scripts\cleanup_unused_files.bat APPLY
    echo ═══════════════════════════════════════════════
) else (
    echo ═══════════════════════════════════════════════
    echo APPLY TAMAMLANDI ✓
    echo ═══════════════════════════════════════════════
)
