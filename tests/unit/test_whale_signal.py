"""Unit tests for WhaleFlowSignal (Phase 60)"""

import asyncio

import aiosqlite
import pytest
from unittest.mock import AsyncMock, MagicMock
from core.signals.whale_flow import WhaleFlowSignal
from core.signal_fusion import SignalFusion, SignalWeights


# Epic 9 T9.5 (2026-04-22): autouse env + module-flag isolation.
# See tests/unit/test_phase66.py for full doctrine — whale/bayesian flags are
# module-level in core.signal_fusion, sibling tests that patch them must not
# leak. This fixture restores canonical True state before each test.
@pytest.fixture(autouse=True)
def _clean_signal_env(monkeypatch):
    for var in (
        "SIGNAL_W_WHALE",
        "SIGNAL_W_MOMENTUM",
        "SIGNAL_W_EMA",
        "SIGNAL_W_ORDERBOOK",
        "SIGNAL_W_TIME",
        "SIGNAL_W_ODDS",
        "SIGNAL_W_VOLATILITY",
        "WHALE_SIGNAL_ENABLED",
        "BAYESIAN_UPDATER_ENABLED",
    ):
        monkeypatch.delenv(var, raising=False)
    import core.signal_fusion as sf_mod
    monkeypatch.setattr(sf_mod, "_WHALE_SIGNAL_ENABLED", True)
    monkeypatch.setattr(sf_mod, "_BAYESIAN_ENABLED", True)
    yield


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
        """Test graceful handling of aiosqlite DB errors.

        T1.4 Faz 3 — compute() now narrows to aiosqlite.Error (the actual
        exception family raised by aiosqlite). Real-world failures
        (connection lost, table locked, schema mismatch) all surface as
        aiosqlite.Error subclasses, so this test exercises the realistic
        failure path rather than a generic Exception.
        """
        mock_db = MagicMock()
        mock_db.conn.execute_fetchall = AsyncMock(
            side_effect=aiosqlite.Error("Database connection failed")
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
    """Test integration of whale signal into signal fusion.

    Epic 9 T9.5 (2026-04-22): Post-Phase-79b rewrite.

    Phase 79b rebalance (c820906) sıfırladı whale_flow default'unu
    0.10 → 0.00 ("next candle prediction" focus). Bu testler artık
    kasıtlı olarak **dependency injection** pattern kullanıyor:
    SignalFusion(weights=SignalWeights(whale_flow=0.10)) ile fusion'ı
    aktive edip whale katkısını doğruluyor. Önceki `importlib.reload`
    + raw `os.environ[]` yaklaşımı state-leak üretiyordu (bkz.
    FLAKY_AUDIT.md CRITICAL section).
    """

    def test_signal_fusion_includes_whale_weight(self):
        """SignalWeights has whale_flow field + configurable via DI.

        Default 0.00 (Phase 79b rebalance), but can be overridden at
        construction time without env patching.
        """
        # Default: whale disabled
        weights_default = SignalWeights()
        assert hasattr(weights_default, "whale_flow")
        assert weights_default.whale_flow == 0.00  # Phase 79b default

        # DI override: whale enabled at 0.10 (legacy Phase 60 default)
        weights_enabled = SignalWeights(whale_flow=0.10)
        assert weights_enabled.whale_flow == 0.10

    def test_signal_result_includes_whale_signal(self):
        """SignalResult.whale_signal field populated regardless of weight."""
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
        """Whale signal contributes to composite when weight > 0.

        Epic 9 T9.5: Previously relied on default weight 0.10 (Phase 60)
        which was zeroed out in Phase 79b. Now uses DI to set weight
        explicitly, independent of ENV.
        """
        # Inject weight 0.10 so whale_signal meaningfully contributes
        weights = SignalWeights(whale_flow=0.10)
        sf = SignalFusion(weights=weights)

        # Evaluate WITHOUT whale signal (whale_signal=0.0)
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

        # With weight=0.10 + whale_signal=0.5 vs 0.0 → composite must differ
        assert result_with_whale.composite_score > result_no_whale.composite_score

    def test_signal_fusion_whale_signal_disabled(self, monkeypatch):
        """Whale signal can be disabled via WHALE_SIGNAL_ENABLED=false.

        Epic 9 T9.5: `importlib.reload` kaldırıldı (state-leak tehlikesi).
        monkeypatch ile module-level _WHALE_SIGNAL_ENABLED flag'i direkt
        patch ediliyor — reload gerekmiyor.
        """
        import core.signal_fusion as sf_mod

        # Module-level flag'i false'a zorla (reload yerine direkt patch)
        monkeypatch.setattr(sf_mod, "_WHALE_SIGNAL_ENABLED", False)

        sf = sf_mod.SignalFusion()
        result = sf.evaluate(
            up_odds=0.60,
            down_odds=0.40,
            threshold=0.50,
            direction="up",
            odds_series=[0.55, 0.57, 0.59, 0.61],
            minutes_remaining=2.5,
            whale_signal=0.5
        )

        # Whale signal should be zeroed out when disabled
        assert "whale" in result.signals
        assert result.signals["whale"] == 0.0


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
