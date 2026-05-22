"""Gerçekçi backtest fill testleri (2026-05-22 Heddas direktifi).

backtest/runner.py artık naif best_ask/slippage=0 yerine FillSimulator
(REAL_ORDERBOOK VWAP derinlik yürüyüşü) kullanıyor. Kanıt:
  - _parse_levels: kayıt formatı [{"price","size"}] → [[p,s]] + depth.
  - İnce defter → fill best_ask'ın ÜZERİNDE (slippage > 0), shares azalır.
  - Derin defter → slippage ~0.
  - L2 yoksa → MIDPOINT+spread fallback (yine fill olur).
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from backtest.runner import BacktestRunner, RunConfig, _parse_levels


# ── _parse_levels birim testleri ──────────────────────────────────────
def test_parse_levels_dict_to_pairs():
    levels, depth = _parse_levels(json.dumps([{"price": 0.50, "size": 4}, {"price": 0.52, "size": 100}]))
    assert levels == [[0.50, 4.0], [0.52, 100.0]]
    assert depth == pytest.approx(0.50 * 4 + 0.52 * 100)  # 54.0


def test_parse_levels_bad_input():
    assert _parse_levels(None) == ([], 0.0)
    assert _parse_levels("") == ([], 0.0)
    assert _parse_levels("not json") == ([], 0.0)
    assert _parse_levels("[]") == ([], 0.0)
    # bozuk eleman atlanır, geçerli kalır
    levels, depth = _parse_levels(json.dumps([{"price": 0.5, "size": 2}, {"bad": 1}, {"price": -1, "size": 5}]))
    assert levels == [[0.5, 2.0]]
    assert depth == pytest.approx(1.0)


# ── E2E yardımcıları ──────────────────────────────────────────────────
async def _db_with_market(asks_json: str):
    """Tek market (C1/UP1, BTC 5m) — 5 snapshot, entry'de verilen asks_json.

    İlk snapshot entry (elapsed>=0 sinyali). Son snapshot UP kazanır
    (up_best_ask yüksek → derived down düşük).
    """
    from db.database import Database

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.unlink(tmp.name)
    db = Database(tmp.name)
    await db.initialize()

    bids = json.dumps([{"price": 0.48, "size": 100}])
    # 5 snapshot; entry (ts=1000) verilen asks; son (ts=5000) UP kazanan
    rows = [
        (1000, "UP1", "C1", "BTC", "5m", "btc-5m-test", 0.48, 0.50, 0.49, 0.02, bids, asks_json, ""),
        (2000, "UP1", "C1", "BTC", "5m", "btc-5m-test", 0.50, 0.52, 0.51, 0.02, bids, asks_json, ""),
        (3000, "UP1", "C1", "BTC", "5m", "btc-5m-test", 0.60, 0.62, 0.61, 0.02, bids, asks_json, ""),
        (4000, "UP1", "C1", "BTC", "5m", "btc-5m-test", 0.80, 0.82, 0.81, 0.02, bids, asks_json, ""),
        (5000, "UP1", "C1", "BTC", "5m", "btc-5m-test", 0.93, 0.95, 0.94, 0.02, bids, asks_json, ""),
    ]
    await db.conn.executemany(
        "INSERT INTO ob_snapshots (ts_ms, asset_id, condition_id, asset, timeframe, slug, "
        "best_bid, best_ask, mid_price, spread, bids_json, asks_json, hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    await db.conn.commit()
    return db, tmp.name


def _up_ruleset():
    """elapsed>=0 → ilk snapshot'ta UP sinyali."""
    return {
        "name": "always_up",
        "direction": "up",
        "entry": {"conditions": [{"field": "elapsed_seconds", "op": ">=", "value": 0}]},
    }


async def _run(asks_json: str, fill_mode: str = "real_orderbook", amount: float = 5.0):
    db, path = await _db_with_market(asks_json)
    try:
        runner = BacktestRunner(db)
        cfg = RunConfig(
            asset="BTC", timeframe="5m", strategy_name="rule_based",
            strategy_params=_up_ruleset(), trade_amount=amount,
            last_n=10, min_snapshots=4, fill_mode=fill_mode,
        )
        summary = await runner.run(cfg)
        trades = list(runner.portfolio.trades)
        return summary, trades
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ── E2E: gerçekçi fill ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_thin_book_applies_slippage():
    """İnce defter (best_ask'ta az size) → VWAP yukarı yürür, slippage>0."""
    # best_ask 0.50'de yalnız 4 share ($2) → $5 order üst seviyeye taşar
    asks = json.dumps([{"price": 0.50, "size": 4}, {"price": 0.52, "size": 100}])
    summary, trades = await _run(asks, amount=5.0)
    assert len(trades) == 1
    t = trades[0]
    # VWAP: $2@0.50 (4 share) + $3@0.52 (5.769 share) = 9.769 share, VWAP≈0.5118
    assert t.entry_price > 0.50, "fill best_ask'ın üzerinde olmalı (slippage)"
    assert t.entry_price == pytest.approx(0.5118, abs=0.002)
    assert t.slippage > 0, "slippage artık sıfır DEĞİL (eski naif fill düzeltildi)"
    assert t.shares == pytest.approx(t.amount / t.entry_price, rel=1e-3)
    assert summary.slippage_total > 0


@pytest.mark.asyncio
async def test_deep_book_minimal_slippage():
    """Derin defter (best_ask'ta bol size) → tüm order best'te dolar, slippage~0."""
    asks = json.dumps([{"price": 0.50, "size": 100000}])  # $50k derinlik
    summary, trades = await _run(asks, amount=5.0)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_price == pytest.approx(0.50, abs=1e-4)
    assert t.slippage == pytest.approx(0.0, abs=1e-4)


@pytest.mark.asyncio
async def test_thin_book_fewer_shares_than_naive():
    """Gerçekçi fill, naif (best_ask) fill'den DAHA AZ share alır = daha az iyimser."""
    asks = json.dumps([{"price": 0.50, "size": 4}, {"price": 0.52, "size": 100}])
    _summary, trades = await _run(asks, amount=5.0)
    t = trades[0]
    naive_shares = 5.0 / 0.50  # eski naif: best_ask'ta tam fill = 10 share
    assert t.shares < naive_shares, "gerçekçi fill naiften az share almalı"


@pytest.mark.asyncio
async def test_no_l2_falls_back_but_fills():
    """L2 yok ama geçerli best_ask → MIDPOINT+spread fallback, yine fill olur."""
    summary, trades = await _run("[]", amount=5.0)  # asks_json boş
    assert len(trades) == 1
    t = trades[0]
    # MIDPOINT: (0.48+0.50)/2 + spread(0.023) = 0.49+0.023 = 0.513
    assert t.entry_price > 0.49
    assert t.slippage > 0
