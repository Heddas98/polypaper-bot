"""Unit tests for WhaleFlowSignal (Phase 60)"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.signals.whale_flow import WhaleFlowSignal
from core.signal_fusion import SignalFusion, SignalWeights


class TestWhaleFlowSignal:
    """Test WhaleFlowSignal computation and integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.whale = WhaleFlowSignal(
            lookback_seconds=300,
            min_trades=2,
            min_volume_usd=100.0
        )

    @pytest.mark.asyncio
    async def test_whale_signal_buy_dominant_up_direction(self):
        """Test positive signal when buys dominate and direction is UP."""
        # Mock DB with 70% buy, 30% sell volume
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 3, 7000.0),    # 3 buys, $7,000 total
                ("sell", 1, 3000.0),   # 1 sell, $3,000 total
            ]
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Net flow = (7000 - 3000) / 10000 = 0.4
        # Direction is UP, so signal stays positive
        assert signal == pytest.approx(0.4, abs=0.001)
        assert 0 < signal <= 1.0

    @pytest.mark.asyncio
    async def test_whale_signal_buy_dominant_down_direction(self):
        """Test negative signal when buys dominate but direction is DOWN."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 3, 7000.0),
                ("sell", 1, 3000.0),
            ]
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="down"
        )

        # Net flow = (7000 - 3000) / 10000 = 0.4
        # Direction is DOWN, so signal flips: -0.4
        assert signal == pytest.approx(-0.4, abs=0.001)
        assert -1.0 <= signal < 0

    @pytest.mark.asyncio
    async def test_whale_signal_sell_dominant(self):
        """Test negative signal when sells dominate (opposite polarity)."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 1, 3000.0),
                ("sell", 3, 7000.0),
            ]
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Net flow = (3000 - 7000) / 10000 = -0.4
        assert signal == pytest.approx(-0.4, abs=0.001)

    @pytest.mark.asyncio
    async def test_whale_signal_insufficient_trades(self):
        """Test zero signal when trade count below minimum."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 1, 5000.0),  # Only 1 trade, but min_trades=2
            ]
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        assert signal == 0.0

    @pytest.mark.asyncio
    async def test_whale_signal_insufficient_volume(self):
        """Test zero signal when volume below minimum."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 5, 50.0),   # Total $50 < min_volume_usd=100
                ("sell", 5, 30.0),
            ]
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        assert signal == 0.0

    @pytest.mark.asyncio
    async def test_whale_signal_no_data(self):
        """Test zero signal when no whale trades exist."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(return_value=[])

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        assert signal == 0.0

    @pytest.mark.asyncio
    async def test_whale_signal_db_error(self):
        """Test graceful handling of DB errors."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            side_effect=Exception("Database connection failed")
        )

        signal = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Should return 0.0, not raise
        assert signal == 0.0

    @pytest.mark.asyncio
    async def test_whale_signal_caching(self):
        """Test that results are cached for 5 seconds."""
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 3, 7000.0),
                ("sell", 1, 3000.0),
            ]
        )

        # First call — hits DB
        sig1 = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Second call immediately after — uses cache
        sig2 = await self.whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        assert sig1 == sig2
        assert mock_db.conn.execute_fetchall.call_count == 1  # Only 1 DB call

    def test_whale_signal_clear_cache(self):
        """Test cache clearing."""
        self.whale._cache[("BTC", "up")] = (0.0, 0.5)
        assert len(self.whale._cache) > 0

        self.whale.clear_cache()

        assert len(self.whale._cache) == 0


