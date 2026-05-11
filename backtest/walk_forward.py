"""
Walk-Forward Backtest Engine — P0.6 (5AI Yol Haritası §5.1)
=============================================================

Naive in-sample optimization (hyperopt — silindi 2026-04-28) yerine
walk-forward (rolling train + holdout test) yaklaşımı.

Algorithm:
  1. Sort all trades chronologically
  2. Window = (train_days, test_days)
  3. For each window: optimize params on train, evaluate on test
  4. Concatenate test-period results → out-of-sample equity curve
  5. Report aggregate metrics (no future leak guarantee)

Slippage uses backtest/slippage_model.py (orderbook depth-aware).

Usage:
    runner = WalkForwardRunner(
        train_days=30,
        test_days=7,
        param_grid={"score_threshold": [0.5, 0.6, 0.7]},
    )
    result = runner.run(events_df, evaluate_fn=my_strategy_eval)
    # result["windows"] = [{"train_pnl", "test_pnl", "best_params", ...}, ...]
    # result["aggregate"] = {"total_test_pnl", "sharpe", "profit_factor", ...}

Heddas yerel'de DB'den event akışı çekilir, sonra runner'a beslenir.
"""

from __future__ import annotations

import itertools
import logging
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Optional

logger = logging.getLogger("polypaper.backtest.walk_forward")


@dataclass
class Window:
    """Single train+test window."""

    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    best_params: dict = field(default_factory=dict)
    train_pnl: float = 0.0
    test_pnl: float = 0.0
    test_trades: int = 0
    test_win_rate: float = 0.0
    metrics: dict = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    """Aggregate outcome."""

    windows: list[Window] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def _grid_product(grid: dict[str, list]) -> Iterable[dict]:
    """Cartesian product over param grid: {a: [1,2], b: [x]} → [{a:1,b:x}, {a:2,b:x}]."""
    if not grid:
        yield {}
        return
    keys = list(grid.keys())
    for combo in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, combo, strict=False))


def _compute_metrics(pnls: list[float]) -> dict:
    """Standard metrics from a list of PnL values."""
    if not pnls:
        return {
            "n": 0,
            "win_rate": 0,
            "expectancy": 0,
            "pf": 0,
            "sharpe": 0,
            "max_dd": 0,
            "total": 0,
        }
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_w = sum(wins)
    gross_l = abs(sum(losses))
    pf = gross_w / gross_l if gross_l > 0 else (999.0 if gross_w > 0 else 0)

    mean = sum(pnls) / n
    if n >= 2:
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var) if var > 0 else 0
        sharpe = mean / std if std > 0 else 0
    else:
        sharpe = 0

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "n": n,
        "win_rate": len(wins) / n,
        "expectancy": mean,
        "pf": round(pf, 4) if pf != float("inf") else 999.0,
        "sharpe": round(sharpe, 4),
        "max_dd": round(max_dd, 4),
        "total": round(sum(pnls), 4),
    }


