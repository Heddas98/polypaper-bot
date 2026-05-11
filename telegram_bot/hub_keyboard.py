"""Phase 52/77 — Unified hub keyboard.

Exposes the single source of truth for the main hub keyboard, grouped
into four semantic rows:

  Trading    : Stratejiler / Pozisyonlar / Piyasa / Live
  Insights   : Dashboard   / İstatistik  / Risk   / AI Brain
  Learning   : Öğrenme     / Deney       / Sağlık / Gelişmiş
  Tools      : Backtest    / Mum         / Ayarlar / Yardım

All buttons dispatch through the `menu_*` callback family defined in
`telegram_bot/handlers/menu_handler.py`, which delegates to each real
feature handler via the _UpdateShim proxy.

`build_main_hub_keyboard()` accepts a `refresh_callback` string so each
surface (dashboard / menu / anywhere else) can keep its own refresh
semantics — e.g. dashboard wants `refresh_dashboard` so it can edit the
banner caption in place, while /menu just rebuilds itself.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_hub_keyboard(refresh_callback: str = "menu_refresh") -> InlineKeyboardMarkup:
    """Return the unified 4-row hub keyboard.

    Args:
        refresh_callback: callback_data for the "Yenile" button at the
            bottom. Defaults to ``"menu_refresh"``; dashboard surfaces
            should pass ``"refresh_dashboard"`` so the banner+caption
            edit path keeps working.
    """
    return InlineKeyboardMarkup(
        [
            # Trading row
            [
                InlineKeyboardButton("🎰 Stratejiler", callback_data="menu_strategies"),
                InlineKeyboardButton("📈 Pozisyonlar", callback_data="menu_positions"),
                InlineKeyboardButton("🔴 Piyasa", callback_data="menu_market"),
                InlineKeyboardButton("💰 Live", callback_data="menu_live"),
            ],
            # Insights row
            [
                InlineKeyboardButton("📊 Dashboard", callback_data="menu_dashboard"),
                InlineKeyboardButton("📉 İstatistik", callback_data="menu_stats"),
                InlineKeyboardButton("🛡 Risk", callback_data="menu_risk"),
                InlineKeyboardButton("🧠 AI Brain", callback_data="menu_brain"),
            ],
            # Learning row (Phase 66-77)
            [
                InlineKeyboardButton("🎓 Öğrenme", callback_data="menu_learning"),
                InlineKeyboardButton("🔬 Deney", callback_data="menu_experiment"),
                InlineKeyboardButton("💊 Sağlık", callback_data="menu_health"),
                InlineKeyboardButton("🚀 Gelişmiş", callback_data="menu_advanced"),
            ],
            # Tools row
            [
                InlineKeyboardButton("🧪 Backtest", callback_data="menu_backtest"),
                InlineKeyboardButton("🕯️ Mum", callback_data="menu_candles"),
                InlineKeyboardButton("⚙️ Ayarlar", callback_data="menu_settings"),
                InlineKeyboardButton("❓ Yardım", callback_data="menu_help"),
            ],
            # Refresh
            [InlineKeyboardButton("🔄 Yenile", callback_data=refresh_callback)],
        ]
    )
