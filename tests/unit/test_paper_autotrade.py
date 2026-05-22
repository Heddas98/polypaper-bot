"""Paper Auto-Trade panel — rev↑ martingale aktivasyon (2026-05-22).

Heddas: LAB rev↑ edge'ini PAPER modda otomatik çalıştır. Bu testler
aktivasyon/durdurma DB akışını + panel guard'larını pin'ler. Sahte DB
(gerçek db.models.Strategy ile) kullanır — canlı bot kilidi yok.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from db.models import (
    Asset,
    Direction,
    Strategy,
    StrategyStatus,
    Timeframe,
)
from telegram_bot.handlers.backtest_lab import (
    _activate_paper_strategy,
    _build_paper_strategy,
    _resolve_user_wallet,
    _stop_paper_strategy,
)


class _FakeDB:
    def __init__(self, has_user=True, has_wallet=True):
        self.conn = object()  # truthy
        self._user = SimpleNamespace(id="u1") if has_user else None
        self._wallet = SimpleNamespace(id="w1") if has_wallet else None
        self.strategies: list[Strategy] = []
        self.created: list[Strategy] = []
        self.status_updates: list[tuple] = []
        self.stats: list[dict] = []  # get_per_strategy_stats payload

    async def get_per_strategy_stats(self, user_id):
        return list(self.stats)

    async def get_user_by_telegram_id(self, tid):
        return self._user

    async def get_active_wallet(self, uid):
        return self._wallet

    async def get_strategies_by_user(self, uid, wid=None):
        return list(self.strategies)

    async def create_strategy(self, strat):
        self.created.append(strat)
        self.strategies.append(strat)
        return strat

    async def update_strategy_status(self, sid, status):
        self.status_updates.append((sid, status))
        for s in self.strategies:
            if s.id == sid:
                s.status = status

    async def get_strategy(self, sid):
        return next((s for s in self.strategies if s.id == sid), None)


def _mart(asset="BTC", tf=Timeframe.M5, status=StrategyStatus.ACTIVE, stype="martingale"):
    return Strategy(
        user_id="u1", wallet_id="w1", label=f"rev↑ {asset}",
        asset=Asset(asset), timeframe=tf, direction=Direction.UP,
        trade_amount=1.0, strategy_type=stype, status=status,
    )


# ── guards ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_user_wallet_no_db():
    assert await _resolve_user_wallet(None, 123) == (None, None)
    assert await _resolve_user_wallet(_FakeDB(), 0) == (None, None)  # tg_id 0


@pytest.mark.asyncio
async def test_build_paper_no_user():
    db = _FakeDB(has_user=False)
    text, kb = await _build_paper_strategy(db, 123)
    assert "bulunamadı" in text
    assert "PAPER" in text


@pytest.mark.asyncio
async def test_activate_invalid_market():
    db = _FakeDB()
    text, kb = await _activate_paper_strategy(db, 123, "DOGE", "5m")
    assert "Geçersiz" in text
    assert db.created == []


def test_is_tradeable_matrix():
    """Discovery matrix: 5m=BTC · 15m=4 coin · 1h=BTC."""
    from telegram_bot.handlers.backtest_lab import _is_tradeable

    assert _is_tradeable("BTC", "5m") is True
    assert _is_tradeable("ETH", "5m") is False   # 5m=BTC only
    assert _is_tradeable("ETH", "15m") is True
    assert _is_tradeable("SOL", "15m") is True
    assert _is_tradeable("SOL", "1h") is False   # 1h=BTC only
    assert _is_tradeable("BTC", "1h") is True


@pytest.mark.asyncio
async def test_activate_rejects_untradeable():
    """Bot'un taramadığı (asset,tf) → ölü strateji oluşturulmaz."""
    db = _FakeDB()
    text, kb = await _activate_paper_strategy(db, 123, "ETH", "5m")  # 5m=BTC only
    assert "taramıyor" in text
    assert db.created == []


