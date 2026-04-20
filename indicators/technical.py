"""
Phase 68: Technical Indicators for Signal Enhancement
=====================================================
RSI, MACD, Bollinger Bands computed from price/odds series.

These operate on Polymarket odds series (0.0-1.0 range), NOT
traditional asset prices. Adjusted accordingly.

Usage:
    from indicators.technical import RSI, MACD, BollingerBands

    rsi = RSI(period=14)
    rsi_value = rsi.calculate(odds_series)

    macd = MACD()
    macd_result = macd.calculate(odds_series)

    bb = BollingerBands()
    bb_result = bb.calculate(odds_series)

All indicators are pure functions — no side effects, no API calls.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("polypaper.indicators.technical")


# ═══════════════════════════════════════════════════════════════
# RSI — Relative Strength Index
# ═══════════════════════════════════════════════════════════════

@dataclass
class RSIResult:
    """RSI calculation result."""
    value: float = 50.0       # RSI value (0-100)
    signal: float = 0.0       # -1 to +1 signal for fusion
    is_overbought: bool = False  # > 70
    is_oversold: bool = False    # < 30
    avg_gain: float = 0.0
    avg_loss: float = 0.0


class RSI:
    """Relative Strength Index adapted for odds series.

    Standard RSI but on odds changes instead of price changes.
    - RSI > 70 → odds overbought (likely to pull back)
    - RSI < 30 → odds oversold (likely to bounce)
    - Used as confidence multiplier, not primary signal.
    """

    def __init__(self, period: int = 14):
        self.period = period

    def calculate(self, series: list[float]) -> RSIResult:
        """Calculate RSI from odds series."""
        if len(series) < self.period + 1:
            return RSIResult()

        # Calculate changes
        changes = [series[i] - series[i - 1] for i in range(1, len(series))]

        # Use last `period` changes
        recent = changes[-(self.period):]
        gains = [c for c in recent if c > 0]
        losses = [-c for c in recent if c < 0]

        avg_gain = sum(gains) / self.period if gains else 0.0
        avg_loss = sum(losses) / self.period if losses else 0.0

        if avg_loss == 0:
            rsi = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # Convert RSI to signal: 0-30 = bullish (+), 70-100 = bearish (-)
        if rsi < 30:
            signal = (30 - rsi) / 30  # 0 to +1
        elif rsi > 70:
            signal = -(rsi - 70) / 30  # 0 to -1
        else:
            signal = 0.0  # Neutral zone

        return RSIResult(
            value=round(rsi, 2),
            signal=round(max(-1, min(1, signal)), 4),
            is_overbought=rsi > 70,
            is_oversold=rsi < 30,
            avg_gain=round(avg_gain, 6),
            avg_loss=round(avg_loss, 6),
        )


# ═══════════════════════════════════════════════════════════════
# MACD — Moving Average Convergence Divergence
# ═══════════════════════════════════════════════════════════════

@dataclass
class MACDResult:
    """MACD calculation result."""
    macd_line: float = 0.0     # MACD line (fast EMA - slow EMA)
    signal_line: float = 0.0   # Signal line (EMA of MACD)
    histogram: float = 0.0     # MACD - Signal
    signal: float = 0.0        # -1 to +1 for fusion
    is_bullish_cross: bool = False
    is_bearish_cross: bool = False


class MACD:
    """MACD adapted for odds series.

    Uses shorter periods than traditional (12/26/9) because
    Polymarket 5-minute windows have limited data points.

    Default: fast=8, slow=16, signal=5 (scaled for binary markets).
    """

    def __init__(self, fast: int = 8, slow: int = 16, signal: int = 5):
        self.fast = fast
        self.slow = slow
        self.signal_period = signal

    def calculate(self, series: list[float]) -> MACDResult:
        """Calculate MACD from odds series."""
        if len(series) < self.slow + self.signal_period:
            return MACDResult()

        # Calculate EMAs
        fast_ema = self._ema(series, self.fast)
        slow_ema = self._ema(series, self.slow)

        if fast_ema is None or slow_ema is None:
            return MACDResult()

        # MACD history for signal line
        macd_history = []
        for i in range(self.slow, len(series) + 1):
            chunk = series[:i]
            fe = self._ema(chunk, self.fast)
            se = self._ema(chunk, self.slow)
            if fe is not None and se is not None:
                macd_history.append(fe - se)

        macd_line = fast_ema - slow_ema

        # Signal line = EMA of MACD history
        if len(macd_history) >= self.signal_period:
            signal_line = self._ema_values(macd_history, self.signal_period)
        else:
            signal_line = macd_line

        histogram = macd_line - signal_line

        # Convert to signal
        # Positive histogram = bullish momentum
        # Scale: odds movements are small (0.01-0.05 range)
        signal = max(-1, min(1, histogram * 50))

        # Detect crossovers
        is_bullish = (macd_line > signal_line and
                      len(macd_history) >= 2 and
                      macd_history[-2] < signal_line)
        is_bearish = (macd_line < signal_line and
                      len(macd_history) >= 2 and
                      macd_history[-2] > signal_line)

        return MACDResult(
            macd_line=round(macd_line, 6),
            signal_line=round(signal_line, 6),
            histogram=round(histogram, 6),
            signal=round(signal, 4),
            is_bullish_cross=is_bullish,
            is_bearish_cross=is_bearish,
        )

    @staticmethod
    def _ema(data: list[float], period: int) -> Optional[float]:
        """Exponential Moving Average."""
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    @staticmethod
    def _ema_values(data: list[float], period: int) -> float:
        """EMA over a list of values."""
        if not data:
            return 0.0
        if len(data) < period:
            return sum(data) / len(data)
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema


# ═══════════════════════════════════════════════════════════════
# Bollinger Bands
# ═══════════════════════════════════════════════════════════════

@dataclass
class BBResult:
    """Bollinger Bands result."""
    upper: float = 0.0
    middle: float = 0.0       # SMA
    lower: float = 0.0
    width: float = 0.0        # (upper - lower) / middle — squeeze indicator
    pct_b: float = 0.5        # Where price is within bands (0-1)
    signal: float = 0.0       # -1 to +1 for fusion
    is_squeeze: bool = False   # Width below threshold → breakout incoming
    squeeze_strength: float = 0.0  # How tight the squeeze is (0-1)


class BollingerBands:
    """Bollinger Bands adapted for odds series.

    Primary use: squeeze detection → breakout anticipation.
    When BB Width < threshold → market is coiling → position larger.

    Default: period=20, std_dev=2.0, squeeze_threshold=0.03
    (odds range is 0-1, so 3c width = very tight squeeze).
    """

    def __init__(self, period: int = 20, std_dev: float = 2.0,
                 squeeze_threshold: float = 0.03):
        self.period = period
        self.std_dev = std_dev
        self.squeeze_threshold = squeeze_threshold

    def calculate(self, series: list[float]) -> BBResult:
        """Calculate Bollinger Bands from odds series."""
        if len(series) < self.period:
            return BBResult()

        recent = series[-self.period:]
        middle = sum(recent) / len(recent)

        # Standard deviation
        variance = sum((x - middle) ** 2 for x in recent) / len(recent)
        std = math.sqrt(variance)

        upper = middle + (self.std_dev * std)
        lower = middle - (self.std_dev * std)

        # Width (normalized)
        width = (upper - lower) / max(middle, 0.01)

        # %B — where current price sits (0 = lower band, 1 = upper band)
        current = series[-1]
        band_range = upper - lower
        if band_range > 0.001:
            pct_b = (current - lower) / band_range
        else:
            pct_b = 0.5

        # Squeeze detection
        is_squeeze = width < self.squeeze_threshold
        squeeze_strength = 0.0
        if is_squeeze:
            # How tight: 0.03 → 0.0 strength, 0.01 → 0.67 strength, 0.005 → 0.83
            squeeze_strength = max(0, 1 - (width / self.squeeze_threshold))

        # Signal: %B based
        # Near lower band → oversold → positive signal
        # Near upper band → overbought → negative signal
        # In squeeze → neutral but amplified later
        if pct_b < 0.2:
            signal = (0.2 - pct_b) / 0.2  # 0 to +1
        elif pct_b > 0.8:
            signal = -(pct_b - 0.8) / 0.2  # 0 to -1
        else:
            signal = 0.0

        return BBResult(
            upper=round(upper, 6),
            middle=round(middle, 6),
            lower=round(lower, 6),
            width=round(width, 6),
            pct_b=round(max(0, min(1, pct_b)), 4),
            signal=round(max(-1, min(1, signal)), 4),
            is_squeeze=is_squeeze,
            squeeze_strength=round(squeeze_strength, 4),
        )


# ═══════════════════════════════════════════════════════════════
# Convenience: compute all indicators at once
# ═══════════════════════════════════════════════════════════════

@dataclass
class TechnicalSignals:
    """Combined technical indicator signals."""
    rsi: RSIResult = None
    macd: MACDResult = None
    bb: BBResult = None
    # Composite technical confidence multiplier
    confidence_mult: float = 1.0
    # Direction agreement: +1 = all agree bullish, -1 = all bearish, 0 = mixed
    direction_agreement: float = 0.0

    def __post_init__(self):
        if self.rsi is None:
            self.rsi = RSIResult()
        if self.macd is None:
            self.macd = MACDResult()
        if self.bb is None:
            self.bb = BBResult()


def compute_technicals(
    odds_series: list[float],
    rsi_period: int = 14,
    macd_params: tuple = (8, 16, 5),
    bb_period: int = 20,
) -> TechnicalSignals:
    """Compute all technical indicators and return combined result.

    Returns confidence multiplier:
      - All indicators agree with direction → 1.3x boost
      - Mixed signals → 1.0x (neutral)
      - All indicators disagree → 0.7x penalty
    """
    rsi = RSI(period=rsi_period).calculate(odds_series)
    macd_ind = MACD(*macd_params).calculate(odds_series)
    bb = BollingerBands(period=bb_period).calculate(odds_series)

    # Direction agreement
    signals = [rsi.signal, macd_ind.signal, bb.signal]
    non_zero = [s for s in signals if abs(s) > 0.1]

    if not non_zero:
        agreement = 0.0
        confidence_mult = 1.0
    else:
        # Average of non-zero signals
        agreement = sum(non_zero) / len(non_zero)

        if len(non_zero) >= 2:
            # Check if all same sign
            all_positive = all(s > 0 for s in non_zero)
            all_negative = all(s < 0 for s in non_zero)

            if all_positive or all_negative:
                confidence_mult = 1.3  # Strong agreement
            else:
                confidence_mult = 0.7  # Conflicting signals → penalty
        else:
            confidence_mult = 1.0  # Only one indicator active

    # BB squeeze boost
    if bb.is_squeeze and bb.squeeze_strength > 0.5:
        confidence_mult *= 1.1  # Extra boost during squeeze

    return TechnicalSignals(
        rsi=rsi,
        macd=macd_ind,
        bb=bb,
        confidence_mult=round(max(0.5, min(1.5, confidence_mult)), 3),
        direction_agreement=round(agreement, 4),
    )
