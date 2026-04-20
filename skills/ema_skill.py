"""
Skill: EMA (Exponential Moving Average)
========================================
Shared EMA calculation used by: momentum, contrarian, signal_fusion, drift_detector.

Provides:
    - ema(): Single EMA computation
    - ema_crossover(): Fast/slow EMA crossover detection
    - ema_direction(): UP/DOWN/FLAT based on EMA slope
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EMACrossover:
    """EMA crossover result."""
    fast: float = 0.0
    slow: float = 0.0
    crossed_up: bool = False      # Fast crossed above slow
    crossed_down: bool = False    # Fast crossed below slow
    spread: float = 0.0           # fast - slow
    direction: str = "flat"       # "up", "down", "flat"


def ema(series: list[float], period: int = 14) -> list[float]:
    """
    Compute EMA over a series.

    Args:
        series: Input values (most recent last)
        period: EMA period

    Returns:
        List of EMA values (same length as input, NaN-equivalent for early values).
    """
    if not series or period < 1:
        return []

    alpha = 2.0 / (period + 1)
    result = [series[0]]

    for i in range(1, len(series)):
        result.append(alpha * series[i] + (1 - alpha) * result[-1])

    return result


def ema_crossover(
    series: list[float],
    fast_period: int = 5,
    slow_period: int = 20,
) -> EMACrossover:
    """
    Detect EMA crossover between fast and slow periods.

    Args:
        series: Price/odds series (most recent last)
        fast_period: Fast EMA period
        slow_period: Slow EMA period

    Returns:
        EMACrossover with crossover detection.
    """
    result = EMACrossover()

    if len(series) < slow_period + 2:
        return result

    fast_ema = ema(series, fast_period)
    slow_ema = ema(series, slow_period)

    result.fast = fast_ema[-1]
    result.slow = slow_ema[-1]
    result.spread = result.fast - result.slow

    # Check crossover (current vs previous)
    prev_spread = fast_ema[-2] - slow_ema[-2]
    if prev_spread <= 0 and result.spread > 0:
        result.crossed_up = True
        result.direction = "up"
    elif prev_spread >= 0 and result.spread < 0:
        result.crossed_down = True
        result.direction = "down"
    elif result.spread > 0:
        result.direction = "up"
    elif result.spread < 0:
        result.direction = "down"
    else:
        result.direction = "flat"

    return result


def ema_direction(series: list[float], period: int = 10, threshold: float = 0.001) -> str:
    """
    Get EMA direction: "up", "down", or "flat".

    Args:
        series: Price/odds series
        period: EMA period
        threshold: Minimum slope for direction

    Returns:
        Direction string.
    """
    if len(series) < period + 1:
        return "flat"

    ema_vals = ema(series, period)
    slope = ema_vals[-1] - ema_vals[-2]

    if slope > threshold:
        return "up"
    elif slope < -threshold:
        return "down"
    return "flat"
