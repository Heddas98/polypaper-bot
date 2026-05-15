"""
PolyPaper Bot — AI Advisor ModelRouter (P1-02 Wave 2a, 2026-05-11).

Extracted from ``core/ai_brain.py`` so the LLM model routing is a pure
config concern, callable both from the in-process bot (legacy path) and
from the standalone `services/ai_advisor` FastAPI service (Wave 2b+).

Behavior is bit-identical to the previous ``core.ai_brain.ModelRouter``.
``core/ai_brain.py`` keeps an import-shim alias so existing imports work.
"""

from __future__ import annotations

import os


class ModelRouter:
    """4-tier model routing with OpenRouter fallback.

    Tier 1: Groq (FREE)             — routine tasks, no $$
    Tier 2: OpenRouter (FREE/CHEAP) — mid-tier, Groq fallback
    Tier 3: Claude (PAID)           — complex reasoning, market decisions
    Tier 4: OpenRouter premium      — Claude/GPT-4o via OpenRouter (final)

    Fallback chain reads ``AI_BRAIN_FALLBACK_CHAIN`` env at import time
    (matches legacy behavior — Wave 2b will move to runtime-read parity
    with Epic 8 helpers if needed).
    """

    TASK_MODEL_MAP: dict[str, tuple[str, str]] = {
        # Tier 1: FREE — routine tasks (Groq Llama 70B)
        "market_scan": ("groq", "llama-3.3-70b-versatile"),
        "data_summary": ("groq", "llama-3.1-8b-instant"),
        "alert_format": ("groq", "llama-3.1-8b-instant"),
        "trade_analysis": ("groq", "llama-3.3-70b-versatile"),
        "mistake_analysis": ("groq", "llama-3.3-70b-versatile"),
        # Phase 75: OpenRouter has no balance → route to Groq free
        "optimist_agent": ("groq", "llama-3.3-70b-versatile"),
        "data_enrichment": ("groq", "llama-3.1-8b-instant"),
        # Tier 3: PAID — complex reasoning (Claude)
        "strategy_decision": ("claude", "claude-sonnet-4-6"),
        "risk_assessment": ("claude", "claude-sonnet-4-6"),
        "brain_cycle": ("claude", "claude-sonnet-4-6"),
        "critic_agent": ("claude", "claude-sonnet-4-6"),
    }

    # Fallback chain: skip openrouter (no balance by default), groq→claude only.
    FALLBACK_CHAIN: list[str] = os.getenv("AI_BRAIN_FALLBACK_CHAIN", "groq,claude").split(",")

    @classmethod
    def get(cls, task_type: str) -> tuple[str, str]:
        """Return (provider, model_name) for a task. Default = Groq Llama 70B."""
        return cls.TASK_MODEL_MAP.get(task_type, ("groq", "llama-3.3-70b-versatile"))


__all__ = ["ModelRouter"]
