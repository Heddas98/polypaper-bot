"""
PolyPaper Bot - Technical Indicators (Phase 1)
EMA, volatility, momentum calculations for strategy filtering.
"""
import math
from typing import Optional


def calculate_ema(values: list[float], period: int = 12) -> Optional[float]:
    """Calculate Exponential Moving Average."""
    if not values or len(values) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = values[0]
    for val in values[1:]:
        ema = (val - ema) * multiplier + ema
    return round(ema, 6)


def calculate_sma(values: list[float], period: int = 12) -> Optional[float]:
    """Calculate Simple Moving Average."""
    if not values or len(values) < period:
        return None
    return round(sum(values[-period:]) / period, 6)


def calculate_volatility(values: list[float], period: int = 12) -> Optional[float]:
    """Calculate volatility as standard deviation of returns."""
    if not values or len(values) < period + 1:
        return None
    recent = values[-(period + 1):]
    returns = []
    for i in range(1, len(recent)):
        if recent[i - 1] != 0:
            returns.append((recent[i] - recent[i - 1]) / recent[i - 1])
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance), 6)


def calculate_momentum(values: list[float], period: int = 5) -> Optional[float]:
    """Calculate momentum (rate of change over period)."""
    if not values or len(values) < period + 1:
        return None
    current = values[-1]
    past = values[-(period + 1)]
    if past == 0:
        return None
    return round((current - past) / past, 6)


def ema_direction_filter(values: list[float], short_period: int = 5, long_period: int = 12) -> Optional[str]:
    """
    EMA direction filter (like Polyscout's Trend Filter).
    Returns "up" if short EMA > long EMA, "down" if short < long, None if insufficient data.
    """
    short_ema = calculate_ema(values, short_period)
    long_ema = calculate_ema(values, long_period)
    if short_ema is None or long_ema is None:
        return None
    if short_ema > long_ema:
        return "up"
    elif short_ema < long_ema:
        return "down"
    return None


def check_volatility_threshold(values: list[float], min_vol: float, period: int = 12) -> bool:
    """Check if market volatility meets minimum threshold."""
    vol = calculate_volatility(values, period)
    if vol is None:
        return False  # Not enough data, default to blocking
    return vol >= min_vol