class TestWhaleSignalFusion:
    """Test integration of whale signal into signal fusion."""

    def test_signal_fusion_includes_whale_weight(self):
        """Test that whale signal weight is properly configured."""
        weights = SignalWeights()
        assert hasattr(weights, "whale_flow")
        assert weights.whale_flow == 0.10

    def test_signal_result_includes_whale_signal(self):
        """Test that SignalResult has whale_signal field."""
        sf = SignalFusion()
        result = sf.evaluate(
            up_odds=0.60,
            down_odds=0.40,
            threshold=0.50,
            direction="up",
            odds_series=[0.55, 0.57, 0.59, 0.61],
            minutes_remaining=2.5,
            whale_signal=0.3  # Pre-computed whale signal
        )

        assert hasattr(result, "whale_signal")
        assert result.whale_signal == 0.3
        assert "whale" in result.signals

    def test_signal_fusion_whale_signal_in_composite(self):
        """Test that whale signal contributes to composite score."""
        sf = SignalFusion()

        # Evaluate WITHOUT whale signal
        result_no_whale = sf.evaluate(
            up_odds=0.60,
            down_odds=0.40,
            threshold=0.50,
            direction="up",
            odds_series=[0.55, 0.57, 0.59, 0.61],
            minutes_remaining=2.5,
            whale_signal=0.0
        )

        # Evaluate WITH strong positive whale signal
        result_with_whale = sf.evaluate(
            up_odds=0.60,
            down_odds=0.40,
            threshold=0.50,
            direction="up",
            odds_series=[0.55, 0.57, 0.59, 0.61],
            minutes_remaining=2.5,
            whale_signal=0.5  # Strong whale support
        )

        # Composite score should be higher with whale support
        assert result_with_whale.composite_score > result_no_whale.composite_score

    def test_signal_fusion_whale_signal_disabled(self):
        """Test that whale signal can be disabled via environment."""
        import os
        old_val = os.environ.get("WHALE_SIGNAL_ENABLED")

        try:
            os.environ["WHALE_SIGNAL_ENABLED"] = "false"
            # Reimport to pick up new ENV
            import importlib
            import core.signal_fusion
            importlib.reload(core.signal_fusion)

            sf = core.signal_fusion.SignalFusion()
            result = sf.evaluate(
                up_odds=0.60,
                down_odds=0.40,
                threshold=0.50,
                direction="up",
                odds_series=[0.55, 0.57, 0.59, 0.61],
                minutes_remaining=2.5,
                whale_signal=0.5
            )

            # Whale signal should not contribute to composite when disabled
            # (checking that it's in signals but not weighted)
            assert "whale" in result.signals
            assert result.signals["whale"] == 0.0  # Should be zeroed out
        finally:
            # Restore original value
            if old_val is not None:
                os.environ["WHALE_SIGNAL_ENABLED"] = old_val
            else:
                os.environ.pop("WHALE_SIGNAL_ENABLED", None)
            importlib.reload(core.signal_fusion)


class TestWhaleSignalEdgeCases:
    """Test edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_whale_signal_perfect_balance(self):
        """Test signal when buys exactly equal sells."""
        whale = WhaleFlowSignal()
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 2, 5000.0),
                ("sell", 2, 5000.0),
            ]
        )

        signal = await whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Net flow = (5000 - 5000) / 10000 = 0
        assert signal == pytest.approx(0.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_whale_signal_single_side_only_buys(self):
        """Test signal when only buy whales, no sells."""
        whale = WhaleFlowSignal()
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 3, 10000.0),
            ]
        )

        signal = await whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Net flow = (10000 - 0) / 10000 = 1.0 (clamped)
        assert signal == pytest.approx(1.0, abs=0.001)

    @pytest.mark.asyncio
    async def test_whale_signal_clamping(self):
        """Test that signal is properly clamped to [-1, 1]."""
        whale = WhaleFlowSignal()
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            return_value=[
                ("buy", 1, 100000.0),
                ("sell", 1, 1.0),
            ]
        )

        signal = await whale.compute(
            db=mock_db,
            slug="BTC-2025-01-10",
            direction="up"
        )

        # Even though ratio is extreme, should clamp to 1.0
        assert signal <= 1.0
        assert signal >= -1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
