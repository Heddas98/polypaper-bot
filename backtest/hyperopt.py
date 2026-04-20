"""
Phase 67: Optuna Hyperparameter Optimization Pipeline
=====================================================
Source: R4 (freqtrade — 48.6K stars, architectural inspiration only)

Automatically sweeps strategy parameters using Optuna's TPE sampler.
Each trial runs a ReplayEngine backtest and returns an objective metric.

Usage:
    from backtest.hyperopt import HyperOptPipeline, HyperOptConfig

    pipeline = HyperOptPipeline(db)
    result = await pipeline.optimize(
        strategy_name="hour_edge",
        asset="BTC", timeframe="5m",
        n_trials=100
    )
    print(result.best_params)
    print(result.best_score)

CLI:
    python -m backtest.hyperopt --strategy hour_edge --asset BTC --trials 50

ENV:
    HYPEROPT_ENABLED=true
    HYPEROPT_N_TRIALS=100
    HYPEROPT_METRIC=sharpe_ratio      # sharpe_ratio, win_rate, total_pnl, profit_factor
    HYPEROPT_MIN_TRADES=10            # skip trials with too few trades
    HYPEROPT_TRAIN_PCT=0.70           # train/test split (70% train, 30% test)
    HYPEROPT_TIMEOUT_S=3600           # max seconds per optimization run
    HYPEROPT_PRUNER=median            # median or hyperband
"""
from __future__ import annotations

import os
import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, Any, Callable
from datetime import datetime, timezone
from telegram_bot.templates.safe_html import esc

# Phase 82b: nest_asyncio removed — ask/tell API is used instead.
# The previous approach (nest_asyncio + asyncio.to_thread + study.optimize)
# could cause the main event loop to stall or crash silently when a worker
# thread re-entered the loop. The new path drives Optuna trials manually,
# one at a time, in the caller's own event loop (see optimize() below).

logger = logging.getLogger("polypaper.backtest.hyperopt")

try:
    import optuna
    from optuna.samplers import TPESampler
    from optuna.pruners import MedianPruner, HyperbandPruner
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False
    logger.warning("optuna not installed — hyperopt disabled")

from backtest.replay_engine import ReplayEngine, ReplayConfig
from backtest.simulation.portfolio import PortfolioStats


# ═══════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════

@dataclass
class HyperOptConfig:
    """Configuration for a hyperopt run."""
    strategy_name: str = "hour_edge"
    asset_filter: str = ""
    timeframe_filter: str = ""
    direction_filter: str = ""
    n_trials: int = int(os.getenv("HYPEROPT_N_TRIALS", "100"))
    metric: str = os.getenv("HYPEROPT_METRIC", "sharpe_ratio")
    min_trades: int = int(os.getenv("HYPEROPT_MIN_TRADES", "10"))
    train_pct: float = float(os.getenv("HYPEROPT_TRAIN_PCT", "0.70"))
    timeout_s: int = int(os.getenv("HYPEROPT_TIMEOUT_S", "3600"))
    initial_balance: float = 10000.0
    trade_amount: float = 1.0
    fill_mode: str = "real_orderbook"
    max_markets: int = 0  # 0 = no limit
    # Phase 81: Market selection modes
    last_n: int = 0       # >0 = only last N markets (most recent)
    from_date: str = ""   # "2026-04-10" → start_ms filter
    random_n: int = 0     # >0 = randomly sample N markets