class WalkForwardRunner:
    """Run walk-forward backtest on event timeline.

    `evaluate_fn(events, params) -> list[float]` — kullanıcı callback.
    Verilen events listesi üzerinde stratejiyi simüle eder, trade PnL listesi döner.
    """

    def __init__(
        self,
        train_days: int = 30,
        test_days: int = 7,
        param_grid: Optional[dict[str, list]] = None,
        step_days: Optional[int] = None,  # None → step = test_days (no overlap)
        min_train_trades: int = 30,
        objective: str = "sharpe",  # "sharpe" | "pf" | "expectancy" | "total"
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.param_grid = param_grid or {}
        self.step_days = step_days or test_days
        self.min_train_trades = min_train_trades
        if objective not in {"sharpe", "pf", "expectancy", "total"}:
            raise ValueError(f"objective must be sharpe/pf/expectancy/total, got {objective}")
        self.objective = objective

    def _generate_windows(
        self, start: datetime, end: datetime
    ) -> Iterable[tuple[datetime, datetime, datetime, datetime]]:
        """Yield (train_start, train_end, test_start, test_end)."""
        cur = start
        while cur + timedelta(days=self.train_days + self.test_days) <= end:
            ts = cur
            te = cur + timedelta(days=self.train_days)
            es = te
            ee = te + timedelta(days=self.test_days)
            yield (ts, te, es, ee)
            cur = cur + timedelta(days=self.step_days)

    def _filter_events(self, events: list[dict], start: datetime, end: datetime) -> list[dict]:
        """Slice events by timestamp window. Events must have 'ts' key (Unix epoch)."""
        s = start.timestamp()
        e = end.timestamp()
        return [ev for ev in events if s <= ev.get("ts", 0) < e]

    def _objective_value(self, metrics: dict) -> float:
        return metrics.get(self.objective, 0)

    def run(
        self,
        events: list[dict],
        evaluate_fn: Callable[[list[dict], dict], list[float]],
    ) -> WalkForwardResult:
        """Execute walk-forward.

        Args:
            events: chronologically sorted list of dicts with 'ts' (Unix epoch)
            evaluate_fn: (events_slice, params) → list[trade_pnl]

        Returns: WalkForwardResult
        """
        if not events:
            return WalkForwardResult(config=self._config())

        events_sorted = sorted(events, key=lambda e: e.get("ts", 0))
        first_ts = events_sorted[0]["ts"]
        last_ts = events_sorted[-1]["ts"]
        start = datetime.fromtimestamp(first_ts, tz=UTC)
        end = datetime.fromtimestamp(last_ts, tz=UTC)

        result = WalkForwardResult(config=self._config())
        all_test_pnls: list[float] = []

        windows = list(self._generate_windows(start, end))
        logger.info(
            f"WalkForward: {len(windows)} windows ({self.train_days}d train + {self.test_days}d test, step={self.step_days}d)"
        )

        for ts, te, es, ee in windows:
            train_evs = self._filter_events(events_sorted, ts, te)
            test_evs = self._filter_events(events_sorted, es, ee)

            # Optimize on train
            best_params: dict = {}
            best_obj = float("-inf")
            best_train_metrics: dict = {}
            for params in _grid_product(self.param_grid):
                pnls = evaluate_fn(train_evs, params)
                if len(pnls) < self.min_train_trades:
                    continue
                metrics = _compute_metrics(pnls)
                obj = self._objective_value(metrics)
                if obj > best_obj:
                    best_obj = obj
                    best_params = params
                    best_train_metrics = metrics

            # Evaluate best on test (out-of-sample)
            test_pnls = evaluate_fn(test_evs, best_params) if best_params else []
            test_metrics = _compute_metrics(test_pnls)

            window = Window(
                train_start=ts,
                train_end=te,
                test_start=es,
                test_end=ee,
                best_params=best_params,
                train_pnl=best_train_metrics.get("total", 0),
                test_pnl=test_metrics.get("total", 0),
                test_trades=test_metrics.get("n", 0),
                test_win_rate=test_metrics.get("win_rate", 0),
                metrics=test_metrics,
            )
            result.windows.append(window)
            all_test_pnls.extend(test_pnls)

            logger.info(
                f"  Window {ts.date()}→{ee.date()}: "
                f"train_pnl=${window.train_pnl:.2f} "
                f"test_pnl=${window.test_pnl:.2f} ({test_metrics.get('n', 0)} trades) "
                f"params={best_params}"
            )

        # Aggregate (out-of-sample stitched)
        result.aggregate = _compute_metrics(all_test_pnls)
        result.aggregate["n_windows"] = len(result.windows)

        return result

    def _config(self) -> dict:
        return {
            "train_days": self.train_days,
            "test_days": self.test_days,
            "step_days": self.step_days,
            "param_grid": self.param_grid,
            "min_train_trades": self.min_train_trades,
            "objective": self.objective,
        }
