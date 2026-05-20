"""
Phase 70: Calibration Deepening Tests
=======================================
Tests 2D Surface, MCI, Penny Contract Strategy, EV Threshold.
"""

import os

import pytest

# ═══ 2D Calibration Surface Tests ═══


class TestCalibrationSurface:
    def test_surface_result_defaults(self):
        from calibration.surface_2d import SurfaceResult

        r = SurfaceResult()
        assert r.delta == 0.0
        assert r.source == "2d"
        assert r.antisym_ok is True

    def test_surface_cell(self):
        from calibration.surface_2d import SurfaceCell

        cell = SurfaceCell(price_bin=0.50, time_bin=2, delta=0.03, n_trades=100)
        assert cell.confidence == 1.0
        assert cell.n_trades == 100

    def test_empty_surface_lookup(self):
        from calibration.surface_2d import CalibrationSurface

        surface = CalibrationSurface()
        result = surface.lookup(0.50)
        assert result.source in ("no_surface", "1d_fallback", "1d_no_data")

    def test_surface_lookup_out_of_range(self):
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        surface.cells[(0.50, 2)] = SurfaceCell(price_bin=0.50, time_bin=2, delta=0.05, n_trades=100)
        surface.built = True
        # Price too low
        r = surface.lookup(0.01)
        assert r.source == "out_of_range"
        # Price too high
        r2 = surface.lookup(0.99)
        assert r2.source == "out_of_range"

    def test_surface_lookup_with_cell(self):
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        surface.cells[(0.50, 2)] = SurfaceCell(
            price_bin=0.50, time_bin=2, delta=0.05, n_trades=200, confidence=1.0
        )
        surface.price_marginal[0.50] = 0.04
        surface.time_marginal[2] = 0.01
        surface.built = True

        result = surface.lookup(0.52, hours_remaining=6.0)
        assert result.source == "2d"
        assert result.delta == 0.05
        assert result.c_k == 0.04
        assert result.c_tau == 0.01
        assert result.n_trades == 200

    def test_surface_time_binning(self):
        from calibration.surface_2d import CalibrationSurface

        surface = CalibrationSurface()
        # <1h → bin 0, 1-4h → bin 1, 4-12h → bin 2, etc.
        assert surface._time_bin(0.5) == 0
        assert surface._time_bin(2.0) == 1
        assert surface._time_bin(6.0) == 2
        assert surface._time_bin(18.0) == 3
        assert surface._time_bin(48.0) == 4
        assert surface._time_bin(100.0) == 5

    def test_surface_antisymmetry_check(self):
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        # Price 0.30 and mirror 0.65 (1.0 - 0.30 - 0.05 = 0.65)
        surface.cells[(0.30, 2)] = SurfaceCell(
            price_bin=0.30, time_bin=2, delta=0.05, n_trades=50, confidence=0.7
        )
        surface.cells[(0.65, 2)] = SurfaceCell(
            price_bin=0.65, time_bin=2, delta=-0.05, n_trades=50, confidence=0.7
        )
        surface.price_marginal[0.30] = 0.04
        surface.price_marginal[0.65] = -0.04
        surface.time_marginal[2] = 0.0
        surface.built = True

        # Perfect antisymmetry → should pass
        result = surface.lookup(0.32, hours_remaining=6.0)
        assert result.antisym_ok is True

    def test_surface_antisymmetry_violation(self):
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        # Both positive → violation
        surface.cells[(0.30, 2)] = SurfaceCell(
            price_bin=0.30, time_bin=2, delta=0.08, n_trades=50, confidence=0.7
        )
        surface.cells[(0.65, 2)] = SurfaceCell(
            price_bin=0.65, time_bin=2, delta=0.08, n_trades=50, confidence=0.7
        )
        surface.price_marginal[0.30] = 0.08
        surface.price_marginal[0.65] = 0.08
        surface.time_marginal[2] = 0.0
        surface.built = True

        result = surface.lookup(0.32, hours_remaining=6.0)
        assert result.antisym_ok is False
        assert result.antisym_violation > 0.03

    def test_surface_delta_convenience(self):
        from calibration.surface_2d import CalibrationSurface, surface_delta

        # No surface, no fallback
        r = surface_delta(None, 0.50)
        assert r.source == "no_surface"

    def test_surface_boost(self):
        from calibration.surface_2d import SurfaceResult, surface_boost

        r = SurfaceResult(boost=0.05)
        assert surface_boost(r) == 0.05

    def test_format_surface_telegram(self):
        from calibration.surface_2d import CalibrationSurface, format_surface_telegram

        surface = CalibrationSurface()
        text = format_surface_telegram(surface)
        assert "Henüz" in text  # Not built yet

        surface.built = True
        text2 = format_surface_telegram(surface)
        assert "2D Calibration" in text2


