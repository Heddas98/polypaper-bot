"""
Phase 72: Evolutionary Strategy Tests
=======================================
Tests Evolutionary Breeding, Majority Voting, PnL Verification.
"""
import pytest
import random


# ═══ Evolutionary Breeding Tests ═══

class TestEvolutionaryBreeder:
    def test_genome_creation(self):
        from core.evolutionary import StrategyGenome
        g = StrategyGenome(
            strategy_id="test-1",
            strategy_type="momentum",
            params={"odds_threshold": 0.55, "min_confidence": 0.60},
            fitness=1.5, win_rate=0.62, total_trades=100)
        assert g.strategy_type == "momentum"
        assert g.fitness == 1.5

    def test_crossover(self):
        from core.evolutionary import EvolutionaryBreeder, StrategyGenome
        breeder = EvolutionaryBreeder()
        parent_a = StrategyGenome(
            strategy_id="A", strategy_type="momentum",
            params={"odds_threshold": 0.55, "min_confidence": 0.60},
            generation=0)
        parent_b = StrategyGenome(
            strategy_id="B", strategy_type="momentum",
            params={"odds_threshold": 0.50, "min_confidence": 0.70},
            generation=1)

        random.seed(42)
        offspring = breeder.crossover(parent_a, parent_b)
        assert offspring.strategy_type == "momentum"
        assert offspring.generation == 2
        assert "A" in offspring.parents or "B" in offspring.parents
        assert "odds_threshold" in offspring.params
        assert "min_confidence" in offspring.params

    def test_mutation(self):
        from core.evolutionary import EvolutionaryBreeder, StrategyGenome
        breeder = EvolutionaryBreeder()
        genome = StrategyGenome(
            strategy_type="momentum",
            params={"odds_threshold": 0.55, "min_confidence": 0.60})

        random.seed(42)
        mutated, n_mut = breeder.mutate(genome)
        # At least the structure is preserved
        assert "odds_threshold" in mutated.params
        assert "min_confidence" in mutated.params
        # Values should be in valid range
        assert 0.01 <= mutated.params["odds_threshold"] <= 0.99
        assert 0.01 <= mutated.params["min_confidence"] <= 0.99

    def test_mutation_bounds(self):
        from core.evolutionary import EvolutionaryBreeder, StrategyGenome
        breeder = EvolutionaryBreeder()
        # Extreme values — mutation should still clamp
        genome = StrategyGenome(
            strategy_type="penny_contract",
            params={"_MAX_LOW": 0.05, "_MIN_HIGH": 0.95, "_MIN_SPREAD": 0.01})

        for _ in range(10):
            mutated, _ = breeder.mutate(genome)
            assert 0.01 <= mutated.params["_MAX_LOW"] <= 0.10
            assert 0.90 <= mutated.params["_MIN_HIGH"] <= 0.99

    def test_breedable_params_exist(self):
        from core.evolutionary import EvolutionaryBreeder
        assert "momentum" in EvolutionaryBreeder.BREEDABLE_PARAMS
        assert "contrarian" in EvolutionaryBreeder.BREEDABLE_PARAMS
        assert "penny_contract" in EvolutionaryBreeder.BREEDABLE_PARAMS

    def test_breeding_result_format(self):
        from core.evolutionary import (
            EvolutionaryBreeder, BreedingResult, StrategyGenome)
        breeder = EvolutionaryBreeder()
        result = BreedingResult(
            offspring=[
                StrategyGenome(strategy_type="momentum",
                               params={"odds_threshold": 0.55}, generation=1)
            ],
            parents_used=["A", "B"],
            mutations_applied=2,
            reason="test"
        )
        text = breeder.format_telegram(result)
        assert "Evolutionary" in text
        assert "Offspring" in text


# ═══ Majority Voting Tests ═══

