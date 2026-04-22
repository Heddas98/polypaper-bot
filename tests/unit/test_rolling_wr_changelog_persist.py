"""Unit tests for T11.2 [E] — strategy_changelog cumulative stats persist.

Context
-------
Phase 79b introduced ``strategy_changelog.pnl_at_time`` and
``trades_at_time`` columns. Every writer of a TUNE / SCALE / DELETE /
RESTART action passes those fields (``core.changelog.log_change`` accepts
them as kwargs). But ``core.auto_optimizer._check_rolling_wr``'s
ROLLING_WR_KILL branch only passed ``wr``, leaving pnl / trades NULL.

T11.2 Windows probe (G5, 2026-04-22 23:09 local) confirmed 7 historical
ROLLING_WR_KILL rows had ``pnl_at_time=NULL, trades_at_time=NULL`` and
only ``wr_at_time`` populated. T11.2 [E] fetches cumulative stats via
``_get_strategy_stats`` before ``log_change`` so the audit trail is
complete and AI Brain / post-mortem scripts can render structured stats
without parsing the ``reason`` string.

Scope (pure-logic unit tests)
-----------------------------
1. **Happy path** — kill path passes ``wr``, ``pnl``, ``trades`` to
   ``log_change`` when ``_get_strategy_stats`` returns a real row.
2. **Empty stats fallback** — when ``_get_strategy_stats`` returns
   ``None`` (DB drift / zero-row aggregate), ``cum_pnl`` / ``cum_trades``
   are ``None`` but the kill still fires and the log row is still
   written (graceful).
3. **Protected bypass** — classic strategies never touch ``log_change``
   (this is the existing behaviour; test pins the invariant so
   future refactors to the helper wiring don't regress it).

Out of scope
------------
* Real DB INSERT round-trip — covered implicitly by the changelog
  INSERT schema (Phase 79b) which already accepts these columns.
* ``_notify_paused`` Telegram send — stubbed via ``engine=None``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.auto_optimizer as ao


# ═══════════════════════════════════════════════════════════════════════
# Stubs — minimal async surface area mirroring the production DB class
# ═══════════════════════════════════════════════════════════════════════


class _AsyncCM:
    """Async context-manager wrapper for ``db.conn.execute(...)``."""

    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *exc):
        return False


class _DictRow(dict):
    """Dict subclass — supports ``r["col"]`` access used by
    ``_get_strategy_stats`` row unpacking."""


class _StubConn:
    """Stand-in for ``db.conn``.

    * ``execute_fetchall`` returns the rolling-WR window rows.
    * ``execute`` returns an async CM yielding a cursor whose
      ``fetchone`` returns the ``_get_strategy_stats`` aggregate row.
    """

    def __init__(self, rolling_rows, stats_row):
        self.rolling_rows = rolling_rows
        self.stats_row = stats_row

    async def execute_fetchall(self, sql, params):
        return self.rolling_rows

    def execute(self, sql, params=None):
        cursor = MagicMock()
        cursor.fetchone = AsyncMock(return_value=self.stats_row)
        return _AsyncCM(cursor)

    async def commit(self):
        return None


class _StubDB:
    def __init__(self, strategies, rolling_rows, stats_row):
        self.strategies = strategies
        self.conn = _StubConn(rolling_rows, stats_row)
        self.status_updates: list = []

    async def get_active_strategies(self):
        return list(self.strategies)

    async def update_strategy_status(self, sid, status):
        self.status_updates.append((sid, status))


def _make_strategy(sid="deadbeefcafebabe00000000deadbeef",
                   label="M_BTC_5m",
                   strategy_type="momentum"):
    """Minimal duck-typed strategy for ``_check_rolling_wr``."""
    return SimpleNamespace(
        id=sid,
        label=label,
        strategy_type=strategy_type,
        asset=SimpleNamespace(value="BTC"),
        timeframe=SimpleNamespace(value="5m"),
    )


@pytest.fixture
def rolling_knobs(monkeypatch):
    """Pin rolling-WR env knobs so kill_threshold is deterministic
    across platforms (local .env might raise the bar)."""
    monkeypatch.setenv("ROLLING_WR_WINDOW", "20")
    monkeypatch.setenv("ROLLING_WR_KILL", "40.0")
    monkeypatch.setenv("PROTECTED_STRATEGY_TYPES", "classic")
    yield


# ═══════════════════════════════════════════════════════════════════════
# 1. Happy path — cumulative pnl + trades persisted alongside WR
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rolling_wr_kill_persists_pnl_and_trades(
        rolling_knobs, monkeypatch):
    """Kill path must pass ``wr`` + ``pnl`` + ``trades`` to ``log_change``.

    Setup:
        * 20 pure-loss rolling trades → WR = 0% (below 40% threshold).
        * Cumulative stats: 60 trades, $-12.34 PnL (deliberately
          different from the rolling-window sum of -$10 so we can tell
          them apart).

    Expected:
        * ``update_strategy_status(STOPPED)`` called once.
        * ``log_change`` called with wr=0.0, pnl=-12.34, trades=60.
    """
    s = _make_strategy()
    rolling_rows = [(-0.50,)] * 20  # all losses → WR 0%
    stats_row = _DictRow(trades=60, wins=18, losses=42, pnl=-12.34)

    db = _StubDB([s], rolling_rows, stats_row)
    opt = ao.AutoOptimizer(db, engine=None)

    captured: dict = {}

    async def _fake_log_change(db_, sid, action, source, **kw):
        captured["sid"] = sid
        captured["action"] = action
        captured["source"] = source
        captured.update(kw)

    monkeypatch.setattr("core.changelog.log_change", _fake_log_change)

    await opt._check_rolling_wr()

    # Strategy was stopped exactly once
    from db.models import StrategyStatus
    assert db.status_updates == [(s.id, StrategyStatus.STOPPED)]

    # log_change invoked with expected structured metrics
    assert captured.get("action") == "ROLLING_WR_KILL"
    assert captured.get("source") == "adaptive_optimizer"
    assert captured.get("wr") == pytest.approx(0.0)
    # Cumulative PnL (from stats aggregate), NOT rolling-window sum
    assert captured.get("pnl") == pytest.approx(-12.34)
    assert captured.get("trades") == 60
    # Label passed through so changelog doesn't re-query DB
    assert captured.get("label") == s.label


# ═══════════════════════════════════════════════════════════════════════
# 2. Fallback — empty stats → pnl/trades pass through as None
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rolling_wr_kill_handles_missing_stats(
        rolling_knobs, monkeypatch):
    """When ``_get_strategy_stats`` short-circuits (``r["trades"]==0``
    makes it return ``None``), the kill still fires and ``log_change``
    still gets the action + wr; pnl/trades pass through as ``None``
    (INSERT writes NULL for those columns — matches pre-[E] behaviour
    for those fields, but avoids crashing the kill loop)."""
    s = _make_strategy()
    rolling_rows = [(-0.50,)] * 20
    # trades=0 → _get_strategy_stats falls through → returns None
    stats_row = _DictRow(trades=0, wins=0, losses=0, pnl=0.0)

    db = _StubDB([s], rolling_rows, stats_row)
    opt = ao.AutoOptimizer(db, engine=None)

    captured: dict = {}

    async def _fake_log_change(db_, sid, action, source, **kw):
        captured["action"] = action
        captured.update(kw)

    monkeypatch.setattr("core.changelog.log_change", _fake_log_change)

    await opt._check_rolling_wr()

    # Kill still fires
    from db.models import StrategyStatus
    assert db.status_updates == [(s.id, StrategyStatus.STOPPED)]

    # log_change still invoked — wr present, pnl/trades None
    assert captured.get("action") == "ROLLING_WR_KILL"
    assert captured.get("wr") == pytest.approx(0.0)
    assert captured.get("pnl") is None
    assert captured.get("trades") is None


# ═══════════════════════════════════════════════════════════════════════
# 3. Protected bypass — classic strategies never reach log_change
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rolling_wr_skips_protected(rolling_knobs, monkeypatch):
    """Phase 82e hotfix invariant: classic strategies must be skipped
    entirely — no status update, no log. T11.2 [E] adds a new fetch call
    (_get_strategy_stats) before log_change; we pin that the protected
    bypass still short-circuits BEFORE that fetch so we don't introduce
    a wasted DB round-trip for every classic strategy on every tick."""
    s = _make_strategy(strategy_type="classic")
    rolling_rows = [(-0.50,)] * 20  # would trigger if not protected
    stats_row = _DictRow(trades=60, wins=18, losses=42, pnl=-12.34)

    db = _StubDB([s], rolling_rows, stats_row)
    opt = ao.AutoOptimizer(db, engine=None)

    called: list = []

    async def _fake_log_change(*args, **kwargs):
        called.append(kwargs)

    monkeypatch.setattr("core.changelog.log_change", _fake_log_change)

    await opt._check_rolling_wr()

    # No stop, no log — clean skip
    assert db.status_updates == []
    assert called == []
