"""Unit tests for core/live_trader.py (Epic 9 T9.6 P1 Tier 1).

Coverage gap baseline (2026-04-22): `live_trader.py` 0% / 271 stmts.
T7.6 post-audit A5 added ENV-override helpers (`_get_max_trade` /
`_get_max_daily_loss` / `_get_min_signal` / `_get_min_odds`) without
tests — a "ghost-toggle" regression waiting to happen.

These tests cover:
  1. ENV helpers: default + override + invalid fallback + runtime re-read
  2. ``is_enabled`` AND-gate (``_enabled`` × ``!_paused`` × ``_auth_verified``)
  3. ``toggle`` paused-flip idempotency
  4. ``_maybe_reset_daily`` day rollover
  5. ``get_status`` dict shape (UI contract)
  6. ``maybe_mirror`` rejection paths (disabled, whitelist, signal, odds,
     daily-loss trip, single-slot guard, budget exhausted)

Scope: SYNC logic + pure gates only. ``_execute_clob`` and ``_place``
(CLOB signature + DB writes) are out-of-scope — those belong to T9.8
integration smoke, not a unit regression fill.
"""

from __future__ import annotations

import os
from datetime import UTC

import pytest

from core.live_trader import (
    LIVE_STRATEGIES,
    LiveTrader,
    _get_live_budget,
    _get_max_daily_loss,
    _get_max_trade,
    _get_min_odds,
    _get_min_signal,
)

# ═══ ENV helpers: default + override + invalid fallback ═══════════════