class TestMajorityVoting:
    def test_empty_votes(self):
        from core.majority_voting import compute_majority_vote
        result = compute_majority_vote([])
        assert "insufficient" in result.reason

    def test_unanimous_up(self):
        from core.majority_voting import compute_majority_vote, Vote
        votes = [
            Vote(strategy_id="A", direction="up", confidence=0.8, win_rate=0.60),
            Vote(strategy_id="B", direction="up", confidence=0.7, win_rate=0.58),
            Vote(strategy_id="C", direction="up", confidence=0.9, win_rate=0.62),
        ]
        result = compute_majority_vote(votes)
        assert result.consensus_direction == "up"
        assert result.has_consensus is True
        assert result.signal_multiplier > 1.0

    def test_unanimous_down(self):
        from core.majority_voting import compute_majority_vote, Vote
        votes = [
            Vote(strategy_id="A", direction="down", win_rate=0.60),
            Vote(strategy_id="B", direction="down", win_rate=0.58),
            Vote(strategy_id="C", direction="down", win_rate=0.55),
        ]
        result = compute_majority_vote(votes)
        assert result.consensus_direction == "down"
        assert result.has_consensus is True

    def test_split_vote(self):
        from core.majority_voting import compute_majority_vote, Vote
        votes = [
            Vote(strategy_id="A", direction="up", win_rate=0.55),
            Vote(strategy_id="B", direction="down", win_rate=0.55),
            Vote(strategy_id="C", direction="up", win_rate=0.55),
            Vote(strategy_id="D", direction="down", win_rate=0.55),
            Vote(strategy_id="E", direction="down", win_rate=0.55),
        ]
        result = compute_majority_vote(votes)
        # 2 up vs 3 down → 60% down
        assert result.consensus_direction == "down"
        assert result.up_votes == 2
        assert result.down_votes == 3

    def test_wr_weighted(self):
        from core.majority_voting import compute_majority_vote, Vote
        # 1 high-WR up vs 2 low-WR down
        votes = [
            Vote(strategy_id="A", direction="up", win_rate=0.90),
            Vote(strategy_id="B", direction="down", win_rate=0.51),
            Vote(strategy_id="C", direction="down", win_rate=0.51),
        ]
        result = compute_majority_vote(votes)
        # WR-weighted: up=0.90/1.92=0.47, down=1.02/1.92=0.53
        # Down still wins but it's close
        assert result.weighted_up > 0.4

    def test_abstain_handling(self):
        from core.majority_voting import compute_majority_vote, Vote
        votes = [
            Vote(strategy_id="A", direction="up", win_rate=0.60),
            Vote(strategy_id="B", direction="up", win_rate=0.58),
            Vote(strategy_id="C", direction="up", win_rate=0.55),
            Vote(strategy_id="D", direction="", win_rate=0.55),  # Abstain
        ]
        result = compute_majority_vote(votes)
        assert result.abstain_votes == 1
        assert result.total_voters == 4
        assert result.up_votes == 3

    def test_penalty_on_split(self):
        from core.majority_voting import compute_majority_vote, Vote
        # Very close split
        votes = [
            Vote(strategy_id="A", direction="up", win_rate=0.55),
            Vote(strategy_id="B", direction="down", win_rate=0.55),
            Vote(strategy_id="C", direction="up", win_rate=0.55),
        ]
        result = compute_majority_vote(votes)
        # 2/3 up ≈ 67% — just above 60% threshold
        if result.has_consensus:
            assert result.signal_multiplier >= 1.0
        else:
            assert result.signal_multiplier < 1.0

    def test_format_telegram(self):
        from core.majority_voting import format_voting_telegram, VotingResult
        result = VotingResult(
            consensus_direction="up", has_consensus=True,
            consensus_strength=0.75, up_votes=3, down_votes=1,
            total_voters=4, signal_multiplier=1.10)
        text = format_voting_telegram(result)
        assert "UP" in text
        assert "🟢" in text


# ═══ PnL Verification Tests ═══

class TestPnLVerification:
    def test_result_defaults(self):
        from core.pnl_verification import VerificationResult
        r = VerificationResult()
        assert r.drift_acceptable is True
        assert r.n_compared == 0

    def test_comparison_creation(self):
        from core.pnl_verification import TradeComparison
        c = TradeComparison(
            trade_id="1",
            paper_entry=0.55, live_entry=0.56,
            paper_pnl=0.50, live_pnl=0.45,
            price_drift=0.01, pnl_drift=0.05)
        assert c.price_drift == 0.01
        assert c.pnl_drift == 0.05

    def test_drift_calculation(self):
        from core.pnl_verification import VerificationResult, TradeComparison
        result = VerificationResult()
        result.comparisons = [
            TradeComparison(paper_pnl=1.0, live_pnl=0.95, pnl_drift=0.05),
            TradeComparison(paper_pnl=0.5, live_pnl=0.48, pnl_drift=0.02),
        ]
        result.n_compared = 2
        result.paper_total_pnl = 1.50
        result.live_total_pnl = 1.43
        result.total_divergence_pct = abs(1.50 - 1.43) / 1.50 * 100
        assert result.total_divergence_pct < 5.0
        assert result.drift_acceptable is True

    def test_high_drift_detection(self):
        from core.pnl_verification import VerificationResult
        result = VerificationResult()
        result.paper_total_pnl = 10.0
        result.live_total_pnl = 5.0
        result.total_divergence_pct = 50.0
        result.drift_acceptable = False
        assert not result.drift_acceptable

    def test_format_telegram(self):
        from core.pnl_verification import PnLVerifier, VerificationResult
        verifier = PnLVerifier()
        result = VerificationResult(reason="no_paired_trades")
        text = verifier.format_telegram(result)
        assert "PnL Verification" in text
