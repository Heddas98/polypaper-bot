"""
PolyPaper Bot - Polymarket Fee Model v2 (Phase 43a)

March 2026 fee change: Polymarket shifted from quadratic (feeRate=0.25, exp=2)
to a lower, category-aware linear / near-linear curve. Crypto Up/Down was the
most affected bucket — taker fee dropped substantially, maker orders now earn
a category-specific rebate from the taker pool.

Formula:
    fee = shares × feeRate × (p × (1-p))^exponent
    fee_pct_of_notional = feeRate × p^(exp-1) × (1-p)^exp

Exponents vary by category: 1 (linear), 2 (quadratic), 0.5 (sqrt).

CATEGORY_FEES is a forward-compatible dict. Dynamic override via
polymarket_taker_fee_v2(..., override_rate=…, override_exp=…) is supported
so a future /fee-rate endpoint listener can hot-patch without a restart.

Tail-zone gate: at p < TAIL_LOW or p > TAIL_HIGH the taker fee is tiny in
absolute terms but the edge required to overcome slippage + execution risk
is large, so callers may want to refuse the trade via FEE_TAIL skip. This
module only computes; the gate lives in engine._evaluate.
"""
from __future__ import annotations

# Default category fee table — Mart 2026, category-aware mixed exponents.
# Keys are lowercase category tags used by the scanner / market.category field.
# taker_rate / taker_exp → plugged into the formula above.
# maker_rebate_pct → share of realized taker fee returned to makers daily.
CATEGORY_FEES: dict[str, dict] = {
    # Verified against Polymarket docs & Pine Analytics (April 2026).
    # Formula: fee = shares × feeRate × (p × (1-p))^exponent
    "crypto":      {"taker_rate": 0.072, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "sports":      {"taker_rate": 0.030, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "politics":    {"taker_rate": 0.040, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "finance":     {"taker_rate": 0.040, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "economics":   {"taker_rate": 0.050, "taker_exp": 0.5, "maker_rebate_pct": 0.25},
    "culture":     {"taker_rate": 0.050, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "weather":     {"taker_rate": 0.050, "taker_exp": 0.5, "maker_rebate_pct": 0.25},
    "tech":        {"taker_rate": 0.040, "taker_exp": 1,   "maker_rebate_pct": 0.25},
    "mentions":    {"taker_rate": 0.040, "taker_exp": 2,   "maker_rebate_pct": 0.25},
    "other":       {"taker_rate": 0.050, "taker_exp": 2,   "maker_rebate_pct": 0.25},
    "geopolitics": {"taker_rate": 0.000, "taker_exp": 1,   "maker_rebate_pct": 0.00},
    # NOTE: Pre-Mart 2026 "legacy" quadratic category removed 2026-04-21 with
    # Epic 4 T4.1. Polymarket deprecated that curve; fees_v2 is the canonical
    # source (verified against live Gamma feeSchedule). See git history for
    # the old {rate: 0.25, exp: 2} entry if ever needed for archival review.
}

DEFAULT_CATEGORY = "crypto"

# Tail-zone thresholds — callers use these to emit FEE_TAIL skips.
TAIL_LOW = 0.15
TAIL_HIGH = 0.85


def _category_params(category: str | None) -> dict:
    if not category:
        return CATEGORY_FEES[DEFAULT_CATEGORY]
    return CATEGORY_FEES.get(category.lower(), CATEGORY_FEES[DEFAULT_CATEGORY])


def polymarket_taker_fee_v2(
    price: float,
    amount_usd: float,
    category: str | None = None,
    override_rate: float | None = None,
    override_exp: float | None = None,
) -> float:
    """Taker fee under the March 2026 model.

    Args:
        price: execution price (0.001..0.999)
        amount_usd: trade notional in USDC
        category: market category ("crypto", "sports", etc.); defaults to crypto
        override_rate: optional hot override (from /fee-rate API)
        override_exp: optional exponent override
    Returns:
        fee in USDC (rounded to 4 decimals)
    """
    if not price or price <= 0 or price >= 0.999 or amount_usd <= 0:
        return 0.0
    # Phase 54 P0-02: explicit zero-division guard (price already checked > 0)
    params = _category_params(category)
    rate = override_rate if override_rate is not None else params["taker_rate"]
    exp = override_exp if override_exp is not None else params["taker_exp"]
    shares = amount_usd / price
    fee = shares * rate * (price * (1 - price)) ** exp
    return round(fee, 4)


def polymarket_fee_percent_v2(
    price: float,
    category: str | None = None,
    override_rate: float | None = None,
    override_exp: float | None = None,
) -> float:
    """Effective taker fee as a % of notional (for EV / display)."""
    if not price or price <= 0.001 or price >= 0.999:
        return 0.0
    params = _category_params(category)
    rate = override_rate if override_rate is not None else params["taker_rate"]
    exp = override_exp if override_exp is not None else params["taker_exp"]
    # fee/amount = rate × price × (1-price)^exp × (price×(1-price))^(exp-1)
    # simplified for exp=1: rate × (1-price)
    # general: rate × (1-price)^exp × price^(exp-1)
    return round(rate * (price ** (exp - 1)) * ((1 - price) ** exp) * 100, 4)


def polymarket_maker_rebate(
    taker_fee_usd: float,
    category: str | None = None,
) -> float:
    """Expected maker rebate credit from a given taker fee pool slice.

    Real rebates are distributed daily from the pool of collected taker fees,
    proportional to the maker's filled volume share. For EV purposes we return
    the category-specific percentage of the taker fee as an upper bound of
    what a maker can expect to rebate-back per equivalent-sized fill.
    """
    if taker_fee_usd <= 0:
        return 0.0
    params = _category_params(category)
    return round(taker_fee_usd * params["maker_rebate_pct"], 4)


def in_tail_zone(price: float) -> bool:
    """True when price is in the extreme (dead-edge) tail.

    Paper trading should skip entries here because:
      1. The absolute fee is tiny but the edge required to overcome
         rounding + slippage + UMA outlier risk is disproportionate.
      2. Liquidity is thin — maker queue advances slowly, taker sweeps
         blow past our limit.
    """
    if not price:
        return True
    return price < TAIL_LOW or price > TAIL_HIGH


def ev_after_fee_v2(
    price: float,
    win_probability: float,
    amount: float = 1.0,
    category: str | None = None,
    is_maker: bool = False,
) -> float:
    """EV after the v2 fee model. Maker path nets the rebate."""
    if price <= 0 or price >= 0.999:
        return 0.0
    # Phase 54 P0-02: price > 0 guaranteed by guard above
    shares = amount / price
    if is_maker:
        taker_equivalent = polymarket_taker_fee_v2(price, amount, category)
        fee = -polymarket_maker_rebate(taker_equivalent, category)
    else:
        fee = polymarket_taker_fee_v2(price, amount, category)
    ev_win = shares * 1.0 - amount - fee
    ev_lose = -amount - fee
    return round(win_probability * ev_win + (1 - win_probability) * ev_lose, 6)
