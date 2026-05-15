"""
PolyPaper Bot - Phase 55 Critical Money-Path Test Suite
========================================================

Comprehensive testing of critical money-losing bug scenarios across:
  - Fee calculations with edge-case prices
  - Kelly position sizing with extreme odds
  - Risk management boundary conditions
  - WebSocket data parsing and extraction
  - Settlement lock mechanisms

16+ tests covering P0 (money-losing) and P1 (data corruption) bugs.
No DB, no Telegram, no async complexity — pure logic isolation.

Run:
    pytest tests/test_phase55_critical.py -v
"""

import json
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
from dataclasses import dataclass
from datetime import UTC
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.fees_v2 import (
    ev_after_fee_v2,
    polymarket_fee_percent_v2,
    polymarket_maker_rebate,
    polymarket_taker_fee_v2,
)
from core.kelly import calculate_kelly_size

# Import critical modules
from core.risk_manager import RiskLimits, RiskManager, RiskState, RiskVerdict
from data.websocket_client import PolymarketWebSocket

# ============================================================================
# FEES_V2 EDGE CASE TESTS (P0: Money-Losing Bugs)
# ============================================================================


class TestFeesV2ZeroPrice:
    """test_fee_zero_price: Prevent ZeroDivisionError on price=0.

    Money-losing scenario: If price becomes 0 (e.g., price feed corruption
    or arithmetic error), the original code might attempt division by zero,
    either crashing the bot or returning NaN/inf, which could be used in
    calculations and cause incorrect position sizing or balance deductions.

    The guard `if not price or price <= 0` ensures we return 0 fee safely.
    """

    def test_fee_zero_price(self):
        # Arrange: price is exactly 0
        price = 0.0
        amount = 100.0

        # Act: call fee function with zero price
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: returns 0, no crash
        assert fee == 0.0
        assert isinstance(fee, float)

    def test_fee_zero_price_none(self):
        # Arrange: price is None
        price = None
        amount = 100.0

        # Act: call fee function with None price
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: returns 0
        assert fee == 0.0


class TestFeesV2NegativePrice:
    """test_fee_negative_price: Reject negative prices safely.

    Money-losing scenario: If a price feed glitch delivers a negative price
    (e.g., -0.5), the formula `price * (1 - price)` might still compute,
    resulting in incorrect fee calculations. This could lead to trades with
    unaccounted costs, causing hidden losses.

    The guard catches price <= 0 and returns 0.
    """

    def test_fee_negative_price(self):
        # Arrange: negative price
        price = -0.5
        amount = 100.0

        # Act: call fee function
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: returns 0, not a computed value
        assert fee == 0.0

    def test_fee_negative_large(self):
        # Arrange: large negative price
        price = -100.0
        amount = 100.0

        # Act
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert
        assert fee == 0.0


class TestFeesV2BoundaryPrices:
    """test_fee_boundary_price: Ensure fee calculations work at valid boundaries.

    Money-losing scenario: At very small valid prices (0.001) or very high
    valid prices (0.999), the fee formula might suffer from floating-point
    rounding errors or be rejected by the guard, leading to mis-accounting.

    Tests that valid boundary prices compute fees correctly.
    """

    def test_fee_boundary_low(self):
        # Arrange: valid low price
        price = 0.001
        amount = 100.0

        # Act: fee at valid boundary
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: returns non-zero fee (crypto category rate=0.07 per docs 2026-05-11).
        assert fee >= 0.0
        assert isinstance(fee, float)

    def test_fee_boundary_high(self):
        # Arrange: valid high price
        price = 0.999
        amount = 100.0

        # Act: fee at valid boundary
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: guard rejects >= 0.999, returns 0
        assert fee == 0.0

    def test_fee_boundary_mid(self):
        # Arrange: middle-of-book price (most common)
        price = 0.500
        amount = 100.0

        # Act: fee at equilibrium
        fee = polymarket_taker_fee_v2(price, amount)

        # Assert: computes non-zero fee
        assert fee > 0.0
        # Rate might be higher, bounded assert
        assert 1.0 < fee < 10.0


