"""Unit tests for core/risk_manager.py — pre-trade gate."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.risk_manager import RiskLimits, RiskManager


@pytest.fixture
def rm():
    limits = RiskLimits(
        max_position_size=10.0,
        max_open_positions=5,
        max_total_exposure=100.0,
        max_daily_loss=50.0,
        max_daily_trades=200,
        max_loss_streak=10,
        min_balance_floor=100.0,
        max_single_market_exposure=20.0,
    )
    m = RiskManager(limits=limits)
    # Prevent _maybe_reset_daily from wiping test-set counters
    m.state.daily_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return m


class TestRiskGates:
    def test_happy_path_passes(self, rm):
        v = rm.check_trade(5.0, "BTC-UP-DOWN-5m-abc", wallet_balance=500)
        assert v.approved is True

    def test_blocks_oversize_position(self, rm):
        v = rm.check_trade(15.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "POSITION_SIZE" in v.reason

    def test_blocks_when_halted(self, rm):
        rm.state.halted = True
        rm.state.halt_reason = "test"
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "HALT" in v.reason

    def test_blocks_on_daily_loss(self, rm):
        rm.state.daily_pnl = -60.0
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "DAILY_LOSS" in v.reason
        # After trip, should also halt the engine
        assert rm.state.halted is True

    def test_blocks_on_loss_streak(self, rm):
        rm.state.consecutive_losses = 11
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "LOSS_STREAK" in v.reason

    def test_blocks_below_balance_floor(self, rm):
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=102.0)
        assert v.approved is False
        assert "BALANCE_FLOOR" in v.reason

    def test_blocks_exposure_limit(self, rm):
        rm.state.total_exposure = 99.0
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "EXPOSURE" in v.reason

    def test_blocks_too_many_positions(self, rm):
        rm.state.open_position_count = 5
        v = rm.check_trade(5.0, "BTC-5m", wallet_balance=500)
        assert v.approved is False
        assert "MAX_POSITIONS" in v.reason


class TestAssetLimits:
    def test_asset_limit_enforced(self, rm):
        rm.per_asset_exposure["BTC"] = 499.0
        ok, reason = rm.check_asset_limit("BTC", 5.0)
        assert ok is False
        assert "BTC" in reason

    def test_asset_limit_unknown_asset_allowed(self, rm):
        # DOGE has no configured limit — should pass through
        ok, _ = rm.check_asset_limit("DOGE", 999.0)
        assert ok is True

    def test_extract_asset_from_slug(self, rm):
        assert rm._extract_asset_from_slug("BTC-UP-DOWN-5m") == "BTC"
        assert rm._extract_asset_from_slug("eth-15m") == "ETH"
        # P0-08-D refactor (2026-05-08): slug parser delegates to
        # core.slug_utils.infer_asset_from_slug, which returns "?" for
        # empty / unparseable inputs (was "" pre-refactor).
        assert rm._extract_asset_from_slug("") == "?"


class TestRecordTradeOpened:
    def test_increments_counters(self, rm):
        rm.record_trade_opened(5.0, "BTC-5m")
        assert rm.state.open_position_count == 1
        assert rm.state.total_exposure == pytest.approx(5.0)
        assert rm.state.daily_trade_count == 1
        assert rm.state.per_market_exposure["BTC-5m"] == pytest.approx(5.0)
        assert rm.per_asset_exposure["BTC"] == pytest.approx(5.0)
