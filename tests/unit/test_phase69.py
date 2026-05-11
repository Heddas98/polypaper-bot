"""
Phase 69: AI Brain Upgrade Tests
=================================
Tests ModelRouter, Reputation Scoring, Champion Tracker helpers.
"""

import pytest

# ═══ ModelRouter Tests ═══


class TestModelRouter:
    def test_task_routing(self):
        from core.ai_brain import ModelRouter

        provider, model = ModelRouter.get("brain_cycle")
        assert provider == "claude"
        assert "claude" in model

    def test_groq_routing(self):
        from core.ai_brain import ModelRouter

        provider, model = ModelRouter.get("market_scan")
        assert provider == "groq"

    def test_openrouter_routing(self):
        from core.ai_brain import ModelRouter

        provider, model = ModelRouter.get("optimist_agent")
        assert provider == "groq"
        assert model is not None

    def test_unknown_task_fallback(self):
        from core.ai_brain import ModelRouter

        provider, model = ModelRouter.get("nonexistent_task")
        assert provider == "groq"  # default fallback

    def test_fallback_chain_exists(self):
        from core.ai_brain import ModelRouter

        assert len(ModelRouter.FALLBACK_CHAIN) == 2
        assert "groq" in ModelRouter.FALLBACK_CHAIN


# ═══ Reputation Scoring Tests ═══


class TestReputation:
    def test_neutral_reputation(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[True, False, True, False],
            win_rate=0.55,
            total_trades=20,
        )
        assert 0.5 <= rep.overall <= 1.5
        assert not rep.is_hot
        assert not rep.is_cold

    def test_hot_streak(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[True, True, True, True, True],
            win_rate=0.65,
            total_trades=50,
        )
        assert rep.is_hot
        assert rep.streak_score > 1.0
        assert rep.overall > 1.0

    def test_cold_streak(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[False, False, False, False],
            win_rate=0.45,
            total_trades=30,
        )
        assert rep.is_cold
        assert rep.streak_score < 1.0
        assert rep.overall < 1.0

    def test_weekend_bonus(self):
        from utils.reputation import compute_reputation

        rep_weekend = compute_reputation(
            strategy_id="test",
            recent_results=[True, False],
            win_rate=0.55,
            total_trades=20,
            is_weekend=True,
        )
        rep_weekday = compute_reputation(
            strategy_id="test",
            recent_results=[True, False],
            win_rate=0.55,
            total_trades=20,
            is_weekend=False,
        )
        assert rep_weekend.market_score >= rep_weekday.market_score

    def test_trending_momentum_boost(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[True, False],
            win_rate=0.55,
            total_trades=20,
            is_trending=True,
            strategy_type="momentum",
        )
        assert rep.market_score > 1.0

    def test_ranging_contrarian_boost(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[True, False],
            win_rate=0.55,
            total_trades=20,
            is_trending=False,
            strategy_type="contrarian",
        )
        assert rep.market_score > 1.0

    def test_bounds(self):
        from utils.reputation import compute_reputation

        # Extreme hot
        rep = compute_reputation(
            strategy_id="test",
            recent_results=[True] * 10,
            win_rate=0.90,
            total_trades=100,
            is_weekend=True,
            is_trending=True,
            strategy_type="momentum",
        )
        assert rep.overall <= 1.5

        # Extreme cold
        rep2 = compute_reputation(
            strategy_id="test",
            recent_results=[False] * 10,
            win_rate=0.30,
            total_trades=100,
        )
        assert rep2.overall >= 0.5

    def test_empty_results(self):
        from utils.reputation import compute_reputation

        rep = compute_reputation(
            strategy_id="test",
            recent_results=[],
            win_rate=0.55,
            total_trades=5,
        )
        assert rep.overall > 0


# ═══ Extract JSON Tests ═══


class TestExtractJson:
    def test_extract_from_text(self):
        from core.ai_brain import AIBrain

        text = 'Some text {"key": "value"} more text'
        result = AIBrain._extract_json(text)
        assert result == '{"key": "value"}'

    def test_extract_from_markdown(self):
        from core.ai_brain import AIBrain

        text = '```json\n{"key": "value"}\n```'
        result = AIBrain._extract_json(text)
        assert result == '{"key": "value"}'

    def test_extract_empty(self):
        from core.ai_brain import AIBrain

        result = AIBrain._extract_json("")
        assert result == "{}"

    def test_extract_no_json(self):
        from core.ai_brain import AIBrain

        result = AIBrain._extract_json("no json here")
        assert result == "{}"


# ═══ Fermi System Prompt Tests ═══


class TestFermiPrompt:
    def test_brain_system_has_fermi(self):
        from core.ai_brain import BRAIN_SYSTEM

        assert "FERMI" in BRAIN_SYSTEM or "Fermi" in BRAIN_SYSTEM

    def test_optimist_system_exists(self):
        from core.ai_brain import OPTIMIST_SYSTEM

        assert "IYIMSER" in OPTIMIST_SYSTEM or "Optimist" in OPTIMIST_SYSTEM

    def test_critic_system_exists(self):
        from core.ai_brain import CRITIC_SYSTEM

        assert "SKEPTIK" in CRITIC_SYSTEM or "risk" in CRITIC_SYSTEM