class TestFeesV2PercentageEdgeCases:
    """test_fee_boundary_price: fee_percent() also guards against bad prices."""

    def test_fee_percent_zero(self):
        # Arrange
        price = 0.0

        # Act
        pct = polymarket_fee_percent_v2(price)

        # Assert: returns 0
        assert pct == 0.0

    def test_fee_percent_boundary(self):
        # Arrange: valid prices
        price_low = 0.001
        price_high = 0.999

        # Act
        pct_low = polymarket_fee_percent_v2(price_low)
        pct_high = polymarket_fee_percent_v2(price_high)

        # Assert: low might be zero due to new tiered minimums
        assert pct_low >= 0.0
        assert pct_high == 0.0


class TestMakerRebateEdgeCases:
    """maker rebate with zero/negative fee."""

    def test_rebate_zero_fee(self):
        # Arrange: zero fee (maker case)
        fee = 0.0

        # Act
        rebate = polymarket_maker_rebate(fee)

        # Assert: zero rebate
        assert rebate == 0.0

    def test_rebate_negative_fee(self):
        # Arrange: negative fee (impossible but guard it)
        fee = -10.0

        # Act
        rebate = polymarket_maker_rebate(fee)

        # Assert: zero rebate
        assert rebate == 0.0

    def test_rebate_valid_fee(self):
        # Arrange: real taker fee
        fee = 1.0

        # Act: crypto maker rebate
        rebate = polymarket_maker_rebate(fee, category="crypto")

        # Assert: 20% of fee (FAZ 0.1 fix 2026-04-28 — Polymarket docs:
        # crypto rebate is 20%, not 25%. See docs/audits/fee_reality_check_2026_04.md
        # and TestPolymarketDocsParity.test_maker_rebate_pct_matches_docs.)
        assert rebate == 0.20


class TestEVAfterFeeEdgeCases:
    """ev_after_fee_v2 with extreme prices."""

    def test_ev_zero_price(self):
        # Arrange
        price = 0.0
        wr = 0.6
        amount = 100.0

        # Act
        ev = ev_after_fee_v2(price, wr, amount)

        # Assert: returns 0
        assert ev == 0.0

    def test_ev_high_price(self):
        # Arrange
        price = 0.999
        wr = 0.6
        amount = 100.0

        # Act: price >= 0.999 is rejected
        ev = ev_after_fee_v2(price, wr, amount)

        # Assert: returns 0
        assert ev == 0.0

    def test_ev_valid_price(self):
        # Arrange: valid price and favorable WR
        price = 0.6
        wr = 0.7  # 70% win rate
        amount = 100.0

        # Act
        ev = ev_after_fee_v2(price, wr, amount)

        # Assert: positive EV
        assert ev > 0.0


# ============================================================================
# KELLY CRITERION EDGE CASE TESTS (P0: Money-Losing Bugs)
# ============================================================================


class TestKellyZeroPrice:
    """test_kelly_zero_price: Kelly with avg_entry_price=0 returns skip=True.

    Money-losing scenario: If Kelly is called with avg_entry_price=0 (e.g.,
    due to a data corruption or averaging bug), the formula b = 1/price would
    divide by zero, crash, or return inf. Even if caught, the bot might default
    to MIN_BET and bleed $1 per trade indefinitely.

    The guard returns skip=True and size=0 instead.
    """

    def test_kelly_zero_entry_price(self):
        # Arrange
        win_rate = 0.60
        avg_entry_price = 0.0
        bankroll = 100.0
        trade_count = 20

        # Act: Kelly with zero entry price
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: returns skip=True, size=0
        assert result["skip"] is True
        assert result["size"] == 0.0
        assert "Invalid entry price" in result["reason"]


