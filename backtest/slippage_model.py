"""
Backtest Slippage Model — P0.6 (5AI Yol Haritası §5.1)
========================================================

Orderbook depth-based slippage simulator. Naive backtest "midpoint fill"
yanılsamasını replace eder — gerçek fill price orderbook depth + spread'e
duyarlıdır.

Polymarket V2 docs:
- Min order size: $5 (pUSD notional)
- Taker fee: C × 0.072 × p × (1-p) (core/fees_v2.py)
- FOK marketable order ladder dolanır until size/price exhausted

Usage:
    sim = SlippageModel(orderbook=ob_dict)
    fill = sim.simulate_market_buy(notional_usd=100, max_price=0.55)
    # fill = {"avg_price": 0.523, "shares": 191.2, "fee": 1.32, "rejected": False}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.fees_v2 import polymarket_taker_fee_v2  # mevcut FAZ 0.1 oracle

# Polymarket V2 docs minimums
MIN_ORDER_USD = 5.0


@dataclass
class FillResult:
    """Simulated fill outcome."""

    filled: bool
    avg_price: float
    shares: float
    notional_filled_usd: float
    fee_usd: float
    slippage_bps: float
    rejected_reason: Optional[str] = None
    levels_consumed: int = 0


class SlippageModel:
    """Orderbook depth-aware fill simulator.

    Orderbook format (Polymarket REST `/book` shape):
        {
            "asks": [[price, size_shares], ...],  # ascending price
            "bids": [[price, size_shares], ...],  # descending price
        }

    asks[0][0] = best ask, asks[0][1] = best ask size in shares
    """

    def __init__(self, orderbook: dict, fee_rate_bps: Optional[float] = None):
        """
        Args:
            orderbook: dict with "asks" + "bids" lists
            fee_rate_bps: opsiyonel override; None ise core/fees_v2 hesaplar
        """
        self.ob = orderbook or {}
        self.fee_rate_bps = fee_rate_bps

    def _ladder(self, side: str) -> list[tuple[float, float]]:
        """Return [(price, size), ...] sorted appropriately.

        BUY → asks (ascending price)
        SELL → bids (descending price)
        """
        if side == "BUY":
            raw = self.ob.get("asks", []) or []
        else:  # SELL
            raw = self.ob.get("bids", []) or []
        out = []
        for level in raw:
            try:
                if isinstance(level, list | tuple) and len(level) >= 2:
                    p, s = float(level[0]), float(level[1])
                elif isinstance(level, dict):
                    p, s = float(level.get("price", 0)), float(level.get("size", 0))
                else:
                    continue
                if p > 0 and s > 0:
                    out.append((p, s))
            except (ValueError, TypeError, KeyError):
                continue
        # Already sorted by API; ensure
        if side == "BUY":
            out.sort(key=lambda x: x[0])
        else:
            out.sort(key=lambda x: x[0], reverse=True)
        return out

    def _midpoint(self) -> Optional[float]:
        """(best_ask + best_bid) / 2"""
        asks = self._ladder("BUY")
        bids = self._ladder("SELL")
        if not asks or not bids:
            return None
        return (asks[0][0] + bids[0][0]) / 2

    def simulate_market_order(
        self,
        side: str,
        notional_usd: Optional[float] = None,
        shares: Optional[float] = None,
        max_price: Optional[float] = None,
    ) -> FillResult:
        """Simulate a marketable FOK/FAK order.

        For BUY: spend notional_usd
        For SELL: sell `shares`
        max_price: worst-price slippage protection. BUY rejects if next level > max_price.

        Args:
            side: "BUY" or "SELL"
            notional_usd: dollar amount to spend (BUY) — required for BUY
            shares: share count (SELL) — required for SELL
            max_price: slippage cap (FOK semantics)

        Returns FillResult.
        """
        side = side.upper()
        ladder = self._ladder(side)
        mid = self._midpoint()

        if not ladder:
            return FillResult(
                filled=False,
                avg_price=0,
                shares=0,
                notional_filled_usd=0,
                fee_usd=0,
                slippage_bps=0,
                rejected_reason="empty_book",
            )

        # Min order check
        if side == "BUY" and notional_usd is not None and notional_usd < MIN_ORDER_USD:
            return FillResult(
                filled=False,
                avg_price=0,
                shares=0,
                notional_filled_usd=0,
                fee_usd=0,
                slippage_bps=0,
                rejected_reason=f"below_min_${MIN_ORDER_USD}",
            )

        filled_shares = 0.0
        filled_notional = 0.0
        levels_used = 0

        if side == "BUY":
            target = float(notional_usd or 0)
            for price, size in ladder:
                if max_price is not None and price > max_price:
                    # FOK: any level beyond max_price rejects
                    return FillResult(
                        filled=False,
                        avg_price=0,
                        shares=0,
                        notional_filled_usd=0,
                        fee_usd=0,
                        slippage_bps=0,
                        rejected_reason=f"price_above_max ({price:.4f} > {max_price:.4f})",
                        levels_consumed=levels_used,
                    )
                level_notional = price * size
                if filled_notional + level_notional >= target:
                    # Partial fill at this level
                    needed_notional = target - filled_notional
                    needed_shares = needed_notional / price
                    filled_shares += needed_shares
                    filled_notional += needed_notional
                    levels_used += 1
                    break
                else:
                    filled_shares += size
                    filled_notional += level_notional
                    levels_used += 1

            # Insufficient liquidity (FOK reject)
            if filled_notional < target * 0.999:  # 0.1% tolerance
                return FillResult(
                    filled=False,
                    avg_price=0,
                    shares=0,
                    notional_filled_usd=0,
                    fee_usd=0,
                    slippage_bps=0,
                    rejected_reason=f"insufficient_liquidity (filled ${filled_notional:.2f} of ${target:.2f})",
                    levels_consumed=levels_used,
                )

        else:  # SELL
            target = float(shares or 0)
            for price, size in ladder:
                if max_price is not None and price < max_price:
                    return FillResult(
                        filled=False,
                        avg_price=0,
                        shares=0,
                        notional_filled_usd=0,
                        fee_usd=0,
                        slippage_bps=0,
                        rejected_reason=f"price_below_max ({price:.4f} < {max_price:.4f})",
                        levels_consumed=levels_used,
                    )
                if filled_shares + size >= target:
                    needed_shares = target - filled_shares
                    filled_shares += needed_shares
                    filled_notional += needed_shares * price
                    levels_used += 1
                    break
                else:
                    filled_shares += size
                    filled_notional += size * price
                    levels_used += 1

            if filled_shares < target * 0.999:
                return FillResult(
                    filled=False,
                    avg_price=0,
                    shares=0,
                    notional_filled_usd=0,
                    fee_usd=0,
                    slippage_bps=0,
                    rejected_reason=f"insufficient_liquidity (filled {filled_shares:.2f} of {target:.2f} shares)",
                    levels_consumed=levels_used,
                )

        avg_price = (filled_notional / filled_shares) if filled_shares > 0 else 0
        fee_usd = polymarket_taker_fee_v2(avg_price, filled_notional, category="crypto")

        # Slippage in basis points vs midpoint
        slippage_bps = 0.0
        if mid and mid > 0:
            slip = (avg_price - mid) if side == "BUY" else (mid - avg_price)
            slippage_bps = (slip / mid) * 10000

        return FillResult(
            filled=True,
            avg_price=round(avg_price, 6),
            shares=round(filled_shares, 4),
            notional_filled_usd=round(filled_notional, 4),
            fee_usd=round(fee_usd, 4),
            slippage_bps=round(slippage_bps, 2),
            levels_consumed=levels_used,
        )

    def simulate_market_buy(
        self, notional_usd: float, max_price: Optional[float] = None
    ) -> FillResult:
        """Convenience: BUY with notional."""
        return self.simulate_market_order("BUY", notional_usd=notional_usd, max_price=max_price)

    def simulate_market_sell(self, shares: float, max_price: Optional[float] = None) -> FillResult:
        """Convenience: SELL with share count (max_price = min acceptable price)."""
        return self.simulate_market_order("SELL", shares=shares, max_price=max_price)
