"""
PolyPaper Bot - RiskLimits Round-Trip Test Suite (T3.4 — Epic 3)
=================================================================

Tests for `RiskLimits.to_dict()` / `RiskLimits.from_dict()` serialization
round-trip fidelity. These are CRITICAL because `bot_settings` persists
the risk config as key/value strings in SQLite — any lossy conversion
means user-configured limits silently revert to defaults after restart.

Edge cases covered:
  1. Default values round-trip (sanity)
  2. Custom scalar values round-trip
  3. per_asset_limits dict preservation (with non-default assets)
  4. Empty per_asset_limits behavior (documented fallback-to-default quirk)
  5. Unicode / non-ASCII asset names
  6. Float precision (str ↔ float)
  7. Corrupt values graceful fallback (ValueError/TypeError)
  8. Missing fields keep defaults (partial dict input)
  9. per_market_limit independent preservation

Run:
    pytest tests/test_risk_limits_roundtrip.py -v

Closes T3.4 (TASKS.md Epic 3).
"""
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest

from core.risk_manager import RiskLimits


# ============================================================================
# DEFAULT VALUE ROUND-TRIP
# ============================================================================

class TestRiskLimitsDefaults:
    """Round-trip with factory defaults — baseline sanity check."""

    def test_default_values_roundtrip(self):
        """Default RiskLimits → to_dict → from_dict → identical values."""
        original = RiskLimits()
        serialized = original.to_dict()
        restored = RiskLimits.from_dict(serialized)

        assert restored.max_position_size == original.max_position_size
        assert restored.max_open_positions == original.max_open_positions
        assert restored.max_total_exposure == original.max_total_exposure
        assert restored.max_daily_loss == original.max_daily_loss
        assert restored.max_daily_trades == original.max_daily_trades
        assert restored.max_loss_streak == original.max_loss_streak
        assert restored.min_balance_floor == original.min_balance_floor
        assert restored.max_single_market_exposure == original.max_single_market_exposure
        assert restored.per_market_limit == original.per_market_limit
        assert restored.per_asset_limits == original.per_asset_limits

    def test_default_per_asset_limits_present(self):
        """Default dict contains BTC/ETH/SOL/XRP — Phase 36 contract."""
        lim = RiskLimits()
        assert "BTC" in lim.per_asset_limits
        assert "ETH" in lim.per_asset_limits
        assert "SOL" in lim.per_asset_limits
        assert "XRP" in lim.per_asset_limits
        assert lim.per_asset_limits["BTC"] == 500.0
        assert lim.per_asset_limits["ETH"] == 300.0

    def test_to_dict_uses_risk_prefix(self):
        """All output keys prefixed with 'risk.' — DB namespace contract."""
        d = RiskLimits().to_dict()
        for key in d:
            assert key.startswith("risk."), f"key {key!r} missing 'risk.' prefix"

    def test_to_dict_values_are_strings(self):
        """All serialized values are strings — SQLite TEXT column contract."""
        d = RiskLimits().to_dict()
        for key, val in d.items():
            assert isinstance(val, str), f"value for {key!r} is {type(val).__name__}, not str"


# ============================================================================
# CUSTOM VALUE ROUND-TRIP
# ============================================================================

class TestRiskLimitsCustomValues:
    """User-modified values must survive persist/restart cycle intact."""

    def test_custom_scalar_values_roundtrip(self):
        """Explicit non-default scalar fields round-trip with correct types."""
        original = RiskLimits()
        original.max_position_size = 25.5
        original.max_open_positions = 10
        original.max_daily_loss = 75.0
        original.max_daily_trades = 500
        original.max_loss_streak = 7
        original.min_balance_floor = 50.0
        original.per_market_limit = 150.0

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.max_position_size == 25.5
        assert isinstance(restored.max_position_size, float)
        assert restored.max_open_positions == 10
        assert isinstance(restored.max_open_positions, int)
        assert restored.max_daily_loss == 75.0
        assert restored.max_daily_trades == 500
        assert restored.max_loss_streak == 7
        assert restored.min_balance_floor == 50.0
        assert restored.per_market_limit == 150.0

    def test_per_asset_limits_custom_roundtrip(self):
        """Custom per_asset_limits dict survives round-trip."""
        original = RiskLimits()
        original.per_asset_limits = {
            "BTC": 1000.0,
            "ETH": 750.0,
            "DOGE": 50.0,   # asset not in default set
        }

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_asset_limits == {
            "BTC": 1000.0,
            "ETH": 750.0,
            "DOGE": 50.0,
        }

    def test_per_asset_additional_asset_preserved(self):
        """New asset added beyond default set persists."""
        original = RiskLimits()
        original.per_asset_limits["MATIC"] = 125.0

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_asset_limits["MATIC"] == 125.0
        # Defaults still present
        assert restored.per_asset_limits["BTC"] == 500.0


