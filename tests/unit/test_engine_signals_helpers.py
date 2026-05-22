"""Unit tests for engine_signals.py pure-logic helpers (Epic 9 T9.6 P2).

Coverage gap baseline (2026-04-22): `engine_signals.py` 4.7% / 1123 stmts.
Phase 65 refactor split monolithic `_evaluate` into 6 helpers; T6.3/T6.4
/T7.6 B touched module-top env constants. We test the static pure-logic
surface only — the hot-path `_eval_*` methods are heavy orchestration
that belongs in T9.8 integration smoke.

Scope (static / pure only):
  1. `_parse_zones(zones_str)`  — "0-35,50-55" → [(0.0, 0.35), ...]
  2. `_in_allowed_zone(price, zones)` — any-match predicate + empty=True
  3. `_classic_free_mode(ctx_or_stype)` — Phase 82e hotfix stype gate
  4. `_compute_pending_reserved(wallet_id)` — Epic 5 T5.3 reservation sum
  5. `_get_brier_bin(price)` — Phase 79 S4-04 label mapping

Out-of-scope (→ T9.8):
  * `_evaluate` orchestrator + 6 `_eval_*` helpers — DB/network-heavy
  * `_load_brier_calibration_cache`, `_check_brier_alarm` — DB reads
  * `_get_ob_cached` — network call
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.engine_signals import EngineSignalsMixin

# ═══ _parse_zones — static ═════════════════════════════════════════════


class TestParseZones:
    """'0-35,50-55' → [(0.0, 0.35), (0.50, 0.55)] (cents → fraction)."""

    def test_empty_string_no_filter(self):
        assert EngineSignalsMixin._parse_zones("") == []

    def test_whitespace_only_no_filter(self):
        assert EngineSignalsMixin._parse_zones("   ") == []

    def test_single_zone(self):
        zones = EngineSignalsMixin._parse_zones("10-20")
        assert zones == [(0.10, 0.20)]

    def test_multiple_zones(self):
        zones = EngineSignalsMixin._parse_zones("0-35,50-55")
        assert zones == [(0.0, 0.35), (0.50, 0.55)]

    def test_internal_whitespace_tolerated(self):
        zones = EngineSignalsMixin._parse_zones(" 10 - 20 , 40 - 45 ")
        assert zones == [(0.10, 0.20), (0.40, 0.45)]

    def test_malformed_returns_empty_no_raise(self):
        """Invalid input must NOT crash engine boot — returns [] (allow-all)."""
        assert EngineSignalsMixin._parse_zones("not-a-range") == []
        assert EngineSignalsMixin._parse_zones("10-20,abc") == []
        assert EngineSignalsMixin._parse_zones("10") == []  # no hyphen


# ═══ _in_allowed_zone — static ════════════════════════════════════════


class TestInAllowedZone:
    def test_empty_zones_allows_everything(self):
        assert EngineSignalsMixin._in_allowed_zone(0.01, []) is True
        assert EngineSignalsMixin._in_allowed_zone(0.99, []) is True

    def test_price_in_first_zone(self):
        zones = [(0.10, 0.20), (0.50, 0.55)]
        assert EngineSignalsMixin._in_allowed_zone(0.15, zones) is True

    def test_price_in_second_zone(self):
        zones = [(0.10, 0.20), (0.50, 0.55)]
        assert EngineSignalsMixin._in_allowed_zone(0.52, zones) is True

    def test_price_in_neither_zone(self):
        zones = [(0.10, 0.20), (0.50, 0.55)]
        assert EngineSignalsMixin._in_allowed_zone(0.35, zones) is False
        assert EngineSignalsMixin._in_allowed_zone(0.75, zones) is False

    def test_boundary_inclusive(self):
        """<=, >= — exact boundary counts as in-zone."""
        zones = [(0.10, 0.20)]
        assert EngineSignalsMixin._in_allowed_zone(0.10, zones) is True
        assert EngineSignalsMixin._in_allowed_zone(0.20, zones) is True


# ═══ _classic_free_mode — static, Phase 82e hotfix ═════════════════════


class TestClassicFreeMode:
    """Classic stype + CLASSIC_BYPASS_ALL_GATES != "false" → True.

    Default env = absent → 'true' fallback → True.
    This gate powers the "user-driven classic skips 14 strategic gates"
    doctrine (BUG-10 docstring audit).
    """

    def test_classic_default_env_is_free(self, monkeypatch):
        monkeypatch.delenv("CLASSIC_BYPASS_ALL_GATES", raising=False)
        assert EngineSignalsMixin._classic_free_mode("classic") is True

    def test_classic_bypass_true_is_free(self, monkeypatch):
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        assert EngineSignalsMixin._classic_free_mode("classic") is True

    def test_classic_bypass_false_is_not_free(self, monkeypatch):
        """Opt-out: classic behaves like any strategy."""
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "false")
        assert EngineSignalsMixin._classic_free_mode("classic") is False

    def test_non_classic_never_free(self, monkeypatch):
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        assert EngineSignalsMixin._classic_free_mode("momentum") is False
        assert EngineSignalsMixin._classic_free_mode("scalper") is False

    def test_accepts_ctx_dict(self, monkeypatch):
        """Docstring: 'Accepts either ctx dict OR bare stype string.'"""
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        assert EngineSignalsMixin._classic_free_mode({"stype": "classic"}) is True
        assert EngineSignalsMixin._classic_free_mode({"stype": "momentum"}) is False

    def test_empty_ctx_dict(self, monkeypatch):
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        assert EngineSignalsMixin._classic_free_mode({}) is False

    def test_none_input(self, monkeypatch):
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        # str(None) = 'None' != 'classic'
        assert EngineSignalsMixin._classic_free_mode(None) is False

    def test_runtime_reread(self, monkeypatch):
        """T6.1/T7.6 A5 pattern: /env_toggle takes effect immediately."""
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "false")
        assert EngineSignalsMixin._classic_free_mode("classic") is False
        monkeypatch.setenv("CLASSIC_BYPASS_ALL_GATES", "true")
        assert EngineSignalsMixin._classic_free_mode("classic") is True


# ═══ _rev_family / _rev_free_mode — 2026-05-23 (rev/streak/rule_based) ══


class TestRevFamily:
    """rev/streak/rule_based üyelik testi — env'den BAĞIMSIZ (band kararı).

    Bu gate, ZONE_BLOCKED'ı rev-stratejileri için fair-coin bandına çevirir
    (canlı stratejiler hiç trade etmiyordu — global ALLOWED_ZONES kesiyordu).
    """

    def test_martingale_is_family(self):
        assert EngineSignalsMixin._rev_family("martingale") is True

    def test_streak_rev_is_family(self):
        assert EngineSignalsMixin._rev_family("streak_rev") is True

    def test_rule_based_is_family(self):
        assert EngineSignalsMixin._rev_family("rule_based") is True

    def test_classic_not_family(self):
        assert EngineSignalsMixin._rev_family("classic") is False

    def test_fusion_not_family(self):
        assert EngineSignalsMixin._rev_family("fusion") is False

    def test_none_not_family(self):
        assert EngineSignalsMixin._rev_family(None) is False

    def test_family_ignores_env(self, monkeypatch):
        """Band üyeliği REV_BYPASS_SIGNAL_GATES'ten ETKİLENMEZ."""
        monkeypatch.setenv("REV_BYPASS_SIGNAL_GATES", "false")
        assert EngineSignalsMixin._rev_family("martingale") is True


