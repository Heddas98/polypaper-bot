"""
PolyPaper Bot - Polymarket Fee Model v2 (Phase 43a)

March 2026 fee change: Polymarket shifted from quadratic (feeRate=0.25, exp=2)
to a uniformly lower bell-shape curve `fee = C x feeRate x p x (1-p)` with
category-specific feeRate. The fee in USDC peaks at p=0.5 and decreases
symmetrically toward both extremes. Crypto Up/Down was the most affected
bucket -- taker fee dropped substantially, maker orders now earn a
category-specific rebate from the taker pool.

Formula (canonical, per https://docs.polymarket.com/trading/fees):
    fee = shares x feeRate x (p x (1-p))^exponent

For all currently documented categories `exponent == 1`, i.e. the
formula simplifies to `fee = shares x feeRate x p x (1-p)`. The
`taker_exp` field is retained for forward-compat -- Polymarket's SDK
exposes per-market `info["fd"] = { "r": fee_rate, "e": exponent }`,
so individual markets MAY override the exponent in the future.

CATEGORY_FEES is a forward-compatible dict. Dynamic override via
polymarket_taker_fee_v2(..., override_rate=..., override_exp=...) is
supported so a /fee-rate endpoint listener can hot-patch without a
restart.

P2.X (2026-05-03 docs re-audit) — Dynamic Fee Query
----------------------------------------------------
V2 SDK exposes `client.get_clob_market_info(condition_id)` which returns
per-market real-time fee parameters under `info["fd"]`. Use
`get_market_fee_params(client, condition_id)` to fetch them, then pass
the result into `polymarket_taker_fee_v2(..., override_rate=..., override_exp=...)`.

This is the canonical way to detect Geopolitics %0 fee markets at runtime
without relying on category strings — `info["feesEnabled"]` will be False
on those markets, and `info["fd"]["r"]` will be 0.

Tail-zone gate: at p < TAIL_LOW or p > TAIL_HIGH the taker fee is tiny in
absolute terms but the edge required to overcome slippage + execution risk
is large, so callers may want to refuse the trade via FEE_TAIL skip. This
module only computes; the gate lives in engine._evaluate.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("polypaper.fees_v2")

# Default category fee table -- Mart 2026, all categories use exp=1 per docs.
# Keys are lowercase category tags used by the scanner / market.category field.
# taker_rate -> plugged into the formula above.
# maker_rebate_pct -> share of realized taker fee returned to makers daily.
CATEGORY_FEES: dict[str, dict] = {
    # Verified 2026-04-28 against official Polymarket docs:
    #   - https://docs.polymarket.com/trading/fees
    #   - https://docs.polymarket.com/market-makers/maker-rebates
    # Formula: fee = shares x feeRate x (p x (1-p))^exponent
    # All documented categories currently use exponent == 1.
    # Audit: docs/audits/fee_reality_check_2026_04.md (FAZ 0.1).
    "crypto": {"taker_rate": 0.072, "taker_exp": 1, "maker_rebate_pct": 0.20},
    "sports": {"taker_rate": 0.030, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "politics": {"taker_rate": 0.040, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "finance": {"taker_rate": 0.040, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "economics": {"taker_rate": 0.050, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "culture": {"taker_rate": 0.050, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "weather": {"taker_rate": 0.050, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "tech": {"taker_rate": 0.040, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "mentions": {"taker_rate": 0.040, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "other": {"taker_rate": 0.050, "taker_exp": 1, "maker_rebate_pct": 0.25},
    "geopolitics": {"taker_rate": 0.000, "taker_exp": 1, "maker_rebate_pct": 0.00},
}

DEFAULT_CATEGORY = "crypto"

# Tail-zone thresholds -- callers use these to emit FEE_TAIL skips.
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
    # fee/amount = rate x price x (1-price)^exp x (price x (1-price))^(exp-1)
    # simplified for exp=1: rate x (1-price)
    # general: rate x (1-price)^exp x price^(exp-1)
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
      2. Liquidity is thin -- maker queue advances slowly, taker sweeps
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


# ─── P2.X — Dynamic Fee Query (2026-05-03 docs re-audit) ──────────────


def get_market_fee_params(client: Any, condition_id: str) -> dict | None:
    """Fetch per-market fee parameters from V2 SDK at runtime.

    Polymarket docs (https://docs.polymarket.com/trading/fees#fee-handling):
        info = client.get_clob_market_info(condition_id)
        # info["fd"] = { "r": fee_rate, "e": exponent, "to": taker_only }

    Args:
        client: py_clob_client_v2.ClobClient instance (or any duck-typed
                object exposing `get_clob_market_info(condition_id)`).
        condition_id: Polymarket market condition ID (0x...).

    Returns:
        Dict with keys:
            - "rate" (float): per-market fee rate (overrides CATEGORY_FEES default)
            - "exp" (float): per-market exponent (overrides CATEGORY_FEES default)
            - "taker_only" (bool): True if only takers pay (always True per docs)
            - "fees_enabled" (bool): False for Geopolitics-style %0 fee markets
        Returns None on any error (caller falls back to CATEGORY_FEES).

    Forward-compat: if Polymarket later splits the curve (different exp per
    market), this function picks up the change without code edits.

    Usage:
        params = get_market_fee_params(client, "0xabc...")
        if params and params["fees_enabled"]:
            fee = polymarket_taker_fee_v2(
                price, amount,
                override_rate=params["rate"],
                override_exp=params["exp"],
            )
        elif params and not params["fees_enabled"]:
            fee = 0.0  # Geopolitics
        else:
            fee = polymarket_taker_fee_v2(price, amount, category="crypto")
    """
    if not client or not condition_id:
        return None

    method = getattr(client, "get_clob_market_info", None)
    if method is None:
        logger.debug(
            "get_market_fee_params: client has no get_clob_market_info "
            "(SDK pre-V2 or stub) — fallback to CATEGORY_FEES"
        )
        return None

    try:
        info = method(condition_id)
    except Exception as exc:  # noqa: BLE001 — defensive runtime fetch
        logger.warning(
            f"get_clob_market_info({condition_id[:10]}…) failed: "
            f"{type(exc).__name__}: {exc} — fallback to CATEGORY_FEES"
        )
        return None

    if not isinstance(info, dict):
        logger.warning(
            f"get_clob_market_info({condition_id[:10]}…) returned non-dict "
            f"({type(info).__name__}) — fallback"
        )
        return None

    fd = info.get("fd") or {}
    if not isinstance(fd, dict):
        logger.warning(
            f"get_clob_market_info({condition_id[:10]}…) fd field non-dict "
            f"({type(fd).__name__}) — fallback"
        )
        return None

    # fees_enabled: docs use camelCase `feesEnabled`. Some endpoints use
    # snake_case. Accept both. Default True (assume fee unless explicitly off).
    fees_enabled = info.get("feesEnabled", info.get("fees_enabled", True))

    rate = fd.get("r")
    exp = fd.get("e")
    taker_only = fd.get("to", True)

    # Validate types — if rate/exp are missing or non-numeric, fall back.
    if rate is None or exp is None:
        logger.debug(
            f"get_clob_market_info({condition_id[:10]}…) fd missing r/e "
            f"(rate={rate}, exp={exp}) — fallback"
        )
        return None
    try:
        rate = float(rate)
        exp = float(exp)
    except (TypeError, ValueError):
        logger.warning(
            f"get_clob_market_info({condition_id[:10]}…) non-numeric fd "
            f"(rate={rate!r}, exp={exp!r}) — fallback"
        )
        return None

    return {
        "rate": rate,
        "exp": exp,
        "taker_only": bool(taker_only),
        "fees_enabled": bool(fees_enabled),
    }


def taker_fee_dynamic(
    client: Any,
    condition_id: str,
    price: float,
    amount_usd: float,
    fallback_category: str | None = None,
) -> float:
    """Convenience wrapper: fetch dynamic fee params + compute taker fee.

    Single-call API for callers that want runtime per-market fee. On any
    failure falls back to CATEGORY_FEES via `fallback_category` (defaults
    to crypto). Geopolitics-style `feesEnabled=False` markets return 0.0.

    Args:
        client: V2 SDK ClobClient
        condition_id: market ID
        price: trade price (0.001..0.999)
        amount_usd: notional in USDC
        fallback_category: category tag if dynamic fetch fails

    Returns:
        Fee in USDC (rounded to 4 decimals).
    """
    params = get_market_fee_params(client, condition_id)
    if params is None:
        # Static fallback
        return polymarket_taker_fee_v2(price, amount_usd, category=fallback_category)
    if not params["fees_enabled"]:
        # Geopolitics %0 fee
        return 0.0
    return polymarket_taker_fee_v2(
        price,
        amount_usd,
        override_rate=params["rate"],
        override_exp=params["exp"],
    )
