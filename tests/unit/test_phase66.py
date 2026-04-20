"""
Phase 66: Measurement Infrastructure Tests
==========================================
Tests BayesianUpdater, Brier Score, Liquidity Check, Unsellable Token Detection.
"""
import pytest
from core.signal_fusion import BayesianUpdater, SignalFusion, SignalWeights
from core.risk_manager import RiskManager, RiskLimits


# ═══ BayesianUpdater Tests ═══

class TestBayesianUpdater:
    def test_init_prior(self):
        bu = BayesianUpdater(prior=0.60)
        assert abs(bu.posterior - 0.60) < 0.001

    def test_prior_clamp(self):
        bu = BayesianUpdater(prior=1.5)
        assert bu.posterior <= 0.999
        bu2 = BayesianUpdater(prior=-0.5)
        assert bu2.posterior >= 0.001

    def test_bullish_signal_increases_posterior(self):
        bu = BayesianUpdater(prior=0.50)
        bu.update(signal_strength=0.8, accuracy=0.65)
        assert bu.posterior > 0.50

    def test_bearish_signal_decreases_posterior(self):
        bu = BayesianUpdater(prior=0.50)
        bu.update(signal_strength=-0.8, accuracy=0.65)
        assert bu.posterior < 0.50

    def test_multiple_updates(self):
        bu = BayesianUpdater(prior=0.50)
        bu.update(0.5, 0.60)
        bu.update(0.3, 0.55)
        bu.update(-0.2, 0.55)
        assert bu.update_count == 3

    def test_edge_calculation(self):
        bu = BayesianUpdater(prior=0.70)
        bu.update(0.5, 0.65)
        edge = bu.get_edge(0.60)
        assert edge > 0  # posterior > market

    def test_confidence(self):
        bu = BayesianUpdater(prior=0.50)
        assert bu.confidence == 0.0  # max uncertainty
        bu.update(0.9, 0.80)
        assert bu.confidence > 0.0

    def test_neutral_signal(self):
        bu = BayesianUpdater(prior=0.60)
        old = bu.posterior
        bu.update(0.0, 0.60)  # zero signal
        assert abs(bu.posterior - old) < 0.01  # barely changes

    def test_accuracy_clamp(self):
        bu = BayesianUpdater(prior=0.50)
        bu.update(0.5, accuracy=1.5)  # above max → clamped to 0.95
        assert 0 < bu.posterior < 1

    def test_summary(self):
        bu = BayesianUpdater(prior=0.60)
        bu.update(0.5, 0.60)
        s = bu.summary()
        assert "BayesPost=" in s
        assert "conf=" in s


# ═══ SignalFusion + Bayesian Integration ═══

class TestSignalFusionBayesian:
    def test_bayesian_added_to_result(self):
        sf = SignalFusion()
        result = sf.evaluate(
            up_odds=0.65, down_odds=0.35,
            threshold=0.55, direction="up",
            odds_series=[0.58, 0.59, 0.60, 0.61, 0.62, 0.63, 0.64, 0.65],
            orderbook={"bids": [(0.64, 100)], "asks": [(0.66, 80)]}
        )
        assert result.bayesian_posterior > 0
        assert "bayes_edge" in result.signals

    def test_no_direction_no_bayesian(self):
        sf = SignalFusion()
        result = sf.evaluate(
            up_odds=0.30, down_odds=0.30,
            threshold=0.55, direction="up"
        )
        assert result.bayesian_posterior == 0  # no direction matched


# ═══ Liquidity Check Tests ═══

class TestLiquidityCheck:
    def setup_method(self):
        self.rm = RiskManager()

    def test_sufficient_liquidity(self):
        v = self.rm.check_liquidity_for_exit(
            5.0, {"bids": [(0.65, 100), (0.64, 200)], "asks": []})
        assert v.approved

    def test_insufficient_liquidity(self):
        v = self.rm.check_liquidity_for_exit(
            10.0, {"bids": [(0.65, 1)], "asks": []})
        assert not v.approved
        assert "LOW_LIQUIDITY" in v.reason

    def test_no_bids(self):
        v = self.rm.check_liquidity_for_exit(5.0, {"bids": [], "asks": []})
        assert not v.approved
        assert "NO_BIDS" in v.reason

    def test_no_orderbook(self):
        v = self.rm.check_liquidity_for_exit(5.0, None)
        assert v.approved  # conservative: allow exit with warning

    def test_penny_bid(self):
        v = self.rm.check_liquidity_for_exit(
            5.0, {"bids": [(0.01, 1000)], "asks": []})
        assert not v.approved
        assert "PENNY_BID" in v.reason


# ═══ Unsellable Token Tests ═══

class TestUnsellableCheck:
    def setup_method(self):
        self.rm = RiskManager()

    def test_extreme_odds_high(self):
        v = self.rm.check_unsellable_risk(0.97, {})
        assert not v.approved
        assert "EXTREME_ODDS" in v.reason

    def test_extreme_odds_low(self):
        v = self.rm.check_unsellable_risk(0.03, {})
        assert not v.approved

    def test_thin_book(self):
        v = self.rm.check_unsellable_risk(
            0.60, {"bids": [(0.59, 2)], "asks": [(0.61, 2)]})
        assert not v.approved
        assert "THIN_BOOK" in v.reason

    def test_near_close(self):
        v = self.rm.check_unsellable_risk(
            0.60, {"bids": [(0.59, 100)], "asks": [(0.61, 80)]},
            minutes_to_close=1.0)
        assert not v.approved
        assert "NEAR_CLOSE" in v.reason

    def test_safe_entry(self):
        v = self.rm.check_unsellable_risk(
            0.60, {"bids": [(0.59, 100)], "asks": [(0.61, 80)]},
            minutes_to_close=3.5)
        assert v.approved

    def test_normal_odds_no_book(self):
        v = self.rm.check_unsellable_risk(0.60, None)
        assert v.approved  # no orderbook → only odds check
