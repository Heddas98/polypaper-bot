@echo off
REM ════════════════════════════════════════════════════════════════════════
REM GitHub About Auto Setup — gh CLI ile description + topics ayarla
REM ════════════════════════════════════════════════════════════════════════
SETLOCAL
cd /d "%~dp0\.."

set REPO=Heddas98/polypaper-bot

echo ============================================================
echo GitHub About Auto Setup
echo Repo: %REPO%
echo ============================================================
echo.

REM 1) gh CLI kurulu mu?
where gh >nul 2>&1
if errorlevel 1 (
    echo [HATA] GitHub CLI ^(gh^) kurulu degil.
    echo.
    echo Cozum 1 ^(2 dk^):
    echo   - https://cli.github.com/ ac
    echo   - Windows installer indir + kur
    echo   - cmd'yi yeniden ac, bu bat'i tekrar calistir
    echo.
    echo Cozum 2 ^(manuel UI, 3 dk^):
    echo   - https://github.com/%REPO%/settings ac
    echo   - "About" kismina tikla ^(sag tarafta^)
    echo   - Description:
    echo     "Polymarket V2 SDK uyumlu kripto Up/Down binary prediction market"
    echo     "trading bot. Paper + live mode, mod-first Telegram UI, gasless"
    echo     "approve+redeem."
    echo   - Topics ekle:
    echo     polymarket trading-bot prediction-markets telegram-bot python
    echo     crypto binary-options paper-trading ai-trading claude-sonnet
    echo     gnosis-safe gasless defi
    echo   - Save changes
    echo.
    pause
    exit /b 1
)

echo [1/4] gh CLI kurulu, version kontrol...
gh --version
echo.

REM 2) Login durumunu kontrol et
echo [2/4] Login kontrol...
gh auth status >nul 2>&1
if errorlevel 1 (
    echo Login gerekli. Asagidaki komutu calistir, sonra bu bat'i tekrar calistir:
    echo.
    echo   gh auth login
    echo.
    echo - Web tarayicisindan auth ol
    echo - Default protocol: HTTPS
    echo - Authenticate with browser
    echo.
    pause
    exit /b 1
)
echo Login OK.
echo.

REM 3) Description set
echo [3/4] Description ayarlaniyor...
gh repo edit %REPO% --description "Polymarket V2 SDK uyumlu kripto Up/Down binary prediction market trading bot. Paper + live mode, mod-first Telegram UI, gasless approve+redeem."
if errorlevel 1 (
    echo [UYARI] Description set basarisiz. Manuel UI'dan dene.
) else (
    echo Description OK.
)
echo.

REM 4) Topics ekle (her birini ayri komut)
echo [4/4] Topics ekleniyor ^(13 adet^)...
gh repo edit %REPO% --add-topic polymarket
gh repo edit %REPO% --add-topic trading-bot
gh repo edit %REPO% --add-topic prediction-markets
gh repo edit %REPO% --add-topic telegram-bot
gh repo edit %REPO% --add-topic python
gh repo edit %REPO% --add-topic crypto
gh repo edit %REPO% --add-topic binary-options
gh repo edit %REPO% --add-topic paper-trading
gh repo edit %REPO% --add-topic ai-trading
gh repo edit %REPO% --add-topic claude-sonnet
gh repo edit %REPO% --add-topic gnosis-safe
gh repo edit %REPO% --add-topic gasless
gh repo edit %REPO% --add-topic defi
echo Topics OK.
echo.

REM 5) Wiki + Discussions kapatilsin (gereksiz)
gh repo edit %REPO% --enable-wiki=false 2>nul
gh repo edit %REPO% --enable-discussions=false 2>nul
gh repo edit %REPO% --enable-projects=false 2>nul

REM 6) Mevcut durumu goster
echo ============================================================
echo Mevcut repo settings:
echo ============================================================
gh repo view %REPO% --json description,repositoryTopics,visibility
echo.

echo ============================================================
echo TAMAM. Web'de kontrol:
echo https://github.com/%REPO%
echo ============================================================
echo.
pause
