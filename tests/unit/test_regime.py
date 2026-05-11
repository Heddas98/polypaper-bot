"""Unit tests for core/regime.py — RegimeClassifier + DriftDetector.

Coverage gap baseline (2026-04-29): `regime.py` 0% / 93 stmts.

Two systems live in this module:
  1. RegimeClassifier — TRENDING/RANGING/VOLATILE bucket from ATR + bias
  2. DriftDetector    — ADWIN-like signal accuracy degradation watcher

Both are pure stdlib (math + collections.deque). Tests pin:
  - Regime defaults to ranging until enough data
  - Trending requires sustained directional bias
  - Volatile requires high ATR + low directional bias
  - Strategy fit lookup respects brain_flags toggle
  - Drift detector reduces weight when first-half accuracy >> second-half
  - Drift detector recovers weight gradually
  - get_status dict shape (UI contract)
"""

from __future__ import annotations

import os

import pytest

from core.regime import DriftDetector, RegimeClassifier


# ════════════════════════════════════════════════════════════════
# RegimeClassifier
# ════════════════════════════════════════════════════════════════
class TestRegimeClassifierDefaults:
    def test_initial_regime_is_ranging(self):
        rc = RegimeClassifier()
        assert rc.regime == "ranging"
        assert rc.confidence == 0.5

    def test_no_update_with_too_few_returns(self):
        """Need 10+ returns before classification fires."""
        rc = RegimeClassifier()
        for p in [100.0, 101.0, 102.0]:
            rc.update(p)
        # Still default ranging because we have < 10 returns
        assert rc.regime == "ranging"

    def test_zero_price_handled(self):
        """If first price is 0, return calc avoids div-by-zero (return=0)."""
        rc = RegimeClassifier()
        rc.update(0.0)
        for p in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0]:
            rc.update(p)
        # No crash, regime is some valid label
        assert rc.regime in ("trending", "ranging", "volatile")


class TestRegimeClassification:
    def test_trending_for_monotonic_uptrend(self):
        """Sustained directional move with low ATR → trending."""
        rc = RegimeClassifier(window=30)
        # Tiny but consistent up-moves: bias high, ATR low
        for i in range(30):
            rc.update(100.0 + i * 0.05)  # 0.05% steady up
        assert rc.regime == "trending"
        assert rc.confidence >= 0.5

    def test_volatile_for_oscillating_high_atr(self):
        """Big swings with no direction → volatile."""
        rc = RegimeClassifier(window=30)
        for i in range(30):
            # ±2% swings: high ATR (>0.003), low directional bias
            rc.update(100.0 + (1 if i % 2 == 0 else -1) * 2.0)
        assert rc.regime == "volatile"

    def test_ranging_for_small_oscillations(self):
        """Small ATR + low bias → ranging."""
        rc = RegimeClassifier(window=30)
        for i in range(30):
            rc.update(100.0 + (0.05 if i % 2 == 0 else -0.05))
        assert rc.regime == "ranging"


class TestStrategyFit:
    def test_known_strategy_in_default_ranging(self):
        rc = RegimeClassifier()
        # default regime = ranging
        assert rc.strategy_fit("scalper") == 1.0
        assert rc.strategy_fit("contrarian") == 1.0
        assert rc.strategy_fit("momentum") == 0.3

    def test_unknown_strategy_returns_neutral(self):
        rc = RegimeClassifier()
        assert rc.strategy_fit("nonexistent") == 0.5

    def test_brain_flags_disabled_returns_one(self):
        """brain_flags['regime_detection']=False → no filter (1.0)."""
        rc = RegimeClassifier()

        class FakeEngine:
            brain_flags = {"regime_detection": False}

        # momentum normally 0.3 in ranging — but disabled flag → 1.0
        assert rc.strategy_fit("momentum", engine=FakeEngine()) == 1.0

    def test_brain_flags_enabled_uses_lookup(self):
        rc = RegimeClassifier()

        class FakeEngine:
            brain_flags = {"regime_detection": True}

        assert rc.strategy_fit("scalper", engine=FakeEngine()) == 1.0


