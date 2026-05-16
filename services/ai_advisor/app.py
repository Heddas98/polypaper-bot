"""
P1-02 (2026-05-11) AI Advisor FastAPI service — Wave 1 scaffold + 2c LLM.
==========================================================================

Endpoints:
    GET  /health         — service liveness + LLM client status
    POST /suggest        — stub HOLD (Wave 1) OR real LLM (Wave 2c if enabled)
    GET  /stats          — request counter + uptime + routing + LLM mode

Start standalone:
    py -3.11 -m uvicorn services.ai_advisor.app:app --port 8001 --host 127.0.0.1

Or via Windows helper:
    scripts\\start_ai_advisor.bat

Wave 1 (2026-05-11): scaffold — /suggest returns fixed HOLD with stub_mode=True.
Wave 2a (2026-05-11): prompts + ModelRouter extracted from core/ai_brain.py.
Wave 2b (2026-05-11): LLM HTTP wrappers extracted (services/ai_advisor/llm_clients.py).
Wave 2c (2026-05-11): /suggest endpoint optionally calls real LLM. ENV-gated:
    AI_ADVISOR_REAL_LLM=true   → call configured LLM via ModelRouter chain
    AI_ADVISOR_REAL_LLM=false  → keep Wave 1 stub HOLD (default — zero cost)
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# P1-02 Wave 2c (2026-05-11): stateless LLM HTTP wrappers + 429 error type.
# /suggest endpoint optionally drives a real LLM call via ModelRouter.
from services.ai_advisor.llm_clients import (
    LLMRateLimitError,
    build_chat_payload,
    build_claude_payload,
    do_claude_call,
    do_groq_call,
    do_openrouter_call,
)
from services.ai_advisor.models import (
    HealthResponse,
    Suggestion,
    SuggestRequest,
    SuggestResponse,
)

# P1-02 Wave 2a (2026-05-11): prompts + ModelRouter extracted from
# core/ai_brain.py. Wave 1 doesn't yet use these to call an LLM
# (still stub HOLD), but importing here makes the package surface
# explicit and lets /stats expose the routing config.
from services.ai_advisor.prompts import BRAIN_SYSTEM
from services.ai_advisor.router import ModelRouter

logger = logging.getLogger("polypaper.ai_advisor")

app = FastAPI(
    title="PolyPaper AI Advisor",
    description="Read-only suggestion API (P1-02 Wave 1 scaffold)",
    version="1.0.0-wave1",
)


# ════════════════════════════════════════════════════════════════════════
# P0-11 (2026-05-13 audit): X-Internal-Key auth middleware
# ════════════════════════════════════════════════════════════════════════
#
# Until 2026-05-13 the service had NO auth — only `host=127.0.0.1` bind
# kept it from external access. If anything ever forwards the port
# (Docker host network, ngrok, reverse tunnel, SSH -L) the /suggest
# endpoint becomes an open LLM-cost proxy: any anonymous caller can
# burn through the configured ANTHROPIC_API_KEY / GROQ_API_KEY budget.
#
# Doctrine:
#   * If env ``AI_ADVISOR_INTERNAL_KEY`` is set, every protected route
#     requires header ``X-Internal-Key: <same value>``. Mismatch → 401.
#   * If env is unset the middleware is a NO-OP (back-compat default).
#     Heddas can opt-in by exporting the env in ``start_ai_advisor.bat``.
#   * /health stays open (used by docker healthchecks / supervisord).
#
# Bot side (``core/ai_brain_client.py``) reads the same env and adds the
# header automatically when calling http://127.0.0.1:8001/suggest.
# ════════════════════════════════════════════════════════════════════════

_OPEN_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _required_internal_key() -> str:
    """Runtime-read so /envt AI_ADVISOR_INTERNAL_KEY <x> takes effect."""
    return os.getenv("AI_ADVISOR_INTERNAL_KEY", "").strip()


class InternalKeyMiddleware(BaseHTTPMiddleware):
    """Reject calls missing X-Internal-Key based on cost-exposure tier.

    H-01 (2026-05-15 ultra-audit): the original middleware was a pure
    no-op whenever ``AI_ADVISOR_INTERNAL_KEY`` was unset (back-compat
    default). That left ``/suggest`` an open LLM-cost proxy as soon as
    real-LLM mode was on — any caller that reached the port could burn
    the configured ANTHROPIC/GROQ budget.

    New cost-tiered doctrine:
      * ``/health`` & docs routes — always open (healthchecks, supervisord).
      * Key configured → every protected route needs a matching header.
      * Key unset + stub mode (AI_ADVISOR_REAL_LLM=false) → allow.
        Zero LLM cost, keeps local-dev ergonomics.
      * Key unset + real-LLM mode → REJECT 401. Real LLM = real money;
        running it without auth is never acceptable.
    """

    async def dispatch(self, request: Request, call_next):
        # /health, /docs etc. are always open (healthchecks, supervisord).
        if request.url.path in _OPEN_PATHS:
            return await call_next(request)
        required = _required_internal_key()
        if not required:
            # H-01: no key configured — decide by cost-exposure tier.
            if _real_llm_enabled():
                logger.error(
                    "🔒 AI Advisor: %s rejected — AI_ADVISOR_REAL_LLM=true "
                    "requires AI_ADVISOR_INTERNAL_KEY (open LLM-cost proxy)",
                    request.url.path,
                )
                return JSONResponse(
                    status_code=401,
                    content={
                        "detail": "AI_ADVISOR_INTERNAL_KEY required when "
                        "AI_ADVISOR_REAL_LLM=true"
                    },
                )
            # Stub mode — zero cost, allow (back-compat).
            return await call_next(request)
        supplied = request.headers.get("X-Internal-Key", "")
        # Constant-time compare to avoid timing oracle.
        import hmac
        if not hmac.compare_digest(supplied, required):
            logger.warning(
                "🔒 AI Advisor: rejected %s — X-Internal-Key %s",
                request.url.path,
                "missing" if not supplied else "mismatch",
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "X-Internal-Key required"},
            )
        return await call_next(request)


app.add_middleware(InternalKeyMiddleware)


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


# ── Wave 2c helpers ─────────────────────────────────────────────────────


def _real_llm_enabled() -> bool:
    """Wave 2c flag — read at request time so /env_toggle flips work live."""
    return os.getenv("AI_ADVISOR_REAL_LLM", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _safe_user_str(s: str, maxlen: int = 120) -> str:
    """C-02 (2026-05-15 ultra-audit): escape untrusted strings before LLM injection.

    Polymarket market slugs and strategy labels are user-controlled free
    text (anyone can create a market with arbitrary slug). Without escaping,
    a slug like ``"BTC UP\\n\\nSYSTEM: ignore previous instructions"`` could
    inject pseudo-instructions into the LLM user message and pollute the
    2-agent (Optimist/Critic) reasoning. P0-01 manual approval still blocks
    *execution*, but the LLM's reasoning leak + approval queue manipulation
    is a real attack surface. ``json.dumps`` quotes the string and escapes
    newlines, quotes, control chars — the model still reads it but cannot
    interpret embedded instructions as system-level.

    Applied to slug + label only — ``asset``/``timeframe`` are short
    pydantic-constrained enum-like values (BTC/ETH/SOL/XRP, 5m/15m/1h/24h)
    that the bot itself fills, not free user input.
    """
    import json as _json
    return _json.dumps(s[:maxlen] if s else "")


def _build_user_prompt(req: SuggestRequest) -> str:
    """Render market + strategy context as the LLM user message.

    Compact, structured so the model can parse without ambiguity. Mirrors
    the shape of context the in-process AIBrain assembles, but limited to
    request-scoped fields (no DB lookups).

    C-02 (2026-05-15): user-controlled free text (slug, strategy label) is
    sanitized via ``_safe_user_str`` to prevent prompt-injection through
    Polymarket market names.
    """
    m = req.market
    s = req.strategy
    lines = [
        "# MARKET",
        f"slug={_safe_user_str(m.slug)}",
        f"asset={m.asset} timeframe={m.timeframe}",
    ]
    if m.up_odds is not None or m.down_odds is not None:
        lines.append(
            f"odds: up={m.up_odds if m.up_odds is not None else '?'} "
            f"down={m.down_odds if m.down_odds is not None else '?'}"
        )
    if m.spread is not None:
        lines.append(f"spread={m.spread:.4f}")
    if m.minutes_remaining is not None:
        lines.append(
            f"time: {m.minutes_remaining:.1f}m left "
            f"of {m.total_minutes if m.total_minutes is not None else '?'}m"
        )
    if s is not None:
        lines.append("")
        lines.append("# STRATEGY")
        # C-02: strategy label is user-controlled (via /quick_strategy wizard).
        lines.append(
            f"label={_safe_user_str(s.label)} "
            f"asset={s.asset}/{s.timeframe} thr={s.threshold:.2f}"
        )
        wr = (s.recent_wins / s.recent_trades * 100.0) if s.recent_trades else 0.0
        lines.append(
            f"recent: n={s.recent_trades} wins={s.recent_wins} "
            f"WR={wr:.1f}% PnL={s.recent_pnl:+.2f}"
        )
    if req.correlation_id:
        lines.append("")
        lines.append(f"correlation_id={req.correlation_id}")
    if req.cycle is not None:
        lines.append(f"cycle={req.cycle}")
    return "\n".join(lines)


def _call_provider_sync(provider: str, model: str, system: str, user: str):
    """Dispatch a single sync HTTP call to the configured provider.

    Returns (text|None, model_used|None). model_used echoes the model on
    success so the caller can report it back. None on soft failure (next
    provider in fallback chain may try).

    Raises:
        LLMRateLimitError: bubbles up — caller's responsibility to skip
        the provider and try the next one.
    """
    if provider == "claude":
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None, None
        payload = build_claude_payload(system, user, model=model)
        text = do_claude_call(payload, api_key)
        return text, (model if text else None)
    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY", "") or os.getenv("GROK_API_KEY", "")
        if not api_key:
            return None, None
        payload = build_chat_payload(system, user, model=model)
        text = do_groq_call(payload, api_key)
        return text, (model if text else None)
    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            return None, None
        payload = build_chat_payload(system, user, model=model)
        text = do_openrouter_call(payload, api_key)
        return text, (model if text else None)
    return None, None


async def _try_real_llm(req: SuggestRequest):
    """Wave 2c: drive ModelRouter fallback chain until a provider returns text.

    Returns:
        (suggestions, model_used) on success.
        (None, None) if every provider in FALLBACK_CHAIN returned None.
    """
    import asyncio

    user = _build_user_prompt(req)
    # ModelRouter resolves the primary (provider, model) for the brain task.
    primary_provider, primary_model = ModelRouter.get("brain_cycle")
    # Fallback order: primary first, then everything else in FALLBACK_CHAIN.
    seen = set()
    chain: list[tuple[str, str]] = []
    chain.append((primary_provider, primary_model))
    seen.add(primary_provider)
    for fallback in ModelRouter.FALLBACK_CHAIN:
        fb = fallback.strip().lower()
        if fb and fb not in seen:
            # Use the per-task model for fallbacks too (router picks Llama for groq, etc.)
            fb_provider, fb_model = ModelRouter.get("brain_cycle")
            # Override provider with the fallback name.
            chain.append((fb, fb_model if fb_provider == fb else primary_model))
            seen.add(fb)

    loop = asyncio.get_running_loop()
    for provider, model in chain:
        try:
            text, model_used = await loop.run_in_executor(
                None, _call_provider_sync, provider, model, BRAIN_SYSTEM, user
            )
        except LLMRateLimitError as rl:
            logger.warning(
                f"ai_advisor: {rl.provider} 429 (retry_after={rl.retry_after:.1f}s) — "
                "trying next provider"
            )
            continue
        if text:
            # Wave 2c minimal parser: return the raw model text as the
            # reason field of a single HOLD suggestion. Wave 3 will parse
            # the JSON `actions` array into structured Suggestion list.
            sug = Suggestion(
                action="HOLD",
                confidence=0.5,
                reason=f"[Wave 2c real-LLM] model={model_used} response="
                       f"{(text[:600] + '…') if len(text) > 600 else text}",
                payload={
                    "correlation_id": req.correlation_id,
                    "model": model_used,
                    "provider": provider,
                    "raw_chars": len(text),
                },
            )
            return [sug], model_used
    return None, None


@app.post("/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest) -> SuggestResponse:
    """Wave 1 stub HOLD by default; Wave 2c real LLM if AI_ADVISOR_REAL_LLM=true.

    Wave 2c path:
      1. Build user prompt from req.market + req.strategy.
      2. Try ModelRouter primary, then FALLBACK_CHAIN (groq → claude).
      3. On 429 from a provider, log and skip to next.
      4. Wrap raw response text in a single Suggestion(action='HOLD',
         confidence=0.5) until Wave 3 wires the JSON action parser.
      5. If every provider fails → fall back to deterministic stub.

    AI_ADVISOR_REAL_LLM defaults to false → zero LLM cost. Heddas opt-in
    only when ready to spend on inference.
    """
    global _REQUEST_COUNT
    _REQUEST_COUNT += 1
    t0 = time.time()

    # Defensive input validation — pydantic already covers types/ranges.
    if not req.market.slug:
        raise HTTPException(status_code=422, detail="market.slug required")

    real_llm = _real_llm_enabled()
    suggestions: list[Suggestion] = []
    model_used: Optional[str] = None
    stub_mode = True

    if real_llm:
        suggestions_real, model_used = await _try_real_llm(req)
        if suggestions_real:
            suggestions = suggestions_real
            stub_mode = False
        else:
            logger.info(
                "ai_advisor: real-LLM mode but all providers returned None — "
                "degrading to stub HOLD"
            )

    if not suggestions:
        # Wave 1 stub: HOLD with mode explanation. Always reachable as
        # final fallback so the bot client never sees a 500.
        mode = "Wave 2c real-LLM all-providers-failed" if real_llm else "stub Wave 1"
        suggestions = [
            Suggestion(
                action="HOLD",
                confidence=0.0,
                reason=(
                    f"[{mode}] slug={req.market.slug} "
                    f"asset={req.market.asset}/{req.market.timeframe} "
                    f"strategy={req.strategy.label if req.strategy else '?'}."
                ),
                payload={
                    "request_count": _REQUEST_COUNT,
                    "correlation_id": req.correlation_id,
                },
            )
        ]

    latency_ms = int((time.time() - t0) * 1000)
    return SuggestResponse(
        suggestions=suggestions,
        model_used=model_used,
        latency_ms=latency_ms,
        stub_mode=stub_mode,
    )


# ── /stats ─────────────────────────────────────────────────────────────


@app.get("/stats")
async def stats() -> dict[str, object]:
    """Lightweight observability — request counter + uptime + routing + LLM mode."""
    return {
        "uptime_s": time.time() - _STARTED_AT,
        "request_count": _REQUEST_COUNT,
        "started_utc": _STARTED_ISO,
        "wave": 1,
        # P1-02 Wave 2a: prompts + ModelRouter now live in this package.
        # `core/ai_brain.py` aliases them. Expose for ops visibility.
        "prompts_loaded": ["BRAIN_SYSTEM", "TRADE_SYSTEM", "MISTAKE_SYSTEM"],
        "brain_system_chars": len(BRAIN_SYSTEM),
        "routing": {
            "tasks": sorted(ModelRouter.TASK_MODEL_MAP.keys()),
            "fallback_chain": ModelRouter.FALLBACK_CHAIN,
        },
        # P1-02 Wave 2c: LLM call surface + current mode flag.
        "wave_2c": {
            "real_llm_enabled": _real_llm_enabled(),
            "providers_available": {
                "anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
                "groq": bool(os.getenv("GROQ_API_KEY") or os.getenv("GROK_API_KEY")),
                "openrouter": bool(os.getenv("OPENROUTER_API_KEY")),
            },
        },
    }