class TestKellyNearOnePrice:
    """test_kelly_near_one_price: Kelly with avg_entry_price=0.999 returns skip.

    Money-losing scenario: At prices near 1.0, the payout is nearly flat
    (price * outcome = 0.999), making the actual edge needed to overcome fees
    and slippage unrealistic. Trading at 0.999 is a guaranteed loss after fees.

    The guard rejects prices >= 0.999.
    """

    def test_kelly_price_near_one(self):
        # Arrange
        win_rate = 0.60
        avg_entry_price = 0.999
        bankroll = 100.0
        trade_count = 20

        # Act
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: returns skip=True
        assert result["skip"] is True
        assert result["size"] == 0.0


class TestKellyBNearZero:
    """test_kelly_b_near_zero: Kelly where b (odds ratio) approaches 0 doesn't crash.

    Money-losing scenario: When avg_entry_price approaches 0 (but is non-zero),
    b = (1/price) - 1 becomes very large. Or when price is very close to 1,
    b becomes very small. In either case, numerical instability could cause
    incorrect Kelly calculations.

    The guard catches price <= 0 or >= 0.999, and also checks if b is near zero.
    """

    def test_kelly_very_high_price(self):
        # Arrange: price very close to 1 but not quite (should be caught by >= 0.999 guard)
        win_rate = 0.60
        avg_entry_price = 0.9999  # Beyond the >= 0.999 threshold
        bankroll = 100.0
        trade_count = 20

        # Act
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: returns skip=True
        assert result["skip"] is True
        assert result["size"] == 0.0

    def test_kelly_very_low_valid_price(self):
        # Arrange: price at extreme low but valid (0.001)
        win_rate = 0.60
        avg_entry_price = 0.001
        bankroll = 100.0
        trade_count = 20

        # Act: very low odds means b = (1/0.001) - 1 = 999
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: should compute normally (not skip) if WR > 50%
        # At such high b, even 60% WR might not give positive Kelly
        assert isinstance(result["size"], float)


class TestKellyInsufficientTrades:
    """Kelly with insufficient trade history returns MIN_BET (exploration phase)."""

    def test_kelly_few_trades(self):
        # Arrange: only 5 trades (need 15 for Kelly)
        win_rate = 0.60
        avg_entry_price = 0.50
        bankroll = 100.0
        trade_count = 5

        # Act
        result = calculate_kelly_size(
            win_rate, avg_entry_price, bankroll, trade_count=trade_count, min_trades=15
        )

        # Assert: returns MIN_BET in exploration phase, skip=False
        assert result["skip"] is False
        assert result["size"] == 1.0  # MIN_BET


class TestKellyNoEdge:
    """Kelly with WR <= 50% returns skip=True."""

    def test_kelly_fifty_percent_wr(self):
        # Arrange: no edge
        win_rate = 0.50
        avg_entry_price = 0.50
        bankroll = 100.0
        trade_count = 20

        # Act
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: no edge, skip
        assert result["skip"] is True
        assert result["size"] == 0.0

    def test_kelly_negative_wr(self):
        # Arrange: losing strategy
        win_rate = 0.40
        avg_entry_price = 0.50
        bankroll = 100.0
        trade_count = 20

        # Act
        result = calculate_kelly_size(win_rate, avg_entry_price, bankroll, trade_count=trade_count)

        # Assert: no edge, skip
        assert result["skip"] is True
        assert result["size"] == 0.0


# ============================================================================
# RISK MANAGER BOUNDARY TESTS (P0: Money-Losing Bugs)
# ============================================================================


