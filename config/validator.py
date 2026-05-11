"""
Phase 48 — Config validator.

Lightweight sanity-check over env-derived Settings values. Catches typos
(string where number expected, unknown enum values, out-of-range numbers)
BEFORE the bot starts accepting trades. Runs as a pure dict-in→list-out
function — no pydantic dependency required in hot path, but compatible
with Settings's existing os.getenv defaults.

Call from main.py after constructing Settings:

    from config.validator import validate_settings
    errors = validate_settings(settings)
    if errors:
        for e in errors: logger.error("CONFIG: %s", e)
        raise SystemExit(1)

Rules are additive — when adding a new env var to Settings, add its rule
here so typos don't get silently swallowed to the default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class Rule:
    def __init__(
        self,
        key: str,
        check: Callable[[Any], bool],
        msg: str,
        required: bool = False,
    ):
        self.key = key
        self.check = check
        self.msg = msg
        self.required = required


def _is_bool(v: Any) -> bool:
    return isinstance(v, bool)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _in_range(lo: float, hi: float) -> Callable[[Any], bool]:
    def inner(v: Any) -> bool:
        return _is_number(v) and lo <= float(v) <= hi

    return inner


def _in_enum(choices: set[str]) -> Callable[[Any], bool]:
    def inner(v: Any) -> bool:
        return isinstance(v, str) and v in choices

    return inner


def _non_empty_str(v: Any) -> bool:
    return isinstance(v, str) and len(v) > 0


# ── Rule table ──────────────────────────────────────────────────────
RULES: list[Rule] = [
    # Core required (validated only when LIVE_ENABLED or to operate the bot)
    Rule("TELEGRAM_BOT_TOKEN", _non_empty_str, "TELEGRAM_BOT_TOKEN is empty — bot cannot start"),
    Rule(
        "ADMIN_TELEGRAM_ID",
        lambda v: _is_number(v) and int(v) > 0,
        "ADMIN_TELEGRAM_ID must be a positive integer",
    ),
    # Kelly sizing
    Rule("KELLY_FRACTION", _in_range(0.0, 1.0), "KELLY_FRACTION must be in [0.0, 1.0]"),
    Rule("KELLY_MAX_BET_PCT", _in_range(0.0, 1.0), "KELLY_MAX_BET_PCT must be in [0.0, 1.0]"),
    # Fee model
    Rule("FEE_MODEL", _in_enum({"v1", "v2"}), "FEE_MODEL must be 'v1' or 'v2'"),
    Rule("FEE_TAIL_LOW", _in_range(0.0, 0.5), "FEE_TAIL_LOW must be in [0.0, 0.5]"),
    Rule("FEE_TAIL_HIGH", _in_range(0.5, 1.0), "FEE_TAIL_HIGH must be in [0.5, 1.0]"),
    # Latency simulation
    Rule(
        "REST_LATENCY_MS",
        lambda v: _is_number(v) and 0 <= float(v) <= 5000,
        "REST_LATENCY_MS must be in [0, 5000]",
    ),
    Rule(
        "REST_LATENCY_JITTER_MS",
        lambda v: _is_number(v) and 0 <= float(v) <= 2000,
        "REST_LATENCY_JITTER_MS must be in [0, 2000]",
    ),
    # UMA timing
    Rule(
        "UMA_FORCE_SETTLE_SECONDS",
        lambda v: _is_number(v) and 60 <= float(v) <= 86400,
        "UMA_FORCE_SETTLE_SECONDS must be in [60, 86400]",
    ),
    # Exposure caps
    Rule(
        "MAX_TOKEN_EXPOSURE_USD",
        lambda v: _is_number(v) and float(v) >= 0,
        "MAX_TOKEN_EXPOSURE_USD must be non-negative",
    ),
    # Live trading
    Rule("LIVE_ENABLED", _is_bool, "LIVE_ENABLED must be a bool"),
    Rule(
        "LIVE_MAX_TRADE",
        lambda v: _is_number(v) and 0.0 < float(v) <= 100.0,
        "LIVE_MAX_TRADE must be in (0, 100]",
    ),
]


def validate_settings(settings: Any) -> list[str]:
    """Return a list of error messages (empty == OK)."""
    errors: list[str] = []
    for rule in RULES:
        if not hasattr(settings, rule.key):
            continue  # rule covers a field not present in this Settings version
        value = getattr(settings, rule.key)
        try:
            ok = rule.check(value)
        except Exception as e:
            ok = False
            rule_msg = f"{rule.msg} (raised {type(e).__name__}: {e})"
            errors.append(f"{rule.key}={value!r}: {rule_msg}")
            continue
        if not ok:
            errors.append(f"{rule.key}={value!r}: {rule.msg}")

    # Cross-field checks
    live_enabled = bool(getattr(settings, "LIVE_ENABLED", False))
    if live_enabled:
        required_for_live = [
            "POLYMARKET_API_KEY",
            "POLYMARKET_API_SECRET",
            "POLYMARKET_PASSPHRASE",
            "POLYGON_WALLET",
            "POLYGON_PRIVATE_KEY",
        ]
        for k in required_for_live:
            v = getattr(settings, k, "")
            if not v:
                errors.append(f"{k} is empty but LIVE_ENABLED=true")

    tail_low = getattr(settings, "FEE_TAIL_LOW", None)
    tail_high = getattr(settings, "FEE_TAIL_HIGH", None)
    if tail_low is not None and tail_high is not None and tail_low >= tail_high:
        errors.append(f"FEE_TAIL_LOW ({tail_low}) must be < FEE_TAIL_HIGH ({tail_high})")

    return errors
