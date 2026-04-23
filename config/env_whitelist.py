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
    # T4.5-B (2026-04-24): maker yolu kapilarini hot-tune edilir hale getir.
    # T4.5 kalibrasyon: 0/960 maker fill -- adaptive yolu cok dar (sig<0.45 +
    # mins>2.0 + spread>0.015). Bu 3 knob /envt ile A/B testi mumkun kilar.
    # Hepsi engine_signals.py:1629-1637 runtime os.getenv() okur (T6.1 doctrin).
    "ADAPTIVE_MAKER_MIN_MINS": {
        "type": "float", "default": "2.0", "min": 0.1, "max": 60.0,
        "group": "sizing",
        "desc": "Adaptive maker min dakika (5m markets icin 0.5-1.0 dene)",
    },
    "ADAPTIVE_MAKER_MAX_SIGNAL": {
        "type": "float", "default": "0.45", "min": 0.1, "max": 1.0,
        "group": "sizing",
        "desc": "Adaptive maker max |sig| (classic 0.60+ icin 0.65 dene)",
    },
    "ADAPTIVE_MAKER_IMPROVE_TICKS": {
        "type": "int", "default": "1", "min": 0, "max": 10,
        "group": "sizing",
        "desc": "Adaptive maker mid+N tick teklif (fill probability)",
    },
    # ── WS / staleness ────────────────────────────────────────────────────
    "WS_STALE_MIN_THRESHOLD": {
        "type": "float", "default": "0.70", "min": 0.0, "max": 1.0,
        "group": "ws",
        "desc": "WS stale min esigi",
    },
    "WS_STALE_THRESHOLD": {
        "type": "float", "default": "60.0", "min": 5.0, "max": 600.0,
        "group": "ws",
        "desc": "WS stale kontrol esigi (saniye) - runtime tunable",
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
        "type": "float", "default": "-8.0", "min": -1000.0, "max": 0.0,
        "group": "risk",
        "desc": "Auto-optimizer PnL pause esigi (USD, negatif) — Sprint 0 -8.0",
    },
    # T7.6 B8: Phase 52 rolling-WR gates — now runtime-read via
    # ``core.auto_optimizer._get_rolling_wr_window`` /
    # ``_get_rolling_wr_kill_threshold``.
    "ROLLING_WR_WINDOW": {
        "type": "int", "default": "20", "min": 10, "max": 1000,
        "group": "risk",
        "desc": "Rolling WR kontrol icin sample sayisi (Phase 52)",
    },
    "ROLLING_WR_KILL": {
        "type": "float", "default": "40.0", "min": 0.0, "max": 100.0,
        "group": "risk",
        "desc": "Rolling WR%% bunun altindaysa pause (Phase 52)",
    },
    # ── PnL divergence monitor (T11.2 G4) ────────────────────────────────
    # Read at runtime every 24h cycle by
    # ``telegram_bot.jobs.pnl_divergence_job._compute_divergence_stats``
    # plus standalone ``scripts/t11_2_g4_divergence_probe.py``. No restart
    # needed after patch — job re-reads env on each cycle (T6.1 pattern).
    "PNL_DIVERGENCE_ENABLED": {
        "type": "bool", "default": "true", "group": "risk",
        "desc": "Paper vs shadow PnL divergence job aktif mi",
    },
    "PNL_DIVERGENCE_WINDOW_H": {
        "type": "float", "default": "24", "min": 1.0, "max": 168.0,
        "group": "risk",
        "desc": "Divergence pencere saat (look-back, max 1 hafta)",
    },
    "PNL_DIVERGENCE_ALERT_PCT": {
        "type": "float", "default": "5.0", "min": 0.01, "max": 100.0,
        "group": "risk",
        "desc": "Divergence alert esigi %% (paper vs shadow)",
    },
    "PNL_DIVERGENCE_MIN_TRADES": {
        "type": "int", "default": "5", "min": 1, "max": 1000,
        "group": "risk",
        "desc": "Bucket basi min trade (INSUFFICIENT gate)",
    },
    # ── REST Timing telemetry (Epic 4 T4.7 + T4.8 + T4.9) ────────────────
    # Default OFF. Enable for empirical RTT calibration (24h sampling).
    # NOTE: `enabled()` helper caches at boot (hot-path). Flipping via
    # /envt takes effect on next process start for safety; this whitelist
    # entry exists to document the knob + enable `.env` persistence.
    "REST_TIMING_TELEMETRY": {
        "type": "bool", "default": "false", "group": "observability",
        "desc": "REST HTTP RTT sampling (T4.7 empirical kalibrasyon)",
    },
    # ── LLM rate-limit (Epic 8 T8.2) ─────────────────────────────────────
    # Read at runtime via
    # ``core.ai_brain._get_llm_ratelimit_backoff`` / ``_get_llm_ratelimit_min_cost``
    # (T6.1 pattern). Module-top constants were frozen at import pre-2026-04-22.
    "LLM_RATELIMIT_BACKOFF_SEC": {
        "type": "float", "default": "60", "min": 1.0, "max": 3600.0,
        "group": "llm",
        "desc": "LLM 429 sonrasi cooldown (sn) — retry kilidi",
    },
    "LLM_RATELIMIT_MIN_COST": {
        "type": "float", "default": "0.001", "min": 0.0, "max": 10.0,
        "group": "llm",
        "desc": "Her 429'da budget'e yazilan min cost ($) — loop koruyucu",
    },
    # ── Live (shadow-mirror) safety limits — T7.6 A5 ─────────────────────
    # core/live_trader.py reads these at runtime on every maybe_mirror().
    "LIVE_BUDGET": {
        "type": "float", "default": "1.49", "min": 0.01, "max": 1000.0,
        "group": "live",
        "desc": "Live toplam butce cap ($) — _total_spent bu degeri asamaz",
    },
    "LIVE_MAX_TRADE": {
        "type": "float", "default": "1.00", "min": 0.10, "max": 100.0,
        "group": "live",
        "desc": "Live trade basi max $ tutari (shadow)",
    },
    "LIVE_MAX_DAILY_LOSS": {
        "type": "float", "default": "1.00", "min": 0.10, "max": 1000.0,
        "group": "live",
        "desc": "Live gunluk zarar durdurma esigi (abs $)",
    },
    "LIVE_MIN_SIGNAL": {
        "type": "float", "default": "0.75", "min": 0.0, "max": 1.0,
        "group": "live",
        "desc": "Live mirror min signal_score",
    },
    "LIVE_MIN_ODDS": {
        "type": "float", "default": "0.75", "min": 0.0, "max": 1.0,
        "group": "live",
        "desc": "Live mirror min odds",
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
