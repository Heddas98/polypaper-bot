"""
PolyPaper Bot - Market Regime Detection + Signal Drift (Phase 33 + Phase 59)

Two systems in one module:
1. Regime Classifier: trending/ranging/volatile based on ATR + trend strength
2. ADWIN-like Drift Detector: monitors signal accuracy, auto-reduces weight

No ML required. Pure math. <10KB RAM total.

Phase 59 DRIFT-01: configurable drift recovery window (SIGNAL_DRIFT_WINDOW env var)
"""

import logging
import os
from collections import deque

logger = logging.getLogger("polypaper.core.regime")

# ═══ REGIME CLASSIFIER ═══


class RegimeClassifier:
    """Simple 3-regime classifier: TRENDING, RANGING, VOLATILE.
    Uses ATR percentile + directional bias from recent price changes.

    Strategy compatibility:
      TRENDING:  momentum ✅, fusion ✅, contrarian ❌, scalper ❌
      RANGING:   scalper ✅, contrarian ✅, momentum ❌
      VOLATILE:  flashcrash ✅, sniper ✅, highthreshold ✅, momentum ❌
    """

    STRATEGY_REGIME_FIT = {
        "momentum": {"trending": 1.0, "ranging": 0.3, "volatile": 0.5},
        "fusion": {"trending": 0.9, "ranging": 0.6, "volatile": 0.7},
        "contrarian": {"trending": 0.3, "ranging": 1.0, "volatile": 0.5},
        "scalper": {"trending": 0.4, "ranging": 1.0, "volatile": 0.3},
        "sniper": {"trending": 0.7, "ranging": 0.5, "volatile": 1.0},
        "highthreshold": {"trending": 0.8, "ranging": 0.6, "volatile": 0.9},
        "flashcrash": {"trending": 0.4, "ranging": 0.3, "volatile": 1.0},
        "streak": {"trending": 0.5, "ranging": 0.7, "volatile": 0.6},
        "martingale": {"trending": 0.3, "ranging": 0.5, "volatile": 0.2},
    }

    def __init__(self, window: int = 30):
        self._prices: deque = deque(maxlen=window)
        self._returns: deque = deque(maxlen=window)
        self._regime: str = "ranging"
        self._confidence: float = 0.5

    def update(self, price: float):
        """Feed a new price (call every 5 minutes or every trade cycle)."""
        if self._prices:
            ret = (price - self._prices[-1]) / self._prices[-1] if self._prices[-1] > 0 else 0
            self._returns.append(ret)
        self._prices.append(price)

        if len(self._returns) < 10:
            return  # Need minimum data

        # ATR proxy: average absolute return
        atr = sum(abs(r) for r in self._returns) / len(self._returns)
        # Directional bias: net return / total absolute return
        net = sum(self._returns)
        total_abs = sum(abs(r) for r in self._returns)
        bias = abs(net) / total_abs if total_abs > 0 else 0

        # Classify
        if atr > 0.003 and bias < 0.3:
            self._regime = "volatile"
            self._confidence = min(0.9, atr * 100)
        elif bias > 0.5:
            self._regime = "trending"
            self._confidence = min(0.9, bias)
        else:
            self._regime = "ranging"
            self._confidence = 1.0 - bias

    @property
    def regime(self) -> str:
        return self._regime

    @property
    def confidence(self) -> float:
        return self._confidence

    def strategy_fit(self, strategy_type: str, engine=None) -> float:
        """Return 0.0-1.0 fitness score for strategy in current regime.

        If brain_flags['regime_detection'] is disabled, returns 1.0 (no filtering).
        """
        # Check if regime detection is disabled via brain_flags
        regime_enabled = True
        if engine:
            regime_enabled = getattr(engine, "brain_flags", {}).get("regime_detection", True)

        if not regime_enabled:
            # Regime detection disabled: all strategies fit equally
            return 1.0

        # Regime detection enabled (normal path)
        fits = self.STRATEGY_REGIME_FIT.get(strategy_type, {})
        return fits.get(self._regime, 0.5)

    def should_skip(self, strategy_type: str, engine=None) -> bool:
        """Should this strategy skip trading in current regime?

        If regime detection is disabled, never skip (return False).
        """
        return self.strategy_fit(strategy_type, engine) < 0.4

    def get_status(self) -> dict:
        return {
            "regime": self._regime,
            "confidence": round(self._confidence, 2),
            "data_points": len(self._returns),
        }


# ═══ SIGNAL DRIFT DETECTOR ═══


class DriftDetector:
    """ADWIN-inspired drift detection for signal accuracy.

    Monitors each signal's recent accuracy. If accuracy drops
    below threshold, reduces that signal's fusion weight.

    Lightweight: ~1KB per signal, O(1) per update.

    Phase 59 DRIFT-01: window is configurable via SIGNAL_DRIFT_WINDOW env var.
      Default 100 samples (long recovery). Set to 20 for fast recovery.
    """

    def __init__(self, window: int = None, drift_threshold: float = 0.15):
        # Phase 59 DRIFT-01: env-controlled drift window
        if window is None:
            try:
                window = int(os.getenv("SIGNAL_DRIFT_WINDOW", "100"))
            except (ValueError, TypeError):
                window = 100

        self._signals: dict[str, deque] = {}  # signal_name → deque of (correct, ts)
        self._window = window
        self._drift_threshold = drift_threshold
        self._weight_adjustments: dict[str, float] = {}  # signal → multiplier

        logger.info(
            f"🔄 DriftDetector initialized: window={self._window} samples, "
            f"threshold={self._drift_threshold}"
        )

    def record(self, signal_name: str, was_correct: bool):
        """Record whether a signal's prediction was correct."""
        if signal_name not in self._signals:
            self._signals[signal_name] = deque(maxlen=self._window)
        self._signals[signal_name].append(1.0 if was_correct else 0.0)
        self._check_drift(signal_name)

    def _check_drift(self, signal_name: str):
        """Check if signal has drifted (accuracy degraded)."""
        data = self._signals.get(signal_name, deque())
        if len(data) < 20:
            self._weight_adjustments[signal_name] = 1.0
            return

        # Compare first half vs second half accuracy
        mid = len(data) // 2
        first_half = list(data)[:mid]
        second_half = list(data)[mid:]
        first_acc = sum(first_half) / len(first_half)
        second_acc = sum(second_half) / len(second_half)

        drop = first_acc - second_acc
        if drop > self._drift_threshold:
            # Significant accuracy drop → reduce weight
            self._weight_adjustments[signal_name] = max(0.3, 1.0 - drop)
            logger.info(
                f"📉 DRIFT: {signal_name} accuracy {first_acc:.0%}→{second_acc:.0%} "
                f"(weight ×{self._weight_adjustments[signal_name]:.2f})"
            )
        else:
            # No drift or improving
            self._weight_adjustments[signal_name] = min(
                1.0, self._weight_adjustments.get(signal_name, 1.0) + 0.05
            )

    def get_weight(self, signal_name: str) -> float:
        """Get weight multiplier for a signal (1.0 = normal, <1.0 = degraded)."""
        return self._weight_adjustments.get(signal_name, 1.0)

    def get_all_weights(self) -> dict[str, float]:
        return dict(self._weight_adjustments)

    def get_status(self) -> dict:
        status = {}
        for name, data in self._signals.items():
            acc = sum(data) / len(data) if data else 0
            weight = self._weight_adjustments.get(name, 1.0)
            drifting = weight < 0.8
            status[name] = {
                "accuracy": round(acc * 100, 1),
                "samples": len(data),
                "weight": round(weight, 2),
                "drifting": drifting,
            }
        return status