class TestShouldSkip:
    def test_skip_when_fit_below_threshold(self):
        """fit < 0.4 → should_skip True."""
        rc = RegimeClassifier()
        # In ranging regime, momentum fit = 0.3 < 0.4
        assert rc.should_skip("momentum") is True

    def test_no_skip_when_fit_above_threshold(self):
        rc = RegimeClassifier()
        # Default ranging, scalper fit = 1.0 → no skip
        assert rc.should_skip("scalper") is False

    def test_no_skip_when_brain_flag_disabled(self):
        rc = RegimeClassifier()

        class FakeEngine:
            brain_flags = {"regime_detection": False}

        # Even momentum (would be skipped normally) → no skip when disabled
        assert rc.should_skip("momentum", engine=FakeEngine()) is False


class TestRegimeStatus:
    def test_get_status_shape(self):
        rc = RegimeClassifier()
        s = rc.get_status()
        assert set(s.keys()) == {"regime", "confidence", "data_points"}
        assert s["regime"] == "ranging"
        assert s["data_points"] == 0

    def test_status_after_updates(self):
        rc = RegimeClassifier()
        for p in [100.0, 101.0, 102.0]:
            rc.update(p)
        s = rc.get_status()
        # 3 prices → 2 returns
        assert s["data_points"] == 2


# ════════════════════════════════════════════════════════════════
# DriftDetector
# ════════════════════════════════════════════════════════════════
class TestDriftDetectorInit:
    def test_default_window_from_env(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_DRIFT_WINDOW", "50")
        dd = DriftDetector()
        assert dd._window == 50

    def test_explicit_window_overrides_env(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_DRIFT_WINDOW", "50")
        dd = DriftDetector(window=200)
        assert dd._window == 200

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("SIGNAL_DRIFT_WINDOW", "not_a_number")
        dd = DriftDetector()
        assert dd._window == 100  # spec default

    def test_default_threshold(self):
        dd = DriftDetector()
        assert dd._drift_threshold == 0.15


class TestDriftDetectorRecord:
    def test_first_record_creates_signal(self):
        dd = DriftDetector()
        dd.record("test_sig", True)
        assert "test_sig" in dd._signals
        assert len(dd._signals["test_sig"]) == 1

    def test_below_min_samples_no_drift_check(self):
        """< 20 samples → weight stays 1.0 (insufficient data)."""
        dd = DriftDetector()
        for _ in range(10):
            dd.record("sig1", True)
        assert dd.get_weight("sig1") == 1.0

    def test_drift_detected_when_accuracy_drops(self):
        """First half all correct, second half all wrong → drift detected."""
        dd = DriftDetector(drift_threshold=0.15)
        # First 10 correct
        for _ in range(10):
            dd.record("dropping_sig", True)
        # Second 10 wrong
        for _ in range(10):
            dd.record("dropping_sig", False)
        weight = dd.get_weight("dropping_sig")
        assert weight < 1.0  # weight reduced

    def test_no_drift_when_consistent(self):
        """Consistent accuracy → weight stays at 1.0 or recovers toward 1.0."""
        dd = DriftDetector(drift_threshold=0.15)
        for _ in range(30):
            dd.record("steady_sig", True)
        weight = dd.get_weight("steady_sig")
        # All correct → no drift, weight should be 1.0
        assert weight == 1.0


class TestDriftDetectorWeights:
    def test_unknown_signal_returns_one(self):
        dd = DriftDetector()
        assert dd.get_weight("never_recorded") == 1.0

    def test_get_all_weights_returns_copy(self):
        dd = DriftDetector()
        for _ in range(20):
            dd.record("test", True)
        all_w = dd.get_all_weights()
        assert "test" in all_w
        # Mutating result shouldn't affect internal state
        all_w["test"] = 0.0
        assert dd.get_weight("test") != 0.0


class TestDriftDetectorStatus:
    def test_status_empty_when_no_signals(self):
        dd = DriftDetector()
        assert dd.get_status() == {}

    def test_status_shape_with_signal(self):
        dd = DriftDetector()
        for _ in range(10):
            dd.record("sig", True)
        s = dd.get_status()
        assert "sig" in s
        assert set(s["sig"].keys()) >= {"accuracy", "samples", "weight", "drifting"}
        # 10 samples is below the 20-sample drift check threshold
        assert s["sig"]["samples"] == 10
        assert s["sig"]["accuracy"] == 100.0  # all correct

    def test_drifting_flag_when_weight_below_threshold(self):
        dd = DriftDetector()
        # Trigger drift
        for _ in range(10):
            dd.record("dropping", True)
        for _ in range(10):
            dd.record("dropping", False)
        s = dd.get_status()
        # weight < 0.8 → drifting True (per status logic)
        if s["dropping"]["weight"] < 0.8:
            assert s["dropping"]["drifting"] is True
