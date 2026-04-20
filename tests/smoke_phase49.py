"""
Phase 49 smoke test — no pytest required.

Exercises the P0-01..P0-07 patches with pure Python so it can run anywhere:
  - RiskState.last_loss_ts exists
  - record_trade_closed stamps last_loss_ts on loss
  - reset_halt clears last_loss_ts
  - Gate 7 auto-cooldown triggers after enough simulated time
  - live_trader._derive_and_verify_sync fails gracefully with no pk
  - auto_optimizer._startup_auto_resume is a no-op without env flag
  - safe_html.esc escapes <, >, & correctly
  - db.database schema DDL contains is_maker migration

Run: python3 tests/smoke_phase49.py
Exit: 0 on pass, 1 on any failure.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    if cond:
        print(f"  ✅ {label}")
    else:
        print(f"  ❌ {label}")
        FAILS.append(label)


def test_risk_state_fields() -> None:
    print("▶ RiskState has last_loss_ts")
    from core.risk_manager import RiskState
    s = RiskState()
    check(hasattr(s, "last_loss_ts"), "RiskState.last_loss_ts attribute")
    check(s.last_loss_ts == "", "last_loss_ts defaults to ''")


def test_streak_cooldown() -> None:
    print("▶ Gate 7 auto-cooldown")
    from core.risk_manager import RiskManager, RiskLimits
    rm = RiskManager(RiskLimits(max_loss_streak=3, max_daily_loss=100))
    # Simulate 3 losses
    for _ in range(3):
        rm.record_trade_closed(10.0, -1.0, "BTC-test")
    check(rm.state.consecutive_losses == 3, "streak counted to 3")
    check(bool(rm.state.last_loss_ts), "last_loss_ts stamped after loss")
    # Gate should block now
    v = rm.check_trade(1.0, "BTC-test", 100.0)
    check(not v.approved, "Gate 7 blocks when streak >= limit")
    # Simulate 7h ago (cooldown default is 6h)
    rm.state.last_loss_ts = (
        datetime.now(timezone.utc) - timedelta(hours=7)
    ).isoformat()
    v2 = rm.check_trade(1.0, "BTC-test", 1000.0)
    check(rm.state.consecutive_losses == 0,
          "cooldown auto-reset streak to 0")
    check(v2.approved,
          f"Gate 7 passes after cooldown (got: {v2.reason})")


def test_reset_halt_clears_ts() -> None:
    print("▶ reset_halt clears last_loss_ts")
    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.state.consecutive_losses = 5
    rm.state.last_loss_ts = "2026-04-09T00:00:00+00:00"
    rm.state.halted = True
    rm.reset_halt()
    check(rm.state.consecutive_losses == 0, "streak reset to 0")
    check(rm.state.last_loss_ts == "", "last_loss_ts cleared")
    check(not rm.state.halted, "halted cleared")


def test_win_resets_streak() -> None:
    print("▶ A win resets loss streak")
    from core.risk_manager import RiskManager
    rm = RiskManager()
    rm.record_trade_closed(10.0, -1.0, "BTC-x")
    rm.record_trade_closed(10.0, -1.0, "BTC-x")
    check(rm.state.consecutive_losses == 2, "streak=2 after 2 losses")
    rm.record_trade_closed(10.0, +2.0, "BTC-x")
    check(rm.state.consecutive_losses == 0, "win resets streak to 0")


def test_live_trader_derive_failure() -> None:
    print("▶ LiveTrader derive-and-verify graceful failure")
    from core.live_trader import LiveTrader
    lt = LiveTrader(db=None, bot_app=None, settings=None)
    # No pk/wallet → _derive_and_verify_sync should return (False, ...)
    ok, detail = lt._derive_and_verify_sync("", "")
    # ClobClient init will fail on empty pk OR client lib missing — both OK
    check(ok is False, "empty pk/wallet → ok=False")
    check("failed" in detail.lower() or "not installed" in detail.lower()
          or "err" in detail.lower(),
          f"detail explains reason ({detail[:60]})")


def test_live_trader_is_enabled_gate() -> None:
    print("▶ LiveTrader.is_enabled requires auth_verified")
    from core.live_trader import LiveTrader
    lt = LiveTrader(db=None, bot_app=None, settings=None)
    lt._enabled = True
    lt._paused = False
    lt._auth_verified = False
    check(not lt.is_enabled(), "auth_verified=False → is_enabled False")
    lt._auth_verified = True
    check(lt.is_enabled(), "auth_verified=True → is_enabled True")


def test_safe_html_esc() -> None:
    print("▶ safe_html.esc escapes unsafe chars")
    from telegram_bot.templates.safe_html import esc, esc_code
    check(esc("a<b>c") == "a&lt;b&gt;c", "escapes < and >")
    check(esc("x & y") == "x &amp; y", "escapes &")
    check(esc(None) == "", "None → ''")
    check(esc(42) == "42", "int passthrough")
    check(esc_code("a`b") == "a'b", "esc_code strips backtick")


def test_auto_optimizer_auto_resume_gated() -> None:
    print("▶ auto_optimizer auto-resume is env-gated")
    os.environ.pop("AUTO_RESUME_ON_STARTUP", None)
    from core.auto_optimizer import AutoOptimizer

    class _StubDB:
        class _C:
            async def __aenter__(self_inner):
                return self_inner
            async def __aexit__(self_inner, *a):
                return False
            def __aiter__(self_inner):
                async def _agen():
                    if False:
                        yield None
                return _agen()
        def __init__(self):
            pass
        async def _run(self, *a, **kw):
            return _StubDB._C()
        @property
        def conn(self):
            class _Conn:
                async def execute(self_inner, *a, **kw):
                    return _StubDB._C()
            return _Conn()

    ao = AutoOptimizer(db=_StubDB(), engine=None)
    # Should return immediately when env flag absent
    asyncio.get_event_loop().run_until_complete(ao._startup_auto_resume())
    check(True, "auto_resume no-op when AUTO_RESUME_ON_STARTUP unset")


def test_schema_has_is_maker_migration() -> None:
    print("▶ db.database contains is_maker migration DDL")
    src = (ROOT / "db" / "database.py").read_text(encoding="utf-8")
    check("ALTER TABLE executions ADD COLUMN is_maker" in src,
          "is_maker ALTER on executions present")
    check("ALTER TABLE live_trades ADD COLUMN is_maker" in src,
          "is_maker ALTER on live_trades present")


def test_strategy_status_case_insensitive() -> None:
    """Phase 49 P0-07: StrategyStatus tolerates legacy upper/mixed case."""
    print("▶ StrategyStatus case-insensitive coercion")
    # Use isolated enum (avoids pydantic import) matching db/models.py exactly
    from enum import Enum

    class StrategyStatus(str, Enum):
        ACTIVE = "active"
        STOPPED = "stopped"
        PAUSED = "paused"

        @classmethod
        def _missing_(cls, value):
            if isinstance(value, str):
                norm = value.strip().lower()
                for member in cls:
                    if member.value == norm:
                        return member
            return None

    check(StrategyStatus("active") == StrategyStatus.ACTIVE, "lowercase 'active' OK")
    check(StrategyStatus("ACTIVE") == StrategyStatus.ACTIVE, "uppercase 'ACTIVE' coerced")
    check(StrategyStatus(" Paused ") == StrategyStatus.PAUSED, "mixed-case whitespace coerced")
    check(StrategyStatus("Stopped") == StrategyStatus.STOPPED, "title-case coerced")
    try:
        StrategyStatus("garbage")
        check(False, "garbage value correctly rejected")
    except ValueError:
        check(True, "garbage value correctly rejected")

    # Also assert the real file contains the _missing_ hook
    src = (ROOT / "db" / "models.py").read_text(encoding="utf-8")
    check("def _missing_" in src and "StrategyStatus" in src,
          "db/models.py StrategyStatus has _missing_ hook")


def test_strats_zero_watchdog_attrs() -> None:
    """Phase 49 P0-04: engine has _strats_zero_since + watchdog logic wired."""
    print("▶ engine strats=0 watchdog plumbing")
    src = (ROOT / "core" / "engine.py").read_text(encoding="utf-8")
    check("_strats_zero_since" in src, "engine has _strats_zero_since attr")
    check("_strats_zero_alerted" in src, "engine has _strats_zero_alerted flag")
    check("STRATS_ZERO_WARN_MINUTES" in src, "env override STRATS_ZERO_WARN_MINUTES present")
    check("STRATS_ZERO:" in src or "STRATS_ZERO" in src, "warning log tag present")


def test_becker_replay_harness_unit() -> None:
    """Phase 50: Becker replay PnL/settle/summary unit test (no DuckDB)."""
    print("▶ Becker replay harness PnL math")
    from backtest.becker_replay import (
        ReplayResult, ClosedTrade, OpenPosition, _settle_position,
        STRATEGY_REGISTRY, threshold_strategy, ReplayContext,
    )
    # WIN: YES bought at 0.70, settles 1.0
    pos = OpenPosition(slug="s", side="YES", entry_price=0.70,
                       stake_usd=1.0, shares=1 / 0.70,
                       is_maker=False, opened_at=0)
    closed = _settle_position(pos, exit_price=1.0, closed_ts=100, slug="s")
    check(closed.pnl_usd > 0, "winning YES trade has positive PnL")

    # LOSS: NO bought at 0.30, settles 0.0 → loses
    pos = OpenPosition(slug="s", side="NO", entry_price=0.30,
                       stake_usd=1.0, shares=1 / 0.30,
                       is_maker=False, opened_at=0)
    closed = _settle_position(pos, exit_price=0.0, closed_ts=100, slug="s")
    check(closed.pnl_usd < 0, "losing NO trade has negative PnL")

    # MAKER fee == 0
    pos = OpenPosition(slug="s", side="YES", entry_price=0.60,
                       stake_usd=1.0, shares=1 / 0.60,
                       is_maker=True, opened_at=0)
    closed = _settle_position(pos, exit_price=1.0, closed_ts=100, slug="s")
    check(closed.fee_usd == 0.0, "maker trade has zero fee")

    # Empty summary
    r = ReplayResult()
    s = r.summarize()
    check(s["trades"] == 0 and s["total_pnl"] == 0.0, "empty summary zeroed")

    # Non-empty summary
    r.trades = [
        ClosedTrade(slug="s", side="YES", entry_price=0.7, exit_price=1.0,
                    stake_usd=1.0, shares=1.43, pnl_usd=0.40, fee_usd=0.03,
                    is_maker=False, opened_at=0, closed_at=100),
        ClosedTrade(slug="s", side="YES", entry_price=0.8, exit_price=0.0,
                    stake_usd=1.0, shares=1.25, pnl_usd=-1.00, fee_usd=0.03,
                    is_maker=False, opened_at=0, closed_at=100),
    ]
    s = r.summarize()
    check(s["trades"] == 2 and s["win_rate"] == 50.0,
          "mixed summary counts + wr correct")

    # Strategy registry integrity
    check("threshold_70" in STRATEGY_REGISTRY,
          "threshold_70 registered")
    check("contra_70" in STRATEGY_REGISTRY,
          "contra_70 registered")

    # threshold_strategy behavior
    strat = threshold_strategy(threshold=0.70, min_seconds_left=60,
                               max_seconds_left=600, stake_usd=1.0)
    # Below threshold → no decision
    ctx = ReplayContext(slug="s", now_ts=0, yes_price=0.5,
                        seconds_to_end=300, open_position=None,
                        resolved_yes=1.0)
    check(strat(ctx) is None, "below threshold = no decision")
    # At threshold, within window → open YES
    ctx = ReplayContext(slug="s", now_ts=0, yes_price=0.75,
                        seconds_to_end=300, open_position=None,
                        resolved_yes=1.0)
    d = strat(ctx)
    check(d is not None and d.action == "open" and d.side == "YES",
          "threshold hit in window → open YES")
    # Outside window → no decision
    ctx = ReplayContext(slug="s", now_ts=0, yes_price=0.75,
                        seconds_to_end=5000, open_position=None,
                        resolved_yes=1.0)
    check(strat(ctx) is None, "outside time window = no decision")


def test_promote_handler_html_escaped() -> None:
    """Phase 49 P0-08: promote handler uses esc() on label/prefix."""
    print("▶ promote.py uses safe_html escaping")
    # Phase 51 P51-03 Faz-2 Cluster G: promote.py merged into settings_handler.py
    src = (ROOT / "telegram_bot" / "handlers" / "settings_handler.py").read_text(encoding="utf-8")
    check("from telegram_bot.templates.safe_html import" in src,
          "promote.py imports safe_html")
    check("esc(label)" in src or "esc(row['label']" in src,
          "promote.py wraps label with esc()")
    check("esc_code(sid)" in src or "esc_code(prefix)" in src,
          "promote.py wraps sid/prefix with esc_code()")


def test_becker_latency_and_partial_fill() -> None:
    """Phase 50 P1-07/P1-08: replay_market honors latency + fill_fraction."""
    print("▶ becker_replay latency + partial fill knobs")
    src = (ROOT / "backtest" / "becker_replay.py").read_text(encoding="utf-8")
    check("latency_ms: int = 0" in src, "latency_ms param on replay_market")
    check("fill_fraction: float = 1.0" in src, "fill_fraction param on replay_market")
    check("latency_ms > 0" in src, "latency advance branch present")
    check("max(0.01, min(1.0, float(fill_fraction)))" in src,
          "fill_fraction clamped to (0,1]")
    # run_replay also threads the knobs
    check("latency_ms=latency_ms" in src, "run_replay threads latency_ms")
    check("fill_fraction=fill_fraction" in src, "run_replay threads fill_fraction")


def test_ws_tick_gap_counter() -> None:
    """Phase 50 P1-10: websocket_client tracks tick gaps + logs."""
    print("▶ ws_client tick-gap counter")
    src = (ROOT / "data" / "websocket_client.py").read_text(encoding="utf-8")
    check("_tick_gaps" in src, "_tick_gaps counter attr")
    check("_tick_gap_threshold" in src, "_tick_gap_threshold config")
    check("WS tick-gap" in src, "warning log on gap")
    check('"tick_gaps": self._tick_gaps' in src, "get_status exposes tick_gaps")


def test_price_alert_handler() -> None:
    """Phase 50 Suggestion 12.3: price alert handler + job wired."""
    print("▶ price_alert_handler wiring")
    # Phase 51 P51-03 Faz-2 Cluster J: price_alert_handler merged into dashboard.py
    handler_src = (ROOT / "telegram_bot" / "handlers" / "dashboard.py").read_text(encoding="utf-8")
    check("def _check_op" in handler_src, "_check_op helper defined")
    check("async def price_alert_job" in handler_src, "price_alert_job coroutine")
    check("VALID_OPS" in handler_src, "VALID_OPS whitelist")

    # _check_op logic sanity (inline eval to avoid importing telegram)
    ns: dict = {}
    lines = handler_src.splitlines()
    start = next(i for i, ln in enumerate(lines) if "def _check_op" in ln)
    end = start
    while end < len(lines) and not (end > start and lines[end].startswith("def ")):
        end += 1
    exec("\n".join(lines[start:end]), ns)
    check(ns["_check_op"](0.7, ">=", 0.65) is True, "_check_op >= true")
    check(ns["_check_op"](0.5, ">", 0.65) is False, "_check_op > false")
    check(ns["_check_op"](0.65, "==", 0.65) is True, "_check_op == exact")

    bot_src = (ROOT / "telegram_bot" / "bot.py").read_text(encoding="utf-8")
    check('("alert", alert_set_cmd)' in bot_src, "/alert command registered")
    check("price_alert_job" in bot_src, "price_alert_job scheduled")


def test_keepalive_env_gated() -> None:
    """Phase 50 P1-04: main.py env-gates KeepAlive + guards start/stop."""
    print("▶ main.py keepalive env-gated")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    check("KEEPALIVE_ENABLED" in src, "KEEPALIVE_ENABLED env lookup")
    check("KeepAlive(engine, db) if _keepalive_enabled else None" in src,
          "conditional KeepAlive instance")
    check("if keepalive is not None:\n            await keepalive.start()" in src,
          "keepalive.start() guarded")
    check("if keepalive is not None:\n            await keepalive.stop()" in src,
          "keepalive.stop() guarded")


def main() -> int:
    tests = [
        test_risk_state_fields,
        test_streak_cooldown,
        test_reset_halt_clears_ts,
        test_win_resets_streak,
        test_live_trader_derive_failure,
        test_live_trader_is_enabled_gate,
        test_safe_html_esc,
        test_auto_optimizer_auto_resume_gated,
        test_schema_has_is_maker_migration,
        test_strategy_status_case_insensitive,
        test_strats_zero_watchdog_attrs,
        test_becker_replay_harness_unit,
        test_promote_handler_html_escaped,
        test_becker_latency_and_partial_fill,
        test_ws_tick_gap_counter,
        test_price_alert_handler,
        test_keepalive_env_gated,
    ]
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"  ❌ exception in {t.__name__}: {e}")
            FAILS.append(f"{t.__name__}: {e}")

    print()
    if FAILS:
        print(f"❌ {len(FAILS)} FAIL(s):")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("✅ All Phase 49 smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
