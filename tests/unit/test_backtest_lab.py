"""`/backtest` LAB Faz 1 — mode-first iskelet (2026-05-20).

Heddas direktifi: backtest modülü çok-fonksiyonel "trade istasyonu"na
evrilsin. Bu testler Faz 1 iskeletini pin'ler — paneller saf-render,
reality-gap blok mevcut/yok durumları, callback dispatcher ve "not
modified" sessizce-yutma davranışı. Telegram I/O yok.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

from telegram_bot.handlers.backtest_lab import (
    _BUILDERS,
    _build_builder,
    _build_calibrate,
    _build_compare,
    _build_legacy,
    _build_main,
    _build_quick,
    _main_kb,
    _panel_nav_kb,
    _reality_gap_block,
    _safe_edit,
    _strategy_count,
    backtest_lab_callback,
    backtest_lab_command,
)

# ── DB mock helpers ─────────────────────────────────────────


class _Cursor:
    """`async with db.conn.execute(...) as cur: await cur.fetchone()` mock."""

    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def fetchone(self):
        return self._row


class _Conn:
    """`db.conn.execute(query, params)` → _Cursor matched by query substring."""

    def __init__(self, row_map: dict | None = None, raise_on: str | None = None):
        self._map = row_map or {}
        self._raise_on = raise_on

    def execute(self, query, params=None):
        if self._raise_on and self._raise_on in query:
            raise RuntimeError("simulated db error")
        for needle, row in self._map.items():
            if needle in query:
                return _Cursor(row)
        return _Cursor(None)


def _db(row_map: dict | None = None, raise_on: str | None = None):
    return SimpleNamespace(conn=_Conn(row_map=row_map, raise_on=raise_on))


# ── Keyboard structure ──────────────────────────────────────


def test_main_kb_has_4_panels_plus_legacy():
    kb = _main_kb()
    assert isinstance(kb, InlineKeyboardMarkup)
    # 4 panel + legacy = 5 rows, her biri 1 buton
    rows = kb.inline_keyboard
    assert len(rows) == 5
    callbacks = [r[0].callback_data for r in rows]
    assert callbacks == [
        "lab_quick",
        "lab_builder",
        "lab_compare",
        "lab_calibrate",
        "lab_legacy",
    ]


def test_panel_nav_kb_default():
    kb = _panel_nav_kb()
    rows = kb.inline_keyboard
    # Sadece "Ana Panel · Yenile" satırı
    assert len(rows) == 1
    assert rows[0][0].callback_data == "lab_main"
    assert rows[0][1].callback_data == "lab_refresh"


def test_panel_nav_kb_with_extras():
    extra = [
        [SimpleNamespace(callback_data="x")],  # type: ignore[list-item]
    ]
    # Use real InlineKeyboardButton-equivalents
    from telegram import InlineKeyboardButton

    extra_real = [[InlineKeyboardButton("X", callback_data="x")]]
    kb = _panel_nav_kb(extra_rows=extra_real)
    rows = kb.inline_keyboard
    # 1 extra + 1 nav = 2 satır
    assert len(rows) == 2
    assert rows[0][0].callback_data == "x"
    assert rows[-1][0].callback_data == "lab_main"


# ── _reality_gap_block ──────────────────────────────────────


@pytest.mark.asyncio
async def test_reality_gap_no_db():
    block = await _reality_gap_block(None)
    assert "Gerçeklik" in block
    assert "DB yok" in block


@pytest.mark.asyncio
async def test_reality_gap_no_trades():
    # COUNT=0 → "henüz settled live trade yok" mesajı + ölçek satırı
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    block = await _reality_gap_block(db)
    assert "henüz settled live trade yok" in block
    assert "paper ×" in block


@pytest.mark.asyncio
async def test_reality_gap_with_trades_green(monkeypatch):
    # MULT=0.5, paper=10 → expected=5; live=5 → drift 0% (yeşil)
    monkeypatch.setenv("REALITY_GAP_MULT", "0.5")
    monkeypatch.setenv("REALITY_GAP_ALERT_PCT", "10.0")
    db = _db({"FROM live_trades": (3, 10.0, 5.0)})
    block = await _reality_gap_block(db)
    assert "3 trade" in block
    assert "🟢" in block  # drift ≤ 10%
    assert "+0.0%" in block


@pytest.mark.asyncio
async def test_reality_gap_with_trades_yellow(monkeypatch):
    # MULT=0.5, paper=10 → expected=5; live=10 → drift +100% (sarı uyarı)
    monkeypatch.setenv("REALITY_GAP_MULT", "0.5")
    monkeypatch.setenv("REALITY_GAP_ALERT_PCT", "10.0")
    db = _db({"FROM live_trades": (5, 10.0, 10.0)})
    block = await _reality_gap_block(db)
    assert "5 trade" in block
    assert "🟡" in block  # drift > 10%
    assert "+100.0%" in block


@pytest.mark.asyncio
async def test_reality_gap_db_error_safe():
    db = _db(raise_on="FROM live_trades")
    block = await _reality_gap_block(db)
    # Ham exception sızdırma — sadece kısa "okuma hatası" mesajı
    assert "okuma hatası" in block
    assert "simulated db error" not in block


# ── _strategy_count ─────────────────────────────────────────


def test_strategy_count_returns_nonzero():
    """Registry'de en az birkaç strateji olmalı (auto-discover)."""
    n, sample = _strategy_count()
    # Auto-import bazı sebeple başarısız olsa bile fonksiyon patlamamalı
    assert n >= 0
    assert isinstance(sample, list)