class TestRevFreeMode:
    """rev-family + REV_BYPASS_SIGNAL_GATES açık → sezgisel sinyal/EV gate'leri
    baypas (EDGE/FEE/BRIER/EV/REGIME/Thompson). Default açık; classic gibi."""

    def test_martingale_default_env_is_free(self, monkeypatch):
        monkeypatch.delenv("REV_BYPASS_SIGNAL_GATES", raising=False)
        assert EngineSignalsMixin._rev_free_mode("martingale") is True

    def test_streak_rev_default_free(self, monkeypatch):
        monkeypatch.delenv("REV_BYPASS_SIGNAL_GATES", raising=False)
        assert EngineSignalsMixin._rev_free_mode("streak_rev") is True

    def test_bypass_false_is_strict(self, monkeypatch):
        """Opt-out: tüm sezgisel gate'ler geri gelir (strict mod)."""
        monkeypatch.setenv("REV_BYPASS_SIGNAL_GATES", "false")
        assert EngineSignalsMixin._rev_free_mode("martingale") is False

    def test_non_family_never_free(self, monkeypatch):
        monkeypatch.setenv("REV_BYPASS_SIGNAL_GATES", "true")
        assert EngineSignalsMixin._rev_free_mode("classic") is False
        assert EngineSignalsMixin._rev_free_mode("fusion") is False

    def test_runtime_reread(self, monkeypatch):
        monkeypatch.setenv("REV_BYPASS_SIGNAL_GATES", "false")
        assert EngineSignalsMixin._rev_free_mode("martingale") is False
        monkeypatch.setenv("REV_BYPASS_SIGNAL_GATES", "true")
        assert EngineSignalsMixin._rev_free_mode("martingale") is True


