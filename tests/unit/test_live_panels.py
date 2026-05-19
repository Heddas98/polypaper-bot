"""`/live` trade istasyonu panelleri — Faz 2B + 3 (2026-05-19).

Heddas direktifi "trade istasyonu": /live kokpitine bağlı paneller —
📡 Piyasa Tara, 🛡 Guards, ⚙️ Risk, 📈 Performans. Bu testler panel
builder'larını mock engine ile pin'ler (Telegram I/O yok — saf render).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, TelegramError

from telegram_bot.handlers.live_handler import (
    _build_market_scan,
    _build_performance,
    _build_risk,
    _live_pnl_detail_block,
    _panel_nav_kb,
    _safe_edit,
    _short_market,
)

# ── _panel_nav_kb ────────────────────────────────────────────────────────


def test_panel_nav_kb_structure():
    kb = _panel_nav_kb("live_risk")
    assert isinstance(kb, InlineKeyboardMarkup)
    row = kb.inline_keyboard[0]
    assert len(row) == 2
    assert row[0].callback_data == "live_main"
    assert row[1].callback_data == "live_risk"  # refresh = aynı panel


# ── ⚙️ Risk paneli ───────────────────────────────────────────────────────


def _risk_engine(halted: bool = False, streak: int = 2):
    risk = SimpleNamespace(
        limits=SimpleNamespace(max_loss_streak=10),
        get_status=lambda: {
            "halted": halted,
            "halt_reason": "Daily loss limit" if halted else "",
            "open_positions": 1,
            "total_exposure": 3.2,
            "daily_pnl": -1.5,
            "daily_trades": 4,
            "loss_streak": streak,
            "limits": {
                "max_position": 1.0,
                "max_positions": 5,
                "max_exposure": 10.0,
                "max_daily_loss": 5.0,
                "balance_floor": 2.0,
            },
            "tiered_limits": {
                "per_asset": {
                    "BTC": {"limit": 5.0, "current": 3.2},
                    "ETH": {"limit": 5.0, "current": 0.0},
                },
                "per_market": {"limit": 2.0, "markets": {"a": 1.0, "b": 2.2}},
            },
        },
    )
    return SimpleNamespace(risk=risk)


@pytest.mark.asyncio
async def test_build_risk_normal():
    txt = await _build_risk(_risk_engine())
    assert "RİSK YÖNETİCİSİ" in txt
    assert "✅ Aktif" in txt
    assert "1 / 5" in txt  # açık pozisyon / max
    assert "$3.20 / $10.00" in txt  # maruziyet
    assert "2 / 10" in txt  # loss-streak
    assert "BTC: $3.20 / $5.00" in txt  # asset bazlı
    assert "2 market" in txt  # per-market sayısı


@pytest.mark.asyncio
async def test_build_risk_halted():
    txt = await _build_risk(_risk_engine(halted=True))
    assert "HALTED" in txt
    assert "Daily loss limit" in txt


@pytest.mark.asyncio
async def test_build_risk_missing_manager():
    """engine.risk None → panel yine açılır (crash etmez)."""
    txt = await _build_risk(SimpleNamespace(risk=None))
    assert "RİSK YÖNETİCİSİ" in txt
    assert "bağlı değil" in txt


@pytest.mark.asyncio
async def test_build_risk_get_status_raises():
    """get_status() exception → güvenli hata mesajı, panel açılır."""
    def _boom():
        raise RuntimeError("db down")

    engine = SimpleNamespace(
        risk=SimpleNamespace(get_status=_boom, limits=SimpleNamespace())
    )
    txt = await _build_risk(engine)
    assert "RİSK YÖNETİCİSİ" in txt
    assert "db down" not in txt  # ham hata sızdırılmaz (M-01 doktrini)


# ── 📡 Piyasa Tara paneli ────────────────────────────────────────────────


def _scan_engine(markets: dict | None = None, ws_connected: bool = True):
    if markets is None:
        markets = {
            "BTC_5m": [{"slug": "b5"}],
            "BTC_1h": [{"slug": "b1"}],
            "BTC_15m": [{"slug": "b15"}],
            "ETH_5m": [{"slug": "e5"}],
        }
    scanner = SimpleNamespace(
        active_markets=markets,
        last_scan=None,
        ws=SimpleNamespace(is_connected=ws_connected),
        get_current_odds=lambda slug: {"up_odds": 0.53, "down_odds": 0.47},
    )
    return SimpleNamespace(scanner=scanner)


@pytest.mark.asyncio
async def test_build_market_scan_normal():
    txt = await _build_market_scan(_scan_engine())
    assert "PİYASA TARAMA" in txt
    assert "4 market" in txt
    assert "Up <b>0.53</b>" in txt
    assert "🟢 WS" in txt


@pytest.mark.asyncio
async def test_build_market_scan_tf_order():
    """Timeframe doğal sırada (5m→15m→1h) — alfabetik '1h<5m' DEĞİL."""
    txt = await _build_market_scan(_scan_engine())
    i5 = txt.index("5m")
    i15 = txt.index("15m")
    i1h = txt.index("1h")
    assert i5 < i15 < i1h


@pytest.mark.asyncio
async def test_build_market_scan_empty():
    txt = await _build_market_scan(_scan_engine(markets={}))
    assert "PİYASA TARAMA" in txt
    assert "Aktif market yok" in txt


@pytest.mark.asyncio
async def test_build_market_scan_missing_scanner():
    txt = await _build_market_scan(SimpleNamespace(scanner=None))
    assert "PİYASA TARAMA" in txt
    assert "bağlı değil" in txt


@pytest.mark.asyncio
async def test_build_market_scan_odds_missing():
    """odds None → 'odds yok' satırı, panel crash etmez."""
    engine = _scan_engine()
    engine.scanner.get_current_odds = lambda slug: None
    txt = await _build_market_scan(engine)
    assert "odds yok" in txt


# ── 📈 Performans paneli (Faz 3) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_performance_merges_sections(monkeypatch):
    """PnL bloğu + paper×real + geçmiş tek panelde birleşir."""
    import data.polymarket_portfolio as pp

    snap = {
        "activity": [
            {
                "type": "TRADE",
                "condition_id": "0xA",
                "timestamp": 9_999_999_999,
                "price": 0.5,
                "size": 2.0,
                "usdc_size": 1.05,
            },
            {
                "type": "REDEEM",
                "condition_id": "0xA",
                "timestamp": 9_999_999_999,
                "usdc_size": 2.0,
            },
        ]
    }
    monkeypatch.setattr(pp, "read_cached_snapshot", AsyncMock(return_value=snap))

    engine = MagicMock()
    engine.live.get_comparison = AsyncMock(
        return_value={
            "total_trades": 1,
            "live_pnl": 0.95,
            "paper_pnl_equiv": 1.0,
            "wr": 100,
            "recent": [],
        }
    )
    engine.live.load_trade_history = AsyncMock(return_value=[])
    engine.live.get_status = MagicMock(return_value={"total_pnl": 0.0, "remaining": 5.0})

    txt = await _build_performance(engine, MagicMock())
    assert "PERFORMANS" in txt
    assert "BOT LIVE PnL" in txt  # aggregate blok
    assert "İŞLEM DÖKÜMÜ" in txt  # on-chain per-market detay (D1 fix)
    assert "Paper vs Real" in txt  # kalibrasyon
    # D1: eski DB-kaynaklı "Live Trade Gecmisi" kaldırıldı — on-chain
    # işlem dökümü gerçek geçmiş; çelişkili "geçmiş yok" satırı yok
    assert "Live Trade Gecmisi" not in txt


@pytest.mark.asyncio
async def test_build_performance_no_snapshot(monkeypatch):
    """Snapshot yok → PnL 'veri bekleniyor', panel yine açılır."""
    import data.polymarket_portfolio as pp

    monkeypatch.setattr(pp, "read_cached_snapshot", AsyncMock(return_value=None))
    engine = MagicMock()
    engine.live.get_comparison = AsyncMock(return_value={"error": "no data"})
    engine.live.load_trade_history = AsyncMock(return_value=[])
    engine.live.get_status = MagicMock(return_value={"total_pnl": 0.0, "remaining": 5.0})
    txt = await _build_performance(engine, MagicMock())
    assert "PERFORMANS" in txt
    assert "veri bekleniyor" in txt


# ── 🛡 Guards paneli (build_guards_text) ─────────────────────────────────


def test_build_guards_text_returns_snapshot():
    """build_guards_text — /lg ve /live panelinin ortak builder'ı.

    engine=None ile çağrılır → G1/G2/G5 gerçek fallback kodu çalışır
    (KillSwitch, _get_live_budget, auto_optimizer helper'ları).
    """
    from telegram_bot.handlers.live_guards_handler import build_guards_text

    ctx = SimpleNamespace(bot_data={"engine": None})
    txt = build_guards_text(ctx)
    assert isinstance(txt, str)
    assert "Live Guards" in txt
    assert "G1 Kill Switch" in txt
    assert "G6 WS Stale" in txt
    assert len(txt) <= 3950  # Telegram 4096 limitine karşı truncate


# ── _safe_edit — B1 audit (refresh "message not modified" duplicate fix) ──


@pytest.mark.asyncio
async def test_safe_edit_success_no_reply():
    """edit başarılı → reply_text ÇAĞRILMAZ (duplicate mesaj yok)."""
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    await _safe_edit(q, "txt", None)
    q.edit_message_text.assert_called_once()
    q.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_safe_edit_not_modified_swallowed():
    """B1 çekirdek: 'message is not modified' → reply_text ÇAĞRILMAZ.

    Eski desen bunu reply_text ile yanıtlayıp her gereksiz "🔄 Yenile"de
    duplicate panel üretiyordu. Artık sessizce yutulur.
    """
    q = MagicMock()
    q.edit_message_text = AsyncMock(
        side_effect=BadRequest("Message is not modified: text/markup same")
    )
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    await _safe_edit(q, "txt", None)
    q.message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_safe_edit_real_badrequest_falls_back():
    """Gerçek BadRequest (not-modified DEĞİL) → reply_text fallback."""
    q = MagicMock()
    q.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    await _safe_edit(q, "txt", None)
    q.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_safe_edit_telegram_error_falls_back():
    """TelegramError → reply_text fallback (panel yine de gösterilir)."""
    q = MagicMock()
    q.edit_message_text = AsyncMock(side_effect=TelegramError("network glitch"))
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    await _safe_edit(q, "txt", None)
    q.message.reply_text.assert_called_once()


# ── İşlem dökümü — _short_market + _live_pnl_detail_block ────────────────


def test_short_market_parses_coin_and_time():
    s = _short_market("Bitcoin Up or Down - May 18, 2:55PM-3:00PM ET")
    assert s.startswith("BTC")
    assert "2:55PM" in s
    assert " ET" not in s
    assert len(s) <= 30


def test_short_market_empty():
    assert _short_market("") == "?"


def test_live_pnl_detail_block_empty():
    txt = _live_pnl_detail_block([])
    assert "İŞLEM DÖKÜMÜ" in txt
    assert "henüz bot trade" in txt


def test_live_pnl_detail_block_renders_trades():
    """per_market satırları — tarih/market/outcome/giriş/net hepsi görünür."""
    pm = [
        {
            "ts": 1779130494,
            "result": "win",
            "title": "Bitcoin Up or Down - May 18, 2:55PM-3:00PM ET",
            "outcome": "Up",
            "entry_price": 0.75,
            "cost": 1.02,
            "payout": 1.33,
            "net": 0.31,
        },
        {
            "ts": 1779000000,
            "result": "loss",
            "title": "Bitcoin Up or Down - May 17, 9:00AM ET",
            "outcome": "Down",
            "entry_price": 0.55,
            "cost": 1.01,
            "payout": 0.0,
            "net": -1.01,
        },
    ]
    txt = _live_pnl_detail_block(pm)
    assert "İŞLEM DÖKÜMÜ" in txt
    assert "🟢" in txt and "🔴" in txt  # win + loss ikonları
    assert "Up" in txt and "Down" in txt
    assert "+$0.31" in txt
    assert "-$1.01" in txt


def test_live_pnl_detail_block_caps_at_limit():
    pm = [
        {"ts": i, "result": "win", "title": f"M{i}", "outcome": "Up",
         "entry_price": 0.5, "cost": 1.0, "payout": 1.5, "net": 0.5}
        for i in range(50)
    ]
    txt = _live_pnl_detail_block(pm, limit=12)
    # 12 satır başlık ikonu (her market 1 🟢) — 50 değil
    assert txt.count("🟢") == 12