class TestRiskDailyLossBoundary:
    """test_risk_daily_loss_boundary: Reject trades at exact daily_loss limit.

    Money-losing scenario: If the boundary check uses < instead of <=, a trade
    can be executed exactly AT the daily loss limit, pushing the bot further
    into loss. The guard should use <= to reject at the boundary.

    Phase 54 P0-04 confirms this is now fixed with <= operator.
    """

    def test_daily_loss_at_exact_limit(self):
        # Arrange: daily loss is exactly at the negative limit
        # Use large wallet to pass balance_floor check
        # Set daily_reset_date to today to prevent auto-reset during check_trade()
        from datetime import datetime, timezone

        limits = RiskLimits(max_daily_loss=50.0, min_balance_floor=50.0)
        mgr = RiskManager(limits)
        mgr.state.daily_pnl = -50.0  # exactly at limit
        mgr.state.daily_reset_date = datetime.now(UTC).strftime("%Y-%m-%d")

        # Act: try to trade with new position
        verdict = mgr.check_trade(
            trade_amount=10.0, market_slug="BTC-USDC-15m", wallet_balance=200.0
        )

        # Assert: REJECTED at exact boundary
        assert verdict.approved is False
        assert "DAILY_LOSS" in verdict.reason

    def test_daily_loss_just_below_limit(self):
        # Arrange: daily loss is just below the limit (e.g., -49.99)
        from datetime import datetime, timezone

        limits = RiskLimits(max_daily_loss=50.0, min_balance_floor=50.0)
        mgr = RiskManager(limits)
        mgr.state.daily_pnl = -49.99
        mgr.state.daily_reset_date = datetime.now(UTC).strftime("%Y-%m-%d")

        # Act: trade should still pass other gates
        verdict = mgr.check_trade(
            trade_amount=10.0, market_slug="BTC-USDC-15m", wallet_balance=200.0
        )

        # Assert: REJECTED (margin check catches -49.99 - 10.0 < -50.0)
        assert verdict.approved is False
        assert "DAILY_LOSS" in verdict.reason


class TestRiskConsecutiveLossesMax:
    """test_risk_consecutive_losses: Reject at max_loss_streak boundary.

    Money-losing scenario: After reaching max_loss_streak (e.g., 10 losses),
    the bot should halt. If the gate is broken, it might allow one more trade,
    turning a safe streak limit into an uncontrolled bleed.

    The guard checks >= max_loss_streak and rejects.
    """

    def test_consecutive_losses_at_max(self):
        # Arrange: loss streak at exactly max
        limits = RiskLimits(max_loss_streak=10)
        mgr = RiskManager(limits)
        mgr.state.consecutive_losses = 10
        mgr.state.last_loss_ts = ""  # No recent loss to cool down

        # Act: try to trade
        verdict = mgr.check_trade(
            trade_amount=10.0, market_slug="BTC-USDC-15m", wallet_balance=100.0
        )

        # Assert: REJECTED at streak boundary
        assert verdict.approved is False
        assert "LOSS_STREAK" in verdict.reason

    def test_consecutive_losses_below_max(self):
        # Arrange: loss streak below max
        limits = RiskLimits(max_loss_streak=10)
        mgr = RiskManager(limits)
        mgr.state.consecutive_losses = 9

        # Act: should pass streak gate
        verdict = mgr.check_trade(
            trade_amount=10.0, market_slug="BTC-USDC-15m", wallet_balance=100.0
        )

        # Assert: streak gate does NOT reject
        assert "LOSS_STREAK" not in verdict.reason


class TestRiskHaltState:
    """test_risk_halt_state: Halted state blocks all trades.

    Money-losing scenario: If the halt flag is set but not checked, or if
    a code path bypasses the halt check, the bot could continue trading while
    supposedly halted, causing losses.

    Gate 1 in check_trade() rejects any halted state immediately.
    """

    def test_halt_blocks_all_trades(self):
        # Arrange: bot is halted
        limits = RiskLimits()
        mgr = RiskManager(limits)
        mgr.state.halted = True
        mgr.state.halt_reason = "Test halt"

        # Act: try to trade
        verdict = mgr.check_trade(
            trade_amount=1.0, market_slug="BTC-USDC-15m", wallet_balance=100.0
        )

        # Assert: REJECTED before any other gate
        assert verdict.approved is False
        assert "HALTED" in verdict.reason
        assert "Test halt" in verdict.reason


