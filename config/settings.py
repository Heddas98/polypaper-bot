"""
PolyPaper Bot - Configuration Settings (Phase 34: Signal Fusion & Kelly Tuning)
"""

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("polypaper.config.settings")


# ════════════════════════════════════════════════════════════════════════
# P0-08-A (2026-05-08): Polymarket Up/Down crypto market discovery matrix.
#
# Bot 4 timeframe için iki farklı discovery yöntemi kullanır:
#   - 5m / 15m  → slug-prefix (e.g. btc-updown-5m-{epoch})
#   - 1h / 24h  → series_id (Polymarket /series/{id} endpoint, daily/hourly
#                  Up/Down series'leri farklı slug pattern kullanıyor — örn.
#                  bitcoin-up-or-down-on-may-9-2026, bitcoin-up-or-down-may-8-2026-12pm-et)
#
# Heddas direktifi 2026-05-08:
#   5m  → BTC only (high-freq, BTC most liquid)
#   15m → BTC, ETH, SOL, XRP (full asset coverage)
#   1h  → BTC only (series_id=10114, btc-up-or-down-hourly)
#   24h → BTC only (series_id=41, btc-up-or-down-daily)
#
# Override via env: TF_DISCOVERY_MATRIX_JSON='{...}' (single-line JSON)
# Reference: memory/reference_polymarket_updown_discovery.md
# ════════════════════════════════════════════════════════════════════════
_DEFAULT_TF_DISCOVERY_MATRIX = {
    "5m": {"method": "slug_prefix", "assets": ["BTC"]},
    "15m": {"method": "slug_prefix", "assets": ["BTC", "ETH", "SOL", "XRP"]},
    "1h": {"method": "series_id", "series_map": {"BTC": 10114}},
    "24h": {"method": "series_id", "series_map": {"BTC": 41}},
}


def _load_tf_discovery_matrix() -> dict:
    """Read TF_DISCOVERY_MATRIX_JSON env override; fall back to defaults."""
    raw = os.environ.get("TF_DISCOVERY_MATRIX_JSON", "").strip()
    if not raw:
        return dict(_DEFAULT_TF_DISCOVERY_MATRIX)
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("TF_DISCOVERY_MATRIX_JSON must be a JSON object")
        return parsed
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("TF_DISCOVERY_MATRIX_JSON parse failed (%s); using defaults", e)
        return dict(_DEFAULT_TF_DISCOVERY_MATRIX)


def _derive_supported_assets(matrix: dict) -> list:
    """Union of assets across all TF entries (preserves first-seen order)."""
    seen: list = []
    for cfg in matrix.values():
        if not isinstance(cfg, dict):
            continue
        method = cfg.get("method")
        if method == "slug_prefix":
            for a in cfg.get("assets", []):
                if a not in seen:
                    seen.append(a)
        elif method == "series_id":
            for a in (cfg.get("series_map") or {}).keys():
                if a not in seen:
                    seen.append(a)
    return seen