# ============================================================================
# EDGE CASES — per_asset_limits
# ============================================================================

class TestRiskLimitsPerAssetEdgeCases:
    """Tricky dict-field serialization scenarios."""

    def test_empty_per_asset_limits_falls_back_to_defaults(self):
        """
        DOCUMENTED QUIRK: If user explicitly sets per_asset_limits={},
        to_dict() emits no 'risk.per_asset.*' keys and from_dict()'s
        `if per_asset:` guard (L85) leaves the default dict intact.

        This means "explicitly cleared" is indistinguishable from "not
        persisted" on reload. Current code intentionally prefers defaults
        to an empty dict. This test locks in that behavior so any future
        change is conscious.
        """
        original = RiskLimits()
        original.per_asset_limits = {}

        serialized = original.to_dict()
        # No per_asset keys should be emitted
        per_asset_keys = [k for k in serialized if k.startswith("risk.per_asset.")]
        assert per_asset_keys == []

        restored = RiskLimits.from_dict(serialized)

        # Fallback behavior: defaults are preserved (NOT empty)
        assert restored.per_asset_limits != {}
        assert "BTC" in restored.per_asset_limits

    def test_unicode_asset_names(self):
        """Non-ASCII asset names (Turkish / symbols) round-trip."""
        original = RiskLimits()
        original.per_asset_limits = {
            "BİTCOİN": 600.0,  # Turkish İ
            "ЕТН": 400.0,      # Cyrillic E
            "€UR": 200.0,      # symbol
        }

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_asset_limits["BİTCOİN"] == 600.0
        assert restored.per_asset_limits["ЕТН"] == 400.0
        assert restored.per_asset_limits["€UR"] == 200.0

    def test_zero_limit_preserved(self):
        """Zero is a legitimate lock-out limit — must not be dropped."""
        original = RiskLimits()
        original.per_asset_limits = {"BTC": 0.0, "ETH": 100.0}

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_asset_limits["BTC"] == 0.0
        assert restored.per_asset_limits["ETH"] == 100.0


# ============================================================================
# EDGE CASES — numeric precision
# ============================================================================

class TestRiskLimitsNumericEdgeCases:
    """String↔float conversion precision."""

    def test_float_precision_roundtrip(self):
        """Multi-decimal floats survive str() / float() conversion."""
        original = RiskLimits()
        original.max_position_size = 12.3456789
        original.min_balance_floor = 0.01
        original.per_market_limit = 1234.5678

        restored = RiskLimits.from_dict(original.to_dict())

        # str(float) → float is exact for most values via repr-based str in Py3
        assert restored.max_position_size == 12.3456789
        assert restored.min_balance_floor == 0.01
        assert restored.per_market_limit == 1234.5678

    def test_large_integer_values(self):
        """Integer fields preserve magnitude after conversion."""
        original = RiskLimits()
        original.max_daily_trades = 999_999
        original.max_open_positions = 100
        original.max_loss_streak = 50

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.max_daily_trades == 999_999
        assert isinstance(restored.max_daily_trades, int)
        assert restored.max_open_positions == 100
        assert restored.max_loss_streak == 50

    def test_negative_values_preserved(self):
        """Negative limits are technically legal (reject-all semantics)."""
        original = RiskLimits()
        original.max_position_size = -1.0  # acts as a kill switch

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.max_position_size == -1.0


# ============================================================================
# EDGE CASES — corrupt / missing input
# ============================================================================

