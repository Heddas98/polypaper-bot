"""
Phase 82e Sprint 6 — /env_toggle whitelist.

Only env knobs that are read **at runtime** (not just at module import) are
listed here. Import-time constants are excluded on purpose — toggling them
from Telegram without a bot restart would be a lie.

Each entry:
    key         : canonical UPPER_SNAKE env name
    type        : "bool" | "int" | "float" | "enum"
    default     : string form of the default (what engine code uses when unset)
    desc        : short Turkish label shown in /env_toggle
    min / max   : optional numeric bounds (inclusive)
    choices     : optional list of allowed string values for enum
    group       : UI grouping label
"""
from __future__ import annotations

from typing import Any

ENV_WHITELIST: dict[str, dict[str, Any]] = {
    # ── Classic free-mode gates ──────────────────────────────────────────
    "CLASSIC_BYPASS_ALL_GATES": {
        "type": "bool", "default": "true", "group": "classic",
        "desc": "Classic tum gate'leri bypass etsin mi",
    },
    "CLASSIC_RESPECT_FEE_TAIL": {
        "type": "bool", "default": "false", "group": "classic",
        "desc": "Classic FEE_TAIL gate'ine uysun mu",
    },
    "CLASSIC_RESPECT_TOKEN_CAP": {
        "type": "bool", "default": "false", "group": "classic",
        "desc": "Classic TOKEN_CAP gate'ine uysun mu",
    },
    "CLASSIC_RESPECT_UNSELLABLE": {
        "type": "bool", "default": "false", "group": "classic",
        "desc": "Classic UNSELLABLE gate'ine uysun mu",
    },
    "CLASSIC_RESPECT_ZONES": {
        "type": "bool", "default": "false", "group": "classic",
        "desc": "Classic ALLOWED_ZONES'a uysun mu",
    },
    "CLASSIC_NOTIFY_RESOLUTION": {
        "type": "bool", "default": "true", "group": "classic",
        "desc": "Classic resolution'da Telegram bildirimi",
    },
    "CLASSIC_TAKER_LIMIT_CEIL": {
        "type": "float", "default": "0.99", "min": 0.0, "max": 1.0,
        "group": "classic",
        "desc": "Classic TAKER limit tavani (v6 fill starvation fix)",
    },
    # ── Order lifecycle ──────────────────────────────────────────────────
    "TAKER_STUCK_TIMEOUT_SEC": {
        "type": "float", "default": "120", "min": 0.0, "max": 3600.0,
        "group": "fills",
        "desc": "Stuck TAKER auto-cancel timeout (sn)",
    },
    # ── Core gates (profitability controls) ──────────────────────────────
    "MIN_COMPOSITE": {
        "type": "float", "default": "0.45", "min": 0.0, "max": 1.0,
        "group": "gates",
        "desc": "Fusion composite min esigi (Phase 74)",
    },
    "CONVICTION_MIN": {
        "type": "float", "default": "0.30", "min": 0.0, "max": 1.0,
        "group": "gates",
        "desc": "Conviction min",
    },
    "EDGE_ZONE_5065_MIN": {
        "type": "float", "default": "0.45", "min": 0.0, "max": 1.0,
        "group": "gates",
        "desc": "50-65c edge zone min (Phase 58)",
    },
    "MIN_EDGE_OVER_FEE": {
        "type": "float", "default": "2.0", "min": 0.0, "max": 20.0,
        "group": "gates",
        "desc": "Edge/fee min orani",
    },
    "FEE_GATE_ENABLED": {
        "type": "bool", "default": "true", "group": "gates",
        "desc": "Fee gate aktif mi",
    },
    "UNSELLABLE_CHECK_ENABLED": {
        "type": "bool", "default": "true", "group": "gates",
        "desc": "Unsellable gate aktif mi",
    },
    "BRIER_ALARM_ENABLED": {
        "type": "bool", "default": "true", "group": "gates",
        "desc": "Brier alarm aktif mi",
    },
    "SLIPPAGE_GATE_ENABLED": {
        "type": "bool", "default": "true", "group": "gates",
        "desc": "Slippage gate aktif mi",
    },
    # ── Optimism / Adaptive maker ────────────────────────────────────────
    "OPTIMISM_TAX_ENABLED": {
        "type": "bool", "default": "true", "group": "sizing",
        "desc": "Optimism tax aktif mi",
    },
    "OPTIMISM_TAX_TICKS": {
        "type": "int", "default": "1", "min": 0, "max": 10,
        "group": "sizing",
        "desc": "Optimism tax tick bonus",
    },
    "ADAPTIVE_MAKER_ENABLED": {
        "type": "bool", "default": "true", "group": "sizing",
        "desc": "Adaptive maker/taker routing",
    },
    # ── WS / staleness ────────────────────────────────────────────────────
    "WS_STALE_MIN_THRESHOLD": {
        "type": "float", "default": "0.70", "min": 0.0, "max": 1.0,
        "group": "ws",
        "desc": "WS stale min esigi",
    },
    # ── Logging ──────────────────────────────────────────────────────────
    "TRADE_REASONING_LOG": {
        "type": "bool", "default": "true", "group": "logging",
        "desc": "Trade karar reasoning logu",
    },
    "PROB_GAP_LOG": {
        "type": "bool", "default": "true", "group": "logging",
        "desc": "PROB_GAP satirlari log",
    },
    # ── Risk / auto-pause ────────────────────────────────────────────────
    "PNL_PAUSE_THRESHOLD": {
        "type": "float", "default": "-3.0", "min": -1000.0, "max": 0.0,
        "group": "risk",
        "desc": "Auto-optimizer startup pause PnL esigi (USD, negatif)",
    },
}


