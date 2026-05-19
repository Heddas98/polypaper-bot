"""Market seçim filtresi — A+B+C audit fix (2026-05-19).

Heddas raporu: live trade'de "no match" — bot ince/boş orderbook'lu
market'lerde trade deniyordu. Kök neden: `has_liquidity` sahteydi
(`polymarket_client.py` midpoint üzerine zorla True yapıyordu) →
engine `NO_LIQ` gate'i etkisizdi.

Fix:
  A — sahte `has_liquidity` override kaldırıldı
  B — gerçek order book derinlik kontrolü (`_compute_liquidity`)
  C — `enableOrderBook`/`acceptingOrders` discovery filtresi
      (`_market_tradeable`)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from data.polymarket_client import PolymarketClient

# ── C: _market_tradeable (discovery filtresi) ────────────────────────────


def test_market_tradeable_normal():
    assert (
        PolymarketClient._market_tradeable(
            {"closed": False, "active": True, "enableOrderBook": True}
        )
        is True
    )


def test_market_tradeable_missing_fields_default_allow():
    """Alan yoksa engellenmez — Gamma yanıtı her zaman tüm alanları döndürmez."""
    assert PolymarketClient._market_tradeable({}) is True
    assert PolymarketClient._market_tradeable({"slug": "btc-updown-5m-1"}) is True


@pytest.mark.parametrize(
    "field,val",
    [
        ("closed", True),
        ("active", False),
        ("enableOrderBook", False),
        ("acceptingOrders", False),
    ],
)
def test_market_tradeable_blocks_untradeable(field, val):
    """closed / active=False / enableOrderBook=False / acceptingOrders=False → ele."""
    assert PolymarketClient._market_tradeable({field: val}) is False


def test_market_tradeable_non_dict():
    assert PolymarketClient._market_tradeable(None) is False  # type: ignore[arg-type]
    assert PolymarketClient._market_tradeable("x") is False  # type: ignore[arg-type]


# ── A+B: _compute_liquidity (gerçek order book derinliği) ────────────────


def _client() -> PolymarketClient:
    return PolymarketClient(settings=MagicMock())


@pytest.mark.asyncio
async def test_compute_liquidity_deep_book_true(monkeypatch):
    """İki yanlı derin defter → has_liquidity True + derinlik USDC kaydı."""
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "true")
    monkeypatch.setenv("MIN_BOOK_DEPTH_USD", "2.0")
    c = _client()
    c.get_orderbook = AsyncMock(
        return_value={"asks": [[0.5, 20.0]], "bids": [[0.5, 20.0]]}
    )
    res: dict = {}
    ok = await c._compute_liquidity("0xUP", res)
    assert ok is True
    assert res["ask_depth_usd"] == 10.0
    assert res["bid_depth_usd"] == 10.0


@pytest.mark.asyncio
async def test_compute_liquidity_thin_book_false(monkeypatch):
    """İnce defter (derinlik < eşik) → False — 'no match' kök nedeni önlenir."""
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "true")
    monkeypatch.setenv("MIN_BOOK_DEPTH_USD", "2.0")
    c = _client()
    # ask derinliği 0.9*0.5 = 0.45 — eşik 2.0'ın altında
    c.get_orderbook = AsyncMock(
        return_value={"asks": [[0.9, 0.5]], "bids": [[0.85, 0.5]]}
    )
    assert await c._compute_liquidity("0xUP", {}) is False


@pytest.mark.asyncio
async def test_compute_liquidity_empty_book_false(monkeypatch):
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "true")
    c = _client()
    c.get_orderbook = AsyncMock(return_value={"asks": [], "bids": []})
    assert await c._compute_liquidity("0xUP", {}) is False


@pytest.mark.asyncio
async def test_compute_liquidity_one_sided_book_false(monkeypatch):
    """Tek yanlı defter (ask derin, bid boş) → False — iki yan da gerekir."""
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "true")
    monkeypatch.setenv("MIN_BOOK_DEPTH_USD", "2.0")
    c = _client()
    c.get_orderbook = AsyncMock(
        return_value={"asks": [[0.5, 100.0]], "bids": []}
    )
    assert await c._compute_liquidity("0xUP", {}) is False


@pytest.mark.asyncio
async def test_compute_liquidity_no_book_falls_back(monkeypatch):
    """get_orderbook None → best_ask+best_bid fallback (sahte midpoint DEĞİL)."""
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "true")
    c = _client()
    c.get_orderbook = AsyncMock(return_value=None)
    assert (
        await c._compute_liquidity("0xUP", {"best_ask_up": 0.5, "best_bid_up": 0.49})
        is True
    )
    # tek taraf → fallback yine False
    assert await c._compute_liquidity("0xUP", {"best_ask_up": 0.5}) is False


@pytest.mark.asyncio
async def test_compute_liquidity_depth_check_disabled(monkeypatch):
    """MARKET_DEPTH_CHECK=false → ask+bid var-mı fallback; get_orderbook çağrılmaz."""
    monkeypatch.setenv("MARKET_DEPTH_CHECK", "false")
    c = _client()
    c.get_orderbook = AsyncMock()
    assert (
        await c._compute_liquidity("0xUP", {"best_ask_up": 0.5, "best_bid_up": 0.49})
        is True
    )
    assert await c._compute_liquidity("0xUP", {"best_ask_up": 0.5}) is False
    c.get_orderbook.assert_not_called()


@pytest.mark.asyncio
async def test_compute_liquidity_no_token_false():
    assert await _client()._compute_liquidity(None, {}) is False