@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = field(
        default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", "")
    )
    GROK_API_KEY: str = field(default_factory=lambda: os.environ.get("GROK_API_KEY", ""))
    GEMINI_API_KEY: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    OPENROUTER_API_KEY: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", "")
    )
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    POLYMARKET_API_KEY: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_API_KEY", "")
    )
    # Phase 34: Mainnet credentials (Replit Secrets only — NEVER hardcode)
    POLYMARKET_API_SECRET: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_API_SECRET", "")
    )
    POLYMARKET_PASSPHRASE: str = field(
        default_factory=lambda: os.environ.get("POLYMARKET_PASSPHRASE", "")
    )
    POLYGON_WALLET: str = field(default_factory=lambda: os.environ.get("POLYGON_WALLET", ""))
    POLYGON_PRIVATE_KEY: str = field(
        default_factory=lambda: os.environ.get("POLYGON_PRIVATE_KEY", "")
    )
    # Phase 34: Live trading mode
    LIVE_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("LIVE_ENABLED", "false").lower() == "true"
    )
    LIVE_MAX_TRADE: float = 1.00  # $1.00 per trade max
    LIVE_BUDGET: float = 1.49  # Total budget
    # Phase 34: Signal fusion thresholds (configurable via env vars)
    SIGNAL_MIN_SCORE: float = field(
        default_factory=lambda: float(os.environ.get("SIGNAL_MIN_SCORE", "0.35"))
    )
    # Phase 34: Kelly criterion — MOVED to line 75 (Phase 43b unified definition)
    # Phase 17: Admin auth — set ADMIN_TELEGRAM_ID env var or first user becomes admin
    ADMIN_TELEGRAM_ID: int = field(
        default_factory=lambda: int(os.environ.get("ADMIN_TELEGRAM_ID", "0"))
    )
    POLYMARKET_BASE_URL: str = "https://clob.polymarket.com"
    POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"
    DATABASE_PATH: str = "data_store/polypaper.db"
    DEFAULT_BALANCE: float = 10000.0
    TRADE_FEE_PERCENT: float = 0.01
    MIN_TRADE_AMOUNT: float = 1.0
    # P0-08-A (2026-05-08): TF × asset matrix-driven. Bu field'lar geri
    # uyumluluk için korunuyor (market_scanner.py iki listeyi cartesian
    # iterate ediyor); P0-08-B refactor'unda scanner matrix'i doğrudan
    # okuyacak. Override için TF_DISCOVERY_MATRIX_JSON env'i kullanılır.
    TF_DISCOVERY_MATRIX: dict = field(default_factory=_load_tf_discovery_matrix)
    SUPPORTED_TIMEFRAMES: list = field(
        default_factory=lambda: list(_load_tf_discovery_matrix().keys())
    )
    SUPPORTED_ASSETS: list = field(
        default_factory=lambda: _derive_supported_assets(_load_tf_discovery_matrix())
    )
    SUPPORTED_DIRECTIONS: list = field(default_factory=lambda: ["up", "down", "any"])
    ODDS_POLL_INTERVAL: int = 10
    STRATEGY_EVAL_INTERVAL: int = 1
    # Phase 39 (P1.3): REST latency simulation — gaussian sleep before
    # paper-trade order create/cancel to mirror real Polymarket REST RTT.
    # ⚠ Defaults (200ms / 80ms) are HEURISTIC plausible-median estimates,
    # NOT measured against live CLOB endpoints. Pending Epic 4 T4.7 Faz B
    # empirical calibration (enable `REST_TIMING_TELEMETRY=true` to collect
    # 24h of live RTT samples via core/observability/rest_timing.py, then
    # re-derive p50 / p_iqr from real data).
    # NOTE: backtest/replay_engine.py:98 currently uses 250ms as its own
    # default — that 50ms drift is intentional placeholder pending Faz B.
    REST_LATENCY_MS: int = field(
        default_factory=lambda: int(os.environ.get("REST_LATENCY_MS", "200"))
    )
    REST_LATENCY_JITTER_MS: int = field(
        default_factory=lambda: int(os.environ.get("REST_LATENCY_JITTER_MS", "80"))
    )
    # Phase 40b: Order cancellation modeling. Maker orders left unfilled past
    # MAKER_TIF_SECONDS get auto-cancelled (mirrors how a real maker bot would
    # refresh stale resting orders). Self-trade prevention rejects new orders
    # that would cross an existing pending order on the same wallet+token.
    MAKER_TIF_SECONDS: int = field(
        default_factory=lambda: int(os.environ.get("MAKER_TIF_SECONDS", "300"))
    )
    SELF_TRADE_PREVENTION: bool = field(
        default_factory=lambda: os.environ.get("SELF_TRADE_PREVENTION", "true").lower() == "true"
    )
    # Phase 41a: UMA Oracle resolution timing. Real Polymarket UMA liveness
    # is typically 2h after market close, with a 24-48h window for disputes.
    # The engine actively polls the Gamma API for the official winner first;
    # this is the FALLBACK force-settle deadline if the Oracle is silent.
    # Default raised from 1800s (30min) to 7200s (2h) — closer to real UMA.
    UMA_FORCE_SETTLE_SECONDS: int = field(
        default_factory=lambda: int(os.environ.get("UMA_FORCE_SETTLE_SECONDS", "7200"))
    )
    UMA_EXTREME_ODDS_WINDOW_SECONDS: int = field(
        default_factory=lambda: int(os.environ.get("UMA_EXTREME_ODDS_WINDOW_SECONDS", "1800"))
    )
    # Sprint 5 HOTFIX v4: shorter force-settle deadline for 5m/15m markets.
    # Short-TF crypto Up/Down markets resolve within a couple of minutes on
    # Polymarket; the 2h fallback (UMA_FORCE_SETTLE_SECONDS) designed for
    # disputable markets is massive overkill. 900s ≈ 15min gives oracle
    # plenty of time while keeping the open-positions panel current.
    UMA_FORCE_SETTLE_SHORT_SEC: int = field(
        default_factory=lambda: int(os.environ.get("UMA_FORCE_SETTLE_SHORT_SEC", "900"))
    )
    # Phase 42b: Cross-strategy token exposure cap. When multiple strategies
    # converge on the same Polymarket token, total notional risk grows fast.
    # This cap aggregates pending+open exposure on a wallet+token pair and
    # refuses new orders that would push past the ceiling. Set 0 to disable.
    MAX_TOKEN_EXPOSURE_USD: float = field(
        default_factory=lambda: float(os.environ.get("MAX_TOKEN_EXPOSURE_USD", "50.0"))
    )
    # Phase 43a: Polymarket Mart 2026 fee model.
    # "v1" = legacy quadratic (feeRate 0.25, exp 2).
    # "v2" = category-aware linear curve (crypto 0.072, exp 1) + maker rebates.
    FEE_MODEL: str = field(default_factory=lambda: os.environ.get("FEE_MODEL", "v2"))
    # Tail-zone gate: refuse trades at extreme odds where thin liquidity +
    # UMA outlier risk dominates the tiny fee advantage. Set to 0.0 to disable.
    FEE_TAIL_LOW: float = field(
        default_factory=lambda: float(os.environ.get("FEE_TAIL_LOW", "0.15"))
    )
    FEE_TAIL_HIGH: float = field(
        default_factory=lambda: float(os.environ.get("FEE_TAIL_HIGH", "0.85"))
    )
    # Phase 43b: Kelly production knobs (Yön B Stream 1).
    # Quarter Kelly default — halves variance compared to full Kelly.
    KELLY_FRACTION: float = field(
        default_factory=lambda: float(os.environ.get("KELLY_FRACTION", "0.25"))
    )
    # Hard cap on per-trade risk as % of bankroll, after Kelly sizing.
    KELLY_MAX_BET_PCT: float = field(
        default_factory=lambda: float(os.environ.get("KELLY_MAX_BET_PCT", "0.05"))
    )
    # Correlated position halving: if there's already an open trade on the
    # same underlying asset (BTC/ETH/SOL/XRP), halve the new Kelly size to
    # control exposure to a single price move.
    KELLY_CORRELATED_HALVING: bool = field(
        default_factory=lambda: os.environ.get("KELLY_CORRELATED_HALVING", "true").lower() == "true"
    )
    # Phase 43c: Maker-first execution (Yön B Stream 1).
    # Spread threshold above which we prefer posting a maker limit instead
    # of crossing as taker. Lower → more maker fills (zero gross fee).
    MAKER_WIDE_SPREAD: float = field(
        default_factory=lambda: float(os.environ.get("MAKER_WIDE_SPREAD", "0.04"))
    )
    # Taker fallback gate: if a strategy is in maker mode and the market is
    # < this many minutes from close OR signal score crosses this threshold,
    # fall back to a taker order to guarantee the fill before settlement.
    MAKER_TAKER_FALLBACK_MINS: float = field(
        default_factory=lambda: float(os.environ.get("MAKER_TAKER_FALLBACK_MINS", "1.0"))
    )
    MAKER_TAKER_FALLBACK_SIGNAL: float = field(
        default_factory=lambda: float(os.environ.get("MAKER_TAKER_FALLBACK_SIGNAL", "0.60"))
    )
    # Phase 44a — Binance multi-stream microstructure feed
    BINANCE_MULTISTREAM_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("BINANCE_MULTISTREAM_ENABLED", "true").lower()
        == "true"
    )
    BINANCE_TRADE_WINDOW_SECONDS: float = field(
        default_factory=lambda: float(os.environ.get("BINANCE_TRADE_WINDOW_SECONDS", "60"))
    )
    BINANCE_FUTURES_FUNDING: bool = field(
        default_factory=lambda: os.environ.get("BINANCE_FUTURES_FUNDING", "true").lower() == "true"
    )
    # Phase 44b — Chainlink oracle parity gate
    CHAINLINK_ORACLE_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("CHAINLINK_ORACLE_ENABLED", "false").lower()
        == "true"
    )
    CHAINLINK_PARITY_BPS: float = field(
        default_factory=lambda: float(os.environ.get("CHAINLINK_PARITY_BPS", "20"))
    )
    # Phase 46 — Signal fusion consumer for microstructure / oracle / funding
    MICRO_BOOST_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("MICRO_BOOST_ENABLED", "true").lower() == "true"
    )
    MICRO_BOOST_WEIGHT: float = field(
        default_factory=lambda: float(os.environ.get("MICRO_BOOST_WEIGHT", "0.15"))
    )
    MICRO_BOOST_CLAMP: float = field(
        default_factory=lambda: float(os.environ.get("MICRO_BOOST_CLAMP", "0.20"))
    )
    PARITY_GATE_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("PARITY_GATE_ENABLED", "true").lower() == "true"
    )
    FUNDING_TILT_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("FUNDING_TILT_ENABLED", "true").lower() == "true"
    )
    FUNDING_TILT_THRESHOLD: float = field(
        default_factory=lambda: float(os.environ.get("FUNDING_TILT_THRESHOLD", "0.0005"))
    )
    FUNDING_TILT_WEIGHT: float = field(
        default_factory=lambda: float(os.environ.get("FUNDING_TILT_WEIGHT", "0.05"))
    )
    # Phase 47a — Adaptive micro weight tracker
    ADAPTIVE_MICRO_WEIGHT_ENABLED: bool = field(
        default_factory=lambda: os.environ.get("ADAPTIVE_MICRO_WEIGHT_ENABLED", "false").lower()
        == "true"
    )
    # Becker calibration system removed 2026-04-29 (Heddas direktifi: Becker
    # tam silme). Kaldırılan settings: BECKER_CALIB_ENABLED, BECKER_CALIB_WEIGHT,
    # BECKER_CALIB_CLAMP, BECKER_KALSHI_WEIGHT, BECKER_DECISION_MODE,
    # BECKER_DECISION_THRESHOLD, BECKER_DECISION_STRATEGY_WHITELIST,
    # ADAPTIVE_BECKER_WEIGHT_ENABLED. Ref: docs/audits/fee_reality_check_2026_04.md.
    BOT_NAME: str = "PolyPaper Bot"
    # BOT_VERSION removed — single source of truth is telegram_bot/version.py
    # (was: BOT_VERSION: str = "9.7.3", drifted from v9.7.9). T0.2 fix 2026-04-20.
    WEBSITE_URL: str = "https://polyscout.io"

    def validate(self) -> list[str]:
        """Boot-time config validation. Returns list of error strings (empty = OK).

        C-01 (2026-05-15 ultra-audit): LIVE mode requires explicit secrets +
        explicit ADMIN_TELEGRAM_ID. The previous version only checked
        TELEGRAM_BOT_TOKEN; with empty .env on a mainnet-LIVE bot, the
        `is_admin()` fallback "ADMIN_TELEGRAM_ID==0 → everyone admin" would
        grant any incoming Telegram user full admin (env_toggle, force_settle,
        live buy/sell). Mainnet pUSD theft window.
        """
        errors = []
        if not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is not set")
        # C-01: defense-in-depth — LIVE mode requires real admin + signer.
        if self.LIVE_ENABLED:
            if self.ADMIN_TELEGRAM_ID == 0:
                errors.append(
                    "LIVE_ENABLED=true but ADMIN_TELEGRAM_ID is unset/0 — "
                    "is_admin() would grant admin to every Telegram user "
                    "(backdoor C-01, audit 2026-05-15)"
                )
            if not self.POLYGON_PRIVATE_KEY:
                errors.append("LIVE_ENABLED=true but POLYGON_PRIVATE_KEY missing")
            if not self.POLYGON_WALLET:
                errors.append("LIVE_ENABLED=true but POLYGON_WALLET missing")
        return errors

    def is_admin(self, telegram_id: int) -> bool:
        """Phase 17 + C-01 (2026-05-15 ultra-audit): strict admin check.

        Previous behavior: ``ADMIN_TELEGRAM_ID==0`` granted admin to ALL
        Telegram users (intended "single-user dev mode" fallback). Audit
        C-01 flagged this as a mainnet backdoor — an empty/corrupted .env
        on a LIVE bot would silently grant the first incoming user full
        admin (env_toggle, force_settle, live buy/sell). Fixed: in LIVE
        mode the fallback is disabled (deny by default). Paper mode keeps
        the single-user fallback for local dev ergonomics.
        """
        if self.ADMIN_TELEGRAM_ID == 0:
            # LIVE mode: deny by default (no admin set = no admin grants).
            # validate() already raises a boot-time error, but we double-gate
            # here in case validate() is bypassed by a future code path.
            if self.LIVE_ENABLED:
                return False
            # Paper mode: keep single-user dev fallback (matches old behavior).
            return True
        return telegram_id == self.ADMIN_TELEGRAM_ID
