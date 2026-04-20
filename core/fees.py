"""
PolyPaper Bot - Polymarket Fee Calculator (Phase 18.5)
Implements the REAL Polymarket quadratic taker fee curve.
Source: docs.polymarket.com/polymarket-learn/trading/fees

Formula: fee = shares × feeRate × (p × (1 - p))^exponent
Where: feeRate = 0.25, exponent = 2

Effective fee by price zone:
  p=0.10 → 0.20%    (cheap to trade extremes)
  p=0.30 → 0.93%
  p=0.50 → 1.56%    (PEAK — most expensive)
  p=0.70 → 0.93%
  p=0.90 → 0.20%

Maker fee: 0% (+ daily rebates from taker fee pool)
"""

FEE_RATE = 0.25
FEE_EXPONENT = 2


def polymarket_taker_fee(price: float, amount_usd: float) -> float:
    """Calculate Polymarket quadratic taker fee.

    Args:
        price: execution price (0.001 to 0.999)
        amount_usd: trade amount in USDC
    Returns:
        fee in USDC
    """
    if not price or price <= 0.001 or price >= 0.999:
        return 0.0
    shares = amount_usd / price
    fee = shares * FEE_RATE * (price * (1 - price)) ** FEE_EXPONENT
    return round(fee, 4)


def polymarket_fee_percent(price: float) -> float:
    """Return effective fee as percentage of trade amount.
    Useful for display and EV calculations."""
    if not price or price <= 0.001 or price >= 0.999:
        return 0.0
    # fee/amount = (1/p) × feeRate × (p(1-p))^2 = feeRate × p × (1-p)^2
    return round(FEE_RATE * price * (1 - price) ** FEE_EXPONENT * 100, 4)


def ev_after_fee(price: float, win_probability: float, amount: float = 1.0) -> float:
    """Calculate Expected Value AFTER real Polymarket fees.
    
    Args:
        price: buy price (e.g., 0.40)
        win_probability: estimated true probability (e.g., 0.55)
        amount: trade size in USDC
    Returns:
        expected value in USDC (positive = profitable)
    """
    shares = amount / price
    fee = polymarket_taker_fee(price, amount)
    # Win: shares × $1.00 - amount - fee
    # Lose: 0 - amount - fee
    ev_win = shares * 1.0 - amount - fee
    ev_lose = -amount - fee
    return round(win_probability * ev_win + (1 - win_probability) * ev_lose, 6)


def kelly_fraction(price: float, win_probability: float) -> float:
    """Kelly Criterion for Polymarket binary outcome.

    For binary bet at price p with win prob w:
    Payout on win = (1/p - 1) (net profit per dollar risked)
    Kelly f* = (b×w - (1-w)) / b  where b = net payout ratio

    Returns: optimal fraction of bankroll (0.0 to 1.0), negative = don't bet
    """
    if price <= 0.001 or price >= 0.999 or win_probability <= 0 or win_probability >= 1:
        return 0.0
    b = (1.0 / price) - 1.0  # net payout ratio
    fee_pct = polymarket_fee_percent(price) / 100.0
    b_after_fee = b - fee_pct  # reduce payout by fee
    if b_after_fee <= 0:
        return 0.0
    f = (b_after_fee * win_probability - (1 - win_probability)) / b_after_fee
    return max(0.0, min(1.0, round(f, 4)))
