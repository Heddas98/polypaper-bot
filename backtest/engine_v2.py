"""
PolyPaper Bot - Backtest Engine v2 (Event-Driven)
Market-window episode replay with pluggable strategies.

Architecture:
  For each market:
    1. on_market_open()  → strategy initializes
    2. for each snapshot: on_snapshot() → strategy may emit Signal
    3. First Signal → fill simulation → trade opened
    4. Market resolves → trade closed → PnL calculated
    5. on_market_close() → strategy learns

Data flow:
  PolyBackTest snapshots → Engine v2 → Strategy → Signal → Fill → Portfolio

Does NOT touch existing engine.py — completely independent.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from backtest.simulation.fee_model_v3 import FeeCalculatorV3 as FeeCalculator  # T4.1 unified
from backtest.simulation.fill_model import FillMode, FillSimulator
from backtest.simulation.portfolio import PortfolioStats, VirtualPortfolio
from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Resolution,
    Signal,
    StrategyRegistryV2,
)

logger = logging.getLogger("polypaper.backtest.engine_v2")

# Duration in seconds for each market type
MARKET_DURATIONS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    strategy_name: str = "hour_edge"
    strategy_params: dict = field(default_factory=dict)
    # Portfolio
    initial_balance: float = 10000.0
    trade_amount: float = 1.0
    # Fill simulation
    fill_mode: str = "midpoint"  # simple, midpoint, orderbook, market_impact
    min_liquidity: float = 0.0  # minimum market volume
    # Filters
    coin_filter: str = ""  # "" = all, "btc", "eth", etc.
    market_type_filter: str = ""  # "" = all, "5m", "15m", etc.
    direction_filter: str = ""  # "" = all, "up", "down"
    hour_filter: list = field(default_factory=list)  # [] = all, [6, 22] = only these
    min_confidence: float = 0.0  # minimum signal confidence
    # Limits
    max_markets: int = 0  # 0 = no limit


class BacktestEngineV2:
    """
    Event-driven backtest engine.
    Replays market windows with orderbook snapshots through strategies.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        # Phase 41c: soft-deprecation. Engine v2 still works but ReplayEngine
        # (Phase 37) is the canonical path for new backtests — it uses real L2
        # orderbook history instead of synthetic snapshots, giving 9/10 vs 4/10
        # realism. Keep v2 for the 11 legacy strategies until they're ported.
        import warnings

        warnings.warn(
            "BacktestEngineV2 is soft-deprecated (Phase 41c). "
            "Prefer backtest.replay_engine.ReplayEngine for new backtests "
            "(real L2 history, 9/10 realism). v2 remains available for the "
            "11 legacy synthetic strategies.",
            DeprecationWarning,
            stacklevel=2,
        )
        logger.warning("⚠️ BacktestEngineV2 instantiated (deprecated — use ReplayEngine)")
        self.config = config or BacktestConfig()
        self.portfolio: Optional[VirtualPortfolio] = None
        self.fill_sim: Optional[FillSimulator] = None
        self.fee_calc: Optional[FeeCalculator] = None
        self._strategy: Optional[BaseBacktestStrategy] = None
        self._markets_processed = 0
        self._markets_skipped = 0
        self._signals_generated = 0

    def _setup(self):
        """Initialize components from config."""
        # Fee calculator — T4.1: for_market_type() removed with legacy fee_model.py.
        # Prior behavior was `FeeMode.STANDARD` in all branches (the `15m` branch
        # was hard-disabled with `and False`), so defaulting to FeeCalculatorV3's
        # V3 mode (taker-only, crypto) preserves identical backtest results.
        self.fee_calc = FeeCalculator()

        # Fill simulator
        fill_mode = FillMode(self.config.fill_mode)
        self.fill_sim = FillSimulator(
            mode=fill_mode,
            min_liquidity=self.config.min_liquidity,
        )

        # Portfolio
        self.portfolio = VirtualPortfolio(
            initial_balance=self.config.initial_balance,
            trade_amount=self.config.trade_amount,
            fee_calculator=self.fee_calc,
        )

        # Strategy
        self._strategy = StrategyRegistryV2.create(
            self.config.strategy_name, **self.config.strategy_params
        )
        if not self._strategy:
            raise ValueError(
                f"Strategy '{self.config.strategy_name}' not found. "
                f"Available: {StrategyRegistryV2.list_all()}"
            )

        logger.info(
            "Engine v2 setup: strategy=%s, balance=$%.0f, trade=$%.2f, fill=%s",
            self.config.strategy_name,
            self.config.initial_balance,
            self.config.trade_amount,
            self.config.fill_mode,
        )

    def run(
        self, markets: list[dict], snapshots_by_market: dict[str, list[dict]]
    ) -> PortfolioStats:
        """
        Run backtest over a set of markets.

        Args:
            markets: list of market metadata dicts
            snapshots_by_market: {market_id: [snapshot_dicts]}
        Returns:
            PortfolioStats with full results
        """
        self._setup()
        self._markets_processed = 0
        self._markets_skipped = 0
        self._signals_generated = 0

        for market_dict in markets:
            mid = market_dict.get("market_id") or market_dict.get("id", "")
            snapshots = snapshots_by_market.get(mid, [])

            if not snapshots:
                # Generate synthetic snapshot from market metadata
                # so strategies can still evaluate based on market data
                snapshots = self._synthetic_snapshots(market_dict)
                if not snapshots:
                    self._markets_skipped += 1
                    continue

            market = self._parse_market(market_dict)

            # Apply filters
            if not self._passes_filters(market):
                self._markets_skipped += 1
                continue

            # Max markets limit
            if self.config.max_markets > 0 and self._markets_processed >= self.config.max_markets:
                break

            self._run_market(market, snapshots)
            self._markets_processed += 1

        stats = self.portfolio.get_stats()
        logger.info(
            "Backtest complete: %d markets, %d trades, WR=%.1f%%, PnL=$%.2f",
            self._markets_processed,
            stats.total_trades,
            stats.win_rate,
            stats.total_pnl,
        )
        return stats

    # Phase 47f.10 P3#14 — train/test split against overfitting
    def run_split(
        self,
        markets: list[dict],
        snapshots_by_market: dict[str, list[dict]],
        train_ratio: float = 0.70,
    ) -> dict:
        """Split markets chronologically into train/test and run each.

        Returns {"train": PortfolioStats, "test": PortfolioStats,
                 "divergence": {...}, "overfit": bool}
        """
        if not 0.3 <= train_ratio <= 0.9:
            train_ratio = 0.70

        def _mkey(m: dict) -> int:
            return int(m.get("end_time_ms") or m.get("end_date_ts") or m.get("created_ts") or 0)

        sorted_mkts = sorted(markets, key=_mkey)
        if len(sorted_mkts) < 10:
            # Too small to split — run everything as train
            stats = self.run(sorted_mkts, snapshots_by_market)
            return {
                "train": stats,
                "test": None,
                "divergence": None,
                "overfit": False,
                "note": "insufficient markets for split",
            }

        cut = int(len(sorted_mkts) * train_ratio)
        train_mkts = sorted_mkts[:cut]
        test_mkts = sorted_mkts[cut:]

        # Reset portfolio between runs by calling _setup inside run()
        train_stats = self.run(train_mkts, snapshots_by_market)
        test_stats = self.run(test_mkts, snapshots_by_market)

        divergence = {
            "train_wr": train_stats.win_rate,
            "test_wr": test_stats.win_rate,
            "wr_delta": train_stats.win_rate - test_stats.win_rate,
            "train_pnl": train_stats.total_pnl,
            "test_pnl": test_stats.total_pnl,
            "sign_flip": (train_stats.total_pnl > 0) != (test_stats.total_pnl > 0),
        }
        # Overfitting heuristic:
        #   * WR drops more than 10 points from train to test, OR
        #   * PnL sign flips (profitable in train, loss in test)
        overfit = divergence["wr_delta"] > 10.0 or divergence["sign_flip"]
        logger.info(
            "Split backtest: train WR=%.1f%% PnL=$%.2f | test WR=%.1f%% PnL=$%.2f | overfit=%s",
            train_stats.win_rate,
            train_stats.total_pnl,
            test_stats.win_rate,
            test_stats.total_pnl,
            overfit,
        )
        return {
            "train": train_stats,
            "test": test_stats,
            "divergence": divergence,
            "overfit": overfit,
        }

    # Phase 65: Rolling walk-forward validation (5-fold)
    def run_walk_forward(
        self,
        markets: list[dict],
        snapshots_by_market: dict[str, list[dict]],
        n_folds: int = 5,
        train_ratio: float = 0.70,
    ) -> dict:
        """Rolling walk-forward validation.

        Splits markets chronologically into N folds, then for each fold i:
          - Train on folds 0..i
          - Test on fold i+1

        Returns:
            {
                "folds": [{"train": Stats, "test": Stats, "divergence": {...}}],
                "summary": {
                    "avg_train_wr", "avg_test_wr", "wr_delta",
                    "avg_train_pnl", "avg_test_pnl",
                    "overfit_count", "overfit": bool
                }
            }
        """

        def _mkey(m: dict) -> int:
            return int(m.get("end_time_ms") or m.get("end_date_ts") or m.get("created_ts") or 0)

        sorted_mkts = sorted(markets, key=_mkey)
        total = len(sorted_mkts)

        if total < n_folds * 5:
            # Not enough markets for walk-forward
            return {
                "folds": [],
                "summary": {
                    "note": f"insufficient markets ({total}) for {n_folds}-fold walk-forward",
                    "overfit": False,
                },
            }

        fold_size = total // n_folds
        folds_result = []

        for i in range(n_folds - 1):
            # Train: folds 0..i, Test: fold i+1
            train_end = (i + 1) * fold_size
            test_end = min((i + 2) * fold_size, total)
            train_mkts = sorted_mkts[:train_end]
            test_mkts = sorted_mkts[train_end:test_end]

            if len(test_mkts) < 3:
                continue

            train_stats = self.run(train_mkts, snapshots_by_market)
            test_stats = self.run(test_mkts, snapshots_by_market)

            div = {
                "fold": i + 1,
                "train_n": len(train_mkts),
                "test_n": len(test_mkts),
                "train_wr": train_stats.win_rate,
                "test_wr": test_stats.win_rate,
                "wr_delta": train_stats.win_rate - test_stats.win_rate,
                "train_pnl": train_stats.total_pnl,
                "test_pnl": test_stats.total_pnl,
                "sign_flip": (train_stats.total_pnl > 0) != (test_stats.total_pnl > 0),
            }
            folds_result.append(
                {
                    "train": train_stats,
                    "test": test_stats,
                    "divergence": div,
                }
            )
            logger.info(
                "WF fold %d/%d: train(%d) WR=%.1f%% $%.2f | test(%d) WR=%.1f%% $%.2f",
                i + 1,
                n_folds - 1,
                len(train_mkts),
                train_stats.win_rate,
                train_stats.total_pnl,
                len(test_mkts),
                test_stats.win_rate,
                test_stats.total_pnl,
            )

        # Summary
        if folds_result:
            avg_train_wr = sum(f["divergence"]["train_wr"] for f in folds_result) / len(
                folds_result
            )
            avg_test_wr = sum(f["divergence"]["test_wr"] for f in folds_result) / len(folds_result)
            avg_train_pnl = sum(f["divergence"]["train_pnl"] for f in folds_result) / len(
                folds_result
            )
            avg_test_pnl = sum(f["divergence"]["test_pnl"] for f in folds_result) / len(
                folds_result
            )
            overfit_count = sum(
                1
                for f in folds_result
                if f["divergence"]["wr_delta"] > 10.0 or f["divergence"]["sign_flip"]
            )
            summary = {
                "n_folds": len(folds_result),
                "avg_train_wr": round(avg_train_wr, 1),
                "avg_test_wr": round(avg_test_wr, 1),
                "wr_delta": round(avg_train_wr - avg_test_wr, 1),
                "avg_train_pnl": round(avg_train_pnl, 2),
                "avg_test_pnl": round(avg_test_pnl, 2),
                "overfit_count": overfit_count,
                "overfit": overfit_count >= len(folds_result) / 2,
            }
            logger.info(
                "Walk-forward summary: %d folds, avg train WR=%.1f%% test WR=%.1f%% "
                "Δ=%.1f%% overfit=%d/%d",
                len(folds_result),
                avg_train_wr,
                avg_test_wr,
                avg_train_wr - avg_test_wr,
                overfit_count,
                len(folds_result),
            )
        else:
            summary = {"n_folds": 0, "note": "no valid folds", "overfit": False}

        return {"folds": folds_result, "summary": summary}

    def _run_market(self, market: MarketData, raw_snapshots: list[dict]):
        """Process a single market episode."""
        # Sort snapshots by time
        raw_snapshots.sort(key=lambda s: s.get("timestamp_ms", s.get("timestamp", 0)))

        duration = MARKET_DURATIONS.get(market.market_type, 300)
        first_ts = raw_snapshots[0].get("timestamp_ms", raw_snapshots[0].get("timestamp", 0))

        # Strategy: market open
        self._strategy.on_market_open(market)

        # Process snapshots
        signal: Optional[Signal] = None
        signal_snapshot: Optional[OrderbookSnapshot] = None

        for raw in raw_snapshots:
            snap = self._parse_snapshot(raw, first_ts, duration)

            # Call strategy
            result = self._strategy.on_snapshot(snap)

            if result and signal is None:
                # First signal — check confidence filter
                if result.confidence >= self.config.min_confidence:
                    # Check direction filter
                    if self._direction_ok(result):
                        signal = result
                        signal_snapshot = snap
                        self._signals_generated += 1

        # Resolve market
        winner_str = market.winner.upper()
        if not winner_str:
            # Try to determine from final snapshot
            if raw_snapshots:
                last = raw_snapshots[-1]
                up_price = last.get("up_best_bid", last.get("up_best_ask", 0.5))
                winner_str = "UP" if up_price > 0.5 else "DOWN"

        resolution = Resolution(
            winner=Direction.UP if winner_str == "UP" else Direction.DOWN,
        )

        # Execute trade if signal was generated
        if signal and signal_snapshot and winner_str:
            fill = self.fill_sim.simulate_fill(
                direction=signal.direction,
                amount_usd=self.config.trade_amount,
                snapshot=signal_snapshot,
                market_volume=market.volume,
            )

            if fill.filled:
                trade = self.portfolio.open_trade(
                    signal=signal,
                    fill=fill,
                    market_id=market.market_id,
                    coin=market.coin,
                    market_type=market.market_type,
                    strategy=self.config.strategy_name,
                    hour_utc=market.hour_utc,
                    entry_time_pct=signal_snapshot.elapsed_pct if signal_snapshot else 0,
                )
                if trade:
                    self.portfolio.close_trade(trade, winner_str)

        # Strategy: market close
        self._strategy.on_market_close(market, resolution)

    def _passes_filters(self, market: MarketData) -> bool:
        """Check if market passes all configured filters."""
        if self.config.coin_filter:
            if market.coin.lower() != self.config.coin_filter.lower():
                return False
        if self.config.market_type_filter:
            if market.market_type != self.config.market_type_filter:
                return False
        if self.config.hour_filter:
            if market.hour_utc not in self.config.hour_filter:
                return False
        return True

    def _direction_ok(self, signal: Signal) -> bool:
        """Check if signal direction passes filter."""
        if not self.config.direction_filter:
            return True
        return signal.direction.value == self.config.direction_filter.lower()

    def _parse_market(self, d: dict) -> MarketData:
        """Parse raw market dict into MarketData."""
        # Detect hour
        hour = 0
        start = d.get("start_time", "")
        if start:
            try:
                dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                hour = dt.hour
            except Exception:
                pass

        mtype = d.get("market_type", "5m")
        return MarketData(
            market_id=d.get("market_id") or d.get("id", ""),
            coin=d.get("coin", "BTC").upper(),
            market_type=mtype,
            question=d.get("question", ""),
            start_time=start,
            end_time=d.get("end_time", ""),
            winner=d.get("winner", ""),
            volume=float(d.get("volume", 0) or 0),
            liquidity=float(d.get("liquidity", 0) or 0),
            up_token_id=d.get("up_token_id", ""),
            down_token_id=d.get("down_token_id", ""),
            duration_seconds=MARKET_DURATIONS.get(mtype, 300),
            hour_utc=hour,
        )

    def _parse_snapshot(self, raw: dict, first_ts: int, duration: int) -> OrderbookSnapshot:
        """Parse raw snapshot dict into OrderbookSnapshot."""
        ts = raw.get("timestamp_ms", raw.get("timestamp", 0))
        elapsed_ms = ts - first_ts if first_ts else 0
        elapsed_s = max(0, elapsed_ms / 1000.0)
        remaining_s = max(0, duration - elapsed_s)

        return OrderbookSnapshot(
            timestamp_ms=int(ts),
            up_best_bid=float(raw.get("up_best_bid", 0)),
            up_best_ask=float(raw.get("up_best_ask", 0)),
            down_best_bid=float(raw.get("down_best_bid", 0)),
            down_best_ask=float(raw.get("down_best_ask", 0)),
            spread=float(raw.get("spread", 0)),
            binance_price=float(raw.get("binance_price", 0)),
            up_bid_depth=float(raw.get("up_bid_depth", 0)),
            up_ask_depth=float(raw.get("up_ask_depth", 0)),
            down_bid_depth=float(raw.get("down_bid_depth", 0)),
            down_ask_depth=float(raw.get("down_ask_depth", 0)),
            elapsed_seconds=elapsed_s,
            remaining_seconds=remaining_s,
            elapsed_pct=min(1.0, elapsed_s / duration) if duration > 0 else 0,
            taker_buy_volume=float(raw.get("taker_buy_volume", 0)),
            taker_sell_volume=float(raw.get("taker_sell_volume", 0)),
            raw=raw,
        )

    def _synthetic_snapshots(self, market_dict: dict) -> list[dict]:
        """
        Generate synthetic snapshots from market metadata when real
        orderbook data is unavailable (e.g. PolyBackTest free tier).

        Creates 5 snapshots with realistic price progression toward
        the known winner direction. Uses market volume for depth and
        adds taker volume imbalance to trigger more strategies.

        Pricing progression (if winner=UP):
          5%:  up=0.48  (slight lean)
          20%: up=0.44  (deviation triggers calibration_arb)
          50%: up=0.55  (mid-market shift)
          80%: up=0.62  (late convergence zone)
          95%: up=0.72  (strong convergence)
        """
        import hashlib
        import time as _time

        mtype = market_dict.get("market_type", "5m")
        duration = MARKET_DURATIONS.get(mtype, 300)
        volume = float(market_dict.get("volume", 0) or 0)
        if volume < 100:
            volume = 500.0  # minimum synthetic volume

        # Determine winner direction
        winner = market_dict.get("winner", "").upper()
        up_wins = winner == "UP"

        # Use start_time or fallback to current time
        start_str = market_dict.get("start_time", "")
        if start_str:
            try:
                dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                base_ts = int(dt.timestamp() * 1000)
            except Exception:
                base_ts = int(_time.time() * 1000)
        else:
            base_ts = int(_time.time() * 1000)

        # Deterministic seed from market_id for reproducibility
        mid = market_dict.get("market_id", "") or str(base_ts)
        seed = int(hashlib.md5(mid.encode()).hexdigest()[:8], 16)
        # Small variation factor (±0.03)
        var = ((seed % 100) - 50) / 1000.0  # -0.05 to +0.05

        # Base BTC price — use real or estimate
        btc_price = float(market_dict.get("btc_price", 0) or 0)
        if btc_price == 0:
            btc_price = 85000.0  # reasonable default

        # Price progression toward winner
        # (up_mid_price at each stage)
        if up_wins:
            # UP wins: price starts near 0.50, dips early, then rises
            stages = [
                (0.05, 0.48 + var, 1.05),  # early: slight UP lean
                (0.20, 0.44 + var, 1.12),  # early-mid: dip triggers arb
                (0.50, 0.55 + var, 1.20),  # mid: shift toward UP
                (0.80, 0.62 + var, 1.35),  # late: convergence
                (0.95, 0.72 + var, 1.50),  # final: strong UP
            ]
        else:
            # DOWN wins: UP price starts near 0.50, rises early, drops
            stages = [
                (0.05, 0.52 - var, 1.05),  # early: slight DOWN lean
                (0.20, 0.56 - var, 1.12),  # early-mid: UP overpriced
                (0.50, 0.45 - var, 1.20),  # mid: shift toward DOWN
                (0.80, 0.38 - var, 1.35),  # late: convergence
                (0.95, 0.28 - var, 1.50),  # final: strong DOWN
            ]

        snapshots = []
        cumul_buy = 0.0
        cumul_sell = 0.0

        for pct, up_mid, depth_mult in stages:
            ts = base_ts + int(duration * pct * 1000)

            # Clamp prices to valid range
            up_mid = max(0.05, min(0.95, up_mid))
            down_mid = 1.0 - up_mid
            spread = 0.02

            up_bid = round(up_mid - spread / 2, 4)
            up_ask = round(up_mid + spread / 2, 4)
            down_bid = round(down_mid - spread / 2, 4)
            down_ask = round(down_mid + spread / 2, 4)

            # Depth: winner side has more bid depth
            base_depth = volume * 0.05
            if up_wins:
                up_bid_d = base_depth * depth_mult
                up_ask_d = base_depth / depth_mult
                down_bid_d = base_depth / depth_mult
                down_ask_d = base_depth * depth_mult
            else:
                up_bid_d = base_depth / depth_mult
                up_ask_d = base_depth * depth_mult
                down_bid_d = base_depth * depth_mult
                down_ask_d = base_depth / depth_mult

            # Taker volume: cumulative, biased toward winner
            if up_wins:
                cumul_buy += volume * 0.08 * depth_mult
                cumul_sell += volume * 0.06
            else:
                cumul_buy += volume * 0.06
                cumul_sell += volume * 0.08 * depth_mult

            # BTC price movement (small shift matching direction)
            btc_shift = (pct - 0.5) * 20  # ±$10
            if not up_wins:
                btc_shift = -btc_shift

            snapshots.append(
                {
                    "timestamp_ms": ts,
                    "up_best_bid": up_bid,
                    "up_best_ask": up_ask,
                    "down_best_bid": down_bid,
                    "down_best_ask": down_ask,
                    "spread": spread,
                    "binance_price": btc_price + btc_shift,
                    "up_bid_depth": round(up_bid_d, 2),
                    "up_ask_depth": round(up_ask_d, 2),
                    "down_bid_depth": round(down_bid_d, 2),
                    "down_ask_depth": round(down_ask_d, 2),
                    "taker_buy_volume": round(cumul_buy, 2),
                    "taker_sell_volume": round(cumul_sell, 2),
                    "_synthetic": True,
                }
            )

        return snapshots

        """Return run summary dict."""
        stats = self.portfolio.get_stats() if self.portfolio else PortfolioStats()
        return {
            "strategy": self.config.strategy_name,
            "markets_processed": self._markets_processed,
            "markets_skipped": self._markets_skipped,
            "signals_generated": self._signals_generated,
            "total_trades": stats.total_trades,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": stats.win_rate,
            "total_pnl": stats.total_pnl,
            "total_fees": stats.total_fees,
            "sharpe": stats.sharpe_ratio,
            "sortino": stats.sortino_ratio,
            "max_drawdown": stats.max_drawdown,
            "profit_factor": stats.profit_factor,
            "avg_pnl": stats.avg_pnl,
        }