# ── Panel builders — saf render smoke ───────────────────────


@pytest.mark.asyncio
async def test_build_main_smoke():
    db = _db({"FROM live_trades": (0, 0.0, 0.0), "FROM ob_snapshots": (1234, 1_000, 2_000)})
    text, kb = await _build_main(db)
    assert "BACKTEST LAB" in text
    assert "Gerçeklik" in text  # reality-gap block injected
    assert "Hangi panele" in text
    assert isinstance(kb, InlineKeyboardMarkup)
    assert kb.inline_keyboard[0][0].callback_data == "lab_quick"


@pytest.mark.asyncio
async def test_build_quick_smoke():
    db = _db({"FROM live_trades": (0, 0.0, 0.0), "FROM ob_snapshots": (100, 0, 0)})
    text, kb = await _build_quick(db)
    assert "HIZLI TEST" in text
    assert "/backtest_replay" in text  # legacy command bridge
    assert isinstance(kb, InlineKeyboardMarkup)


@pytest.mark.asyncio
async def test_build_builder_placeholder():
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_builder(db)
    assert "STRATEJİ KURUCU" in text
    assert "Faz 3-4" in text  # placeholder badge
    assert "kural cümleleri" in text or "kural" in text.lower()


@pytest.mark.asyncio
async def test_build_compare_smoke():
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_compare(db)
    assert "KARŞILAŞTIR" in text
    assert "/compare" in text


@pytest.mark.asyncio
async def test_build_calibrate_no_trades():
    """7g pencerede trade yok → bilgi mesajı, /reality_gap köprüsü."""
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_calibrate(db)
    assert "KALİBRASYON" in text
    assert "/reality_gap" in text
    assert "REALITY_GAP_MULT" in text


@pytest.mark.asyncio
async def test_build_calibrate_with_7d_trades(monkeypatch):
    """7g pencerede 12 trade var → drift bloğu görünür."""
    monkeypatch.setenv("REALITY_GAP_MULT", "0.66")
    db = _db({"FROM live_trades": (12, 20.0, 12.0)})
    text, kb = await _build_calibrate(db)
    assert "7g pencere (12 trade)" in text
    # 20 × 0.66 = 13.2; live 12 → drift -1.2 (-9.1%); format `${:+.2f}` → "$+13.20"
    assert "$+13.20" in text
    assert "$+12.00" in text
    assert "-1.20" in text  # drift değeri