class TestEnvHelpers:
    """T7.6 A5 invariant: every call re-reads os.environ (no import-time freeze)."""

    def test_max_trade_default(self, monkeypatch):
        monkeypatch.delenv("LIVE_MAX_TRADE", raising=False)
        assert _get_max_trade() == 1.00

    def test_max_trade_override(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAX_TRADE", "2.50")
        assert _get_max_trade() == 2.50

    def test_max_trade_invalid_falls_back(self, monkeypatch):
        """Malformed ENV must NOT raise — must fall back to default."""
        monkeypatch.setenv("LIVE_MAX_TRADE", "not-a-number")
        assert _get_max_trade() == 1.00  # silent fallback

    def test_max_daily_loss_override(self, monkeypatch):
        monkeypatch.setenv("LIVE_MAX_DAILY_LOSS", "5.00")
        assert _get_max_daily_loss() == 5.00

    def test_min_signal_override(self, monkeypatch):
        monkeypatch.setenv("LIVE_MIN_SIGNAL", "0.90")
        assert _get_min_signal() == 0.90

    def test_min_odds_default_and_override(self, monkeypatch):
        monkeypatch.delenv("LIVE_MIN_ODDS", raising=False)
        assert _get_min_odds() == 0.75
        monkeypatch.setenv("LIVE_MIN_ODDS", "0.60")
        assert _get_min_odds() == 0.60

    def test_runtime_rread_no_freeze(self, monkeypatch):
        """CRITICAL: ghost-toggle guard — 2 sequential reads must see fresh value.

        This is the T6.1 / T6.4 / T7.6 A5 doctrine — if the helper ever frosts
        to a module-top constant, ``/env_toggle`` would silently no-op and
        operators would lose real-money safety control.
        """
        monkeypatch.setenv("LIVE_MAX_TRADE", "1.00")
        first = _get_max_trade()
        monkeypatch.setenv("LIVE_MAX_TRADE", "3.14")
        second = _get_max_trade()
        assert first == 1.00
        assert second == 3.14  # fresh read, not frozen


# ═══ T11.2 [B]: LIVE_BUDGET runtime re-read (ghost-toggle class) ═══════


class TestLiveBudgetRuntime:
    """``LIVE_BUDGET`` was ctor-fixed on ``self._budget`` pre-T11.2 [B].
    An operator tightening the cap via ``/envt LIVE_BUDGET 0.50`` would
    patch os.environ but the trader kept spending up to the old ceiling.

    These tests pin:
      1. ``_get_live_budget`` default + override + invalid fallback.
      2. ``_get_live_budget`` runtime re-read (no module-top freeze).
      3. ``LiveTrader._budget`` property delegates to the helper — i.e.
         the ``/envt`` update is visible at the instance level without
         re-instantiating or restarting the bot.
    """

    def test_default_is_phase48_deposit(self, monkeypatch):
        """Default = 1.49 — matches the $1.49 USDC in the derived-L2 wallet."""
        monkeypatch.delenv("LIVE_BUDGET", raising=False)
        assert _get_live_budget() == 1.49

    def test_explicit_override(self, monkeypatch):
        monkeypatch.setenv("LIVE_BUDGET", "5.00")
        assert _get_live_budget() == 5.00

    def test_malformed_falls_back(self, monkeypatch):
        """Garbage env → silent fallback to default (do NOT crash the
        mirror loop over a typo in /envt)."""
        monkeypatch.setenv("LIVE_BUDGET", "not-a-number")
        assert _get_live_budget() == 1.49

    def test_runtime_reread_no_freeze(self, monkeypatch):
        """Ghost-toggle guard — two sequential calls see the fresh value."""
        monkeypatch.setenv("LIVE_BUDGET", "1.49")
        assert _get_live_budget() == 1.49
        monkeypatch.setenv("LIVE_BUDGET", "3.00")
        assert _get_live_budget() == 3.00

    def test_instance_property_tracks_env(self, monkeypatch):
        """The ``lt._budget`` property must reflect every env flip
        immediately — this is the whole point of T11.2 [B]. A single
        LiveTrader instance, two env flips, three distinct reads."""
        monkeypatch.setenv("LIVE_BUDGET", "1.49")
        lt = LiveTrader()
        assert lt._budget == 1.49
        monkeypatch.setenv("LIVE_BUDGET", "2.50")
        assert lt._budget == 2.50
        monkeypatch.setenv("LIVE_BUDGET", "0.75")
        assert lt._budget == 0.75

    def test_property_is_readonly(self):
        """T11.2 [B]: ``_budget`` is intentionally read-only.

        No ``@<prop>.setter`` is defined, so direct attr write raises
        ``AttributeError``. This pins the "env-is-the-source-of-truth"
        invariant — anyone needing to mutate the cap at runtime MUST
        go through the env (``/envt LIVE_BUDGET ...``), which keeps an
        audit trail; silent in-process mutation would bypass that.
        """
        lt = LiveTrader()
        with pytest.raises(AttributeError):
            lt._budget = 99.0  # type: ignore[misc]

    def test_get_status_budget_tracks_env(self, monkeypatch):
        """/live status reads ``budget`` + ``remaining`` from the dict.
        Both fields must reflect the current env, not a frozen snapshot."""
        monkeypatch.setenv("LIVE_BUDGET", "1.49")
        lt = LiveTrader()
        lt._total_spent = 0.25
        s1 = lt.get_status()
        assert s1["budget"] == 1.49
        assert s1["remaining"] == pytest.approx(1.24)
        # Operator raises the cap mid-session
        monkeypatch.setenv("LIVE_BUDGET", "5.00")
        s2 = lt.get_status()
        assert s2["budget"] == 5.00
        assert s2["remaining"] == pytest.approx(4.75)


# ═══ is_enabled AND-gate ═══════════════════════════════════════════════


class TestIsEnabled:
    """``is_enabled`` = ``_enabled`` AND ``!_paused`` AND ``_auth_verified``.

    All three flags are required. Any single False disables mirroring.
    Phase 49 A-01 introduced ``_auth_verified`` — verifies L2 CLOB creds
    before allowing real-money placement.
    """

    def _make(self, enabled: bool, paused: bool, auth: bool) -> LiveTrader:
        lt = LiveTrader()
        lt._enabled = enabled
        lt._paused = paused
        lt._auth_verified = auth
        return lt

    def test_all_three_true_active(self):
        assert self._make(True, False, True).is_enabled() is True

    def test_enabled_false_blocks(self):
        assert self._make(False, False, True).is_enabled() is False

    def test_paused_true_blocks(self):
        assert self._make(True, True, True).is_enabled() is False

    def test_auth_not_verified_blocks(self):
        """Phase 49 A-01 invariant — auth-unverified trader MUST refuse."""
        assert self._make(True, False, False).is_enabled() is False


# ═══ toggle pause flip ═════════════════════════════════════════════════


class TestToggle:
    def test_toggle_flips_paused_and_returns_active(self):
        """toggle() returns NEW active state (= !paused). Double-toggle = identity."""
        lt = LiveTrader()
        lt._paused = False
        # 1st toggle: now paused → returns active=False
        assert lt.toggle() is False
        assert lt._paused is True
        # 2nd toggle: now unpaused → returns active=True
        assert lt.toggle() is True
        assert lt._paused is False


# ═══ _maybe_reset_daily day rollover ═══════════════════════════════════


class TestDailyReset:
    def test_same_day_no_reset(self):
        lt = LiveTrader()
        from datetime import datetime, timezone

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        lt._daily_date = today
        lt._daily_pnl = -1.23
        lt._daily_trades = 7
        lt._maybe_reset_daily()
        assert lt._daily_pnl == -1.23
        assert lt._daily_trades == 7

    def test_new_day_resets_counters(self):
        lt = LiveTrader()
        lt._daily_date = "1970-01-01"  # certainly not today
        lt._daily_pnl = -1.23
        lt._daily_trades = 7
        lt._maybe_reset_daily()
        assert lt._daily_pnl == 0.0
        assert lt._daily_trades == 0
        # Date must be updated to today (not still 1970)
        assert lt._daily_date != "1970-01-01"


# ═══ get_status dict shape ═════════════════════════════════════════════


class TestGetStatus:
    def test_keys_present(self, monkeypatch):
        """Handler UI contract — /live status reads these fields.

        If a key disappears (rename/refactor), handler crashes silently.
        Pin the contract here.
        """
        monkeypatch.setenv("POLYGON_WALLET", "0xabcdef0123456789")
        lt = LiveTrader()
        s = lt.get_status()
        expected_keys = {
            "enabled",
            "paused",
            "auth_verified",
            "active",
            "wallet",
            "total_spent",
            "total_pnl",
            "daily_pnl",
            "daily_trades",
            "trade_count",
            "open",
            "open_detail",
            "budget",
            "remaining",
        }
        actual_keys = set(s.keys())
        # Epic 9 post-audit tightening: exact-match pin (replaces prior
        # subset check). Subset let additive growth pass silently, but a
        # rename (drop old + add new) also slipped through because test
        # owners would update expected_keys to match without catching the
        # handler-side breakage. Exact match forces an intentional
        # review whenever the status shape changes.
        assert actual_keys == expected_keys, (
            f"get_status shape drift — missing: {expected_keys - actual_keys}, "
            f"unexpected: {actual_keys - expected_keys}"
        )
        # Wallet display: first6 + '...' + last4
        assert s["wallet"] == "0xabcd...6789"

    def test_wallet_na_when_missing(self, monkeypatch):
        monkeypatch.delenv("POLYGON_WALLET", raising=False)
        s = LiveTrader().get_status()
        assert s["wallet"] == "N/A"


# ═══ LIVE_STRATEGIES whitelist invariant ════════════════════════════════


class TestLiveStrategiesWhitelist:
    def test_whitelist_is_set_with_exactly_3_entries(self):
        """Whitelist is a closed set — new strategies require explicit add.

        Anyone proposing a 4th entry must go through Epic 4 T4.4 parity audit
        (auto_optimizer pause governance). This test locks the current shape.
        """
        assert isinstance(LIVE_STRATEGIES, set)
        assert len(LIVE_STRATEGIES) == 3
        # Canonical members (from Phase 82e governance)
        assert "M_BTC_5m_any_0.92" in LIVE_STRATEGIES
        assert "BTC High-Threshold Pure" in LIVE_STRATEGIES
        assert "AI_F_BTC_5m_up_0.38" in LIVE_STRATEGIES


# ═══ maybe_mirror rejection paths ═══════════════════════════════════════

# These mirror the gate ladder in L243-265 of live_trader.py. Each test
# ensures a single gate rejects and returns None BEFORE _place is called.


@pytest.fixture
def active_trader(monkeypatch):
    """LiveTrader wired with defaults — is_enabled()==True, no open slot."""
    # Make helpers predictable
    monkeypatch.setenv("LIVE_MAX_TRADE", "1.00")
    monkeypatch.setenv("LIVE_MAX_DAILY_LOSS", "1.00")
    monkeypatch.setenv("LIVE_MIN_SIGNAL", "0.75")
    monkeypatch.setenv("LIVE_MIN_ODDS", "0.75")
    # T11.2 [B]: `lt._budget` is now a read-only @property backed by
    # env. Pin LIVE_BUDGET via monkeypatch instead of direct attr write.
    monkeypatch.setenv("LIVE_BUDGET", "1.49")
    lt = LiveTrader()
    lt._enabled = True
    lt._paused = False
    lt._auth_verified = True
    lt._total_spent = 0.0
    lt._daily_pnl = 0.0
    return lt


class TestMaybeMirrorRejections:
    """Each rejection path below returns None and does NOT reach _place."""

    @pytest.mark.asyncio
    async def test_rejects_when_disabled(self, active_trader):
        active_trader._enabled = False  # trip is_enabled
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.90, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_strategy_not_in_whitelist(self, active_trader):
        result = await active_trader.maybe_mirror(
            "NOT_A_LIVE_STRATEGY", 0.90, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_signal_below_threshold(self, active_trader, monkeypatch):
        monkeypatch.setenv("LIVE_MIN_SIGNAL", "0.80")
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.70, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None  # signal 0.70 < threshold 0.80

    @pytest.mark.asyncio
    async def test_rejects_odds_below_threshold(self, active_trader, monkeypatch):
        monkeypatch.setenv("LIVE_MIN_ODDS", "0.80")
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.90, "up", "0xtok", 0.70, "btc-up-5m"
        )
        assert result is None  # odds 0.70 < threshold 0.80

    @pytest.mark.asyncio
    async def test_rejects_on_daily_loss_trip(self, active_trader, monkeypatch):
        monkeypatch.setenv("LIVE_MAX_DAILY_LOSS", "1.00")
        from datetime import datetime, timezone

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        active_trader._daily_date = today  # prevent reset
        active_trader._daily_pnl = -1.50  # past -1.00 cutoff
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.90, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_when_open_slot_held(self, active_trader):
        """Single-slot concurrency guard — ``if self._open: return None``."""
        active_trader._open = {"slug": "other-market"}  # occupied
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.90, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_rejects_when_budget_exhausted(self, active_trader):
        active_trader._total_spent = active_trader._budget - 0.05  # < $0.10 left
        result = await active_trader.maybe_mirror(
            "M_BTC_5m_any_0.92", 0.90, "up", "0xtok", 0.80, "btc-up-5m"
        )
        assert result is None


# ═══ check_settlement slug matching ═════════════════════════════════════


class TestCheckSettlement:
    """Settlement mirror — updates daily/total PnL only if slug matches open."""

    @pytest.mark.asyncio
    async def test_no_open_is_noop(self):
        lt = LiveTrader()
        lt._open = None
        lt._daily_pnl = 0.0
        await lt.check_settlement("some-slug", True, pnl_paper=2.0)
        assert lt._daily_pnl == 0.0  # unchanged

    @pytest.mark.asyncio
    async def test_slug_mismatch_is_noop(self):
        lt = LiveTrader()
        lt._open = {"slug": "btc-up-5m", "amount": 1.00, "entry_odds": 0.80}
        lt._daily_pnl = 0.0
        await lt.check_settlement("other-slug", True, pnl_paper=2.0)
        assert lt._daily_pnl == 0.0  # unchanged
        assert lt._open is not None  # still open

    @pytest.mark.asyncio
    async def test_slug_match_updates_pnl_and_closes_slot(self):
        """1:1 scale (paper_amount=0 → fallback to live_amount) — PnL mirrors exactly."""
        lt = LiveTrader()
        lt._open = {"slug": "btc-up-5m", "amount": 1.00, "entry_odds": 0.80}
        lt._daily_pnl = 0.0
        lt._total_pnl = 0.0
        # db is None → _save_state early-returns, no crash
        lt.db = None
        await lt.check_settlement("btc-up-5m", True, pnl_paper=0.50, paper_amount=0.0)
        # scale = live 1.00 / paper 1.00 = 1.0 → live_pnl = 0.50 * 1.0 = 0.50
        assert lt._daily_pnl == pytest.approx(0.50, abs=0.01)
        assert lt._total_pnl == pytest.approx(0.50, abs=0.01)
        assert lt._open is None  # slot freed
