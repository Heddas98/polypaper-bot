"""
P1-02 (2026-05-11) Wave 1 — AI Advisor HTTP client.
======================================================

Bot-side thin wrapper around the ai_advisor service.

ENV:
    AI_ADVISOR_ENABLED  default false  — when true, suggest_via_service() is
                                          used; when false (or unset), the
                                          bot's in-process AI Brain logic
                                          continues to run unchanged.
    AI_ADVISOR_URL      default http://127.0.0.1:8001
    AI_ADVISOR_TIMEOUT_S default 8.0

Defensive: any HTTP error / timeout / non-2xx → log + return None, signaling
the caller to fall back to its in-process path. Bot never crashes because the
advisor service is down or returns garbage.

Wave 1: service returns a stub HOLD suggestion. Wave 2 will move real AI
Brain logic into the service.

Usage from core/ai_brain.py (Wave 2):

    from core.ai_brain_client import suggest_via_service, is_enabled

    if is_enabled():
        result = await suggest_via_service(market_ctx, strategy_ctx,
                                            correlation_id=cid)
        if result is not None:
            return result  # service-side suggestion
    # otherwise fall through to in-process path (current behavior)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("polypaper.ai_brain_client")


def is_enabled() -> bool:
    """Read AI_ADVISOR_ENABLED at call time (lets /env_toggle flip live)."""
    return os.getenv("AI_ADVISOR_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _service_url() -> str:
    return os.getenv("AI_ADVISOR_URL", "http://127.0.0.1:8001").rstrip("/")


def _timeout_s() -> float:
    try:
        return float(os.getenv("AI_ADVISOR_TIMEOUT_S", "8.0"))
    except (TypeError, ValueError):
        return 8.0


async def health_check() -> Optional[dict]:
    """GET /health. Returns the parsed dict or None on any failure.

    Cheap probe — bot startup / heartbeat can call this to log advisor
    status without affecting trading.
    """
    if not is_enabled():
        return None
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; advisor client disabled")
        return None

    url = f"{_service_url()}/health"
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            r = await client.get(url)
        if r.status_code != 200:
            logger.warning(
                f"advisor /health returned {r.status_code} — falling back")
            return None
        return r.json()
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.warning(
            f"advisor /health failed: {type(e).__name__}: {e}")
        return None


async def suggest_via_service(
    market: dict,
    strategy: Optional[dict] = None,
    correlation_id: Optional[str] = None,
    cycle: Optional[int] = None,
) -> Optional[dict]:
    """POST /suggest. Returns parsed response dict or None on any failure.

    Caller (core/ai_brain.py Wave 2) checks `is_enabled()` first and falls
    back to in-process path when this returns None.

    `market` and `strategy` are passed through to the service's pydantic
    schemas. See services/ai_advisor/models.py for the expected shape.
    """
    if not is_enabled():
        return None
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed; advisor client disabled")
        return None

    payload: dict[str, Any] = {
        "market": market,
        "strategy": strategy,
        "correlation_id": correlation_id,
        "cycle": cycle,
    }

    url = f"{_service_url()}/suggest"
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            r = await client.post(url, json=payload)
        if r.status_code == 422:
            logger.warning(f"advisor /suggest 422 — payload invalid: "
                           f"{r.text[:200]}")
            return None
        if r.status_code != 200:
            logger.warning(
                f"advisor /suggest returned {r.status_code}, falling back")
            return None
        return r.json()
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        logger.warning(
            f"advisor /suggest failed: {type(e).__name__}: {e} "
            "— falling back to in-process path")
        return None
