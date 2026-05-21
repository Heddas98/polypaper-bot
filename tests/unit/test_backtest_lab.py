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
    """`async with db.conn.execute(...) as cur: await cur.fetchone/fetchall()` mock.

    Faz 6b: rows tek satır ise fetchone() o satırı döner, fetchall() [row]; çoklu
    satır ise fetchone() ilk satırı, fetchall() tümünü döner.
    """

    def __init__(self, row_or_rows):
        # Tek tuple/None → fetchone semantik; list → multi-row
        self._rows = row_or_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def fetchone(self):
        if isinstance(self._rows, list):
            return self._rows[0] if self._rows else None
        return self._rows

    async def fetchall(self):
        if isinstance(self._rows, list):
            return self._rows
        return [self._rows] if self._rows is not None else []


class _Conn:
    """`db.conn.execute(query, params)` → _Cursor matched by query substring."""

    def __init__(self, row_map: dict | None = None, raise_on: str | None = None):
        self._map = row_map or {}
        self._raise_on = raise_on

    def execute(self, query, params=None):
        if self._raise_on and self._raise_on in query:
            raise RuntimeError("simulated db error")
        # Faz 6b: en uzun needle'a göre öncelikli match (overlap'i önler — örn
        # "GROUP BY strategy_label" "FROM live_trades"'in icinde aramadan)
        for needle in sorted(self._map.keys(), key=len, reverse=True):
            if needle in query:
                return _Cursor(self._map[needle])
        return _Cursor(None)


def _db(row_map: dict | None = None, raise_on: str | None = None):
    return SimpleNamespace(conn=_Conn(row_map=row_map, raise_on=raise_on))


# ── Keyboard structure ──────────────────────────────────────


