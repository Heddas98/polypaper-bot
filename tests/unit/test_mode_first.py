"""Mode-first tek-kapı redesign — 2026-05-19.

Heddas direktifi: bot ikiye bölünür — /start /dashboard /d /main hepsi
mode-seçim ekranını açar (PAPER vs LIVE). Seçilen mod kendi menü
dünyasına götürür. Mod seçimi = navigasyon; canlı trading ayrı toggle.

Bu testler tek-kapı akışını pin'ler: admin gate, mode-select metni,
PAPER MODE → detaylı dashboard, LIVE MODE → trade istasyonu.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import telegram_bot.handlers.main_dashboard as md
from telegram_bot.handlers.main_dashboard import (
    _build_main_dashboard_text_kb,
    _is_admin,
    live_dashboard,
    main_callback,
    main_command,
    paper_dashboard,
)


def _ctx(admin: bool = True, with_settings: bool = True):
    ctx = MagicMock()
    bot_data = {"engine": MagicMock(), "db": MagicMock()}
    if with_settings:
        settings = MagicMock()
        settings.is_admin = MagicMock(return_value=admin)
        bot_data["settings"] = settings
    ctx.bot_data = bot_data
    return ctx


# ── _is_admin ────────────────────────────────────────────────────────────


def test_is_admin_missing_settings_denies():
    """settings yoksa fail-closed — deny."""
    ctx = MagicMock()
    ctx.bot_data = {}
    assert _is_admin(ctx, 123) is False


def test_is_admin_true_false():
    assert _is_admin(_ctx(admin=True), 1) is True
    assert _is_admin(_ctx(admin=False), 1) is False


# ── main_command admin gate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_command_denies_non_admin():
    update = MagicMock()
    update.effective_user = MagicMock(id=999)
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    await main_command(update, _ctx(admin=False))
    update.message.reply_text.assert_called_once()
    assert "Admin" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_main_command_admin_shows_mode_select(monkeypatch):
    import data.polymarket_portfolio as pp

    monkeypatch.setattr(pp, "read_cached_snapshot", AsyncMock(return_value=None))
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    update.callback_query = None
    await main_command(update, _ctx(admin=True))
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args[0][0]
    assert "Mod Seçimi" in text


# ── mode-select ekranı ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mode_select_has_both_worlds(monkeypatch):
    import data.polymarket_portfolio as pp

    monkeypatch.setattr(pp, "read_cached_snapshot", AsyncMock(return_value=None))
    text, kb = await _build_main_dashboard_text_kb(_ctx(), 1)
    assert "PAPER MODE" in text
    assert "LIVE MODE" in text
    # güvenlik notu: mod seçimi navigasyon, trading ayrı
    assert "navigasyon" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "main_paper" in cbs
    assert "main_live" in cbs


# ── main_callback yönlendirme ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_main_callback_denies_non_admin():
    update = MagicMock()
    q = MagicMock()
    q.from_user = MagicMock(id=999)
    q.answer = AsyncMock()
    q.data = "main_paper"
    update.callback_query = q
    await main_callback(update, _ctx(admin=False))
    q.answer.assert_called_once()
    # admin-only alert, paper_dashboard çağrılmamalı
    assert q.answer.call_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_main_callback_routes_to_modes(monkeypatch):
    routed = []
    monkeypatch.setattr(
        md, "paper_dashboard", AsyncMock(side_effect=lambda *a: routed.append("paper"))
    )
    monkeypatch.setattr(
        md, "live_dashboard", AsyncMock(side_effect=lambda *a: routed.append("live"))
    )
    for data, expect in (("main_paper", "paper"), ("main_live", "live")):
        update = MagicMock()
        q = MagicMock()
        q.from_user = MagicMock(id=1)
        q.answer = AsyncMock()
        q.data = data
        update.callback_query = q
        await main_callback(update, _ctx(admin=True))
    assert routed == ["paper", "live"]


# ── PAPER MODE → detaylı dashboard içeriği ───────────────────────────────


@pytest.mark.asyncio
async def test_paper_dashboard_shows_detailed_content(monkeypatch):
    """PAPER MODE → dashboard._build() detaylı içeriği gösterir."""
    import telegram_bot.handlers.dashboard as dash

    monkeypatch.setattr(dash, "_build", AsyncMock(return_value="🏦 DETAY DASHBOARD"))

    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.message = None
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    update.callback_query = q

    ctx = _ctx(admin=True)
    ctx.bot_data["db"].get_user_by_telegram_id = AsyncMock(return_value=MagicMock())

    await paper_dashboard(update, ctx)
    q.edit_message_text.assert_called_once()
    text = q.edit_message_text.call_args[0][0]
    assert "PAPER MODE" in text
    assert "DETAY DASHBOARD" in text  # _build içeriği gömülü
    kb = q.edit_message_text.call_args.kwargs["reply_markup"]
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "main_live" in cbs  # LIVE MODE'a geçiş
    assert "main_dashboard" in cbs  # mode seçimine dön


@pytest.mark.asyncio
async def test_paper_dashboard_no_user_fallback(monkeypatch):
    """Kullanıcı yoksa panel crash etmez, fallback metni gösterir."""
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.message = None
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    update.callback_query = q
    ctx = _ctx(admin=True)
    ctx.bot_data["db"].get_user_by_telegram_id = AsyncMock(return_value=None)
    await paper_dashboard(update, ctx)
    q.edit_message_text.assert_called_once()
    assert "PAPER MODE" in q.edit_message_text.call_args[0][0]


# ── LIVE MODE → trade istasyonu kokpiti ──────────────────────────────────


@pytest.mark.asyncio
async def test_live_dashboard_renders_trade_station(monkeypatch):
    """LIVE MODE → live_handler._build_main() trade istasyonu kokpiti."""
    import telegram_bot.handlers.live_handler as lh

    fake_kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Mode Seçimi", callback_data="main_dashboard")]]
    )
    monkeypatch.setattr(
        lh, "_build_main", AsyncMock(return_value=("🎯 TRADE İSTASYONU", fake_kb))
    )
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.message = None
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    update.callback_query = q
    await live_dashboard(update, _ctx(admin=True))
    q.edit_message_text.assert_called_once()
    assert "TRADE İSTASYONU" in q.edit_message_text.call_args[0][0]


@pytest.mark.asyncio
async def test_live_dashboard_build_fail_fallback(monkeypatch):
    """_build_main patlasa bile LIVE MODE crash etmez."""
    import telegram_bot.handlers.live_handler as lh

    monkeypatch.setattr(lh, "_build_main", AsyncMock(side_effect=RuntimeError("boom")))
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.message = None
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    update.callback_query = q
    await live_dashboard(update, _ctx(admin=True))
    q.edit_message_text.assert_called_once()
    assert "LIVE MODE" in q.edit_message_text.call_args[0][0]
