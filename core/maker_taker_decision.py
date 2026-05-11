"""
PolyPaper Bot — Taker/Maker Karar Matrisi
============================================
P1.6 (5AI Yol Haritası §5.2 + Phase D Bulgu 10)

Polymarket V2 fee yapısı:
- Maker (resting GTC limit) → fee_rebate (%20 of platform fee)
- Taker (FOK marketable) → tam taker fee (C × 0.072 × p × (1-p))
- FAK marketable → partial fill OK, taker fee

Karar matrisi:
- Spread > N tick → post-only GTC (maker, rebate kazan)
- Spread ≤ N tick → FOK marketable (taker, hızlı giriş)
- Hızla giriş zorunlu (whale signal vb.) → FAK partial OK

Bot şu an FOK-only. Bu modül post-only GTC opsiyonu ekler.

ZORUNLU PRE-REQ:
- P1.6.1 core/heartbeat.py (5s coroutine — GTC için zorunlu)
- HEARTBEAT_ENABLED=true ENV

Public API:
    decide_order_type(orderbook, urgency="normal") -> OrderDecision
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from core.fees_v2 import polymarket_taker_fee_v2  # FAZ 0.1 oracle

logger = logging.getLogger("polypaper.core.maker_taker")


# ENV-tunable thresholds (T6.1 hot-tune ready)
def _get_maker_spread_threshold_ticks() -> int:
    """Spread > N tick → maker. Default 2."""
    try:
        return int(os.getenv("MAKER_SPREAD_THRESHOLD_TICKS", "2"))
    except (ValueError, TypeError):
        return 2


def _get_maker_enabled() -> bool:
    """Master switch — false ise her şey FOK taker."""
    val = os.getenv("MAKER_MODE_ENABLED", "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


@dataclass
class OrderDecision:
    """Output of decide_order_type."""

    order_type: str  # "GTC_POST_ONLY" | "FOK" | "FAK"
    role: str  # "maker" | "taker"
    estimated_fee_usd: float
    estimated_rebate_usd: float  # Sadece maker için >0
    reason: str
    spread_ticks: float
    urgency: str

    @property
    def net_cost_usd(self) -> float:
        """Fee - rebate."""
        return self.estimated_fee_usd - self.estimated_rebate_usd

    def html_breakdown(self) -> str:
        """Telegram HTML format."""
        emoji = "🟢" if self.role == "maker" else "🔵"
        lines = [
            f"{emoji} <b>{self.order_type}</b> ({self.role})",
            f"  Spread: {self.spread_ticks:.1f} tick",
            f"  Fee:    ${self.estimated_fee_usd:.4f}",
        ]
        if self.estimated_rebate_usd > 0:
            lines.append(f"  Rebate: ${self.estimated_rebate_usd:.4f}")
        lines.append(f"  Net:    ${self.net_cost_usd:.4f}")
        lines.append(f"  Reason: {self.reason}")
        return "\n".join(lines)


def _orderbook_spread_ticks(orderbook: dict, tick_size: float = 0.01) -> Optional[float]:
    """Compute bid-ask spread in tick units."""
    if not orderbook:
        return None
    asks = orderbook.get("asks") or []
    bids = orderbook.get("bids") or []
    if not asks or not bids:
        return None
    try:
        if isinstance(asks[0], list | tuple):
            best_ask = float(asks[0][0])
        else:
            best_ask = float(asks[0].get("price", 0))
        if isinstance(bids[0], list | tuple):
            best_bid = float(bids[0][0])
        else:
            best_bid = float(bids[0].get("price", 0))
        if best_ask <= 0 or best_bid <= 0:
            return None
        spread = best_ask - best_bid
        return spread / tick_size
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def _compute_maker_rebate(notional_usd: float, price: float) -> float:
    """Maker rebate ≈ %20 of platform fee.

    Polymarket V2 docs: maker_rebate_rate ~0.20 of crypto category fee.
    """
    base_fee = polymarket_taker_fee_v2(price, notional_usd, category="crypto")
    return base_fee * 0.20


def decide_order_type(
    orderbook: dict,
    notional_usd: float,
    price: float,
    tick_size: float = 0.01,
    urgency: str = "normal",
) -> OrderDecision:
    """Karar matrisi — order type + role.

    Args:
        orderbook: {"asks": [[price, size], ...], "bids": [[...]]}
        notional_usd: Trade dollar amount
        price: Limit price (0..1)
        tick_size: Market tick size
        urgency: "normal" | "high" | "extreme"
            - "normal": maker tercih (spread izin verirse)
            - "high": FAK partial fill OK
            - "extreme": FOK whatever

    Returns: OrderDecision
    """
    spread_ticks = _orderbook_spread_ticks(orderbook, tick_size) or 0.0
    threshold = _get_maker_spread_threshold_ticks()
    maker_enabled = _get_maker_enabled()

    base_fee = polymarket_taker_fee_v2(price, notional_usd, category="crypto")

    # URGENCY OVERRIDES
    if urgency == "extreme":
        return OrderDecision(
            order_type="FOK",
            role="taker",
            estimated_fee_usd=base_fee,
            estimated_rebate_usd=0,
            reason="extreme urgency override",
            spread_ticks=spread_ticks,
            urgency=urgency,
        )

    if urgency == "high":
        return OrderDecision(
            order_type="FAK",
            role="taker",
            estimated_fee_usd=base_fee,
            estimated_rebate_usd=0,
            reason="high urgency, partial fill OK",
            spread_ticks=spread_ticks,
            urgency=urgency,
        )

    # NORMAL — maker enabled mı?
    if not maker_enabled:
        return OrderDecision(
            order_type="FOK",
            role="taker",
            estimated_fee_usd=base_fee,
            estimated_rebate_usd=0,
            reason="MAKER_MODE_ENABLED=false (FOK fallback)",
            spread_ticks=spread_ticks,
            urgency=urgency,
        )

    # Spread analizi
    if spread_ticks >= threshold:
        # Wide spread → post-only GTC maker
        rebate = _compute_maker_rebate(notional_usd, price)
        return OrderDecision(
            order_type="GTC_POST_ONLY",
            role="maker",
            estimated_fee_usd=base_fee,  # Hala fee var ama rebate ile düşer
            estimated_rebate_usd=rebate,
            reason=f"spread {spread_ticks:.1f} tick ≥ {threshold} → maker rebate",
            spread_ticks=spread_ticks,
            urgency=urgency,
        )
    else:
        # Tight spread → taker FOK
        return OrderDecision(
            order_type="FOK",
            role="taker",
            estimated_fee_usd=base_fee,
            estimated_rebate_usd=0,
            reason=f"spread {spread_ticks:.1f} tick < {threshold} → taker (no maker edge)",
            spread_ticks=spread_ticks,
            urgency=urgency,
        )


def render_decision_log(decision: OrderDecision, slug: str = "") -> str:
    """Compact log line."""
    return (
        f"🎯 [{slug[:24]}] {decision.order_type} ({decision.role}) "
        f"spread={decision.spread_ticks:.1f}t fee=${decision.estimated_fee_usd:.4f} "
        f"net=${decision.net_cost_usd:.4f} ({decision.reason})"
    )