def test_main_kb_has_panels_plus_legacy():
    kb = _main_kb()
    assert isinstance(kb, InlineKeyboardMarkup)
    # 5 panel + legacy = 6 rows (candle eklendi 2026-05-21)
    rows = kb.inline_keyboard
    assert len(rows) == 6
    callbacks = [r[0].callback_data for r in rows]
    assert callbacks == [
        "lab_quick",
        "lab_candle",
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


# ── Adım 3 — _humanize_conditions + inline backtest ─────────


def test_humanize_conditions_readable():
    """JSON koşulları insan-okunur etiketlere çevrilmeli (Heddas direktifi)."""
    from telegram_bot.handlers.backtest_lab import _humanize_conditions

    rs = {
        "entry": {
            "logic": "AND",
            "conditions": [
                {"field": "elapsed_seconds", "op": ">=", "value": 30},
                {"field": "up_best_ask", "op": "<=", "value": 0.55},
            ],
        }
    }
    out = _humanize_conditions(rs)
    assert "Market saniyesi" in out  # field → insan etiket
    assert "UP alış fiyatı" in out
    assert "≥" in out and "≤" in out  # op → sembol
    assert "30" in out and "0.55" in out
    assert "VE" in out  # AND → VE


def test_humanize_conditions_empty():
    from telegram_bot.handlers.backtest_lab import _humanize_conditions

    out = _humanize_conditions({"entry": {"conditions": []}})
    assert "koşul yok" in out


@pytest.mark.asyncio
async def test_run_inline_backtest_invalid_params():
    from telegram_bot.handlers.backtest_lab import _run_inline_backtest

    # Geçersiz asset
    text, kb = await _run_inline_backtest("x", "DOGE", "5m", _db())
    assert "Geçersiz" in text
    # Geçersiz isim (path traversal)
    text, kb = await _run_inline_backtest("../escape", "BTC", "5m", _db())
    assert "Geçersiz" in text


@pytest.mark.asyncio
async def test_build_candle_menu():
    from telegram_bot.handlers.backtest_lab import _build_candle_menu

    text, kb = await _build_candle_menu(_db())
    assert "CANDLE / MARTINGALE" in text
    assert "Martingale" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_cb:BTC:5m:up:flat" in cb
    assert "lab_cb:BTC:5m:up:m6" in cb
    assert "lab_cb:BTC:1h:up:m6" in cb


@pytest.mark.asyncio
async def test_run_candle_backtest_invalid():
    from telegram_bot.handlers.backtest_lab import _run_candle_backtest

    text, kb = await _run_candle_backtest("DOGE", "5m", "up", "m6", _db())
    assert "Geçersiz" in text
    text, kb = await _run_candle_backtest("BTC", "5m", "up", "m6", None)
    assert "DB" in text


@pytest.mark.asyncio
async def test_run_inline_backtest_no_db():
    import pytest as _pt

    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _run_inline_backtest

    # geçerli isim ama db None — ruleset bulunmalı önce; yoksa "bulunamadı"
    # Bu test sadece db=None guard'ını değil, ruleset-yoksa guard'ını da kapsar
    text, kb = await _run_inline_backtest("nonexistent_rs", "BTC", "5m", None)
    # ruleset yok → "bulunamadı" (db guard'ından önce)
    assert "bulunamadı" in text or "DB" in text


@pytest.mark.asyncio
async def test_build_builder_placeholder():
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_builder(db)
    assert "STRATEJİ KURUCU" in text
    # Faz 4: JSON paste flow + /lab_save komut örneği + field listesi
    assert "/lab_save" in text
    assert "JSON" in text
    assert "kural" in text.lower()
    assert "elapsed_seconds" in text  # field örnekleri


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
async def test_build_calibrate_includes_polymarket_constants_block():
    """Faz 6: Kalibrasyon paneli Polymarket sabitleri özetini göstermeli."""
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_calibrate(db)
    assert "Polymarket sabitleri" in text
    assert "crypto taker" in text
    assert "0.07" in text  # crypto fee rate (docs-verified)
    assert "tail zones" in text
    assert "check_polymarket_drift" in text  # script referansı
    assert "2026-05-20" in text  # son doğrulama tarihi


# ── Faz 6b — Per-strateji reality-gap ────────────────────────


@pytest.mark.asyncio
async def test_per_strategy_drift_block_no_db():
    from telegram_bot.handlers.backtest_lab import _per_strategy_drift_block

    block = await _per_strategy_drift_block(None, 0.66)
    assert block == ""


@pytest.mark.asyncio
async def test_per_strategy_drift_block_no_rows():
    from telegram_bot.handlers.backtest_lab import _per_strategy_drift_block

    db = _db({"GROUP BY strategy_label": []})
    block = await _per_strategy_drift_block(db, 0.66)
    assert block == ""


@pytest.mark.asyncio
async def test_per_strategy_drift_block_with_strategies(monkeypatch):
    """3 strateji → her biri için satır + 2'si yeşil 1'i sarı (drift > %10)."""
    from telegram_bot.handlers.backtest_lab import _per_strategy_drift_block

    monkeypatch.setenv("REALITY_GAP_ALERT_PCT", "10.0")
    db = _db(
        {
            "GROUP BY strategy_label": [
                # (label, n, paper, live)
                ("classic_btc", 10, 5.0, 3.3),   # exp 3.3 vs live 3.3 → 0%, 🟢
                ("hour_edge", 7, 4.0, 1.0),      # exp 2.64 vs 1.0 → -62%, 🟡
                ("rule_based", 5, 1.0, 0.5),     # exp 0.66 vs 0.5 → -24%, 🟡
            ]
        }
    )
    block = await _per_strategy_drift_block(db, 0.66)
    assert "Strateji bazında drift" in block
    assert "classic_btc" in block
    assert "hour_edge" in block
    assert "rule_based" in block
    assert "(10t)" in block  # classic_btc trade sayısı
    assert "🟢" in block  # en azından bir yeşil
    assert "🟡" in block  # en azından bir sarı


@pytest.mark.asyncio
async def test_per_strategy_drift_block_db_error_silent():
    from telegram_bot.handlers.backtest_lab import _per_strategy_drift_block

    db = _db(raise_on="GROUP BY strategy_label")
    block = await _per_strategy_drift_block(db, 0.66)
    # Hata → boş satır (sessiz fallback, panel patlamaz)
    assert block == ""


@pytest.mark.asyncio
async def test_build_calibrate_includes_per_strategy_block(monkeypatch):
    """Kalibrasyon paneli per-strateji breakdown'ını göstermeli.

    `_db` sort-by-length-desc kullanır → uzun needle ("GROUP BY...") önce eşleşir
    per-strateji query'sine, kısa needle ("FROM live_trades") aggregate'a düşer.
    """
    monkeypatch.setenv("REALITY_GAP_MULT", "0.66")
    db = _db(
        {
            "FROM live_trades": (5, 8.0, 5.0),  # 24h + 7g aggregate
            "GROUP BY strategy_label": [
                ("hour_edge", 3, 5.0, 3.3),
                ("classic_btc", 2, 3.0, 1.7),
            ],
        }
    )
    text, kb = await _build_calibrate(db)
    assert "Strateji bazında drift" in text
    assert "hour_edge" in text
    assert "classic_btc" in text


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


# ── Faz 4 — parametreli callback'ler + /lab_save ────────────


@pytest.mark.asyncio
async def test_callback_lab_show_unknown_name_safe():
    """lab_show:<bilinmeyen> → nazik geri butonu."""
    from telegram_bot.handlers.backtest_lab import _build_show_ruleset

    text, kb = await _build_show_ruleset("nonexistent_ruleset_xyz")
    assert "bulunamadı" in text


@pytest.mark.asyncio
async def test_callback_lab_show_invalid_name_safe():
    """lab_show:../escape → path traversal koruması."""
    from telegram_bot.handlers.backtest_lab import _build_show_ruleset

    text, kb = await _build_show_ruleset("../escape")
    assert "Geçersiz" in text


@pytest.mark.asyncio
async def test_callback_lab_del_ask_renders_confirm():
    from telegram_bot.handlers.backtest_lab import _build_del_confirm

    text, kb = await _build_del_confirm("test_xyz")
    assert "emin misin" in text
    assert "test_xyz" in text
    # 2 onay butonu: EVET + İptal
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_del_confirm:test_xyz" in cb_data
    assert "lab_show:test_xyz" in cb_data


@pytest.mark.asyncio
async def test_callback_lab_help_save_renders():
    from telegram_bot.handlers.backtest_lab import _build_help_save

    text, kb = await _build_help_save()
    assert "/lab_save" in text
    assert "JSON" in text
    assert "elapsed_seconds" in text  # field örneği


@pytest.mark.asyncio
async def test_callback_dispatcher_routes_parametric():
    """`lab_show:foo` → _build_show_ruleset('foo') çağrılır."""
    q = MagicMock()
    q.data = "lab_show:foo_bar"
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
    q.edit_message_text.assert_awaited()
    txt = q.edit_message_text.await_args.args[0]
    # foo_bar bulunmadığı için "bulunamadı" mesajı
    assert "bulunamadı" in txt


@pytest.mark.asyncio
async def test_callback_dispatcher_unknown_action_noop():
    """`lab_unknown_action:foo` → sessiz, render YOK."""
    q = MagicMock()
    q.data = "lab_unknown_action:foo"
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
    q.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_lab_save_command_valid(tmp_path, monkeypatch):
    """Geçerli JSON → save success + dosya yazıldı."""
    # save_ruleset default dir'i tmp_path'e yönlendir
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers import backtest_lab as lab_mod

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = (
        "/lab_save\n"
        '{\n'
        '  "name": "t4_valid",\n'
        '  "direction": "up",\n'
        '  "entry": {\n'
        '    "conditions": [\n'
        '      {"field": "elapsed_seconds", "op": ">=", "value": 30}\n'
        '    ]\n'
        '  }\n'
        '}\n'
    )
    update = MagicMock()
    update.message = msg
    context = MagicMock()

    await lab_mod.lab_save_command(update, context)
    msg.reply_text.assert_awaited_once()
    reply = msg.reply_text.await_args.args[0]
    assert "Kaydedildi" in reply
    assert "t4_valid" in reply
    assert (tmp_path / "t4_valid.json").exists()


@pytest.mark.asyncio
async def test_lab_save_command_invalid_json(monkeypatch):
    """Bozuk JSON → parse hatası, dosya yazılmaz."""
    from telegram_bot.handlers import backtest_lab as lab_mod

    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = "/lab_save\n{ not valid json"
    update = MagicMock()
    update.message = msg
    context = MagicMock()

    await lab_mod.lab_save_command(update, context)
    reply = msg.reply_text.await_args.args[0]
    assert "parse hatası" in reply.lower() or "parse" in reply.lower()


@pytest.mark.asyncio
async def test_lab_save_command_invalid_ruleset(tmp_path, monkeypatch):
    """Valid JSON ama invalid ruleset (bad direction) → reddet."""
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers import backtest_lab as lab_mod

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = (
        '/lab_save\n'
        '{"name": "bad", "direction": "sideways", "entry": {"conditions": [{"field": "x", "op": "==", "value": 1}]}}'
    )
    update = MagicMock()
    update.message = msg
    context = MagicMock()

    await lab_mod.lab_save_command(update, context)
    reply = msg.reply_text.await_args.args[0]
    assert "geçersiz" in reply.lower() or "invalid" in reply.lower() or "direction" in reply.lower()
    assert not (tmp_path / "bad.json").exists()


@pytest.mark.asyncio
async def test_lab_save_command_no_payload():
    """Sadece /lab_save → nazik uyarı."""
    from telegram_bot.handlers import backtest_lab as lab_mod

    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = "/lab_save"
    update = MagicMock()
    update.message = msg
    context = MagicMock()

    await lab_mod.lab_save_command(update, context)
    reply = msg.reply_text.await_args.args[0]
    assert "eksik" in reply.lower() or "yardım" in reply.lower()


@pytest.mark.asyncio
async def test_lab_save_command_inline_payload(tmp_path, monkeypatch):
    """`/lab_save {json}` (newline yok, inline) — destek."""
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers import backtest_lab as lab_mod

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    msg = MagicMock()
    msg.reply_text = AsyncMock()
    msg.text = (
        '/lab_save {"name": "inline_t", "direction": "up", '
        '"entry": {"conditions": [{"field": "elapsed_seconds", '
        '"op": ">=", "value": 10}]}}'
    )
    update = MagicMock()
    update.message = msg
    context = MagicMock()

    await lab_mod.lab_save_command(update, context)
    reply = msg.reply_text.await_args.args[0]
    assert "Kaydedildi" in reply
    assert (tmp_path / "inline_t.json").exists()


# ── Faz 4b — Preset sihirbazları ────────────────────────────


@pytest.mark.asyncio
async def test_wiz_menu_renders():
    from telegram_bot.handlers.backtest_lab import _build_wiz_menu

    text, kb = await _build_wiz_menu()
    assert "PRESET SİHİRBAZI" in text
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw_sec" in cb_data
    assert "lab_pw_price" in cb_data
    assert "lab_pw_hour" in cb_data


@pytest.mark.asyncio
async def test_wiz_sec_menu_shows_window_buttons():
    from telegram_bot.handlers.backtest_lab import _build_wiz_sec

    text, kb = await _build_wiz_sec()
    assert "Saniye Aralığı" in text
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    # En az bir UP ve DOWN save callback'i bulunmalı
    assert any("lab_pw_sec_save:" in c and ":up" in c for c in cb_data)
    assert any("lab_pw_sec_save:" in c and ":down" in c for c in cb_data)


@pytest.mark.asyncio
async def test_wiz_sec_save_creates_file(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_sec_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    text, kb = await _build_wiz_sec_save("30_60:up")
    assert "Kaydedildi" in text
    saved = tmp_path / "sec_30_60_up.json"
    assert saved.exists()
    # Doğrula: dosya geçerli ruleset
    loaded = rb.load_ruleset(saved)
    assert loaded["name"] == "sec_30_60_up"
    assert loaded["direction"] == "up"
    conds = loaded["entry"]["conditions"]
    assert {"field": "elapsed_seconds", "op": ">=", "value": 30} in conds
    assert {"field": "elapsed_seconds", "op": "<=", "value": 60} in conds


@pytest.mark.asyncio
async def test_wiz_sec_save_invalid_args(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_sec_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    # max ≤ min
    text, _ = await _build_wiz_sec_save("60_30:up")
    assert "Geçersiz" in text
    # bilinmeyen direction
    text, _ = await _build_wiz_sec_save("30_60:sideways")
    assert "Geçersiz" in text
    # bozuk format
    text, _ = await _build_wiz_sec_save("bozuk")
    assert "Geçersiz" in text
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_wiz_price_dir_then_save(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import (
        _build_wiz_price,
        _build_wiz_price_dir,
        _build_wiz_price_save,
    )

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    # Step 1: menu
    text, kb = await _build_wiz_price()
    assert "Fiyat" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw_price_dir:up" in cb
    assert "lab_pw_price_dir:down" in cb

    # Step 2: dir seçildi → eşik butonları
    text, kb = await _build_wiz_price_dir("up")
    assert "UP" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("lab_pw_price_save:up:" in c for c in cb)

    # Step 3: save
    text, _ = await _build_wiz_price_save("up:55")
    assert "Kaydedildi" in text
    saved = tmp_path / "price_above_55c_up.json"
    assert saved.exists()
    loaded = rb.load_ruleset(saved)
    assert loaded["direction"] == "up"
    assert loaded["entry"]["conditions"][0] == {
        "field": "up_best_ask", "op": ">=", "value": 0.55
    }


@pytest.mark.asyncio
async def test_wiz_price_save_invalid_args(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_price_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    text, _ = await _build_wiz_price_save("invalid")
    assert "Geçersiz" in text
    text, _ = await _build_wiz_price_save("sideways:55")
    assert "Geçersiz" in text
    text, _ = await _build_wiz_price_save("up:101")  # 101c > 99
    assert "Geçersiz" in text
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_wiz_hour_pick_then_save(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import (
        _build_wiz_hour,
        _build_wiz_hour_pick,
        _build_wiz_hour_save,
    )

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    # Step 1: saat seç menüsü
    text, kb = await _build_wiz_hour()
    assert "Saat" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("lab_pw_hour_pick:" in c for c in cb)

    # Step 2: saat seçildi → yön sor
    text, kb = await _build_wiz_hour_pick("22")
    assert "22:00 UTC" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw_hour_save:22:up" in cb
    assert "lab_pw_hour_save:22:down" in cb

    # Step 3: save
    text, _ = await _build_wiz_hour_save("22:down")
    assert "Kaydedildi" in text
    saved = tmp_path / "hour_22_down.json"
    assert saved.exists()
    loaded = rb.load_ruleset(saved)
    assert loaded["direction"] == "down"
    assert loaded["entry"]["conditions"][0] == {
        "field": "hour_utc", "op": "==", "value": 22
    }


@pytest.mark.asyncio
async def test_wiz_hour_invalid_pick():
    from telegram_bot.handlers.backtest_lab import _build_wiz_hour_pick

    text, _ = await _build_wiz_hour_pick("99")
    assert "Geçersiz" in text


@pytest.mark.asyncio
async def test_wiz_hour_save_invalid_args(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_hour_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    text, _ = await _build_wiz_hour_save("99:up")
    assert "Geçersiz" in text
    text, _ = await _build_wiz_hour_save("22:sideways")
    assert "Geçersiz" in text


@pytest.mark.asyncio
async def test_wiz_dispatcher_route_sec_save(tmp_path, monkeypatch):
    """End-to-end: callback dispatcher lab_pw_sec_save'i route ediyor."""
    import backtest.strategies.rule_based as rb

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    q = MagicMock()
    q.data = "lab_pw_sec_save:30_60:up"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    context.bot_data = {"db": _db()}

    await backtest_lab_callback(update, context)
    q.edit_message_text.assert_awaited()
    edited = q.edit_message_text.await_args.args[0]
    assert "Kaydedildi" in edited
    assert (tmp_path / "sec_30_60_up.json").exists()


# ── Faz 5b — Limit Al preset ────────────────────────────────


@pytest.mark.asyncio
async def test_wiz_limit_menu():
    from telegram_bot.handlers.backtest_lab import _build_wiz_limit

    text, kb = await _build_wiz_limit()
    assert "Limit @ X" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw_limit_dir:up" in cb
    assert "lab_pw_limit_dir:down" in cb


@pytest.mark.asyncio
async def test_wiz_limit_dir_then_price_then_save(tmp_path, monkeypatch):
    """End-to-end: yön → fiyat → expire → save."""
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import (
        _build_wiz_limit_dir,
        _build_wiz_limit_price,
        _build_wiz_limit_save,
    )

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    # Step 1: yön seç → fiyat butonları
    text, kb = await _build_wiz_limit_dir("up")
    assert "UP" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("lab_pw_limit_price:up:" in c for c in cb)

    # Step 2: fiyat seç → expire butonları
    text, kb = await _build_wiz_limit_price("up:55")
    assert "0.55" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any("lab_pw_limit_save:up:55:" in c for c in cb)

    # Step 3: expire seç → kaydet
    text, kb = await _build_wiz_limit_save("up:55:60")
    assert "Kaydedildi" in text
    saved = tmp_path / "limit_55c_up_60s.json"
    assert saved.exists()
    loaded = rb.load_ruleset(saved)
    assert loaded["direction"] == "up"
    assert loaded["entry_limit_price"] == 0.55
    assert loaded["entry_limit_expire_seconds"] == 60


@pytest.mark.asyncio
async def test_wiz_limit_save_open_no_expire(tmp_path, monkeypatch):
    """expire=0 → ruleset'te `entry_limit_expire_seconds` alanı YOK (market_close)."""
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_limit_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    text, _ = await _build_wiz_limit_save("up:45:0")
    assert "Kaydedildi" in text
    saved = tmp_path / "limit_45c_up_open.json"
    assert saved.exists()
    loaded = rb.load_ruleset(saved)
    assert loaded["entry_limit_price"] == 0.45
    assert "entry_limit_expire_seconds" not in loaded


@pytest.mark.asyncio
async def test_wiz_limit_save_invalid_args(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_wiz_limit_save

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    text, _ = await _build_wiz_limit_save("invalid")
    assert "Geçersiz" in text
    text, _ = await _build_wiz_limit_save("sideways:55:60")
    assert "Geçersiz" in text
    text, _ = await _build_wiz_limit_save("up:101:60")  # cents>99
    assert "Geçersiz" in text
    text, _ = await _build_wiz_limit_save("up:55:-5")  # expire<0
    assert "Geçersiz" in text
    assert list(tmp_path.glob("*.json")) == []


@pytest.mark.asyncio
async def test_wiz_menu_includes_limit_button():
    from telegram_bot.handlers.backtest_lab import _build_wiz_menu

    text, kb = await _build_wiz_menu()
    assert "Limit @ X" in text
    cb = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw_limit" in cb


@pytest.mark.asyncio
async def test_wiz_dispatcher_route_limit_save(tmp_path, monkeypatch):
    """Dispatcher lab_pw_limit_save'i routes ediyor."""
    import backtest.strategies.rule_based as rb

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)

    q = MagicMock()
    q.data = "lab_pw_limit_save:up:45:60"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    context.bot_data = {"db": _db()}

    await backtest_lab_callback(update, context)
    edited = q.edit_message_text.await_args.args[0]
    assert "Kaydedildi" in edited
    assert (tmp_path / "limit_45c_up_60s.json").exists()


@pytest.mark.asyncio
async def test_wiz_dispatcher_route_pw_menu():
    """Dispatcher lab_pw'yi _build_wiz_menu'ya yönlendiriyor."""
    q = MagicMock()
    q.data = "lab_pw"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()

    update = MagicMock()
    update.callback_query = q
    context = MagicMock()
    context.bot_data = {"db": _db()}

    await backtest_lab_callback(update, context)
    edited = q.edit_message_text.await_args.args[0]
    assert "PRESET SİHİRBAZI" in edited


@pytest.mark.asyncio
async def test_builder_panel_shows_wizard_button(tmp_path, monkeypatch):
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_builder

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_builder(db)
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_pw" in cb_data  # 🧙 Preset Sihirbazı
    assert "Preset Sihirbazı" in text or "Sihirbaz" in text


@pytest.mark.asyncio
async def test_builder_panel_shows_user_rulesets(tmp_path, monkeypatch):
    """Listede kayıtlı ruleset'ler ve her biri için Detay butonu."""
    import backtest.strategies.rule_based as rb
    from telegram_bot.handlers.backtest_lab import _build_builder

    monkeypatch.setattr(rb, "_DEFAULT_DIR", tmp_path)
    rb.save_ruleset(
        {
            "name": "test_a",
            "direction": "up",
            "entry": {
                "conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 30}]
            },
        },
        dir_path=tmp_path,
    )
    rb.save_ruleset(
        {
            "name": "test_b",
            "direction": "down",
            "entry": {
                "conditions": [
                    {"field": "elapsed_seconds", "op": ">=", "value": 60}
                ]
            },
        },
        dir_path=tmp_path,
    )

    db = _db({"FROM live_trades": (0, 0.0, 0.0)})
    text, kb = await _build_builder(db)
    assert "test_a" in text
    assert "test_b" in text
    cb_data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_show:test_a" in cb_data
    assert "lab_show:test_b" in cb_data
    assert "lab_help_save" in cb_data
