"""/mode komutu — mode-seçim ekranına alias (2026-05-19 C1 audit).

ESKİ DAVRANIŞ (kaldırıldı): `/mode` `LIVE_ENABLED` env'ini TEK TIK toggle
ediyordu — gerçek parayla trading'i tek dokunuşla açıyordu. Footgun'dı:
mode-first redesign'da "mode" = menü navigasyonu; kullanıcı `/mode`'u
navigasyon sanıp yanlışlıkla canlı trading açabilirdi.

YENİ: `/mode` (alias `/m`) → mode-seçim ekranı (`main_dashboard.main_command`).
Gerçek parayla trading açmanın TEK yolu artık `/live` trade istasyonu
kokpitindeki 2-tık onaylı `live_toggle`.

Heddas 2026-04-29 direktifi ("paper ayrı dünya, real ayrı dünya") hâlâ
geçerli — mode-first dashboard onu uyguluyor; bu modül artık yalnız
geriye-dönük komut alias'ı.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from telegram_bot.handlers.main_dashboard import main_command

logger = logging.getLogger("polypaper.handlers.mode")


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/mode` `/m` — mode-seçim ekranını açar (yalnız navigasyon)."""
    await main_command(update, context)


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Eski `mode_*` inline butonları → mode-seçim ekranına yönlendir.

    Stale `/mode` mesajlarındaki eski butonlar (`mode_set_real` vb.) artık
    `LIVE_ENABLED`'i toggle ETMEZ — mode-seçim ekranına düşer. Tek-tık
    canlı-trading footgun'u kapalı.
    """
    await main_command(update, context)