# ── activation happy path ───────────────────────────────────


@pytest.mark.asyncio
async def test_activate_creates_martingale_row():
    db = _FakeDB()
    await _activate_paper_strategy(db, 123, "BTC", "5m")
    assert len(db.created) == 1
    s = db.created[0]
    assert s.strategy_type == "martingale"
    assert s.direction == Direction.UP
    assert s.asset == Asset.BTC
    assert s.timeframe == Timeframe.M5
    assert s.trade_amount == 1.0
    assert s.max_executions_per_event == 1
    assert s.max_entry_slippage is None  # SLIP gate kapalı
    assert s.odds_threshold == 0.50      # truthy (motor 0/None reddediyor)
    # post-create ACTIVE damgası
    assert any(st == StrategyStatus.ACTIVE for _, st in db.status_updates)


@pytest.mark.asyncio
async def test_activate_1h_timeframe():
    db = _FakeDB()
    await _activate_paper_strategy(db, 123, "BTC", "1h")
    assert db.created[0].timeframe == Timeframe.H1


@pytest.mark.asyncio
async def test_activate_eth_15m_rev_down():
    """ETH 15m rev↓ (pump-fade) — yeni edge, direction=DOWN."""
    db = _FakeDB()
    await _activate_paper_strategy(db, 123, "ETH", "15m", "down")
    s = db.created[0]
    assert s.asset == Asset.ETH
    assert s.timeframe == Timeframe.M15
    assert s.direction == Direction.DOWN
    assert s.strategy_type == "martingale"
    assert "rev↓" in (s.label or "")


@pytest.mark.asyncio
async def test_activate_same_tf_both_directions_no_collision():
    """Aynı (asset,tf) farklı yön → ayrı satır (dedup yön-duyarlı)."""
    db = _FakeDB()
    await _activate_paper_strategy(db, 123, "ETH", "15m", "up")
    await _activate_paper_strategy(db, 123, "ETH", "15m", "down")
    assert len(db.created) == 2  # up + down ayrı


@pytest.mark.asyncio
async def test_activate_reactivates_existing_no_duplicate():
    db = _FakeDB()
    stopped = _mart(tf=Timeframe.M5, status=StrategyStatus.STOPPED)
    db.strategies.append(stopped)
    await _activate_paper_strategy(db, 123, "BTC", "5m")
    # yeni satır oluşturulmamalı — mevcut yeniden aktive edilir
    assert db.created == []
    assert (stopped.id, StrategyStatus.ACTIVE) in db.status_updates


@pytest.mark.asyncio
async def test_activate_already_active_noop():
    db = _FakeDB()
    db.strategies.append(_mart(tf=Timeframe.M5, status=StrategyStatus.ACTIVE))
    await _activate_paper_strategy(db, 123, "BTC", "5m")
    assert db.created == []  # zaten aktif → yeni yok


# ── stop ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_martingale():
    db = _FakeDB()
    s = _mart(status=StrategyStatus.ACTIVE)
    db.strategies.append(s)
    await _stop_paper_strategy(db, 123, s.id)
    assert (s.id, StrategyStatus.STOPPED) in db.status_updates


@pytest.mark.asyncio
async def test_stop_ignores_non_martingale():
    """Yanlış sid koruması — martingale olmayan strateji durdurulmaz."""
    db = _FakeDB()
    fusion = _mart(stype="fusion", status=StrategyStatus.ACTIVE)
    db.strategies.append(fusion)
    await _stop_paper_strategy(db, 123, fusion.id)
    assert db.status_updates == []  # dokunulmadı


# ── panel render with active strategy ───────────────────────