class TestRiskLimitsCorruptInput:
    """from_dict() must degrade gracefully — never crash the bot on reload."""

    def test_corrupt_scalar_value_keeps_default(self):
        """Non-numeric string in numeric field → field stays at default."""
        d = {
            "risk.max_position_size": "not_a_number",
            "risk.max_daily_loss": "75.0",  # valid — should be applied
        }
        lim = RiskLimits.from_dict(d)

        # Corrupt field kept default
        assert lim.max_position_size == 10.0
        # Valid field applied
        assert lim.max_daily_loss == 75.0

    def test_corrupt_per_asset_value_skipped(self):
        """Non-numeric per_asset value skipped, others preserved."""
        d = {
            "risk.per_asset.BTC": "not_a_number",
            "risk.per_asset.ETH": "400.0",
        }
        lim = RiskLimits.from_dict(d)

        # Only ETH applied — per_asset dict replaces default because
        # `if per_asset:` is truthy (one valid entry)
        assert lim.per_asset_limits == {"ETH": 400.0}

    def test_empty_dict_input_returns_defaults(self):
        """from_dict({}) → all defaults, no crashes."""
        lim = RiskLimits.from_dict({})

        defaults = RiskLimits()
        assert lim.max_position_size == defaults.max_position_size
        assert lim.max_open_positions == defaults.max_open_positions
        assert lim.per_asset_limits == defaults.per_asset_limits
        assert lim.per_market_limit == defaults.per_market_limit

    def test_missing_fields_keep_defaults(self):
        """Partial input — unlisted fields keep factory defaults."""
        d = {
            "risk.max_position_size": "42.0",
            # all other fields intentionally absent
        }
        lim = RiskLimits.from_dict(d)

        assert lim.max_position_size == 42.0
        # Unlisted fields = defaults
        assert lim.max_open_positions == 5
        assert lim.max_daily_loss == 50.0
        assert lim.per_market_limit == 100.0

    def test_unknown_keys_ignored(self):
        """Unknown risk.* keys don't crash (forward-compat)."""
        d = {
            "risk.max_position_size": "15.0",
            "risk.future_field_xyz": "123",     # unknown — should be ignored
            "risk.per_asset.BTC": "600.0",
        }
        lim = RiskLimits.from_dict(d)

        assert lim.max_position_size == 15.0
        assert lim.per_asset_limits["BTC"] == 600.0
        # No AttributeError raised — unknown key silently dropped


# ============================================================================
# INDEPENDENCE — per_market_limit must NOT leak into per_asset handling
# ============================================================================

class TestRiskLimitsFieldIndependence:
    """
    Regression guards for serialization key collisions between
    per_asset_limits (a dict) and per_market_limit (a scalar) — their
    key prefixes are similar enough ('risk.per_asset.*' vs 'risk.per_market_limit')
    that a naive handler could confuse them.
    """

    def test_per_market_limit_roundtrip(self):
        """per_market_limit survives round-trip untouched."""
        original = RiskLimits()
        original.per_market_limit = 77.77

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_market_limit == 77.77

    def test_per_market_limit_not_parsed_as_per_asset(self):
        """
        'risk.per_market_limit' must not be matched by the
        'risk.per_asset.' prefix scanner in from_dict.
        """
        d = {"risk.per_market_limit": "200.0"}
        lim = RiskLimits.from_dict(d)

        assert lim.per_market_limit == 200.0
        # per_asset_limits stays at defaults (no 'risk.per_asset.*' in input)
        assert "BTC" in lim.per_asset_limits  # default preserved

    def test_both_per_fields_independent(self):
        """Custom per_asset_limits + custom per_market_limit both persist."""
        original = RiskLimits()
        original.per_asset_limits = {"BTC": 777.0}
        original.per_market_limit = 333.0

        restored = RiskLimits.from_dict(original.to_dict())

        assert restored.per_asset_limits == {"BTC": 777.0}
        assert restored.per_market_limit == 333.0


# ============================================================================
# DOUBLE ROUND-TRIP (idempotency)
# ============================================================================

class TestRiskLimitsIdempotency:
    """Round-tripping twice should yield the same result as once."""

    def test_double_roundtrip_is_stable(self):
        """to_dict → from_dict → to_dict → from_dict = same as single pass."""
        original = RiskLimits()
        original.max_position_size = 33.0
        original.per_asset_limits = {"BTC": 800.0, "SOL": 150.0}
        original.per_market_limit = 175.0

        once = RiskLimits.from_dict(original.to_dict())
        twice = RiskLimits.from_dict(once.to_dict())

        assert once.max_position_size == twice.max_position_size
        assert once.per_asset_limits == twice.per_asset_limits
        assert once.per_market_limit == twice.per_market_limit