# ═══ Surface Builder Tests ═══


class TestSurfaceBuilder:
    def test_build_from_rows(self):
        from calibration.surface_2d import SurfaceBuilder

        builder = SurfaceBuilder.__new__(SurfaceBuilder)
        rows = [
            (0.30, 2, 0.35, 150),  # actual_wr=0.35, bin_mid=0.325, delta=0.025
            (0.50, 2, 0.52, 200),  # actual_wr=0.52, bin_mid=0.525, delta=-0.005
            (0.70, 1, 0.73, 80),  # actual_wr=0.73, bin_mid=0.725, delta=0.005
        ]
        surface = builder._build_from_rows(rows)
        assert surface.built is True
        assert surface.n_populated_cells == 3
        assert surface.total_trades == 430
        assert 0.30 in surface.price_marginal
        assert 2 in surface.time_marginal


# ═══ Market Coherence Index Tests ═══


class TestMCI:
    def test_mci_disabled(self, monkeypatch):
        # T11.5: monkeypatch.setenv auto-restores on test teardown
        monkeypatch.setenv("MCI_ENABLED", "false")
        # Need to reimport to pick up env change (module-top constant)
        import importlib

        import calibration.coherence as coh

        importlib.reload(coh)
        try:
            result = coh.compute_mci(None)
            assert result.score == 1.0
            assert result.reason == "disabled"
        finally:
            # Restore module with the (now-restored) env value so later
            # tests see MCI_ENABLED=true / unset as originally configured
            monkeypatch.setenv("MCI_ENABLED", "true")
            importlib.reload(coh)

    def test_mci_no_surface(self):
        from calibration.coherence import compute_mci

        result = compute_mci(None)
        assert result.score == 0.5
        assert result.should_trade is True

    def test_mci_sparse_surface(self):
        from calibration.coherence import compute_mci
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        surface.built = True
        surface.cells[(0.50, 2)] = SurfaceCell(price_bin=0.50, time_bin=2, delta=0.02, n_trades=50)
        result = compute_mci(surface, min_cells=10)
        assert "sparse" in result.reason

    def test_mci_good_surface(self):
        from calibration.coherence import compute_mci
        from calibration.surface_2d import CalibrationSurface, SurfaceCell

        surface = CalibrationSurface()
        surface.built = True
        surface.total_trades = 10000
        # Create 15 cells with good antisymmetry
        for i in range(15):
            pb = round(0.10 + i * 0.05, 2)
            mirror_pb = round(1.0 - pb - 0.05, 2)
            delta = 0.02 * (0.5 - pb)  # Small deltas
            surface.cells[(pb, 2)] = SurfaceCell(
                price_bin=pb, time_bin=2, delta=delta, n_trades=200, confidence=1.0
            )
            if mirror_pb != pb:
                surface.cells[(mirror_pb, 2)] = SurfaceCell(
                    price_bin=mirror_pb, time_bin=2, delta=-delta, n_trades=200, confidence=1.0
                )

        result = compute_mci(surface, min_cells=5)
        assert result.score > 0.5
        assert result.should_trade is True

    def test_mci_format_telegram(self):
        from calibration.coherence import MCIResult, format_mci_telegram

        mci = MCIResult(score=0.75, should_trade=True)
        text = format_mci_telegram(mci)
        assert "🟢" in text
        assert "Market Coherence" in text

    def test_mci_low_score(self):
        from calibration.coherence import MCIResult

        mci = MCIResult(score=0.30, should_trade=False)
        assert not mci.should_trade


# ═══ Penny Contract Strategy Tests — 2026-05-21 SKIPPED ═══
# PennyContractStrategy Heddas direktifiyle silindi (core/strategy_plugins.py
# bone-thin yapildi). Tum test_penny_* artik silinmis import'a bagli, skip.


