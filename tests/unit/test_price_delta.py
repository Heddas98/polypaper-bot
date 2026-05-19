"""Fiyat-hareketi (delta) özelliği — 2026-05-19 Heddas direktifi.

Her crypto Up/Down market'i bir zaman penceresidir; pencerenin Binance
mumu açılış→kapanış delta'sı "fiyat farkı"dır. `compute_price_deltas`
son pencerelerin delta'larından volatilite/eğilim istatistiği üretir;
`/live` Piyasa Tara paneli + market BUY onay ekranı bunu gösterir.
Veri kaynağı `candles_ext` (zaten toplanıyor — yeni API yok).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from data.candle_collector import candles_24h_count, compute_price_deltas
from telegram_bot.handlers.live_handler import _fetch_price_deltas, _price_delta_block


def _c(o: float, cl: float) -> dict:
    return {"open": o, "close": cl, "open_ts": 0}


# ── candles_24h_count ────────────────────────────────────────────────────


def test_candles_24h_count():
    assert candles_24h_count("5m") == 288
    assert candles_24h_count("15m") == 96
    assert candles_24h_count("1h") == 24
    assert candles_24h_count("99m") == 96  # bilinmeyen → 15m varsayım
    assert candles_24h_count("24h") >= 2  # min guard


# ── compute_price_deltas ─────────────────────────────────────────────────


def test_compute_deltas_empty():
    r = compute_price_deltas([])
    assert r["n"] == 0
    assert r["last_delta"] == 0.0
    assert r["up_count"] == 0


def test_compute_deltas_basic():
    """3 pencere — drop_last=False ile hepsi sayılır."""
    candles = [_c(100, 101), _c(101, 100), _c(100, 102)]
    r = compute_price_deltas(candles, drop_last=False)
    assert r["n"] == 3
    assert r["last_delta"] == 2.0  # son pencere 100→102
    assert r["last_delta_pct"] == pytest.approx(2.0)
    assert r["last_dir"] == "up"
    assert r["up_count"] == 2  # +1, +2
    assert r["down_count"] == 1  # -1
    # avg |hareket| = (1.0 + 0.9901 + 2.0) / 3
    assert r["avg_abs_pct"]["all"] == pytest.approx((1.0 + 0.990099 + 2.0) / 3, abs=0.01)


def test_compute_deltas_drop_last_default():
    """drop_last=True (default) — en yeni mum (devam eden pencere) atılır."""
    candles = [_c(100, 101), _c(101, 100), _c(100, 102)]
    r = compute_price_deltas(candles)  # drop_last varsayılan True
    assert r["n"] == 2  # son mum atıldı
    assert r["last_delta"] == -1.0  # artık c(101,100) son
    assert r["last_dir"] == "down"


def test_compute_deltas_avg_windows():
    """5/10/all ortalamaları doğru pencere dilimlerinden."""
    # 12 pencere, hepsi +1% (open 100, close 101)
    candles = [_c(100, 101) for _ in range(12)]
    r = compute_price_deltas(candles, drop_last=False)
    assert r["n"] == 12
    assert r["avg_abs_pct"]["5"] == pytest.approx(1.0, abs=0.01)
    assert r["avg_abs_pct"]["10"] == pytest.approx(1.0, abs=0.01)
    assert r["avg_abs_pct"]["all"] == pytest.approx(1.0, abs=0.01)
    assert r["net_pct_all"] == pytest.approx(1.0, abs=0.01)  # hepsi up → net +


def test_compute_deltas_malformed_robust():
    """Bozuk mumlar (boş/0/string) — exception YOK, geçerliler sayılır."""
    candles = [
        {},
        {"open": 0, "close": 5},  # open<=0 → atla
        {"open": "x", "close": "y"},  # non-numeric → atla
        _c(100, 101),
    ]
    r = compute_price_deltas(candles, drop_last=False)
    assert r["n"] == 1  # yalnız c(100,101)
    assert r["last_delta"] == 1.0


def test_compute_deltas_flat():
    """Hareketsiz pencere → last_dir flat."""
    r = compute_price_deltas([_c(100, 100), _c(100, 100)], drop_last=False)
    assert r["last_dir"] == "flat"
    assert r["up_count"] == 0
    assert r["down_count"] == 0


# ── _price_delta_block render ────────────────────────────────────────────


def test_price_delta_block_empty():
    """Veri yoksa boş string — panel atlar."""
    assert _price_delta_block("BTC", "5m", {}) == ""
    assert _price_delta_block("BTC", "5m", {"n": 0}) == ""


def test_price_delta_block_renders():
    st = compute_price_deltas(
        [_c(100, 101), _c(101, 100), _c(100, 102)], drop_last=False
    )
    txt = _price_delta_block("BTC", "5m", st)
    assert "BTC 5m" in txt
    assert "Fiyat Hareketi" in txt
    assert "Son pencere" in txt
    assert "Ort.|hareket|" in txt
    assert "pencere" in txt  # up/down sayısı


# ── _fetch_price_deltas ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_price_deltas_normal():
    engine = MagicMock()
    engine.candle_collector.get_ext_candles = AsyncMock(
        return_value=[_c(100, 101), _c(101, 102), _c(102, 100)]
    )
    r = await _fetch_price_deltas(engine, "BTC", "5m")
    assert r["n"] >= 1  # drop_last sonrası ≥1 pencere


@pytest.mark.asyncio
async def test_fetch_price_deltas_no_collector():
    """candle_collector yok → boş dict, panel crash etmez."""
    engine = MagicMock()
    engine.candle_collector = None
    assert await _fetch_price_deltas(engine, "BTC", "5m") == {}


@pytest.mark.asyncio
async def test_fetch_price_deltas_unknown_coin():
    engine = MagicMock()
    engine.candle_collector.get_ext_candles = AsyncMock(return_value=[])
    assert await _fetch_price_deltas(engine, "DOGE", "5m") == {}
