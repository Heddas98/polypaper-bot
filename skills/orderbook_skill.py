"""
Skill: Orderbook Analysis
==========================
Shared orderbook calculations used by: signal_fusion, spread_signal, whale_tracker.

Provides:
    - compute_microprice(): Volume-weighted mid price
    - compute_imbalance(): Bid-ask depth imbalance
    - depth_at_level(): USD depth at specific price levels
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MicropriceResult:
    """Microprice calculation result."""

    microprice: float = 0.0  # Volume-weighted mid
    mid_price: float = 0.0  # Simple (bid+ask)/2
    imbalance: float = 0.0  # [-1, 1]: positive = more bids
    bid_depth_usd: float = 0.0  # Total bid USD (top N levels)
    ask_depth_usd: float = 0.0  # Total ask USD
    spread: float = 0.0  # ask - bid


def compute_microprice(
    orderbook: dict,
    levels: int = 5,
) -> MicropriceResult:
    """
    Compute volume-weighted mid price (microprice).

    Microprice = (bid_vol * ask_price + ask_vol * bid_price) / (bid_vol + ask_vol)
    Better estimate of true value than simple mid.

    Args:
        orderbook: {"bids": [...], "asks": [...]}
        levels: Number of OB levels to use

    Returns:
        MicropriceResult
    """
    result = MicropriceResult()

    if not orderbook:
        return result

    bids = orderbook.get("bids", [])
    asks = orderbook.get("asks", [])

    if not bids or not asks:
        return result

    try:
        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 0))
    except (IndexError, TypeError, ValueError):
        return result

    result.mid_price = round((best_bid + best_ask) / 2, 4)
    result.spread = round(best_ask - best_bid, 4)

    # Depth
    bid_vol = 0.0
    for lvl in bids[:levels]:
        try:
            bid_vol += float(lvl.get("size", 0))
        except (TypeError, ValueError):
            continue

    ask_vol = 0.0
    for lvl in asks[:levels]:
        try:
            ask_vol += float(lvl.get("size", 0))
        except (TypeError, ValueError):
            continue

    result.bid_depth_usd = round(bid_vol * best_bid, 2)
    result.ask_depth_usd = round(ask_vol * best_ask, 2)

    total_vol = bid_vol + ask_vol
    if total_vol > 0:
        # Microprice formula
        result.microprice = round((bid_vol * best_ask + ask_vol * best_bid) / total_vol, 4)
        result.imbalance = round((bid_vol - ask_vol) / total_vol, 4)
    else:
        result.microprice = result.mid_price

    return result


def compute_imbalance(orderbook: dict, levels: int = 5) -> float:
    """
    Quick orderbook imbalance: positive = more bid depth.

    Returns float in [-1, 1].
    """
    mp = compute_microprice(orderbook, levels)
    return mp.imbalance


def depth_at_level(
    orderbook: dict,
    level: int = 0,
    side: str = "bid",
) -> tuple[float, float]:
    """
    Get (price, size) at a specific orderbook level.

    Args:
        orderbook: Orderbook dict
        level: 0-indexed level (0 = best)
        side: "bid" or "ask"

    Returns:
        (price, size) tuple. (0, 0) if not available.
    """
    if not orderbook:
        return 0.0, 0.0

    book = orderbook.get("bids" if side == "bid" else "asks", [])
    if level >= len(book):
        return 0.0, 0.0

    try:
        entry = book[level]
        return float(entry.get("price", 0)), float(entry.get("size", 0))
    except (TypeError, ValueError):
        return 0.0, 0.0
