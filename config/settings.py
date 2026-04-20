"""
PolyPaper Bot - Configuration Settings (Phase 34: Signal Fusion & Kelly Tuning)
"""
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    TELEGRAM_BOT_TOKEN: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    GROK_API_KEY: str = field(default_factory=lambda: os.environ.get("GROK_API_KEY", ""))
    GEMINI_API_KEY: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    OPENROUTER_API_KEY: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    ANTHROPIC_API_KEY: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    POLYMARKET_API_KEY: str = field(default_factory=lambda: os.environ.get("POLYMARKET_API_KEY", ""))
    # Phase 34: Mainnet credentials (Replit Secrets only — NEVER hardcode)
    POLYMARKET_API_SECRET: str = field(default_factory=lambda: os.environ.get("POLYMARKET_API_SECRET", ""))
    POLYMARKET_PASSPHRASE: str = field(default_factory=lambda: os.environ.get("POLYMARKET_PASSPHRASE", ""))
    POLYGON_WALLET: str = field(default_factory=lambda: os.environ.get("POLYGON_WALLET", ""))
    POLYGON_PRIVATE_KEY: str = field(default_factory=lambda: os.environ.get("POLYGON_PRIVATE_KEY", ""))
    # Phase 34: Live trading mode
    LIVE_ENABLED: bool = field(default_factory=lambda: os.environ.get("LIVE_ENABLED", "false").lower() == "true")
    LIVE_MAX_TRADE: float = 1.00  # $1.00 per trade max
    LIVE_BUDGET: float = 1.49     # Total budget
    # Phase 34: Signal fusion thresholds (configurable via env vars)
    SIGNAL_MIN_SCORE: float = field(default_factory=lambda: float(os.environ.get("SIGNAL_MIN_SCORE", "0.35")))
    # Phase 34: Kelly criterion — MOVED to line 75 (Phase 43b unified definition)
    # Phase 17: Admin auth — set ADMIN_TELEGRAM_ID env var or first user becomes admin
    ADMIN_TELEGRAM_ID: int = field(default_factory=lambda: int(os.environ.get("ADMIN_TELEGRAM_ID", "0")))
    POLYMARKET_BASE_URL: str = "https://clob.polymarket.com"
    POLYMARKET_GAMMA_URL: str = "https://gamma-api.polymarket.com"
    DATABASE_PATH: str = "data_store/polypaper.db"
    DEFAULT_BALANCE: float = 10000.0
    TRADE_FEE_PERCENT: float = 0.01
    MIN_TRADE_AMOUNT: float = 1.0
    SUPPORTED_ASSETS: list = field(default_factory=lambda: ["BTC", "ETH", "SOL", "XRP"])
    SUPPORTED_TIMEFRAMES: list = field(default_factory=lambda: ["5m", "15m"])
    SUPPORTED_DIRECTIONS: list = field(default_factory=lambda: ["up", "down", "any"])
    ODDS_POLL_INTERVAL: int = 10
    STRATEGY_EVAL_INTERVAL: int = 1
    # Phase 39 (P1.3): REST latency simulation — gaussian sleep before
    # paper-trade order create/cancel to mirror real Polymarket REST RTT.
    # Real-world median is ~150-250ms; jitter ~50-100ms one-sigma.
    REST_LATENCY_MS: int = field(default_factory=lambda: int(os.environ.get("REST_LATENCY_MS", "200")))
    REST_LATENCY_JITTER_MS: int = field(default_factory=lambda: int(os.environ.get("REST_LATENCY_JITTER_MS", "80")))
    # Phase 40b: Order cancellation modeling. Maker orders left unfilled past
    # MAKER_TIF_SECONDS get auto-cancelled (mirrors how a real maker bot would
    # refresh stale resting orders). Self-trade prevention rejects new orders
    # that would cross an existing pending order on the same wallet+token.
    MAKER_TIF_SECONDS: int = field(default_factory=lambda: int(os.environ.get("MAKER_TIF_SECONDS", "300")))
    SELF_TRADE_PREVENTION: bool = field(default_factory=lambda: os.environ.get("SELF_TRADE_PREVENTION", "true").lower() == "true")
    # Phase 41a: UMA Oracle resolution timing. Real Polymarket UMA liveness
    # is typically 2h after market close, with a 24-48h window for disputes.
    # The engine actively polls the Gamma API for the official winner first;
    # this is the FALLBACK force-settle deadline if the Oracle is silent.
    # Default raised from 1800s (30min) to 7200s (2h) — closer to real UMA.
    UMA_FORCE_SETTLE_SECONDS: int = field(default_factory=lambda: int(os.environ.get("UMA_FORCE_SETTLE_SECONDS", "7200")))
    UMA_EXTREME_ODDS_WINDOW_SECONDS: int = field(default_factory=lambda: int(os.environ.get("UMA_EXTREME_ODDS_WINDOW_SECONDS", "1800")))
    # Sprint 5 HOTFIX v4: shorter force-settle deadline for 5m/15m markets.
    # Short-TF crypto Up/Down markets resolve within a couple of minutes on
    # Polymarket; the 2h fallback (UMA_FORCE_SETTLE_SECONDS) designed for
    # disputable markets is massive overkill. 900s ≈ 15min gives oracle
    # plenty of time while keeping the open-positions panel current.
    UMA_FORCE_SETTLE_SHORT_SEC: int = field(default_factory=lambda: int(os.environ.get("UMA_FORCE_SETTLE_SHORT_SEC", "900")))
    # Phase 42b: Cross-strategy token exposure cap. When multiple strategies
    # converge on the same Polymarket token, total notional risk grows fast.
    # This cap aggregates pending+open exposure on a wallet+token pair and
    # refuses new orders that would push past the ceiling. Set 0 to disable.
    MAX_TOKEN_EXPOSURE_USD: float = field(default_factory=lambda: float(os.environ.get("MAX_TOKEN_EXPOSURE_USD", "50.0")))
    # Phase 43a: Polymarket Mart 2026 fee model.
    # "v1" = legacy quadratic (feeRate 0.25, exp 2).
    # "v2" = category-aware linear curve (crypto 0.072, exp 1) + maker rebates.
    FEE_MODEL: str = field(default_factory=lambda: os.environ.get("FEE_MODEL", "v2"))
    # Tail-zone gate: refuse trades at extreme odds where thin liquidity +
    # UMA outlier risk dominates the tiny fee advantage. Set to 0.0 to disable.
    FEE_TAIL_LOW: float = field(default_factory=lambda: float(os.environ.get("FEE_TAIL_LOW", "0.15")))
    FEE_TAIL_HIGH: float = field(default_factory=lambda: float(os.environ.get("FEE_TAIL_HIGH", "0.85")))
    # Phase 43b: Kelly production knobs (Yön B Stream 1).
    # Quarter Kelly default — halves variance compared to full Kelly.
    KELLY_FRACTION: float = field(default_factory=lambda: float(os.environ.get("KELLY_FRACTION", "0.25")))
    # Hard cap on per-trade risk as % of bankroll, after Kelly sizing.
    KELLY_MAX_BET_PCT: float = field(default_factory=lambda: float(os.environ.get("KELLY_MAX_BET_PCT", "0.05")))
    # Correlated position halving: if there's already an open trade on the
    # same underlying asset (BTC/ETH/SOL/XRP), halve the new Kelly size to
    # control exposure to a single price move.
    KELLY_CORRELATED_HALVING: bool = field(default_factory=lambda: os.environ.get("KELLY_CORRELATED_HALVING", "true").lower() == "true")
    # Phase 43c: Maker-first execution (Yön B Stream 1).
    # Spread threshold above which we prefer posting a maker limit instead
    # of crossing as taker. Lower → more maker fills (zero gross fee).
    MAKER_WIDE_SPREAD: float = field(default_factory=lambda: float(os.environ.get("MAKER_WIDE_SPREAD", "0.04")))
    # Taker fallback gate: if a strategy is in maker mode and the market is
    # < this many minutes from close OR signal score crosses this threshold,
    # fall back to a taker order to guarantee the fill before settlement.
    MAKER_TAKER_FALLBACK_MINS: float = field(default_factory=lambda: float(os.environ.get("MAKER_TAKER_FALLBACK_MINS", "1.0")))
    MAKER_TAKER_FALLBACK_SIGNAL: float = field(default_factory=lambda: float(os.environ.get("MAKER_TAKER_FALLBACK_SIGNAL", "0.60")))
    # Phase 44a — Binance multi-stream microstructure feed
    BINANCE_MULTISTREAM_ENABLED: bool = field(default_factory=lambda: os.environ.get("BINANCE_MULTISTREAM_ENABLED", "true").lower() == "true")
    BINANCE_TRADE_WINDOW_SECONDS: float = field(default_factory=lambda: float(os.environ.get("BINANCE_TRADE_WINDOW_SECONDS", "60")))
    BINANCE_FUTURES_FUNDING: bool = field(default_factory=lambda: os.environ.get("BINANCE_FUTURES_FUNDING", "true").lower() == "true")
    # Phase 44b — Chainlink oracle parity gate
    CHAINLINK_ORACLE_ENABLED: bool = field(default_factory=lambda: os.environ.get("CHAINLINK_ORACLE_ENABLED", "false").lower() == "true")
    CHAINLINK_PARITY_BPS: float = field(default_factory=lambda: float(os.environ.get("CHAINLINK_PARITY_BPS", "20")))
    # Phase 46 — Signal fusion consumer for microstructure / oracle / funding
    MICRO_BOOST_ENABLED: bool = field(default_factory=lambda: os.environ.get("MICRO_BOOST_ENABLED", "true").lower() == "true")
    MICRO_BOOST_WEIGHT: float = field(default_factory=lambda: float(os.environ.get("MICRO_BOOST_WEIGHT", "0.15")))
    MICRO_BOOST_CLAMP: float = field(default_factory=lambda: float(os.environ.get("MICRO_BOOST_CLAMP", "0.20")))
    PARITY_GATE_ENABLED: bool = field(default_factory=lambda: os.environ.get("PARITY_GATE_ENABLED", "true").lower() == "true")
    FUNDING_TILT_ENABLED: bool = field(default_factory=lambda: os.environ.get("FUNDING_TILT_ENABLED", "true").lower() == "true")
    FUNDING_TILT_THRESHOLD: float = field(default_factory=lambda: float(os.environ.get("FUNDING_TILT_THRESHOLD", "0.0005")))
    FUNDING_TILT_WEIGHT: float = field(default_factory=lambda: float(os.environ.get("FUNDING_TILT_WEIGHT", "0.05")))
    # Phase 47a — Adaptive micro weight tracker
    ADAPTIVE_MICRO_WEIGHT_ENABLED: bool = field(default_factory=lambda: os.environ.get("ADAPTIVE_MICRO_WEIGHT_ENABLED", "false").lower() == "true")
    # Phase 47f — Becker δ(p) calibration boost (poly+kalshi ensemble)
    BECKER_CALIB_ENABLED: bool = field(default_factory=lambda: os.environ.get("BECKER_CALIB_ENABLED", "false").lower() == "true")
    BECKER_CALIB_WEIGHT: float = field(default_factory=lambda: float(os.environ.get("BECKER_CALIB_WEIGHT", "0.10")))
    BECKER_CALIB_CLAMP: float = field(default_factory=lambda: float(os.environ.get("BECKER_CALIB_CLAMP", "0.15")))
    # Phase 47f.1 — kalshi cross-platform ensemble weight (0=poly only, 1=kalshi only)
    BECKER_KALSHI_WEIGHT: float = field(default_factory=lambda: float(os.environ.get("BECKER_KALSHI_WEIGHT", "0.30")))
    # Phase 47f.7 — Becker decision-mode wiring (data-driven, strategy-specific).
    # Default "boost" preserves Phase 47f.2 live-parity behavior. Backtest sweep
    # 47f.8 (BTC 5m 400m, 30 runs across 6 strategies) found late_convergence is
    # the only calibration-FRIENDLY strategy with flip@0.01 producing +$19.52
    # PnL gain (+105% on baseline). All other strategies are HOSTILE or NEUTRAL.
    # Whitelist is the data-driven gate: only listed strategies use the new
    # decision-mode path; everything else continues with the additive boost.
    #   BECKER_DECISION_MODE: boost (default, no-op) | veto | flip | off
    #   BECKER_DECISION_THRESHOLD: |delta| floor for veto/flip to fire
    #   BECKER_DECISION_STRATEGY_WHITELIST: CSV of strategies decision-mode
    #     applies to (empty = no strategy). Case-insensitive match.
    BECKER_DECISION_MODE: str = field(default_factory=lambda: os.environ.get("BECKER_DECISION_MODE", "boost").strip().lower())
    BECKER_DECISION_THRESHOLD: float = field(default_factory=lambda: float(os.environ.get("BECKER_DECISION_THRESHOLD", "0.01")))
    BECKER_DECISION_STRATEGY_WHITELIST: str = field(default_factory=lambda: os.environ.get("BECKER_DECISION_STRATEGY_WHITELIST", "late_convergence"))
    # Phase 48 — Adaptive per-asset Becker weight (BeckerWeightTracker).
    # Disabled by default. When enabled, the engine multiplies the Becker boost
    # by a per-asset factor in [0.50, 1.50] tuned online from the Pearson
    # correlation between (signed delta at order open) and (pnl sign at close).
    # Wiring is opt-in: tracker class is built but not yet referenced from
    # core/engine.py — that happens in a follow-up phase after live shadow
    # data confirms the 47f.7 wins are stable.
    ADAPTIVE_BECKER_WEIGHT_ENABLED: bool = field(default_factory=lambda: os.environ.get("ADAPTIVE_BECKER_WEIGHT_ENABLED", "false").lower() == "true")
    BOT_NAME: str = "PolyPaper Bot"
    BOT_VERSION: str = "9.7.3"
    WEBSITE_URL: str = "https://polyscout.io"

    def validate(self) -> list[str]:
        errors = []
        if not self.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is not set")
        return errors

    def is_admin(self, telegram_id: int) -> bool:
        """Phase 17: Check admin. If ADMIN_TELEGRAM_ID=0, first user is admin."""
        if self.ADMIN_TELEGRAM_ID == 0:
            return True  # No admin set = single-user mode
        return telegram_id == self.ADMIN_TELEGRAM_ID
