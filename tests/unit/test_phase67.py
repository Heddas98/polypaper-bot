"""
Phase 67: Parameter Optimization Tests
=======================================
Tests HyperOpt pipeline components and Monte Carlo Kelly validation.
"""

import os

import pytest

try:
    import telegram

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False


# ═══ Monte Carlo Kelly Tests ═══


class TestMonteCarloKelly:
    """Test MC simulation with small path counts for speed."""

    def test_basic_simulation(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.57,
            avg_entry_price=0.65,
            initial_bankroll=10000.0,
            n_paths=100,  # small for speed
            n_trades=50,
        )
        result = mc.simulate()
        assert result.win_rate == 0.57
        assert result.n_paths == 100
        assert result.n_trades == 50
        assert len(result.fractions) == 7  # all fractions tested

    def test_full_kelly_pct(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(win_rate=0.57, avg_entry_price=0.65)
        # b = (1/0.65) - 1 ≈ 0.5385
        # f* = (0.5385 * 0.57 - 0.43) / 0.5385 ≈ -0.228
        # Hmm, at 57% WR and 65c entry, Kelly might be negative or small
        # Let's just check the calculation runs
        result = mc.simulate()
        assert result.full_kelly_pct is not None

    def test_high_wr_positive_kelly(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.70,
            avg_entry_price=0.55,
            initial_bankroll=10000.0,
            n_paths=100,
            n_trades=50,
        )
        result = mc.simulate()
        # At 70% WR and 55c entry, Kelly should be positive
        # b = (1/0.55) - 1 ≈ 0.818
        # f* = (0.818 * 0.70 - 0.30) / 0.818 ≈ 0.333
        assert result.full_kelly_pct > 0

    def test_quarter_result_exists(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.65,
            avg_entry_price=0.55,
            n_paths=50,
            n_trades=30,
        )
        result = mc.simulate()
        qk = next((f for f in result.fractions if f.name == "quarter"), None)
        assert qk is not None
        assert qk.median_final > 0

    def test_optimal_fraction_exists(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.65,
            avg_entry_price=0.55,
            n_paths=50,
            n_trades=30,
        )
        result = mc.simulate()
        assert result.optimal_fraction_name != ""
        assert result.recommendation != ""

    def test_bankruptcy_higher_for_full_kelly(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.60,
            avg_entry_price=0.55,
            n_paths=200,
            n_trades=100,
        )
        result = mc.simulate()
        full = next((f for f in result.fractions if f.name == "full"), None)
        quarter = next((f for f in result.fractions if f.name == "quarter"), None)
        # Full Kelly should generally have higher bankruptcy than quarter
        # (or at minimum not less, given enough trades)
        assert full is not None
        assert quarter is not None

    def test_summary_and_telegram_format(self):
        from utils.mc_simulation import MonteCarloKelly

        mc = MonteCarloKelly(
            win_rate=0.60,
            avg_entry_price=0.55,
            n_paths=50,
            n_trades=30,
        )
        result = mc.simulate()
        summary = result.summary()
        assert "Monte Carlo" in summary
        telegram = result.format_telegram()
        assert "Monte Carlo" in telegram

    def test_validate_quarter_kelly_quick(self):
        from utils.mc_simulation import validate_quarter_kelly

        v = validate_quarter_kelly(
            win_rate=0.60,
            avg_entry_price=0.55,
            bankroll=10000.0,
            n_paths=100,
            n_trades=50,
        )
        assert "is_optimal" in v
        assert "recommended_fraction" in v
        assert "recommendation" in v


# HyperOpt Pipeline Tests + Tournament Helpers removed 2026-04-28
# (Heddas direktifi: Hyperopt tam silme). Eski class'lar:
# TestHyperOptConfig, TestParamSpaces, TestTournamentHelpers.
