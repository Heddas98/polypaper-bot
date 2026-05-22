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
    # 1h hâlâ kapalı → çalıştır butonu var
    assert "lab_paper_on:BTC:1h" in cbs
