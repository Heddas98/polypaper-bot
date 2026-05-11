"""Top-level Mode Handler — /mode command (Aşama 3.B).

Heddas 2026-04-29 direktifi: "paper veya real diye seçilecek. paper ayrı
bir dünya real ayrı bi dünya gidecek."

Mode toggle uses LIVE_ENABLED env as source of truth. Toggling via
engine.live.toggle() (runtime safe) + os.environ patch (other modules
that re-read env via getenv pick up new value).

UI:
  /mode (alias /m) — current mode status + toggle button
  Inline buttons:
    "Paper'a geç" / "Real'e geç"  → toggle
    "🔍 Detay /live" / "💼 /portfolio" → quick navigation
"""

from __future__ import annotations

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from telegram_bot.templates.mode_banner import (
    format_mode_status_text,
    get_current_mode,
)

logger = logging.getLogger("polypaper.handlers.mode")


def _is_admin(context, telegram_id: int) -> bool:
    settings = context.bot_data.get("settings")
    if not settings:
        return False
    return settings.is_admin(telegram_id)


def _build_keyboard() -> InlineKeyboardMarkup:
    cur = get_current_mode()
    if cur == "paper":
        toggle_btn = InlineKeyboardButton(
            "💰 REAL Mode'a geç (gerçek pUSD)",
            callback_data="mode_set_real",
        )
    else:
        toggle_btn = InlineKeyboardButton(
            "📋 PAPER Mode'a geç (simülasyon)",
            callback_data="mode_set_paper",
        )
    return InlineKeyboardMarkup(
        [
            [toggle_btn],
            [
                InlineKeyboardButton("🔍 /live detay", callback_data="mode_nav_live"),
                InlineKeyboardButton("💼 /portfolio", callback_data="mode_nav_portfolio"),
            ],
            [InlineKeyboardButton("🔄 Yenile", callback_data="mode_refresh")],
        ]
    )


async def mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mode entry point. Admin-only."""
    user_id = update.effective_user.id
    if not _is_admin(context, user_id):
        await update.message.reply_text("⛔ Admin only.")
        return
    text = format_mode_status_text()
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_build_keyboard(),
        disable_web_page_preview=True,
    )


async def mode_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline button callbacks: mode_set_<paper|real>, mode_refresh, mode_nav_*."""
    q = update.callback_query
    user_id = q.from_user.id
    if not _is_admin(context, user_id):
        await q.answer("Admin only", show_alert=True)
        return

    data = q.data or ""

    # Navigation shortcuts
    if data == "mode_nav_live":
        await q.answer("/live komutuyla aç")
        return
    if data == "mode_nav_portfolio":
        await q.answer("/portfolio komutuyla aç")
        return

    if data == "mode_refresh":
        await q.answer("Yenilendi")
        text = format_mode_status_text()
        try:
            await q.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=_build_keyboard(),
                disable_web_page_preview=True,
            )
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"mode refresh edit fail: {_e}")
        return

    if data in ("mode_set_paper", "mode_set_real"):
        target = "real" if data == "mode_set_real" else "paper"
        cur = get_current_mode()

        if cur == target:
            await q.answer(f"Zaten {target.upper()} mode'da.")
            return

        # Apply: toggle engine.live (which sets self._enabled) + env patch
        engine = context.bot_data.get("engine")
        os.environ["LIVE_ENABLED"] = "true" if target == "real" else "false"
        if engine and getattr(engine, "live", None):
            try:
                # Engine live state flag (reads LIVE_ENABLED via _start path).
                # Direct set on internal flag (idempotent — toggle for parity).
                engine.live._enabled = target == "real"
                engine.live._paused = False
            except (AttributeError, TypeError) as _se:
                logger.warning(f"engine.live state set: {_se}")

        logger.warning(
            f"🔄 MODE_SWITCH: {cur} → {target} | "
            f"user={user_id} | LIVE_ENABLED={os.environ.get('LIVE_ENABLED')}"
        )

        await q.answer(
            f"✅ {target.upper()} mode'a geçildi",
            show_alert=False,
        )
        text = format_mode_status_text()
        try:
            await q.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=_build_keyboard(),
                disable_web_page_preview=True,
            )
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"mode switch edit fail: {_e}")
        return

    await q.answer("Bilinmeyen aksiyon", show_alert=True)