def _as_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def coerce_value(key: str, raw: str) -> tuple[bool, Any, str]:
    """Validate + coerce a raw string value for a whitelisted key.

    Returns (ok, coerced_value_as_str, err_msg).
    """
    meta = ENV_WHITELIST.get(key)
    if not meta:
        return False, None, f"Bilinmeyen key: {key}"
    t = meta["type"]
    raw = str(raw).strip()
    if not raw:
        return False, None, "Bos deger kabul edilmez."

    if t == "bool":
        if raw.lower() not in ("true", "false", "1", "0", "yes", "no",
                               "on", "off"):
            return False, None, (
                "bool icin 'true' veya 'false' (yada 1/0/on/off) kullan.")
        coerced = "true" if _as_bool(raw) else "false"
        return True, coerced, ""

    if t == "int":
        try:
            iv = int(raw)
        except ValueError:
            return False, None, "Tam sayi bekleniyor."
        if "min" in meta and iv < meta["min"]:
            return False, None, f"Min {meta['min']} olmali."
        if "max" in meta and iv > meta["max"]:
            return False, None, f"Max {meta['max']} olmali."
        return True, str(iv), ""

    if t == "float":
        try:
            fv = float(raw)
        except ValueError:
            return False, None, "Ondalik sayi bekleniyor."
        if "min" in meta and fv < meta["min"]:
            return False, None, f"Min {meta['min']} olmali."
        if "max" in meta and fv > meta["max"]:
            return False, None, f"Max {meta['max']} olmali."
        # Keep numeric compact; strip trailing zeros sensibly.
        return True, f"{fv:g}", ""

    if t == "enum":
        choices = meta.get("choices") or []
        if raw not in choices:
            return False, None, (
                f"Izinli degerler: {', '.join(choices)}")
        return True, raw, ""

    return False, None, f"Bilinmeyen tip: {t}"


def list_groups() -> list[str]:
    seen: list[str] = []
    for v in ENV_WHITELIST.values():
        g = v.get("group", "other")
        if g not in seen:
            seen.append(g)
    return seen
