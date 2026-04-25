@echo off
REM T9.8-REG Windows integration smoke regression -- run pytest on tests/integration/.
REM Tum dosyalar read-only DB / mock client / fee oracle / paper-shadow identity test.
REM Bot calisirken safe (DB sadece okur).

setlocal
set "REPO=%~dp0.."
pushd "%REPO%" || (echo Repo not found & pause & exit /b 1)

echo === Repo: %CD%
echo.

echo === Phase 1: Integration suite tam koşum ===
py -3.11 -m pytest tests\integration\ -v --tb=short
set INT_EXIT=%errorlevel%
echo.
echo === Integration exit code: %INT_EXIT% ===
echo.

echo === Phase 2: Hizli sayim (passed/failed/skipped) ===
py -3.11 -m pytest tests\integration\ -q --tb=no 2>nul
set Q_EXIT=%errorlevel%
echo.
echo === Quick exit code: %Q_EXIT% ===
echo.

echo === Phase 3: 3-seed determinism kontrolu (paper vs shadow identity) ===
py -3.11 -m pytest "tests\integration\test_paper_shadow_divergence.py::TestPaperShadowIdentity::test_1000_events_identical" -v
set DET_EXIT=%errorlevel%
echo.
echo === Determinism exit code: %DET_EXIT% ===
echo.

echo === SONUC ===
if "%INT_EXIT%"=="0" (echo Integration suite: PASS) else (echo Integration suite: FAIL exit=%INT_EXIT%)
if "%DET_EXIT%"=="0" (echo Determinism 3-seed: PASS) else (echo Determinism 3-seed: FAIL exit=%DET_EXIT%)
echo.

popd
echo.
echo Sonucu sandbox'a yapistir.
pause
