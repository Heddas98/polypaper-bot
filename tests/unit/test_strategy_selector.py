"""Unit tests for core/strategy_selector.py — Thompson Sampling bandit.

Coverage gap baseline (2026-04-29): `strategy_selector.py` 0% / 89 stmts.

Phase 33+58: multi-armed bandit allocates capital to winning strategies.
Beta(alpha, beta) per arm, decay 0.995 per trade, top THOMPSON_TOP_PCT
(default 0.40) trades each cycle.

Tests pin:
  - ArmState defaults (α=2, β=2, neutral prior)
  - update() applies decay + increments correct side
  - win_rate from α/(α+β)
  - sample() in [0, 1]
  - StrategySelector.should_trade exploration phase (< 10 trades → True)
  - Thompson disable via brain_flags falls back to equal-weight
  - get_rankings sorted by sample desc
  - get_status returns top-10 + total count
"""

from __future__ import annotations

import pytest

from core.strategy_selector import ArmState, StrategySelector


# ════════════════════════════════════════════════════════════════
# ArmState
# ════════════════════════════════════════════════════════════════
class TestArmStateDefaults:
    def test_default_priors(self):
        a = ArmState()
        assert a.alpha == 2.0
        assert a.beta == 2.0
        assert a.total_trades == 0
        assert a.recent_pnl == 0.0

    def test_default_win_rate_is_neutral(self):
        a = ArmState()
        assert a.win_rate == 0.5

    def test_zero_alpha_zero_beta_returns_half(self):
        """Even if priors zero (extreme), win_rate falls back to 0.5."""
        a = ArmState(alpha=0.0, beta=0.0)
        assert a.win_rate == 0.5


class TestArmStateUpdate:
    def test_win_increments_alpha(self):
        # decay 0.995 applied to BOTH alpha and beta on every update,
        # then +1 to the winning side. Spec from update():
        #   alpha = max(MIN_ALPHA=1.0, alpha*0.995) [+1 if won]
        #   beta  = max(MIN_BETA=1.0,  beta*0.995)  [+1 if not won]
        a = ArmState(alpha=2.0, beta=2.0)
        a.update(won=True, pnl=1.0)
        assert a.alpha == pytest.approx(2.99, abs=1e-6)
        assert a.beta == pytest.approx(1.99, abs=1e-6)
        assert a.total_trades == 1
        assert a.recent_pnl == 1.0

    def test_loss_increments_beta(self):
        a = ArmState(alpha=2.0, beta=2.0)
        a.update(won=False, pnl=-0.5)
        assert a.alpha == pytest.approx(1.99, abs=1e-6)
        assert a.beta == pytest.approx(2.99, abs=1e-6)
        assert a.recent_pnl == -0.5

    def test_decay_floor_min_alpha(self):
        """Repeated updates must respect MIN_ALPHA = 1.0 floor."""
        a = ArmState(alpha=1.0, beta=1.0)
        for _ in range(100):
            a.update(won=False)
        assert a.alpha >= 1.0
        assert a.beta > 1.0

    def test_pnl_accumulates(self):
        a = ArmState()
        a.update(won=True, pnl=1.5)
        a.update(won=True, pnl=2.0)
        a.update(won=False, pnl=-0.5)
        assert a.recent_pnl == pytest.approx(3.0, abs=1e-6)


class TestArmStateSample:
    def test_sample_in_unit_range(self):
        """Beta sample always in [0, 1]."""
        a = ArmState()
        for _ in range(20):
            x = a.sample()
            assert 0.0 <= x <= 1.0

    def test_sample_robust_to_extreme_args(self):
        """Even with α=β=0, sample uses max(α, 0.01) so result is in [0,1]."""
        a = ArmState(alpha=0.0, beta=0.0)
        x = a.sample()
        assert 0.0 <= x <= 1.0


# ════════════════════════════════════════════════════════════════
# StrategySelector
# ════════════════════════════════════════════════════════════════
class TestSelectorBasics:
    def test_get_or_create_lazy(self):
        s = StrategySelector()
        arm = s.get_or_create("strat_1")
        assert isinstance(arm, ArmState)
        assert "strat_1" in s._arms

    def test_get_or_create_idempotent(self):
        s = StrategySelector()
        a1 = s.get_or_create("x")
        a2 = s.get_or_create("x")
        assert a1 is a2

    def test_record_result_updates_arm(self):
        s = StrategySelector()
        s.record_result("strat", won=True, pnl=2.0)
        arm = s.get_or_create("strat")
        assert arm.total_trades == 1
        assert arm.recent_pnl == 2.0


class TestShouldTrade:
    def test_exploration_phase_always_true(self):
        """First 10 trades → always allow."""
        s = StrategySelector()
        assert s.should_trade("new_strat") is True

    def test_thompson_disabled_exploration_path(self):
        """Disabled flag + total_trades < 10 → True."""
        s = StrategySelector()

        class FakeEngine:
            brain_flags = {"thompson_sampling": False}

        assert s.should_trade("brand_new", engine=FakeEngine()) is True

    def test_thompson_enabled_exploration(self):
        """Enabled + < 10 trades → exploration → True."""
        s = StrategySelector()

        class FakeEngine:
            brain_flags = {"thompson_sampling": True}

        assert s.should_trade("strat", engine=FakeEngine()) is True

    def test_unknown_strategy_after_exploration_returns_true(self):
        """Brand-new strategy in exploration → True regardless of others."""
        s = StrategySelector()
        for _ in range(15):
            s.record_result("a", won=True)
        # 'b' is brand new — total_trades=0 → exploration → True
        assert s.should_trade("b") is True


class TestRankings:
    def test_empty_rankings(self):
        s = StrategySelector()
        assert s.get_rankings() == []

    def test_rankings_have_required_keys(self):
        s = StrategySelector()
        s.record_result("a", won=True, pnl=1.0)
        rankings = s.get_rankings()
        assert len(rankings) == 1
        keys = set(rankings[0].keys())
        expected = {"id", "alpha", "beta", "win_rate", "sample", "trades", "pnl"}
        assert expected <= keys

    def test_rankings_sorted_by_sample_desc(self):
        s = StrategySelector()
        s.record_result("a", won=True)
        s.record_result("b", won=True)
        rankings = s.get_rankings()
        samples = [r["sample"] for r in rankings]
        assert samples == sorted(samples, reverse=True)


class TestStatus:
    def test_status_empty(self):
        s = StrategySelector()
        st = s.get_status()
        assert st["total_arms"] == 0
        assert st["rankings"] == []

    def test_status_includes_top_10(self):
        s = StrategySelector()
        for i in range(15):
            s.record_result(f"strat_{i:02d}", won=True)
        st = s.get_status()
        assert st["total_arms"] == 15
        assert len(st["rankings"]) == 10  # capped at 10
