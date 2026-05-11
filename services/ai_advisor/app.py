"""
P1-02 (2026-05-11) Wave 1 — AI Advisor FastAPI service (scaffold).
====================================================================

Endpoints:
    GET  /health         — service liveness + LLM client status
    POST /suggest        — stub suggestion (Wave 1 returns a no-op HOLD)
    GET  /stats          — request counter + uptime

Start standalone:
    py -3.11 -m uvicorn services.ai_advisor.app:app --port 8001 --host 127.0.0.1

Or via Windows helper:
    scripts\\start_ai_advisor.bat

Wave 1 is a scaffold ONLY — the /suggest endpoint returns a fixed "HOLD"
suggestion with stub_mode=True. Wave 2 will extract real AI Brain logic
from core/ai_brain.py.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException

from services.ai_advisor.models import (
    HealthResponse,
    SuggestRequest,
    Suggestion,
    SuggestResponse,
)

logger = logging.getLogger("polypaper.ai_advisor")

app = FastAPI(
    title="PolyPaper AI Advisor",
    description="Read-only suggestion API (P1-02 Wave 1 scaffold)",
    version="1.0.0-wave1",
)


# Module-level state
_STARTED_AT: float = time.time()
_STARTED_ISO: str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
_REQUEST_COUNT: int = 0


def _detect_llm_clients() -> dict[str, str]:
    """Best-effort detection of LLM provider env vars.

    Returns a map provider→status ("configured" / "missing").
    Doesn't actually call any LLM (Wave 1).
    """
    out: dict[str, str] = {}
    out["anthropic"] = (
        "configured" if os.getenv("ANTHROPIC_API_KEY") else "missing")
    out["groq"] = (
        "configured" if os.getenv("GROQ_API_KEY") else "missing")
    out["openrouter"] = (
        "configured" if os.getenv("OPENROUTER_API_KEY") else "missing")
    return out


# ── /health ────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness + readiness probe.

    Returns 200 always (service up). LLM client status is informational.
    """
    return HealthResponse(
        status="ok",
        uptime_s=time.time() - _STARTED_AT,
        llm_clients=_detect_llm_clients(),
        wave=1,
        started_utc=_STARTED_ISO,
    )


# ── /suggest ───────────────────────────────────────────────────────────


@app.post("/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest) -> SuggestResponse:
    """Wave 1: returns a stub HOLD suggestion.

    Wave 2 will:
      1. Build BRAIN_SYSTEM prompt from req.market + req.strategy.
      2. Call configured LLM (Anthropic/Groq/OpenRouter via ModelRouter).
      3. Parse response → list[Suggestion].
      4. Apply optimist + critic evaluator chain.
      5. Return with model_used + latency_ms.

    For now, the response is deterministic so the bot client can be wired
    + tested end-to-end without API keys.
    """
    global _REQUEST_COUNT
    _REQUEST_COUNT += 1
    t0 = time.time()

    # Defensive input validation — pydantic already covers types/ranges.
    if not req.market.slug:
        raise HTTPException(status_code=422, detail="market.slug required")

    # Wave 1 stub: HOLD with reasoning that explains we're in stub mode.
    stub = Suggestion(
        action="HOLD",
        confidence=0.0,
        reason=(
            f"[stub Wave 1] received slug={req.market.slug} "
            f"asset={req.market.asset}/{req.market.timeframe} "
            f"strategy={req.strategy.label if req.strategy else '?'}. "
            "AI Brain logic not yet extracted (Wave 2 backlog)."
        ),
        payload={
            "request_count": _REQUEST_COUNT,
            "correlation_id": req.correlation_id,
        },
    )
    latency_ms = int((time.time() - t0) * 1000)

    return SuggestResponse(
        suggestions=[stub],
        model_used=None,
        latency_ms=latency_ms,
        stub_mode=True,
    )


# ── /stats ─────────────────────────────────────────────────────────────


@app.get("/stats")
async def stats() -> dict[str, float | int | str]:
    """Lightweight observability — request counter + uptime."""
    return {
        "uptime_s": time.time() - _STARTED_AT,
        "request_count": _REQUEST_COUNT,
        "started_utc": _STARTED_ISO,
        "wave": 1,
    }