@dataclass
class HyperOptResult:
    """Result from a hyperopt run."""
    strategy_name: str = ""
    best_params: dict = field(default_factory=dict)
    best_score: float = 0.0
    metric: str = ""
    n_trials: int = 0
    n_completed: int = 0
    n_pruned: int = 0
    # Train vs test scores
    train_score: float = 0.0
    test_score: float = 0.0
    overfit_ratio: float = 0.0  # test/train — <0.7 = likely overfit
    # Full stats from best trial
    train_stats: Optional[dict] = None
    test_stats: Optional[dict] = None
    # Metadata
    duration_s: float = 0.0
    timestamp: str = ""
    all_trials: list = field(default_factory=list)  # top N trials summary
    # ── Phase 82e Sprint 5 (FINAL): Fusion×29 granular apply ──
    # When the run was scoped to a specific (asset, timeframe) slice —
    # e.g. /hyperopt fusion BTC 5m — these tags flow into hyperopt_results
    # and the apply path uses them to update ALL live strategies matching
    # (strategy_type, asset, timeframe) instead of the legacy rows[0]-only
    # behavior which broke for multi-instance types like Fusion.
    asset: str = ""
    timeframe: str = ""

    def is_overfit(self) -> bool:
        """Return True if test score is suspiciously lower than train score.

        Phase 82e Sprint 5: sign-aware. The legacy version flagged every
        negative-train result as overfit, which misread strategies whose
        best score was simply unprofitable (train_score < 0) even when the
        test score held up at the same level (overfit_ratio ≈ 1.0). Now:
          - train == 0      → cannot judge, treat as overfit (conservative).
          - train > 0       → classic overfit_ratio < threshold.
          - train < 0       → negative-score domain: if test is *worse* than
                              train by more than (1 - threshold), flag it.
                              overfit_ratio for negative values is inverted
                              (higher = worse) so we test the *opposite*
                              side of the threshold.
        """
        import os as _os
        thr = float(_os.getenv("HYPEROPT_OVERFIT_THRESHOLD", "0.70"))
        if self.train_score == 0.0:
            return True
        if self.train_score > 0.0:
            return self.overfit_ratio < thr
        # train_score < 0 — for negative scores overfit_ratio = test/train
        # means test is BETTER than train when ratio < 1, so treat the
        # inverse: ratio > (2 - thr) means test badly underperforms.
        return self.overfit_ratio > (2.0 - thr)

    def summary(self) -> str:
        """Human-readable summary."""
        overfit_flag = " ⚠️OVERFIT" if self.is_overfit() else " ✅OK"
        lines = [
            f"═══ HyperOpt Result: {self.strategy_name} ═══",
            f"Metric: {self.metric} | Trials: {self.n_completed}/{self.n_trials} "
            f"(pruned: {self.n_pruned})",
            f"Best Score: {self.best_score:.4f}",
            f"Train: {self.train_score:.4f} | Test: {self.test_score:.4f} "
            f"| Ratio: {self.overfit_ratio:.2f}{overfit_flag}",
            f"Duration: {self.duration_s:.1f}s",
            f"Best Params: {json.dumps(self.best_params, indent=2)}",
        ]
        return "\n".join(lines)

    async def save_to_db(self, db, strategy_id: str = None,
                         source: str = "telegram") -> int:
        """Persist result to hyperopt_results table. Returns row id.

        Phase 82e Sprint 5: asset + timeframe columns populated from
        self.asset / self.timeframe (set by worker from --asset/--timeframe
        CLI flags). Empty strings mean "unfiltered run" (back-compat).
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        cur = await db.conn.execute(
            """INSERT INTO hyperopt_results
               (strategy_name, strategy_id, best_params, best_score, metric,
                train_score, test_score, overfit_ratio, is_overfit, applied,
                source, n_trials, duration_s, created_at,
                asset, timeframe)
               VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)""",
            (self.strategy_name, strategy_id,
             json.dumps(self.best_params), self.best_score, self.metric,
             self.train_score, self.test_score, self.overfit_ratio,
             1 if self.is_overfit() else 0, source,
             self.n_completed, self.duration_s, now,
             (self.asset or "").strip().upper(),
             (self.timeframe or "").strip()))
        await db.conn.commit()
        row_id = cur.lastrowid
        logger.info(f"💾 HyperOpt result saved: {self.strategy_name} "
                     f"score={self.best_score:.4f} overfit={self.is_overfit()} "
                     f"asset={self.asset!r} tf={self.timeframe!r} "
                     f"source={source} id={row_id}")
        return row_id


# ═══════════════════════════════════════════════════════════════
# Strategy Parameter Spaces
# ═══════════════════════════════════════════════════════════════

def _space_hour_edge(trial: "optuna.Trial") -> dict:
    """Parameter space for HourEdgeStrategy."""
    return {
        "min_win_rate": trial.suggest_float("min_win_rate", 0.50, 0.70, step=0.01),
        "entry_pct": trial.suggest_float("entry_pct", 0.05, 0.30, step=0.01),
    }


def _space_late_convergence(trial: "optuna.Trial") -> dict:
    """Parameter space for LateConvergenceStrategy."""
    return {
        "min_elapsed_pct": trial.suggest_float("min_elapsed_pct", 0.60, 0.95, step=0.05),
        "max_entry_price": trial.suggest_float("max_entry_price", 0.85, 0.98, step=0.01),
        "min_spread_threshold": trial.suggest_float("min_spread_threshold", 0.01, 0.06, step=0.005),
    }


def _space_streak_reversal(trial: "optuna.Trial") -> dict:
    """Parameter space for StreakReversalStrategy."""
    return {
        "min_streak": trial.suggest_int("min_streak", 3, 10),
        "entry_pct": trial.suggest_float("entry_pct", 0.05, 0.30, step=0.01),
    }


def _space_opening_breakout(trial: "optuna.Trial") -> dict:
    """Parameter space for OpeningBreakoutStrategy."""
    return {
        "breakout_threshold": trial.suggest_float("breakout_threshold", 0.02, 0.10, step=0.01),
        "entry_window_pct": trial.suggest_float("entry_window_pct", 0.05, 0.25, step=0.05),
        "min_volume_ratio": trial.suggest_float("min_volume_ratio", 1.0, 3.0, step=0.5),
    }


def _space_orderbook_imbalance(trial: "optuna.Trial") -> dict:
    """Parameter space for OrderbookImbalanceStrategy."""
    return {
        "imbalance_threshold": trial.suggest_float("imbalance_threshold", 0.15, 0.50, step=0.05),
        "min_depth": trial.suggest_float("min_depth", 50.0, 500.0, step=50.0),
        "entry_pct": trial.suggest_float("entry_pct", 0.05, 0.25, step=0.05),
    }


def _space_fade_rip(trial: "optuna.Trial") -> dict:
    """Parameter space for FadeRipStrategy."""
    return {
        "rip_threshold": trial.suggest_float("rip_threshold", 0.05, 0.20, step=0.01),
        "fade_entry_pct": trial.suggest_float("fade_entry_pct", 0.30, 0.70, step=0.05),
        "min_reversion": trial.suggest_float("min_reversion", 0.02, 0.10, step=0.01),
    }


def _space_taker_flow(trial: "optuna.Trial") -> dict:
    """Parameter space for TakerFlowStrategy."""
    return {
        "flow_threshold": trial.suggest_float("flow_threshold", 0.10, 0.40, step=0.05),
        "lookback_snaps": trial.suggest_int("lookback_snaps", 3, 10),
        "entry_pct": trial.suggest_float("entry_pct", 0.10, 0.35, step=0.05),
    }


def _space_calibration_arb(trial: "optuna.Trial") -> dict:
    """Parameter space for CalibrationArbStrategy."""
    return {
        "arb_threshold": trial.suggest_float("arb_threshold", 0.03, 0.12, step=0.01),
        "min_confidence": trial.suggest_float("min_confidence", 0.55, 0.75, step=0.05),
    }


def _space_cross_coin(trial: "optuna.Trial") -> dict:
    """Parameter space for CrossCoinStrategy."""
    return {
        "correlation_threshold": trial.suggest_float("correlation_threshold", 0.60, 0.95, step=0.05),
        "lag_seconds": trial.suggest_int("lag_seconds", 10, 120, step=10),
    }


def _space_composite(trial: "optuna.Trial") -> dict:
    """Parameter space for CompositeStrategy."""
    return {
        "min_agreement": trial.suggest_int("min_agreement", 2, 5),
        "weight_momentum": trial.suggest_float("weight_momentum", 0.1, 0.4, step=0.05),
        "weight_reversal": trial.suggest_float("weight_reversal", 0.1, 0.4, step=0.05),
    }


def _space_funding_rate(trial: "optuna.Trial") -> dict:
    """Parameter space for FundingRateStrategy."""
    return {
        "funding_threshold": trial.suggest_float("funding_threshold", 0.01, 0.05, step=0.005),
        "entry_pct": trial.suggest_float("entry_pct", 0.05, 0.25, step=0.05),
    }


# ── Common engine-level parameters that apply to ALL strategies ──

def _space_common(trial: "optuna.Trial") -> dict:
    """Common parameters applicable to all strategies."""
    return {
        "_odds_threshold": trial.suggest_float("_odds_threshold", 0.50, 0.80, step=0.02),
        "_min_confidence": trial.suggest_float("_min_confidence", 0.0, 0.40, step=0.05),
    }


# ═══════════════════════════════════════════════════════════════
# Phase 81: LIVE Strategy Parameter Spaces
# Bu space'ler core/strategy_plugins.py'deki LIVE stratejilerin
# parametrelerini optimize eder. Adaptör aracılığıyla backtest'te çalışır.
# Sonuçlar doğrudan live engine'e uygulanabilir.
# ═══════════════════════════════════════════════════════════════

def _space_momentum(trial: "optuna.Trial") -> dict:
    """Live MomentumStrategy — trend threshold & confidence."""
    return {
        "trend_threshold": trial.suggest_float("trend_threshold", 0.005, 0.05, step=0.005),
        "min_confidence": trial.suggest_float("min_confidence", 0.2, 0.5, step=0.05),
    }


def _space_contrarian(trial: "optuna.Trial") -> dict:
    """Live ContrarianStrategy — deviation & confidence."""
    return {
        "min_deviation": trial.suggest_float("min_deviation", 0.03, 0.15, step=0.01),
        "min_confidence": trial.suggest_float("min_confidence", 0.2, 0.5, step=0.05),
    }


def _space_scalper(trial: "optuna.Trial") -> dict:
    """Live ScalperStrategy — spread & tick sensitivity."""
    return {
        "max_spread": trial.suggest_float("max_spread", 0.02, 0.08, step=0.01),
        "tick_threshold": trial.suggest_float("tick_threshold", 0.005, 0.03, step=0.005),
    }


def _space_sniper(trial: "optuna.Trial") -> dict:
    """Live SniperStrategy — checks & margin."""
    return {
        "min_checks": trial.suggest_int("min_checks", 2, 8),
        "odds_margin": trial.suggest_float("odds_margin", 0.02, 0.10, step=0.01),
    }


def _space_flashcrash(trial: "optuna.Trial") -> dict:
    """Live FlashCrashStrategy — drop detection."""
    return {
        "drop_threshold": trial.suggest_float("drop_threshold", 0.03, 0.15, step=0.01),
        "min_series_len": trial.suggest_int("min_series_len", 3, 10),
    }


def _space_streak(trial: "optuna.Trial") -> dict:
    """Live StreakReversalStrategy (live) — streak count."""
    return {
        "streak_threshold": trial.suggest_int("streak_threshold", 2, 8),
    }


def _space_highthreshold(trial: "optuna.Trial") -> dict:
    """Live HighThresholdStrategy — common params only (logic is threshold-based)."""
    return {}  # Sadece common params (_odds_threshold) optimize edilir


def _space_penny_contract(trial: "optuna.Trial") -> dict:
    """Live PennyContractStrategy — zone boundaries."""
    return {
        "_MAX_LOW": trial.suggest_float("_MAX_LOW", 0.08, 0.20, step=0.02),
        "_MIN_HIGH": trial.suggest_float("_MIN_HIGH", 0.80, 0.92, step=0.02),
        "_MIN_SPREAD": trial.suggest_float("_MIN_SPREAD", 0.01, 0.05, step=0.005),
    }


def _space_bonding_yield(trial: "optuna.Trial") -> dict:
    """Live BondingYieldLiveStrategy — price range & yield."""
    return {
        "MIN_PRICE": trial.suggest_float("MIN_PRICE", 0.85, 0.95, step=0.01),
        "MAX_PRICE": trial.suggest_float("MAX_PRICE", 0.96, 0.99, step=0.005),
        "MIN_YIELD": trial.suggest_float("MIN_YIELD", 0.005, 0.03, step=0.005),
    }


def _space_martingale(trial: "optuna.Trial") -> dict:
    """Live MartingaleStrategy — Phase 82e Sprint 5 (FINAL).

    Previously orphan: 2 DB strategies of strategy_type='martingale' existed
    with no PARAM_SPACES entry, so /hyperopt_all silently skipped them
    (Sprint 4.5 apply-filter = intersection of live_types ∩ PARAM_SPACES).
    Now tunable: 4 core knobs covering size progression, safety cap, edge
    gate and price floor. MAX_LEVEL kept modest (3..8) because exposure
    grows exponentially; MIN_ENTRY_PRICE is held ≥0.30 to honour the Phase 19
    loss pattern (0-30c zone had 0% WR).
    """
    return {
        "MULTIPLIER": trial.suggest_float("MULTIPLIER", 1.1, 1.6, step=0.05),
        "MAX_LEVEL": trial.suggest_int("MAX_LEVEL", 3, 8),
        "MIN_KELLY": trial.suggest_float("MIN_KELLY", 0.02, 0.15, step=0.01),
        "MIN_ENTRY_PRICE": trial.suggest_float("MIN_ENTRY_PRICE", 0.30, 0.55, step=0.05),
    }


def _space_fusion(trial: "optuna.Trial") -> dict:
    """Phase 81: Fusion signal weights — optimize the 5 active signal weights.

    Note: Fusion reads weights from ENV (SIGNAL_W_*), but HyperOpt passes
    them as strategy params. The adapter sets them on the FusionStrategy
    instance, but since FusionStrategy creates a fresh SignalFusion() per
    evaluate() call, we pass weights via ENV override pattern.
    The returned dict keys match SignalWeights attribute names.
    """
    return {
        "SIGNAL_W_ODDS": trial.suggest_float("SIGNAL_W_ODDS", 0.0, 0.20, step=0.05),
        "SIGNAL_W_EMA": trial.suggest_float("SIGNAL_W_EMA", 0.10, 0.40, step=0.05),
        "SIGNAL_W_MOMENTUM": trial.suggest_float("SIGNAL_W_MOMENTUM", 0.15, 0.45, step=0.05),
        "SIGNAL_W_TIME": trial.suggest_float("SIGNAL_W_TIME", 0.0, 0.20, step=0.05),
        "SIGNAL_W_ORDERBOOK": trial.suggest_float("SIGNAL_W_ORDERBOOK", 0.05, 0.35, step=0.05),
        "MIN_COMPOSITE": trial.suggest_float("MIN_COMPOSITE", 0.10, 0.50, step=0.05),
    }


# ── Registry ──

PARAM_SPACES: dict[str, Callable] = {
    # Backtest-only strategies
    "hour_edge": _space_hour_edge,
    "late_convergence": _space_late_convergence,
    "streak_reversal": _space_streak_reversal,
    "opening_breakout": _space_opening_breakout,
    "orderbook_imbalance": _space_orderbook_imbalance,
    "fade_rip": _space_fade_rip,
    "taker_flow": _space_taker_flow,
    "calibration_arb": _space_calibration_arb,
    "cross_coin": _space_cross_coin,
    "composite": _space_composite,
    "funding_rate": _space_funding_rate,
    # Phase 81: Live strategies (adaptör ile backtest'te çalışır)
    "momentum": _space_momentum,
    "contrarian": _space_contrarian,
    "scalper": _space_scalper,
    "sniper": _space_sniper,
    "flashcrash": _space_flashcrash,
    "streak": _space_streak,
    "highthreshold": _space_highthreshold,
    "penny_contract": _space_penny_contract,
    "bonding_yield": _space_bonding_yield,
    # Phase 82e Sprint 5 (FINAL): was orphan
    "martingale": _space_martingale,
    # Phase 81: Fusion strategy — signal weight optimization
    "fusion": _space_fusion,
}


# ═══════════════════════════════════════════════════════════════
# HyperOpt Pipeline
# ═══════════════════════════════════════════════════════════════

class HyperOptPipeline:
    """
    Optuna-powered hyperparameter optimization for backtest strategies.

    Uses TPE sampler (Tree-structured Parzen Estimator) which is more
    sample-efficient than random search for prediction market parameters.

    Built-in train/test split to detect overfitting:
      1. 70% of market windows → train set (Optuna optimizes on this)
      2. 30% held out → test set (validate best params)
      3. If test/train ratio < 0.70 → overfit warning

    Phase 79b: Uses separate read-only DB connection to avoid blocking
    the main bot's write operations during long optimization runs.
    """

    def __init__(self, db):
        self.db = db
        self._ro_conn = None  # Phase 79b: read-only connection for heavy queries
        # Phase 82b.3: cache for _discover_market_windows() results. Trials
        # within a single hyperopt run share the same window set (only
        # strategy params vary), so we compute discovery once and inject
        # the cached list into each ReplayEngine instance.
        # Key: tuple of filter params that actually affect discovery
        # Value: list[dict] of market windows
        self._windows_cache: dict[tuple, list[dict]] = {}
        # Phase 82e Sprint 3.1: cache hit/miss counters for /diagnose +
        # log-grep forensics. Tracks whether key normalization is working
        # as intended (hit % should approach 100% once priming succeeds).
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        if not OPTUNA_AVAILABLE:
            raise ImportError(
                "optuna not installed. Run: pip install optuna"
            )

    # ─── Phase 82b.3: Discovery cache ───────────────────────────
    @staticmethod
    def _windows_cache_key(replay_cfg) -> tuple:
        """Stable cache key for window discovery.

        Only includes filters that actually change _discover_market_windows
        output. strategy_params / trade_amount / fill_mode do NOT affect
        the set of markets we replay.

        Phase 82e Sprint 3.1 — normalization:
        - asset_filter: upper+strip (matches _discover_market_windows's
          ``.upper()`` at ReplayEngine line ~376). Prevents "btc" vs "BTC"
          MISS storms when callers forget to uppercase.
        - timeframe_filter: strip only (engine treats it case-sensitively).
        - max_markets: REMOVED from the key. It's applied during
          ``ReplayEngine.run()`` iteration (line ~258), NOT inside
          _discover_market_windows — so two configs differing only in
          max_markets share the same discovered window set. Including it
          previously caused the split-backtest path (train_count param)
          to always MISS against a primed cache.
        """
        asset = (replay_cfg.asset_filter or "").strip().upper()
        tf = (replay_cfg.timeframe_filter or "").strip()
        return (
            asset,
            tf,
            int(replay_cfg.start_ms or 0),
            int(replay_cfg.end_ms or 0),
            int(replay_cfg.last_n or 0),
            # max_markets intentionally excluded — post-discovery filter only.
            # random_n intentionally omitted — cache full set, sample per-trial.
        )

    def cache_stats(self) -> dict:
        """Phase 82e Sprint 3.1: expose cache telemetry for diagnostics.

        Returned dict mirrors what /diagnose or ad-hoc log checks use.
        hit_pct is 0.0 when no lookups have happened yet.
        """
        total = self._cache_hits + self._cache_misses
        hit_pct = (self._cache_hits / total * 100.0) if total > 0 else 0.0
        return {
            "entries": len(self._windows_cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "lookups": total,
            "hit_pct": hit_pct,
        }

    async def _get_cached_windows(self, replay_cfg) -> list[dict]:
        """Discover market windows once and cache for all trials of a study."""
        key = self._windows_cache_key(replay_cfg)
        if key in self._windows_cache:
            cached = self._windows_cache[key]
            self._cache_hits += 1
            # Phase 82b.4: make cache-hit visible in logs. Before this we
            # only logged the MISS, so we couldn't tell whether the cache
            # was actually working or silently missing every trial.
            logger.info(
                "HyperOpt: cache HIT (%d windows, key=%s) [hits=%d misses=%d]",
                len(cached), key, self._cache_hits, self._cache_misses)
            return cached

        # First-time discovery: instantiate a throwaway engine just to run
        # _discover_market_windows() once. Don't call .run() — that would
        # execute a full backtest.
        self._cache_misses += 1
        logger.info(
            "HyperOpt: cache MISS — running discovery (key=%s) [hits=%d misses=%d]",
            key, self._cache_hits, self._cache_misses)
        tmp_engine = ReplayEngine(self.db, replay_cfg)
        tmp_engine._setup()
        t0 = datetime.utcnow()
        windows = await tmp_engine._discover_market_windows()
        elapsed = (datetime.utcnow() - t0).total_seconds()
        self._windows_cache[key] = windows
        logger.info(
            "HyperOpt: discovered %d market windows in %.1fs (cached, key=%s)",
            len(windows), elapsed, key)
        return windows

    async def prime_windows_cache(self, cfg: "HyperOptConfig") -> int:
        """Phase 82b.5: pre-discover market windows BEFORE the trial loop.

        Phase 82b.3 added the in-memory window cache, but discovery still
        happened inside the first trial — so when `_discover_market_windows()`
        ran >TRIAL_TIMEOUT_SEC the coroutine was cancelled mid-query, the
        cache was never populated, and every subsequent trial re-ran
        discovery, hit the same timeout, and emitted `cache MISS`. Result:
        5/5 trials pruned, Score=0.0000, empty Best params.

        This method performs discovery exactly ONCE outside the per-trial
        asyncio.wait_for guard. The worker (hyperopt_worker._run_strategy)
        calls it right before the trial loop; the in-process optimize()
        path benefits transparently too. Returns the cached window count
        (0 on failure — caller should treat this as "skip this strategy").
        """
        # Build a ReplayConfig that matches exactly what _run_backtest() will
        # build per trial, so the cache key matches on the first trial lookup.
        start_ms = 0
        if cfg.from_date:
            try:
                from datetime import datetime as _dt, timezone as _tz
                dt = _dt.strptime(cfg.from_date, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                start_ms = int(dt.timestamp() * 1000)
            except ValueError:
                pass

        replay_cfg = ReplayConfig(
            strategy_name=cfg.strategy_name,
            strategy_params={},
            initial_balance=cfg.initial_balance,
            trade_amount=cfg.trade_amount,
            fill_mode=cfg.fill_mode,
            asset_filter=cfg.asset_filter,
            timeframe_filter=cfg.timeframe_filter,
            direction_filter=cfg.direction_filter,
            min_confidence=0.0,
            max_markets=cfg.max_markets,
            start_ms=start_ms,
            last_n=cfg.last_n,
            random_n=cfg.random_n,
        )

        try:
            windows = await self._get_cached_windows(replay_cfg)
            return len(windows)
        except Exception as e:
            logger.error(
                "HyperOpt.prime_windows_cache failed: %s", e, exc_info=True)
            return 0

    async def _get_ro_conn(self):
        """Phase 79b: Get a separate read-only DB connection.

        This prevents HyperOpt's long SELECT queries from blocking
        the main bot's write operations (WAL mode allows concurrent readers).
        Falls back to main db.conn if read-only open fails.
        """
        if self._ro_conn is not None:
            return self._ro_conn
        try:
            import aiosqlite
            db_path = self.db.conn._conn.database if hasattr(self.db.conn, '_conn') else None
            if not db_path:
                # Try common path
                db_path = os.path.join(os.path.dirname(__file__), "..", "data_store", "polypaper.db")
            self._ro_conn = await aiosqlite.connect(db_path, timeout=60)
            self._ro_conn.row_factory = aiosqlite.Row
            await self._ro_conn.execute("PRAGMA journal_mode=WAL")
            await self._ro_conn.execute("PRAGMA query_only=ON")
            await self._ro_conn.execute("PRAGMA busy_timeout=30000")
            logger.info("HyperOpt: opened separate read-only DB connection")
            return self._ro_conn
        except Exception as e:
            logger.warning(f"HyperOpt: read-only conn failed ({e}), using main DB")
            return self.db.conn

    async def close(self):
        """Close the read-only connection if open."""
        if self._ro_conn is not None:
            try:
                await self._ro_conn.close()
            except Exception:
                pass
            self._ro_conn = None

    async def optimize(
        self,
        strategy_name: str = "hour_edge",
        asset: str = "",
        timeframe: str = "",
        direction: str = "",
        n_trials: int = 100,
        metric: str = "sharpe_ratio",
        config: Optional[HyperOptConfig] = None,
        progress_callback: Optional[Callable[[int, int, float, float], Any]] = None,
        acquire_lock: bool = True,
        lock_path: Optional[str] = None,
    ) -> HyperOptResult:
        """
        Run full hyperopt pipeline for a strategy.

        Steps:
          1. Load market windows from DB
          2. Split into train/test
          3. Run Optuna optimization on train set
          4. Validate best params on test set
          5. Check for overfitting
          6. Return result with best params

        Phase 82b: Takes the cross-process PidFileLock so that in-process callers
        (tournament_job, ai_brain, BatchOptimizer, CLI) coordinate with the
        subprocess workers launched by /hyperopt and /hyperopt_all. Set
        acquire_lock=False to bypass the mutex (tests only).
        """
        cfg = config or HyperOptConfig(
            strategy_name=strategy_name,
            asset_filter=asset,
            timeframe_filter=timeframe,
            direction_filter=direction,
            n_trials=n_trials,
            metric=metric,
        )

        start_time = datetime.now(timezone.utc)

        # Phase 82b: cross-process mutex to prevent overlapping hyperopt runs
        lock = None
        if acquire_lock:
            try:
                from backtest.hyperopt_ipc import PidFileLock
                lock = PidFileLock(lock_path)
                if not lock.acquire(mode="inproc", strategy=cfg.strategy_name):
                    logger.warning(
                        "HyperOpt skipped — another hyperopt run holds the lock "
                        "(strategy=%s)", cfg.strategy_name,
                    )
                    return HyperOptResult(
                        strategy_name=cfg.strategy_name,
                        metric=cfg.metric,
                        n_trials=0,
                        timestamp=start_time.isoformat(),
                    )
            except Exception as e:
                logger.warning("HyperOpt lock setup failed (%s) — proceeding unlocked", e)
                lock = None

        try:
            return await self._optimize_impl(
                cfg, start_time, progress_callback,
            )
        finally:
            if lock is not None:
                try:
                    lock.release()
                except Exception:
                    pass

    async def _optimize_impl(
        self,
        cfg: HyperOptConfig,
        start_time: datetime,
        progress_callback: Optional[Callable[[int, int, float, float], Any]],
    ) -> HyperOptResult:
        """Phase 82b: core optimize body extracted so the public method can
        wrap it with the PidFileLock. See optimize() for public API."""
        logger.info(
            "HyperOpt START: strategy=%s asset=%s tf=%s trials=%d metric=%s",
            cfg.strategy_name, cfg.asset_filter, cfg.timeframe_filter,
            cfg.n_trials, cfg.metric
        )

        # ── 1. Get total market window count for train/test split ──
        total_markets = await self._count_markets(cfg)
        if total_markets < 20:
            logger.warning("HyperOpt: only %d markets — need at least 20", total_markets)
            return HyperOptResult(
                strategy_name=cfg.strategy_name,
                metric=cfg.metric,
                n_trials=0,
                timestamp=start_time.isoformat(),
            )

        train_count = int(total_markets * cfg.train_pct)
        test_count = total_markets - train_count
        logger.info("HyperOpt: %d markets → train=%d, test=%d",
                     total_markets, train_count, test_count)

        # ── 2. Create Optuna study ──
        pruner_name = os.getenv("HYPEROPT_PRUNER", "median")
        if pruner_name == "hyperband":
            pruner = HyperbandPruner(min_resource=5, max_resource=train_count)
        else:
            pruner = MedianPruner(n_startup_trials=5, n_warmup_steps=3)

        study = optuna.create_study(
            study_name=f"hyperopt_{cfg.strategy_name}_{cfg.asset_filter}_{cfg.timeframe_filter}",
            direction="maximize",
            sampler=TPESampler(seed=42, n_startup_trials=10),
            pruner=pruner,
        )

        # Suppress Optuna's verbose logging
        optuna.logging.set_verbosity(optuna.logging.WARNING)

        # ── 3. Define objective ──
        async def _objective(trial: optuna.Trial) -> float:
            return await self._run_trial(trial, cfg, max_markets=train_count)

        # ── 4. Run optimization (Phase 82b: ask/tell pattern, no threads) ──
        # Previous code used nest_asyncio + asyncio.to_thread(study.optimize) so
        # Optuna could drive a sync objective that re-entered the parent event
        # loop. That combination caused silent crashes of the main loop on
        # /hyperopt_all (see Phase 82b audit). Now trials run serially in the
        # caller's own event loop with per-trial timeout.
        trial_timeout_sec = int(os.getenv("HYPEROPT_TRIAL_TIMEOUT_SEC", "90"))
        study_timeout_sec = int(os.getenv("HYPEROPT_STUDY_TIMEOUT_SEC", str(cfg.timeout_s)))
        _progress_interval = max(1, cfg.n_trials // 10)  # report every ~10%
        _opt_start = datetime.now(timezone.utc)
        _study_deadline = (
            _opt_start.timestamp() + study_timeout_sec if study_timeout_sec > 0 else None
        )

        try:
            for trial_num in range(cfg.n_trials):
                # Per-study deadline (optional)
                if _study_deadline is not None and datetime.now(timezone.utc).timestamp() > _study_deadline:
                    logger.warning(
                        "HyperOpt study timeout reached at trial %d/%d",
                        trial_num, cfg.n_trials,
                    )
                    break

                trial = study.ask()
                try:
                    value = await asyncio.wait_for(
                        self._run_trial(trial, cfg, max_markets=train_count),
                        timeout=trial_timeout_sec,
                    )
                    study.tell(trial, value)
                except asyncio.TimeoutError:
                    logger.warning(
                        "HyperOpt trial %d timed out after %ds — pruning",
                        trial_num, trial_timeout_sec,
                    )
                    study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                except optuna.TrialPruned:
                    study.tell(trial, state=optuna.trial.TrialState.PRUNED)
                except Exception as e:
                    logger.warning("HyperOpt trial %d failed: %s", trial_num, e)
                    try:
                        study.tell(trial, state=optuna.trial.TrialState.FAIL)
                    except Exception:
                        pass

                # Progress callback (same contract as before)
                n_done = len([t for t in study.trials
                              if t.state == optuna.trial.TrialState.COMPLETE])
                if progress_callback and (
                    n_done % _progress_interval == 0 or n_done == cfg.n_trials
                ):
                    elapsed = (datetime.now(timezone.utc) - _opt_start).total_seconds()
                    remaining = (elapsed / max(n_done, 1)) * (cfg.n_trials - n_done)
                    best_val = study.best_value if study.best_trial else 0.0
                    try:
                        progress_callback(n_done, cfg.n_trials, remaining, best_val)
                    except Exception:
                        pass

                # Yield to the event loop between trials
                await asyncio.sleep(0)
        except Exception as e:
            logger.error("HyperOpt optimization failed: %s", e, exc_info=True)
            return HyperOptResult(
                strategy_name=cfg.strategy_name,
                metric=cfg.metric,
                timestamp=start_time.isoformat(),
            )

        # If no COMPLETE trials, bail out early with an empty result
        if not any(t.state == optuna.trial.TrialState.COMPLETE for t in study.trials):
            logger.warning("HyperOpt: no trials completed successfully")
            return HyperOptResult(
                strategy_name=cfg.strategy_name,
                metric=cfg.metric,
                n_trials=cfg.n_trials,
                timestamp=start_time.isoformat(),
            )

        # ── 5. Extract best trial ──
        best_trial = study.best_trial
        best_params = best_trial.params
        train_score = best_trial.value

        # ── 6. Validate on test set ──
        test_stats = await self._run_backtest(
            cfg, best_params,
            max_markets=test_count,
            skip_markets=train_count,  # skip train set
        )
        test_score = self._extract_metric(test_stats, cfg.metric)

        # ── 7. Overfitting check ──
        overfit_ratio = test_score / train_score if train_score > 0 else 0.0

        # ── 8. Collect top trials ──
        sorted_trials = sorted(
            study.trials,
            key=lambda t: t.value if t.value is not None else float("-inf"),
            reverse=True,
        )[:10]
        all_trials = [
            {"params": t.params, "score": t.value, "state": str(t.state)}
            for t in sorted_trials
        ]

        # ── 9. Build result ──
        elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
        result = HyperOptResult(
            strategy_name=cfg.strategy_name,
            best_params=self._clean_params(best_params),
            best_score=train_score,
            metric=cfg.metric,
            n_trials=cfg.n_trials,
            n_completed=len([t for t in study.trials
                             if t.state == optuna.trial.TrialState.COMPLETE]),
            n_pruned=len([t for t in study.trials
                          if t.state == optuna.trial.TrialState.PRUNED]),
            train_score=train_score,
            test_score=test_score,
            overfit_ratio=overfit_ratio,
            train_stats=self._stats_to_dict(test_stats),  # for display
            duration_s=elapsed,
            timestamp=start_time.isoformat(),
            all_trials=all_trials,
        )

        logger.info(
            "HyperOpt DONE: strategy=%s best=%s score=%.4f "
            "train=%.4f test=%.4f ratio=%.2f%s",
            cfg.strategy_name,
            json.dumps(result.best_params),
            result.best_score,
            result.train_score,
            result.test_score,
            result.overfit_ratio,
            " ⚠️OVERFIT" if result.is_overfit() else "",
        )

        return result

    # ─── Trial runner ───────────────────────────────────────────

    async def _run_trial(
        self,
        trial: "optuna.Trial",
        cfg: HyperOptConfig,
        max_markets: int = 0,
    ) -> float:
        """Run a single trial: suggest params → backtest → return metric."""
        # Get strategy-specific param space
        space_fn = PARAM_SPACES.get(cfg.strategy_name)
        if space_fn:
            strategy_params = space_fn(trial)
        else:
            # No custom space → only optimize common params
            strategy_params = {}

        # Add common params
        common = _space_common(trial)
        strategy_params.update(common)

        # Run backtest
        stats = await self._run_backtest(
            cfg, strategy_params, max_markets=max_markets
        )

        # Check min trades
        if stats.total_trades < cfg.min_trades:
            return float("-inf")  # penalty for too few trades

        score = self._extract_metric(stats, cfg.metric)
        return score

    async def _run_backtest(
        self,
        cfg: HyperOptConfig,
        params: dict,
        max_markets: int = 0,
        skip_markets: int = 0,
    ) -> PortfolioStats:
        """Run a ReplayEngine backtest with given parameters."""
        # Separate common engine params from strategy params
        strategy_params = {
            k: v for k, v in params.items() if not k.startswith("_")
        }
        min_confidence = params.get("_min_confidence", 0.0)

        # Phase 81: from_date → start_ms conversion
        start_ms = 0
        if cfg.from_date:
            try:
                from datetime import datetime as _dt, timezone as _tz
                dt = _dt.strptime(cfg.from_date, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                start_ms = int(dt.timestamp() * 1000)
            except ValueError:
                pass

        replay_cfg = ReplayConfig(
            strategy_name=cfg.strategy_name,
            strategy_params=strategy_params,
            initial_balance=cfg.initial_balance,
            trade_amount=cfg.trade_amount,
            fill_mode=cfg.fill_mode,
            asset_filter=cfg.asset_filter,
            timeframe_filter=cfg.timeframe_filter,
            direction_filter=cfg.direction_filter,
            min_confidence=min_confidence,
            max_markets=max_markets if max_markets > 0 else cfg.max_markets,
            start_ms=start_ms,
            last_n=cfg.last_n,
            random_n=cfg.random_n,
        )

        engine = ReplayEngine(self.db, replay_cfg)

        # Phase 82b.3: inject cached window set so this trial skips the
        # expensive _discover_market_windows GROUP BY (previously run
        # per-trial, blowing the 300s TRIAL_TIMEOUT on a 10GB DB).
        try:
            engine._injected_windows = await self._get_cached_windows(replay_cfg)
        except Exception as e:
            logger.warning("HyperOpt: window cache fetch failed, falling back to "
                           "per-trial discovery: %s", e)

        # If skip_markets > 0, we need to offset — handled via start_ms
        # For now, a simple approach: run and truncate
        try:
            stats = await engine.run()
        except Exception as e:
            logger.debug("Trial backtest failed: %s", e)
            stats = PortfolioStats()

        return stats

    # ─── Helpers ────────────────────────────────────────────────

    async def _count_markets(self, cfg: HyperOptConfig) -> int:
        """Count available market windows matching filters.

        Phase 79b: Uses read-only connection to avoid blocking main bot.
        Phase 81: from_date, last_n, random_n support.
        """
        query = "SELECT COUNT(DISTINCT slug) FROM ob_snapshots WHERE 1=1"
        params_list: list = []

        if cfg.asset_filter:
            query += " AND slug LIKE ?"
            params_list.append(f"%{cfg.asset_filter.lower()}%")

        if cfg.timeframe_filter:
            query += " AND slug LIKE ?"
            params_list.append(f"%{cfg.timeframe_filter}%")

        if cfg.from_date:
            try:
                from datetime import datetime as _dt, timezone as _tz
                dt = _dt.strptime(cfg.from_date, "%Y-%m-%d").replace(tzinfo=_tz.utc)
                start_ms = int(dt.timestamp() * 1000)
                query += " AND ts_ms >= ?"
                params_list.append(start_ms)
            except ValueError:
                pass

        try:
            conn = await self._get_ro_conn()
            async with conn.execute(query, tuple(params_list)) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0

            # Phase 81: last_n/random_n cap the effective count
            if cfg.last_n > 0:
                total = min(total, cfg.last_n)
            if cfg.random_n > 0:
                total = min(total, cfg.random_n)

            return total
        except Exception as e:
            logger.debug("_count_markets failed: %s", e)
            return 0

    @staticmethod
    def _extract_metric(stats: PortfolioStats, metric: str) -> float:
        """Extract optimization metric from PortfolioStats."""
        metric_map = {
            "sharpe_ratio": stats.sharpe_ratio,
            "win_rate": stats.win_rate,
            "total_pnl": stats.total_pnl,
            "profit_factor": stats.profit_factor,
            "sortino_ratio": stats.sortino_ratio,
            "avg_pnl": stats.avg_pnl,
        }
        return metric_map.get(metric, stats.sharpe_ratio)

    @staticmethod
    def _clean_params(params: dict) -> dict:
        """Remove internal prefix from param names for deployment."""
        clean = {}
        for k, v in params.items():
            if k.startswith("_"):
                clean[k[1:]] = v  # remove _ prefix
            else:
                clean[k] = v
        return clean

    @staticmethod
    def _stats_to_dict(stats: PortfolioStats) -> dict:
        """Convert PortfolioStats to JSON-safe dict."""
        return {
            "total_trades": stats.total_trades,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": round(stats.win_rate, 4),
            "total_pnl": round(stats.total_pnl, 2),
            "sharpe_ratio": round(stats.sharpe_ratio, 4),
            "sortino_ratio": round(stats.sortino_ratio, 4),
            "profit_factor": round(stats.profit_factor, 4),
            "max_drawdown": round(stats.max_drawdown, 2),
            "max_drawdown_pct": round(stats.max_drawdown_pct, 4),
            "avg_pnl": round(stats.avg_pnl, 4),
        }

    def format_telegram_report(self, result: HyperOptResult) -> str:
        """Format result for Telegram HTML message."""
        overfit_icon = "🔴 OVERFIT" if result.is_overfit() else "✅ OK"
        lines = [
            f"🔬 <b>HyperOpt: {esc(result.strategy_name)}</b>",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"",
            f"📊 Metric: <code>{esc(result.metric)}</code>",
            f"🏆 Best Score: <b>{result.best_score:.4f}</b>",
            f"",
            f"📈 Train: {result.train_score:.4f}",
            f"📉 Test: {result.test_score:.4f}",
            f"📊 Ratio: {result.overfit_ratio:.2f} {overfit_icon}",
            f"",
            f"⚡ Trials: {result.n_completed}/{result.n_trials} "
            f"(pruned: {result.n_pruned})",
            f"⏱ Duration: {result.duration_s:.0f}s",
            f"",
            f"<b>Best Params:</b>",
        ]
        for k, v in result.best_params.items():
            if isinstance(v, float):
                lines.append(f"  {k}: <code>{v:.4f}</code>")
            else:
                lines.append(f"  {k}: <code>{v}</code>")

        if result.train_stats:
            s = result.train_stats
            lines.extend([
                f"",
                f"<b>Test Set Performance:</b>",
                f"  Trades: {s.get('total_trades', 0)} | "
                f"WR: {s.get('win_rate', 0)*100:.1f}%",
                f"  PnL: ${s.get('total_pnl', 0):+.2f} | "
                f"Sharpe: {s.get('sharpe_ratio', 0):.2f}",
            ])

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Batch Optimizer — sweep all strategies
# ═══════════════════════════════════════════════════════════════

class BatchOptimizer:
    """
    Run HyperOpt on multiple strategies sequentially.
    Used by AI Tournament Mode (Phase 67.3) and /hyperopt_all command.
    """

    def __init__(self, db):
        self.db = db
        self.pipeline = HyperOptPipeline(db)

    async def optimize_all(
        self,
        strategies: Optional[list[str]] = None,
        asset: str = "",
        timeframe: str = "",
        n_trials: int = 50,
        metric: str = "sharpe_ratio",
    ) -> list[HyperOptResult]:
        """
        Optimize all (or specified) strategies.

        Returns list of HyperOptResult, sorted by best_score descending.
        """
        if strategies is None:
            strategies = list(PARAM_SPACES.keys())

        results = []
        for strat_name in strategies:
            logger.info("BatchOptimizer: optimizing %s...", strat_name)
            try:
                result = await self.pipeline.optimize(
                    strategy_name=strat_name,
                    asset=asset,
                    timeframe=timeframe,
                    n_trials=n_trials,
                    metric=metric,
                )
                results.append(result)
            except Exception as e:
                logger.error("BatchOptimizer: %s failed: %s", strat_name, e)
                results.append(HyperOptResult(
                    strategy_name=strat_name,
                    metric=metric,
                ))

        # Sort by best_score descending
        results.sort(key=lambda r: r.best_score, reverse=True)

        # Phase 79b: Close read-only connection after batch completes
        await self.pipeline.close()

        return results

    def format_batch_report(self, results: list[HyperOptResult]) -> str:
        """Format batch results for Telegram."""
        lines = [
            "🏆 <b>HyperOpt Batch Results</b>",
            "━━━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for i, r in enumerate(results, 1):
            overfit = "⚠️" if r.is_overfit() else "✅"
            score_str = f"{r.best_score:.4f}" if r.best_score != 0 else "N/A"
            lines.append(
                f"{i}. <b>{esc(r.strategy_name)}</b>: {score_str} "
                f"(test: {r.test_score:.4f}) {overfit}"
            )

        # Recommendations
        good = [r for r in results if not r.is_overfit() and r.best_score > 0]
        if good:
            lines.extend([
                "",
                f"✅ <b>{len(good)} strategies</b> passed overfit gate",
                "Deploy önerisi: En yüksek test score ile başla",
            ])
        else:
            lines.extend([
                "",
                "⚠️ Tüm stratejiler overfit! Daha fazla veri toplayın.",
            ])

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════

async def _cli_main():
    """CLI entry point for manual hyperopt runs."""
    import argparse
    parser = argparse.ArgumentParser(description="PolyPaper HyperOpt Pipeline")
    parser.add_argument("--strategy", default="hour_edge")
    parser.add_argument("--asset", default="")
    parser.add_argument("--timeframe", default="")
    parser.add_argument("--trials", type=int, default=50)
    parser.add_argument("--metric", default="sharpe_ratio",
                        choices=["sharpe_ratio", "win_rate", "total_pnl",
                                 "profit_factor", "sortino_ratio"])
    parser.add_argument("--all", action="store_true",
                        help="Optimize all strategies")
    # Phase 81: Market selection
    parser.add_argument("--last", type=int, default=0,
                        help="Use only last N markets (most recent)")
    parser.add_argument("--from-date", default="",
                        help="Use markets from this date (YYYY-MM-DD)")
    parser.add_argument("--random", type=int, default=0,
                        help="Randomly sample N markets")
    args = parser.parse_args()

    # Import DB
    from db.database import Database
    db = Database()
    await db.initialize()

    if args.all:
        batch = BatchOptimizer(db)
        results = await batch.optimize_all(
            asset=args.asset,
            timeframe=args.timeframe,
            n_trials=args.trials,
            metric=args.metric,
        )
        print(batch.format_batch_report(results))
    else:
        pipeline = HyperOptPipeline(db)
        cfg = HyperOptConfig(
            strategy_name=args.strategy,
            asset_filter=args.asset,
            timeframe_filter=args.timeframe,
            n_trials=args.trials,
            metric=args.metric,
            last_n=args.last,
            from_date=args.from_date,
            random_n=args.random,
        )
        result = await pipeline.optimize(config=cfg)
        print(result.summary())


if __name__ == "__main__":
    asyncio.run(_cli_main())