# ═══ _compute_pending_reserved — instance method ═══════════════════════


class _PendingHarness(EngineSignalsMixin):
    """Minimal stub with `_pending` list — only attr touched."""

    def __init__(self):
        self._pending = []


class TestComputePendingReserved:
    """T5.3 (2026-04-21): sum(o.amount) filtered by wallet_id."""

    def test_empty_pending_zero(self):
        h = _PendingHarness()
        assert h._compute_pending_reserved("W1") == 0.0

    def test_single_matching(self):
        h = _PendingHarness()
        h._pending.append(SimpleNamespace(amount=10.0, wallet_id="W1"))
        assert h._compute_pending_reserved("W1") == 10.0

    def test_multiple_matching_sum(self):
        h = _PendingHarness()
        h._pending.append(SimpleNamespace(amount=10.0, wallet_id="W1"))
        h._pending.append(SimpleNamespace(amount=15.0, wallet_id="W1"))
        h._pending.append(SimpleNamespace(amount=5.0, wallet_id="W1"))
        assert h._compute_pending_reserved("W1") == 30.0

    def test_other_wallet_ignored(self):
        """Multi-wallet isolation: only W1 rows count for W1."""
        h = _PendingHarness()
        h._pending.append(SimpleNamespace(amount=10.0, wallet_id="W1"))
        h._pending.append(SimpleNamespace(amount=999.0, wallet_id="W2"))
        h._pending.append(SimpleNamespace(amount=5.0, wallet_id="W1"))
        assert h._compute_pending_reserved("W1") == 15.0
        assert h._compute_pending_reserved("W2") == 999.0

    def test_unknown_wallet_zero(self):
        h = _PendingHarness()
        h._pending.append(SimpleNamespace(amount=10.0, wallet_id="W1"))
        assert h._compute_pending_reserved("W_DOES_NOT_EXIST") == 0.0


# ═══ _get_brier_bin — instance method, pure ════════════════════════════


class _BrierHarness(EngineSignalsMixin):
    def __init__(self):
        pass


class TestGetBrierBin:
    """Map 0.0-1.0 price → '0.X-0.Y' calibration bin label."""

    def test_mid_range(self):
        h = _BrierHarness()
        assert h._get_brier_bin(0.62) == "0.6-0.7"

    def test_low(self):
        h = _BrierHarness()
        assert h._get_brier_bin(0.05) == "0.0-0.1"

    def test_boundary_start(self):
        h = _BrierHarness()
        # 0.5 → bin_idx = int(5.0) = 5 → "0.5-0.6"
        assert h._get_brier_bin(0.5) == "0.5-0.6"

    def test_max_clamped(self):
        """price=1.0 → bin_idx=10 → clamp to 9 → '0.9-1.0'."""
        h = _BrierHarness()
        assert h._get_brier_bin(1.0) == "0.9-1.0"
        assert h._get_brier_bin(1.5) == "0.9-1.0"

    def test_zero_clamped(self):
        h = _BrierHarness()
        assert h._get_brier_bin(0.0) == "0.0-0.1"
        assert h._get_brier_bin(-0.1) == "0.0-0.1"
