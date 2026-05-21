"""Candle-based market-level backtest + martingale (2026-05-21).

Heddas direktifi: candles_ext (Binance OHLC) backtest'te kullanılmıyordu.
Bu testler candle motorunu pin'ler — saf PnL matematiği, streak, yön
kararı, flat + martingale simülasyonu. DB mock (candles_ext rows).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backtest.candle_runner import (
    CandleBacktestRunner,
    CandleMarket,
    CandleRunConfig,
    _max_streak,
    _trade_pnl,
    streak_distribution,
)

# ── DB mock (candles_ext rows) ──────────────────────────────


class _Conn:
    def __init__(self, rows):
        self._rows = rows

    async def execute_fetchall(self, query, params=None):
        return self._rows


def _db(rows):
    return SimpleNamespace(conn=_Conn(rows))


def _candles_5m_alternating(n=20, start_ts=1_700_000_000):
    """n adet 5m candle — sırayla up/down (open_ts saniye)."""
    rows = []
    price = 100.0
    for i in range(n):
        op = price
        cl = price + 1 if i % 2 == 0 else price - 1  # even=up, odd=down
        rows.append((start_ts + i * 300, op, cl))
        price = cl
    return rows


# ── _trade_pnl ──────────────────────────────────────────────


def test_trade_pnl_win():
    # entry 0.50, bet 1 → 2 share. Win → payout 2.0 − 1 − fee
    pnl = _trade_pnl("up", "up", bet=1.0, entry=0.50, fee_rate=0.07)
    # fee = 0.07 × 0.5 × 1 = 0.035; pnl = 2.0 − 1 − 0.035 = 0.965
    assert pnl == pytest.approx(0.965, abs=0.001)


def test_trade_pnl_loss():
    pnl = _trade_pnl("up", "down", bet=1.0, entry=0.50, fee_rate=0.07)
    # loss = −(1 + 0.035) = −1.035
    assert pnl == pytest.approx(-1.035, abs=0.001)


def test_trade_pnl_invalid_entry():
    assert _trade_pnl("up", "up", 1.0, 0.0, 0.07) == 0.0
    assert _trade_pnl("up", "up", 1.0, 1.0, 0.07) == 0.0


# ── streak helpers ──────────────────────────────────────────


def test_max_streak():
    assert _max_streak(["up", "up", "up", "down", "down"]) == 3
    assert _max_streak(["up", "down", "up", "down"]) == 1
    assert _max_streak([]) == 0
    assert _max_streak(["up"]) == 1


def test_streak_distribution():
    dist = streak_distribution(["up", "up", "down", "up", "up", "up"])
    # 2 ardışık up (1 kez), 1 down (1 kez), 3 up (1 kez)
    assert dist.get(2) == 1
    assert dist.get(1) == 1
    assert dist.get(3) == 1


# ── _decide_direction ───────────────────────────────────────


def _market(direction="up", hour=12, weekday=0):
    return CandleMarket(
        ts=1_700_000_000, open_price=100, close_price=101,
        direction=direction, hour_utc=hour, weekday=weekday,
    )


def test_decide_direction_fixed():
    r = CandleBacktestRunner(_db([]))
    assert r._decide_direction(_market(), None, CandleRunConfig(bet_direction="up")) == "up"
    assert r._decide_direction(_market(), None, CandleRunConfig(bet_direction="down")) == "down"


def test_decide_direction_follow_fade():
    r = CandleBacktestRunner(_db([]))
    assert r._decide_direction(_market(), "up", CandleRunConfig(bet_direction="follow_trend")) == "up"
    assert r._decide_direction(_market(), "up", CandleRunConfig(bet_direction="fade_trend")) == "down"


def test_decide_direction_hour_filter():
    r = CandleBacktestRunner(_db([]))
    cfg = CandleRunConfig(bet_direction="up", hour_filter=[22, 23])
    assert r._decide_direction(_market(hour=22), None, cfg) == "up"
    assert r._decide_direction(_market(hour=12), None, cfg) is None  # filtre dışı


def test_decide_direction_weekday_filter():
    r = CandleBacktestRunner(_db([]))
    cfg = CandleRunConfig(bet_direction="up", weekday_filter=[0, 1, 2, 3, 4])
    assert r._decide_direction(_market(weekday=2), None, cfg) == "up"  # Çarşamba
    assert r._decide_direction(_market(weekday=5), None, cfg) is None  # Cmt


# ── E2E: flat + martingale (mock candles) ───────────────────


@pytest.mark.asyncio
async def test_run_flat_alternating():
    """Alternating up/down, hep UP al → WR ~%50."""
    db = _db(_candles_5m_alternating(20))
    s = await CandleBacktestRunner(db).run(
        CandleRunConfig(asset="BTC", timeframe="5m", bet_direction="up", last_n=0)
    )
    assert s.n_markets == 20
    assert s.n_trades == 20
    # Alternating → 10 up 10 down, hep up al → 10W/10L
    assert s.wins == 10
    assert s.losses == 10
    assert s.win_rate == pytest.approx(50.0, abs=0.1)


@pytest.mark.asyncio
async def test_run_martingale_max_level():
    """Martingale max_levels — bet katlanır, bust sayılır."""
    db = _db(_candles_5m_alternating(20))
    s = await CandleBacktestRunner(db).run(
        CandleRunConfig(
            asset="BTC", timeframe="5m", bet_direction="up",
            martingale=True, max_levels=3, base_bet=1.0, last_n=0,
        )
    )
    assert s.n_trades == 20
    # max_bet base'ten büyük olmalı (katlama oldu)
    assert s.max_bet >= 2.0
    assert s.max_level_reached >= 2


@pytest.mark.asyncio
async def test_run_no_data():
    db = _db([])
    s = await CandleBacktestRunner(db).run(CandleRunConfig(asset="DOGE"))
    assert s.n_markets == 0
    assert "yok" in s.note.lower()


@pytest.mark.asyncio
async def test_run_tf_aggregate_15m():
    """15m = 3×5m aggregate. 30 candle → 10 market."""
    db = _db(_candles_5m_alternating(30))
    s = await CandleBacktestRunner(db).run(
        CandleRunConfig(asset="BTC", timeframe="15m", bet_direction="up", last_n=0)
    )
    assert s.n_markets == 10  # 30 / 3


@pytest.mark.asyncio
async def test_run_ms_timestamp_normalize():
    """open_ts ms cinsindeyse saniyeye normalize (OSError önler)."""
    rows = [(1_700_000_000_000 + i * 300_000, 100.0, 101.0) for i in range(6)]
    db = _db(rows)
    s = await CandleBacktestRunner(db).run(
        CandleRunConfig(asset="BTC", timeframe="5m", bet_direction="up", last_n=0)
    )
    assert s.n_markets == 6  # ms ts patlamadı
