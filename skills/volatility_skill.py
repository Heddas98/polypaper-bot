"""
Skill: Volatility Measurement
==============================
Shared volatility calculation used by: regime, risk_manager, signal_fusion.

Provides:
    - rolling_volatility(): Standard deviation of returns
    - volatility_regime(): Low/Medium/High classification
    - price_range(): High-low range over window
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class VolatilityResult:
    """Volatility measurement."""
    value: float = 0.0          # Annualized or raw volatility
    regime: str = "medium"      # "low", "medium", "high"
    percentile: float = 0.5     # Where current vol sits vs history
    range_high: float = 0.0     # Highest value in window
    range_low: float = 0.0      # Lowest value in window
    range_pct: float = 0.0      # (high-low)/mid as percentage


def rolling_volatility(series: list[float], window: int = 20) -> float:
    """
    Compute rolling standard deviation of returns.

    Args:
        series: Price/odds series
        window: Rolling window size

    Returns:
        Standard deviation of returns over the window.
    """
    if len(series) < window + 1:
        return 0.0

    # Compute returns
    returns = []
    for i in range(-window, 0):
        if series[i - 1] != 0:
            returns.append((series[i] - series[i - 1]) / abs(series[i - 1]))

    if not returns:
        return 0.0

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    return math.sqrt(variance)


def volatility_regime(
    series: list[float],
    window: int = 20,
    low_threshold: float = 0.005,
    high_threshold: float = 0.020,
) -> VolatilityResult:
    """
    Classify current volatility regime.

    Args:
        series: Price/odds series
        window: Measurement window
        low_threshold: Below this = low volatility
        high_threshold: Above this = high volatility

    Returns:
        VolatilityResult with regime classification.
    """
    result = VolatilityResult()

    if len(series) < window + 1:
        return result

    vol = rolling_volatility(series, window)
    result.value = round(vol, 6)

    if vol < low_threshold:
        result.regime = "low"
    elif vol > high_threshold:
        result.regime = "high"
    else:
        result.regime = "medium"

    # Price range
    recent = series[-window:]
    result.range_high = max(recent)
    result.range_low = min(recent)
    mid = (result.range_high + result.range_low) / 2
    if mid > 0:
        result.range_pct = round((result.range_high - result.range_low) / mid * 100, 2)

    return result


def price_range(series: list[float], window: int = 20) -> tuple[float, float, float]:
    """
    Get high, low, range percentage over window.

    Returns (high, low, range_pct).
    """
    if len(series) < window:
        return 0.0, 0.0, 0.0

    recent = series[-window:]
    high = max(recent)
    low = min(recent)
    mid = (high + low) / 2
    range_pct = (high - low) / max(0.001, mid) * 100

    return round(high, 4), round(low, 4), round(range_pct, 2)