@pytest.mark.skip(reason="PennyContractStrategy removed 2026-05-21 (Heddas)")
class TestPennyContract:
    def test_penny_name(self):
        from core.strategy_plugins import PennyContractStrategy

        s = PennyContractStrategy()
        assert s.name == "penny_contract"

    def test_penny_low_zone_signal(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.03, down_odds=0.97, spread=0.02, minutes_remaining=10.0)
        result = s.evaluate(snap)
        assert result.should_trade is True
        assert result.direction == "down"
        assert result.metadata.get("force_maker") is True

    def test_penny_high_zone_signal(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.97, down_odds=0.03, spread=0.02, minutes_remaining=10.0)
        result = s.evaluate(snap)
        assert result.should_trade is True
        assert result.direction == "up"

    def test_penny_not_in_zone(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.50, down_odds=0.50, spread=0.02, minutes_remaining=10.0)
        result = s.evaluate(snap)
        assert result.should_trade is False
        assert "not_penny_zone" in result.reason

    def test_penny_tight_spread_skip(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.03, down_odds=0.97, spread=0.005, minutes_remaining=10.0)
        result = s.evaluate(snap)
        assert result.should_trade is False
        assert "spread_too_tight" in result.reason

    def test_penny_too_close_to_close(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.03, down_odds=0.97, spread=0.02, minutes_remaining=0.5)
        result = s.evaluate(snap)
        assert result.should_trade is False
        assert "too_close" in result.reason

    def test_penny_confidence_bounded(self):
        from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

        s = PennyContractStrategy()
        snap = MarketSnapshot(up_odds=0.01, down_odds=0.99, spread=0.02, minutes_remaining=10.0)
        result = s.evaluate(snap)
        assert result.confidence <= 0.70  # MAX_CONFIDENCE cap

    def test_penny_in_registry(self):
        from core.strategy_plugins import StrategyRegistry

        reg = StrategyRegistry()
        assert "penny_contract" in reg.names


# ═══ EV Threshold Tests ═══


class TestEVThreshold:
    def test_ev_positive(self):
        from calibration.ev_threshold import compute_ev

        # model_wr=0.60 at price=0.50 → big positive EV
        ev = compute_ev(model_wr=0.60, market_price=0.50, fee_pct=0.02)
        assert ev.ev_positive is True
        assert ev.ev_above_threshold is True
        assert ev.should_trade is True
        assert ev.ev_per_dollar > 0

    def test_ev_negative(self):
        from calibration.ev_threshold import compute_ev

        # model_wr=0.40 at price=0.50 → negative EV
        ev = compute_ev(model_wr=0.40, market_price=0.50, fee_pct=0.02)
        assert ev.ev_positive is False
        assert ev.ev_per_dollar < 0

    def test_ev_marginal(self):
        from calibration.ev_threshold import compute_ev

        # Barely positive
        ev = compute_ev(model_wr=0.52, market_price=0.50, fee_pct=0.02)
        # EV is small but positive
        assert ev.ev_per_dollar > -0.1

    def test_ev_maker_rebate(self):
        from calibration.ev_threshold import compute_ev

        ev_taker = compute_ev(model_wr=0.55, market_price=0.50, fee_pct=0.02)
        ev_maker = compute_ev(model_wr=0.55, market_price=0.50, fee_pct=0.02, is_maker=True)
        # Maker should have better EV due to rebate
        assert ev_maker.ev_per_dollar > ev_taker.ev_per_dollar

    def test_ev_extreme_price_bypass(self):
        from calibration.ev_threshold import compute_ev

        # Extreme prices bypass the check
        ev = compute_ev(model_wr=0.50, market_price=0.001)
        assert ev.reason == "extreme_price"
        assert ev.should_trade is True

    def test_ev_edge_calculation(self):
        from calibration.ev_threshold import compute_ev

        ev = compute_ev(model_wr=0.60, market_price=0.50, fee_pct=0.02)
        assert ev.edge == 0.10  # 0.60 - 0.50
        assert ev.model_wr == 0.60
        assert ev.market_price == 0.50

    def test_ev_tracker_basics(self):
        from calibration.ev_threshold import EVTracker, compute_ev

        tracker = EVTracker()
        assert tracker.ev_positive_pct == 0.0
        assert tracker.mean_ev == 0.0

        # Record a positive EV trade
        ev1 = compute_ev(0.60, 0.50, 0.02)
        tracker.record(ev1)
        assert tracker._total == 1
        assert tracker._ev_positive == 1
        assert tracker.ev_positive_pct == 100.0

        # Record a negative EV trade
        ev2 = compute_ev(0.40, 0.50, 0.02)
        tracker.record(ev2)
        assert tracker._total == 2
        assert tracker.ev_positive_pct == 50.0

    def test_ev_tracker_beats_baseline(self):
        from calibration.ev_threshold import EVTracker, compute_ev

        tracker = EVTracker()
        # 20% positive = above 12.3% baseline
        for _ in range(2):
            tracker.record(compute_ev(0.65, 0.50, 0.02))
        for _ in range(8):
            tracker.record(compute_ev(0.40, 0.50, 0.02))
        assert tracker.beats_baseline is True
        assert tracker.ev_positive_pct == 20.0

    def test_ev_tracker_format(self):
        from calibration.ev_threshold import EVTracker

        tracker = EVTracker()
        text = tracker.format_telegram()
        assert "EV Threshold" in text

    def test_ev_tracker_reset(self):
        from calibration.ev_threshold import EVTracker, compute_ev

        tracker = EVTracker()
        tracker.record(compute_ev(0.60, 0.50, 0.02))
        assert tracker._total == 1
        tracker.reset()
        assert tracker._total == 0
