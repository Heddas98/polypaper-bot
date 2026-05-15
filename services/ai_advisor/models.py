"""
P1-02 (2026-05-11) — Pydantic schemas for AI Advisor service.
==============================================================

Request/response models for the /suggest endpoint. Designed to be
self-contained — does NOT import from core/ so the service can be
deployed independently in Wave 3.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

# ── Request side ───────────────────────────────────────────────────────


class MarketContext(BaseModel):
    """Market snapshot the bot sends with each suggestion request."""

    slug: str = Field(..., min_length=1, description="event slug")
    asset: str = Field("?", description="BTC/ETH/SOL/XRP")
    timeframe: str = Field("?", description="5m/15m/1h/24h")
    up_odds: float | None = Field(None, ge=0.0, le=1.0)
    down_odds: float | None = Field(None, ge=0.0, le=1.0)
    spread: float | None = Field(None, ge=0.0)
    minutes_remaining: float | None = Field(None, ge=0.0)
    total_minutes: float | None = Field(None, ge=0.0)


class StrategyContext(BaseModel):
    """Strategy state the bot includes for AI to reason about."""

    label: str = Field(..., min_length=1)
    asset: str = "?"
    timeframe: str = "?"
    threshold: float = 0.50
    recent_trades: int = 0
    recent_wins: int = 0
    recent_pnl: float = 0.0


class SuggestRequest(BaseModel):
    """Inbound request to /suggest.

    correlation_id lets the bot tie suggestions back to its own log lines.
    """

    market: MarketContext
    strategy: StrategyContext | None = None
    correlation_id: str | None = None
    cycle: int | None = None


# ── Response side ──────────────────────────────────────────────────────


class Suggestion(BaseModel):
    """A single AI-recommended action."""

    action: str = Field(
        ...,
        description="HOLD / TUNE / SCALE / STOP / RESTART / CREATE",
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    reason: str = ""
    payload: dict | None = None


class SuggestResponse(BaseModel):
    """Outbound response from /suggest."""

    # P1-02 Wave 2c fix (2026-05-11): `model_used` field conflicts with
    # pydantic's protected `model_` namespace (UserWarning at import time).
    # Disable the protected namespace check explicitly — there's no model
    # config attribute we use here, so it's safe to clear.
    model_config = {"protected_namespaces": ()}

    suggestions: list[Suggestion] = Field(default_factory=list)
    model_used: str | None = Field(
        None, description="LLM identifier — None in Wave 1 stub mode")
    latency_ms: int = 0
    stub_mode: bool = False


# ── /health response ───────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    uptime_s: float = 0.0
    llm_clients: dict[str, str] = Field(default_factory=dict)
    wave: int = 1
    started_utc: str = ""
