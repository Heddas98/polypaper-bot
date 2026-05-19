"""Mode-first tek-kapı redesign — 2026-05-19.

Heddas direktifi: bot ikiye bölünür — /start /dashboard /d /main hepsi
mode-seçim ekranını açar (PAPER vs LIVE). Seçilen mod kendi menü
dünyasına götürür. Mod seçimi = navigasyon; canlı trading ayrı toggle.

Bu testler tek-kapı akışını pin'ler: admin gate, mode-select metni,
PAPER MODE → detaylı dashboard, LIVE MODE → trade istasyonu.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    import pytest_asyncio

    _ASYNC_FIXTURE = pytest_asyncio.fixture
except ImportError:  # pragma: no cover - pytest-asyncio is a dev dep
    _ASYNC_FIXTURE = pytest.fixture

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import telegram_bot.handlers.main_dashboard as md
from telegram_bot.handlers.main_dashboard import (
    _build_main_dashboard_text_kb,
    _get_paper_summary,
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


# ── _get_paper_summary — PAPER MODE özet kartı sorguları ─────────────────


@_ASYNC_FIXTURE
async def paper_db():
    """Gerçek :memory: bot DB + parent user/wallet seed.

    executions/strategies tablolarının user_id/wallet_id NOT NULL FK'leri
    var (PRAGMA foreign_keys=ON) — parent satırlar önce yazılır.
    """
    from db.database import Database

    db = Database(":memory:")
    await db.initialize()
    now = datetime.now(UTC).isoformat()
    await db.conn.execute(
        "INSERT INTO users (id,telegram_id,created_at) VALUES (?,?,?)",
        ("u1", 1, now),
    )
    await db.conn.execute(
        "INSERT INTO wallets (id,user_id,created_at) VALUES (?,?,?)",
        ("w1", "u1", now),
    )
    await db.conn.commit()
    try:
        yield db
    finally:
        await db.close()


def _paper_ctx(db):
    """_get_paper_summary yalnız context.bot_data['engine'].db okur."""
    return SimpleNamespace(bot_data={"engine": SimpleNamespace(db=db)})


async def _add_execution(db, *, eid, status, pnl, closed_at):
    """closed_at=None → henüz settle olmamış açık pozisyon."""
    ts = closed_at or datetime.now(UTC).isoformat()
    await db.conn.execute(
        "INSERT INTO executions (id,user_id,wallet_id,event_slug,direction,"
        "trade_amount,status,pnl,closed_at,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (eid, "u1", "w1", f"slug-{eid}", "up", 1.0, status, pnl, closed_at, ts, ts),
    )


async def _add_strategy(db, *, sid, status):
    now = datetime.now(UTC).isoformat()
    await db.conn.execute(
        "INSERT INTO strategies (id,user_id,wallet_id,status,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?)",
        (sid, "u1", "w1", status, now, now),
    )


@pytest.mark.asyncio
async def test_paper_summary_daily_pnl_sums_today_claimed(paper_db):
    """daily_pnl = bugün settle olan ('claimed') execution pnl toplamı.

    Regression: eski sorgu status='filled' arıyordu — 'filled' geçerli bir
    ExecutionStatus değil (pending/bet_placed/claimed/failed), hiçbir satır
    eşleşmiyordu, kart hep "Bugün PnL: $0" gösteriyordu.
    """
    today = datetime.now(UTC).isoformat()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _add_execution(paper_db, eid="e1", status="claimed", pnl=2.50, closed_at=today)
    await _add_execution(paper_db, eid="e2", status="claimed", pnl=-1.00, closed_at=today)
    # dün settle — date(closed_at) filtresi dışlar
    await _add_execution(paper_db, eid="e3", status="claimed", pnl=99.0, closed_at=yesterday)
    # açık pozisyon — henüz settle değil, sayılmaz
    await _add_execution(paper_db, eid="e4", status="bet_placed", pnl=0.0, closed_at=None)
    await paper_db.conn.commit()

    summary = await _get_paper_summary(_paper_ctx(paper_db))

    assert summary["daily_pnl"] == pytest.approx(1.50)
    assert summary["pnl_emoji"] == "🟢"


@pytest.mark.asyncio
async def test_paper_summary_daily_pnl_negative_red_emoji(paper_db):
    """Bugünkü net negatifse kart 🔴 gösterir."""
    today = datetime.now(UTC).isoformat()
    await _add_execution(paper_db, eid="e1", status="claimed", pnl=-3.0, closed_at=today)
    await paper_db.conn.commit()

    summary = await _get_paper_summary(_paper_ctx(paper_db))

    assert summary["daily_pnl"] == pytest.approx(-3.0)
    assert summary["pnl_emoji"] == "🔴"


@pytest.mark.asyncio
async def test_paper_summary_open_strategies_counts_active(paper_db):
    """open_strategies = status='active' strateji sayısı.

    Regression: eski sorgu status='started' arıyordu — gerçek aktif statü
    'active' (db/migrations.py status'leri 'active'e normalize eder),
    hiçbir satır eşleşmiyordu, kart hep "0 aktif strateji" gösteriyordu.
    """
    await _add_strategy(paper_db, sid="s1", status="active")
    await _add_strategy(paper_db, sid="s2", status="active")
    await _add_strategy(paper_db, sid="s3", status="stopped")
    await paper_db.conn.commit()

    summary = await _get_paper_summary(_paper_ctx(paper_db))

    assert summary["open_strategies"] == 2


@pytest.mark.asyncio
async def test_paper_summary_no_engine_safe():
    """engine yoksa özet kartı çökmez — güvenli default döner."""
    summary = await _get_paper_summary(SimpleNamespace(bot_data={}))

    assert summary["daily_pnl"] == 0.0
    assert summary["open_strategies"] == 0