@pytest.mark.asyncio
async def test_panel_shows_active_and_stop_button():
    db = _FakeDB()
    db.strategies.append(_mart(tf=Timeframe.M5, status=StrategyStatus.ACTIVE))
    text, kb = await _build_paper_strategy(db, 123)
    assert "aktif" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(c.startswith("lab_paper_off:") for c in cbs)  # durdur butonu
    # 1h hâlâ kapalı → çalıştır butonu var (yön ekli)
    assert "lab_paper_on:BTC:1h:up" in cbs
    # ETH 15m rev↓ (pump-fade) + SOL/XRP 15m rev↑ konfigürasyonları
    assert "lab_paper_on:ETH:15m:down" in cbs
    assert "lab_paper_on:SOL:15m:up" in cbs
    assert "lab_paper_on:XRP:15m:up" in cbs
    # Adım 4: Canlıya Geçiş giriş butonu
    assert "lab_live" in cbs


# ── Adım 4: Canlıya Geçiş (live promote) paneli ─────────────


def _stat(sid="s1", label="rev↑ martingale BTC 5m", completed=10, wins=5,
          losses=5, pnl=-2.0, stype="martingale"):
    return {
        "id": sid, "strategy_type": stype, "label": label,
        "asset": "BTC", "timeframe": "5m",
        "completed": completed, "wins": wins, "losses": losses,
        "realized_pnl": pnl,
    }


@pytest.mark.asyncio
async def test_live_promote_not_ready_no_button(monkeypatch, tmp_path):
    import core.live_strategies as ls
    from telegram_bot.handlers.backtest_lab import _build_live_promote

    monkeypatch.setattr(ls, "_LIVE_CANDIDATES_PATH", tmp_path / "c.json")
    db = _FakeDB()
    db.stats = [_stat(completed=10, wins=5, losses=5, pnl=-2.0)]
    text, kb = await _build_live_promote(db, 123)
    assert "Hazır değil" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert not any(c.startswith("lab_live_cand:") for c in cbs)  # aday butonu YOK
    assert "GERÇEK PARA" in text  # go-live uyarısı


@pytest.mark.asyncio
async def test_live_promote_ready_shows_button(monkeypatch, tmp_path):
    import core.live_strategies as ls
    from telegram_bot.handlers.backtest_lab import _build_live_promote

    monkeypatch.setattr(ls, "_LIVE_CANDIDATES_PATH", tmp_path / "c.json")
    db = _FakeDB()
    db.stats = [_stat(completed=150, wins=100, losses=40, pnl=20.0)]
    text, kb = await _build_live_promote(db, 123)
    assert "Canlıya hazır" in text
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "lab_live_cand:s1" in cbs


@pytest.mark.asyncio
async def test_mark_candidate_gated_by_readiness(monkeypatch, tmp_path):
    """Hazır olmayan strateji aday İŞARETLENMEZ (server-side re-validate)."""
    import core.live_strategies as ls
    from telegram_bot.handlers.backtest_lab import _mark_live_candidate_action

    p = tmp_path / "c.json"
    monkeypatch.setattr(ls, "_LIVE_CANDIDATES_PATH", p)
    db = _FakeDB()
    db.stats = [_stat(completed=10, wins=5, losses=5, pnl=-2.0)]  # hazır değil
    await _mark_live_candidate_action(db, 123, "s1")
    assert ls.load_live_candidates(p) == {}  # işaretlenmedi


@pytest.mark.asyncio
async def test_mark_candidate_ready_marks(monkeypatch, tmp_path):
    import core.live_strategies as ls
    from telegram_bot.handlers.backtest_lab import _mark_live_candidate_action

    p = tmp_path / "c.json"
    monkeypatch.setattr(ls, "_LIVE_CANDIDATES_PATH", p)
    db = _FakeDB()
    db.stats = [_stat(label="rev↑ martingale BTC 5m",
                      completed=150, wins=100, losses=40, pnl=20.0)]
    await _mark_live_candidate_action(db, 123, "s1")
    cands = ls.load_live_candidates(p)
    assert "rev↑ martingale BTC 5m" in cands
