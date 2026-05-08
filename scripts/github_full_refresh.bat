@echo off
REM GitHub Full Refresh — README + CHANGELOG yeniden yazildi
SETLOCAL
cd /d "%~dp0\.."

if exist ".git\HEAD.lock" del /Q ".git\HEAD.lock"

echo ============================================================
echo GitHub Full Refresh — 2026-05-08
echo ============================================================
echo.

git add README.md CHANGELOG.md SECURITY.md docs/GITHUB_REPO_SETTINGS.md
git add -A scripts/

echo === Stage edilenler ===
git diff --cached --stat
echo.

git commit -m "docs: GitHub full refresh — README + CHANGELOG modernize" -m "" -m "Heddas direktifi: GitHub repo'yu bastan asagi guncelle, modern, anlasilir." -m "" -m "README.md (komple yeniden yazildi):" -m "- Hizli baslangic 10dk kurulum sectionu" -m "- Mod-first komut tablosu (Live, Paper, Operasyon)" -m "- Polymarket V2 uyumluluk detayi (SDK, contracts, WSS events)" -m "- 12 strateji + 3 lifecycle evresi" -m "- Mimari klasor agaci (yorum satirli)" -m "- Modern badge'ler (Polymarket V2, 3,474 tests, 43.7%% coverage)" -m "" -m "CHANGELOG.md (sadelestirildi):" -m "- 16KB -> ~6KB" -m "- Eski 80+ phase tarihcesi tek paragrafa indirgendi" -m "- Son 1 ay (Sprint 2 + Sprint 3) detayli" -m "- Silinmis ozellikler (Becker, HyperOpt) acikca belirtildi" -m "- V2 cutover breaking change'leri net" -m "" -m "SECURITY.md:" -m "- RELAYER_API_KEY rotation prosedurleri eklendi" -m "" -m "docs/GITHUB_REPO_SETTINGS.md:" -m "- Repo description + 13 topic onerisi" -m "- Manuel ayar checklist (UI uzerinden yapilacaklar)"

echo.
echo === git log son 3 ===
git log --oneline -3
echo.

git push origin main
echo.
echo ============================================================
echo Tamam. GitHub: https://github.com/Heddas98/polypaper-bot
echo ============================================================
echo.
echo Manuel adim: docs/GITHUB_REPO_SETTINGS.md icindeki
echo description + topics'i GitHub Settings'ten ayarla.
echo.
pause