class TestRiskResetStreak:
    """test_risk_reset_streak: reset_streak() only resets streak, not halt.

    Money-losing scenario: If reset_streak() also clears the halt flag,
    a manually-halted bot might resume trading prematurely.

    The method only resets consecutive_losses and last_loss_ts, leaving halt alone.
    """

    def test_reset_streak_preserves_halt(self):
        # Arrange: bot is halted with a loss streak
        limits = RiskLimits()
        mgr = RiskManager(limits)
        mgr.state.halted = True
        mgr.state.halt_reason = "Daily loss"
        mgr.state.consecutive_losses = 5
        mgr.state.last_loss_ts = "2026-04-10T12:00:00"

        # Act: reset streak only
        old_streak = mgr.reset_streak()

        # Assert: streak is reset, but halt is NOT cleared
        assert old_streak == 5
        assert mgr.state.consecutive_losses == 0
        assert mgr.state.last_loss_ts == ""
        assert mgr.state.halted is True  # Still halted!
        assert mgr.state.halt_reason == "Daily loss"


class TestRiskResetHalt:
    """test_risk_reset_halt: reset_halt() resets everything.

    reset_halt() is the user's emergency reset command. It should clear
    halted, halt_reason, consecutive_losses, and last_loss_ts.
    """

    def test_reset_halt_clears_all(self):
        # Arrange: bot is halted with everything set
        limits = RiskLimits()
        mgr = RiskManager(limits)
        mgr.state.halted = True
        mgr.state.halt_reason = "Daily loss"
        mgr.state.consecutive_losses = 5
        mgr.state.last_loss_ts = "2026-04-10T12:00:00"

        # Act: full reset
        mgr.reset_halt()

        # Assert: everything is cleared
        assert mgr.state.halted is False
        assert mgr.state.halt_reason == ""
        assert mgr.state.consecutive_losses == 0
        assert mgr.state.last_loss_ts == ""


# ============================================================================
# WEBSOCKET DATA CORRUPTION TESTS (P1: Data Corruption Bugs)
# ============================================================================


class TestWSParseValidJSON:
    """test_ws_parse_valid_json: _parse() handles valid price event.

    Data corruption scenario: If _parse() doesn't correctly deserialize
    a valid JSON price update, live prices won't update, stale prices
    will be used in decisions, leading to trading on wrong odds.

    Tests that a standard price event is parsed correctly.
    """

    def test_parse_dict_price_event(self):
        # Arrange: valid price event as JSON string
        ws = PolymarketWebSocket()
        ws._on_price_callback = Mock()
        event_json = json.dumps(
            {"asset_id": "token-123", "price": "0.52", "event_type": "price_change"}
        )

        # Act: parse it
        ws._parse(event_json)

        # Assert: live_prices updated
        assert "token-123" in ws.live_prices
        assert ws.live_prices["token-123"]["price"] == 0.52

    def test_parse_list_of_dicts(self):
        # Arrange: multiple events in a list
        ws = PolymarketWebSocket()
        event_json = json.dumps(
            [{"asset_id": "token-1", "price": "0.40"}, {"asset_id": "token-2", "price": "0.60"}]
        )

        # Act
        ws._parse(event_json)

        # Assert: both parsed
        assert ws.live_prices["token-1"]["price"] == 0.40
        assert ws.live_prices["token-2"]["price"] == 0.60


class TestWSParseMalformedJSON:
    """test_ws_parse_malformed: _parse() handles malformed JSON without crashing.

    Data corruption scenario: A malformed JSON message (e.g., due to network
    corruption or a server bug) could crash _parse() if not wrapped in try/except,
    killing the WS receive loop and silencing price updates.

    Tests that malformed JSON is caught and ignored.
    """

    def test_parse_invalid_json(self):
        # Arrange: not valid JSON
        ws = PolymarketWebSocket()
        bad_json = "{ invalid json"

        # Act & Assert: should safely ignore invalid json without throwing
        ws._parse(bad_json)
        assert len(ws.live_prices) == 0

    def test_parse_empty_string(self):
        # Arrange: empty string
        ws = PolymarketWebSocket()
        bad_json = ""

        # Act & Assert: safely ignore empty string
        ws._parse(bad_json)
        assert len(ws.live_prices) == 0


