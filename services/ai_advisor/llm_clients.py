"""
PolyPaper Bot — AI Advisor LLM HTTP clients (P1-02 Wave 2b, 2026-05-11).

Stateless synchronous HTTP wrappers for the three providers the bot's
AI Brain uses. Extracted from ``core/ai_brain.py`` so the standalone
``services/ai_advisor`` FastAPI service can call them without dragging
the bot's DB/budget/cooldown state.

Design contract (Wave 2b):

  - **Pure** wrt PolyPaper Bot state. No reads/writes of `self.*`,
    no DB calls, no budget tracking, no cooldown bookkeeping. Caller
    owns all of that.
  - **API key as parameter** (not module global). Lets a service
    instance have a different key than the bot. Empty string → caller's
    responsibility to short-circuit BEFORE calling.
  - **Synchronous httpx**. Designed to be wrapped with
    ``loop.run_in_executor`` (same pattern as the legacy `_do_*`
    methods). Async wrapping is the caller's choice.
  - **429 → raise** :class:`LLMRateLimitError` with the parsed
    ``retry_after`` so the caller can update its cooldown state.
  - **Other errors → return None**. Soft failure; caller picks next
    provider per ``ModelRouter.FALLBACK_CHAIN``.

The legacy ``core/ai_brain.py._do_claude / _do_groq / _do_openrouter``
methods are now thin wrappers around these functions. Existing imports
``from core.ai_brain import LLMRateLimitError`` keep working via shim.
"""

from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger("polypaper.ai_advisor.llm_clients")


__all__ = [
    "LLMRateLimitError",
    "parse_retry_after",
    "do_claude_call",
    "do_groq_call",
    "do_openrouter_call",
    "build_claude_payload",
    "build_chat_payload",
]


class LLMRateLimitError(RuntimeError):
    """Raised by ``do_*_call`` helpers when the upstream API returns 429.

    Carries the server-provided retry-after (seconds) so the async wrapper
    can populate its cooldown bookkeeping accurately instead of using the
    fallback backoff constant.

    Wave 2b (2026-05-11): moved from ``core/ai_brain.py``. ``core.ai_brain``
    re-imports it for backward compatibility with existing call sites
    (``from core.ai_brain import LLMRateLimitError`` still works).
    """

    def __init__(self, provider: str, retry_after: float):
        self.provider = provider
        self.retry_after = retry_after
        super().__init__(f"{provider} rate-limit (retry after {retry_after:.1f}s)")


def _default_backoff_sec() -> float:
    """``LLM_RATELIMIT_BACKOFF_SEC`` — cooldown (s) after a 429 (default 60).

    Wave 2b: extracted from ``core/ai_brain.py._get_llm_ratelimit_backoff``.
    Runtime-read each call so ``/env_toggle`` changes take immediate effect.
    """
    try:
        return float(os.getenv("LLM_RATELIMIT_BACKOFF_SEC", "60"))
    except (TypeError, ValueError):
        return 60.0


def parse_retry_after(header_val: str | None, default: float | None = None) -> float:
    """Parse a Retry-After header value into a float number of seconds.

    Providers send either an integer-as-string ("30") or an HTTP-date.
    We accept the integer form here (the providers we use prefer it);
    HTTP-dates fall through to the default. Always returns a positive
    float — minimum 1.0 to avoid degenerate 0s tight-loops.
    """
    if default is None:
        default = _default_backoff_sec()
    if not header_val:
        return default
    try:
        return max(1.0, float(header_val))
    except (TypeError, ValueError):
        return default


# ── Payload builders (shared shape) ───────────────────────────────────


def build_claude_payload(
    system: str, user: str, model: str = "claude-sonnet-4-6", max_tokens: int = 2000
) -> str:
    """Anthropic Messages API payload with ephemeral cache_control on system."""
    return json.dumps(
        {
            "model": model,
            "max_tokens": max_tokens,
            "system": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ],
            "messages": [{"role": "user", "content": user}],
        }
    )


def build_chat_payload(
    system: str,
    user: str,
    model: str,
    max_tokens: int = 1500,
    temperature: float = 0.3,
) -> str:
    """OpenAI-compatible chat completions payload (Groq, OpenRouter)."""
    return json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    )


# ── Provider HTTP calls (synchronous, stateless) ──────────────────────


def do_claude_call(payload: str, api_key: str) -> str | None:
    """Synchronous POST to Anthropic Messages API.

    Args:
        payload: JSON-encoded request body (see ``build_claude_payload``).
        api_key: Anthropic API key (caller must validate non-empty).

    Returns:
        Response text content on success, or None on any non-429 error
        (HTTP timeout, JSON decode, error response, etc.). Soft-failure
        semantics — caller picks next provider.

    Raises:
        LLMRateLimitError: on HTTP 429. Carries parsed retry_after.
    """
    try:
        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            content=payload,
            timeout=60.0,
        )
        if r.status_code == 429:
            retry_after = parse_retry_after(r.headers.get("Retry-After"))
            raise LLMRateLimitError("claude", retry_after)
        d = r.json()
        if "content" in d and d["content"]:
            return d["content"][0].get("text", "")
        if "error" in d:
            logger.warning(f"Claude: {d['error'].get('message','')[:100]}")
        return None
    except LLMRateLimitError:
        raise
    except Exception:  # noqa: BLE001
        # Soft failure — sync HTTP can raise httpx.HTTPError, TimeoutError,
        # ConnectionError, JSONDecodeError. All map to "try next provider".
        return None


def do_groq_call(
    payload: str, api_key: str
) -> str | None:
    """Synchronous POST to Groq chat completions (OpenAI-compatible).

    Same contract as :func:`do_claude_call` — returns text or None;
    raises :class:`LLMRateLimitError` on 429.
    """
    try:
        r = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            content=payload,
            timeout=30.0,
        )
        if r.status_code == 429:
            retry_after = parse_retry_after(r.headers.get("Retry-After"))
            raise LLMRateLimitError("groq", retry_after)
        d = r.json()
        ch = d.get("choices", [])
        return ch[0].get("message", {}).get("content", "") if ch else None
    except LLMRateLimitError:
        raise
    except Exception:  # noqa: BLE001
        return None


def do_openrouter_call(payload: str, api_key: str) -> str | None:
    """Synchronous POST to OpenRouter chat completions.

    Same contract as :func:`do_claude_call`.
    """
    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://polypaper.local",
                "X-Title": "PolyPaper Bot",
            },
            content=payload,
            timeout=45.0,
        )
        if r.status_code == 429:
            retry_after = parse_retry_after(r.headers.get("Retry-After"))
            raise LLMRateLimitError("openrouter", retry_after)
        d = r.json()
        ch = d.get("choices", [])
        return ch[0].get("message", {}).get("content", "") if ch else None
    except LLMRateLimitError:
        raise
    except Exception:  # noqa: BLE001
        return None
