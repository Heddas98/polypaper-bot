"""
Phase 68: Signal Enhancement Tests
====================================
Tests Technical Indicators, Confluence Gate, and BB Squeeze.
"""

import math

import pytest

# ═══ RSI Tests ═══


class TestRSI:
    def test_basic_rsi(self):
        from indicators.technical import RSI

        rsi = RSI(period=14)
        # Generate an uptrending series (RSI should be high)
        series = [0.50 + i * 0.005 for i in range(20)]
        result = rsi.calculate(series)
        assert result.value > 50

    def test_rsi_oversold(self):
        from indicators.technical import RSI

        rsi = RSI(period=14)
        # Downtrending series
        series = [0.70 - i * 0.01 for i in range(20)]
        result = rsi.calculate(series)
        assert result.value < 50
        # If strong enough, should have positive signal (contrarian)
        if result.is_oversold:
            assert result.signal > 0

    def test_rsi_overbought(self):
        from indicators.technical import RSI

        rsi = RSI(period=14)
        # Strong uptrend
        series = [0.30 + i * 0.015 for i in range(20)]
        result = rsi.calculate(series)
        if result.is_overbought:
            assert result.signal < 0

    def test_rsi_insufficient_data(self):
        from indicators.technical import RSI

        rsi = RSI(period=14)
        result = rsi.calculate([0.5, 0.51, 0.52])
        assert result.value == 50.0  # default

    def test_rsi_signal_range(self):
        from indicators.technical import RSI

        rsi = RSI(period=14)
        series = [0.50 + i * 0.003 for i in range(20)]
        result = rsi.calculate(series)
        assert -1 <= result.signal <= 1


# ═══ MACD Tests ═══


class TestMACD:
    def test_basic_macd(self):
        from indicators.technical import MACD

        macd = MACD(fast=8, slow=16, signal=5)
        series = [0.50 + i * 0.003 for i in range(25)]
        result = macd.calculate(series)
        assert result.macd_line != 0 or result.signal_line != 0

    def test_macd_bullish_trend(self):
        from indicators.technical import MACD

        macd = MACD(fast=8, slow=16, signal=5)
        # Strong uptrend
        series = [0.40 + i * 0.005 for i in range(30)]
        result = macd.calculate(series)
        assert result.macd_line > 0  # Fast EMA > Slow EMA

    def test_macd_insufficient_data(self):
        from indicators.technical import MACD

        macd = MACD(fast=8, slow=16, signal=5)
        result = macd.calculate([0.5, 0.51])
        assert result.macd_line == 0

    def test_macd_signal_range(self):
        from indicators.technical import MACD

        macd = MACD()
        series = [0.50 + math.sin(i / 3) * 0.05 for i in range(30)]
        result = macd.calculate(series)
        assert -1 <= result.signal <= 1


# ═══ Bollinger Bands Tests ═══


class TestBollingerBands:
    def test_basic_bb(self):
        from indicators.technical import BollingerBands

        bb = BollingerBands(period=20)
        series = [0.60 + (i % 5) * 0.005 for i in range(25)]
        result = bb.calculate(series)
        assert result.upper > result.middle > result.lower

    def test_bb_squeeze(self):
        from indicators.technical import BollingerBands

        bb = BollingerBands(period=20, squeeze_threshold=0.05)
        # Very tight series → squeeze
        series = [0.600 + (i % 3) * 0.001 for i in range(25)]
        result = bb.calculate(series)
        assert result.width < 0.05
        assert result.is_squeeze

    def test_bb_no_squeeze(self):
        from indicators.technical import BollingerBands

        bb = BollingerBands(period=20, squeeze_threshold=0.03)
        # Volatile series
        series = [0.50 + math.sin(i) * 0.10 for i in range(25)]
        result = bb.calculate(series)
        assert not result.is_squeeze

    def test_bb_pct_b_range(self):
        from indicators.technical import BollingerBands

        bb = BollingerBands(period=20)
        series = [0.60 + (i % 5) * 0.003 for i in range(25)]
        result = bb.calculate(series)
        assert 0 <= result.pct_b <= 1

    def test_bb_insufficient_data(self):
        from indicators.technical import BollingerBands

        bb = BollingerBands(period=20)
        result = bb.calculate([0.5, 0.51, 0.52])
        assert result.middle == 0


# ═══ Combined Technical Signals ═══


class TestComputeTechnicals:
    def test_compute_all(self):
        from indicators.technical import compute_technicals

        series = [0.50 + i * 0.003 for i in range(30)]
        result = compute_technicals(series)
        assert result.rsi is not None
        assert result.macd is not None
        assert result.bb is not None
        assert 0.5 <= result.confidence_mult <= 1.5

    def test_confidence_mult_boost(self):
        from indicators.technical import compute_technicals

        # Strong consistent uptrend → all indicators should agree
        series = [0.40 + i * 0.006 for i in range(30)]
        result = compute_technicals(series)
        # With strong trend, indicators tend to agree
        assert result.confidence_mult >= 1.0

    def test_short_series(self):
        from indicators.technical import compute_technicals

        result = compute_technicals([0.5, 0.51, 0.52])
        # With insufficient data, should still return defaults
        assert result.confidence_mult == 1.0


# ═══ Confluence Gate Tests ═══


class TestConfluenceGate:
    def test_confluence_in_signal_result(self):
        from core.signal_fusion import SignalFusion

        sf = SignalFusion()
        result = sf.evaluate(
            up_odds=0.70,
            down_odds=0.30,
            threshold=0.55,
            direction="up",
            odds_series=[0.55, 0.57, 0.59, 0.61, 0.63, 0.65, 0.67, 0.69, 0.70] * 3,
            orderbook={"bids": [(0.69, 100)], "asks": [(0.71, 80)]},
        )
        assert result.confluence_count >= 0
        assert result.confluence_required > 0
        assert "confluence" in result.signals

    def test_confluence_count_basic(self):
        from core.signal_fusion import SignalFusion

        sf = SignalFusion()
        # Strong signals in one direction
        result = sf.evaluate(
            up_odds=0.80,
            down_odds=0.20,
            threshold=0.55,
            direction="up",
            odds_series=[0.60, 0.62, 0.65, 0.68, 0.70, 0.73, 0.76, 0.78, 0.80] * 3,
            orderbook={"bids": [(0.79, 200)], "asks": [(0.81, 50)]},
            minutes_remaining=3.0,
            total_minutes=5.0,
        )
        # With strong directional signals, most should be positive
        assert result.confluence_count >= 3


# ═══ Technical Integration in SignalFusion ═══


class TestTechnicalInFusion:
    def test_technical_mult_in_result(self):
        from core.signal_fusion import SignalFusion

        sf = SignalFusion()
        # Need 15+ data points for technical indicators
        series = [0.55 + i * 0.005 for i in range(20)]
        result = sf.evaluate(
            up_odds=0.65,
            down_odds=0.35,
            threshold=0.55,
            direction="up",
            odds_series=series,
            orderbook={"bids": [(0.64, 100)], "asks": [(0.66, 80)]},
        )
        # technical_mult should be set (not default 1.0 if enough data)
        assert result.technical_mult > 0

    def test_summary_includes_new_fields(self):
        from core.signal_fusion import SignalResult

        sr = SignalResult(
            composite_score=0.35,
            signals={"odds": 0.5, "ema": 0.3},
            confluence_count=4,
            confluence_required=4,
            technical_mult=1.3,
            bb_squeeze=True,
        )
        s = sr.summary()
        assert "conf=4/4" in s
        assert "tech=1.30" in s
        assert "squeeze" in s