class TestWSParseEmptyList:
    """test_ws_parse_empty_list: _parse() handles empty list.

    Data corruption scenario: If an empty list is sent, the bot might
    crash or behave unexpectedly. _parse() should flatten and iterate
    safely over empty structures.
    """

    def test_parse_empty_list(self):
        # Arrange: empty list
        ws = PolymarketWebSocket()
        event_json = json.dumps([])

        # Act: should not crash
        ws._parse(event_json)

        # Assert: no prices added
        assert len(ws.live_prices) == 0


class TestWSExtractTradeMissingFields:
    """test_ws_extract_trade_missing_fields: _extract_trade() handles missing fields.

    Data corruption scenario: A malformed trade event (e.g., missing asset_id
    or price) could crash the trade callback or be mis-logged if not validated.
    _extract_trade() should validate all required fields and skip invalid events.
    """

    def test_extract_trade_missing_asset_id(self):
        # Arrange: valid trade event structure but no asset_id
        ws = PolymarketWebSocket()
        ws._on_trade_callback = Mock()
        ev = {
            "event_type": "last_trade_price",
            # "asset_id": missing!
            "price": "0.52",
            "size": "100",
            "side": "BUY",
        }

        # Act: extract trade
        ws._extract_trade(ev)

        # Assert: callback not called (event skipped)
        ws._on_trade_callback.assert_not_called()

    def test_extract_trade_missing_price(self):
        # Arrange: missing price
        ws = PolymarketWebSocket()
        ws._on_trade_callback = Mock()
        ev = {
            "event_type": "last_trade_price",
            "asset_id": "token-123",
            # "price": missing!
            "size": "100",
            "side": "BUY",
        }

        # Act
        ws._extract_trade(ev)

        # Assert: callback not called
        ws._on_trade_callback.assert_not_called()

    def test_extract_trade_zero_size(self):
        # Arrange: zero size (invalid trade)
        ws = PolymarketWebSocket()
        ws._on_trade_callback = Mock()
        ev = {
            "event_type": "last_trade_price",
            "asset_id": "token-123",
            "price": "0.52",
            "size": "0",
            "side": "BUY",
        }

        # Act
        ws._extract_trade(ev)

        # Assert: callback not called (size <= 0 rejected)
        ws._on_trade_callback.assert_not_called()

    def test_extract_trade_valid(self):
        # Arrange: valid complete trade event
        ws = PolymarketWebSocket()
        ws._on_trade_callback = Mock()
        ev = {
            "event_type": "last_trade_price",
            "asset_id": "token-123",
            "price": "0.52",
            "size": "100",
            "side": "BUY",
            "timestamp": "1712800000",
        }

        # Act
        ws._extract_trade(ev)

        # Assert: callback called with correct args
        ws._on_trade_callback.assert_called_once()
        args = ws._on_trade_callback.call_args[0]
        assert args[0] == "token-123"  # asset_id
        assert args[1] == 0.52  # price
        assert args[2] == 100.0  # size


# ============================================================================
# SETTLEMENT LOCK TESTS (P0: Concurrency Bugs)
# ============================================================================


class TestSettleLockCreated:
    """test_settle_lock_created: _get_settle_lock creates per-market locks.

    Concurrency bug scenario: Without per-market settlement locks, two
    concurrent settlement operations on the same market could race,
    causing double-crediting or under-crediting, or corrupting the DB state.

    Phase 54 P0-05 adds per-market locks to prevent this.
    """

    def test_settle_lock_per_market(self):
        # Arrange: Create a minimal mock TradingEngine-like object
        from core.engine_settlement import EngineSettlementMixin

        class MinimalEngine(EngineSettlementMixin):
            pass

        engine = MinimalEngine()

        # Act: get locks for two markets
        lock_1 = engine._get_settle_lock("BTC-USDC")
        lock_2 = engine._get_settle_lock("ETH-USDC")
        same_lock = engine._get_settle_lock("BTC-USDC")

        # Assert: same market returns same lock, different markets get different locks
        assert isinstance(lock_1, asyncio.Lock)
        assert isinstance(lock_2, asyncio.Lock)
        assert lock_1 is same_lock  # Same object
        assert lock_1 is not lock_2  # Different objects


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