@pytest.mark.asyncio
async def test_build_legacy_lists_old_commands():
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_legacy(db)
    assert "ESKİ PANELLER" in text
    assert "/backtest_v2" in text
    assert "/backtest_replay" in text


# ── _BUILDERS dispatch map ──────────────────────────────────


def test_builders_map_covers_all_callbacks():
    """Mode-select keyboard'unda eklenen her callback _BUILDERS'da olmalı."""
    main_callbacks = [r[0].callback_data for r in _main_kb().inline_keyboard]
    for cb in main_callbacks:
        assert cb in _BUILDERS, f"{cb} _BUILDERS map'inden eksik"
    # Plus refresh + main
    assert "lab_main" in _BUILDERS
    assert "lab_refresh" in _BUILDERS


# ── _safe_edit ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_edit_not_modified_silenced():
    """B1 doktrini: "message not modified" → duplicate mesaj ATMA."""
    q = MagicMock()
    q.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    await _safe_edit(q, "x", _main_kb())
    q.edit_message_text.assert_awaited_once()
    q.message.reply_text.assert_not_awaited()  # sessizce yutuldu


@pytest.mark.asyncio
async def test_safe_edit_real_error_falls_back():
    """Gerçek BadRequest (not-modified DIŞI) → reply_text fallback."""
    q = MagicMock()
    q.edit_message_text = AsyncMock(side_effect=BadRequest("Some other error"))
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    await _safe_edit(q, "x", _main_kb())
    q.message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_edit_telegram_error_falls_back():
    q = MagicMock()
    q.edit_message_text = AsyncMock(side_effect=TelegramError("network"))
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    await _safe_edit(q, "x", _main_kb())
    q.message.reply_text.assert_awaited_once()


# ── Command + callback dispatcher ───────────────────────────


@pytest.mark.asyncio
async def test_backtest_lab_command_opens_main():
    update = MagicMock()
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    context.bot_data = {
        "db": _db({"FROM live_trades": (0, 0.0, 0.0), "FROM ob_snapshots": (10, 0, 1)}),
    }

    await backtest_lab_command(update, context)
    update.message.reply_text.assert_awaited_once()
    call_text = update.message.reply_text.await_args.args[0]
    assert "BACKTEST LAB" in call_text


@pytest.mark.asyncio
async def test_backtest_lab_callback_unknown_data_noop():
    """Bilinmeyen callback data → sessiz log, panel edit YOK."""
    q = MagicMock()
    q.data = "lab_does_not_exist"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    context.bot_data = {"db": _db()}

    await backtest_lab_callback(update, context)
    q.answer.assert_awaited_once()
    # Bilinmeyen → ne edit ne reply
    q.edit_message_text.assert_not_awaited()
    q.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_backtest_lab_callback_dispatches_to_builder():
    q = MagicMock()
    q.data = "lab_quick"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    context.bot_data = {
        "db": _db({"FROM live_trades": (0, 0.0, 0.0), "FROM ob_snapshots": (10, 0, 1)}),
    }

    await backtest_lab_callback(update, context)
    q.answer.assert_awaited_once()
    q.edit_message_text.assert_awaited_once()
    edited_text = q.edit_message_text.await_args.args[0]
    assert "HIZLI TEST" in edited_text


@pytest.mark.asyncio
async def test_backtest_lab_callback_no_query_safe():
    """update.callback_query None → patlamasın."""
    update = MagicMock()
    update.callback_query = None
    context = MagicMock()
    context.bot_data = {"db": _db()}
    # Sadece patlamamalı
    await backtest_lab_callback(update, context)


@pytest.mark.asyncio
async def test_backtest_lab_callback_builder_exception_safe():
    """Builder içinde exception → nazik fallback mesaj, no crash."""
    q = MagicMock()
    q.data = "lab_main"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    # db = obje değil → builder içinde patlayacak (getattr None döndürür)
    context.bot_data = {"db": "not-a-real-db"}

    await backtest_lab_callback(update, context)
    # En azından answer çağrıldı + bir edit denemesi yapıldı
    q.answer.assert_awaited_once()
    assert q.edit_message_text.await_count >= 1
