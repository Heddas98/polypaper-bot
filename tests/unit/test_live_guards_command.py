"""Unit tests for ``/live_guards`` command (Epic 11 T11.2 [D]).

Scope
-----
Pure-logic tests pinning four invariants of the new 6-guard snapshot
handler (``telegram_bot/handlers/live_guards_handler.py``):

1. **Admin gate** — non-admin callers get the standard ``⛔`` denial
   and the guard section is never rendered. Mirrors T10.2
   authenticated-only doctrine applied across all diagnostic handlers.
2. **Content shape** — admin rendering must include every guard label
   (G1-G6) plus the ``LIVE_ENABLED`` master line so the operator sees
   the full state in one message.
3. **Runtime env re-read** — changing ``LIVE_BUDGET`` between two
   invocations must surface the new value in the second message (T6.1
   parity, now at UI layer).
4. **Engine-absent fallback** — when ``bot_data['engine']`` is missing
   (sandbox / bot bootstrap race), the handler still renders thresholds
   from env, does NOT raise, and does NOT leak an exception traceback.

Out of scope
------------
* Live KillSwitch file I/O — covered by ``tests/unit/test_kill_switch.py``.
* Live WS freshness probe — covered indirectly by
  ``tests/unit/test_ws_stale_sec_env.py``.
* Bot wiring (bot.py import + CommandHandler tuple) — covered by the
  regular bot bootstrap smoke (test_bot_start).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_bot.handlers.live_guards_handler import live_guards_command

# ═══════════════════════════════════════════════════════════════════════
# Test fixtures — minimal Update / Context shims
# ═══════════════════════════════════════════════════════════════════════


def _make_update(user_id: int = 1234):
    """Minimal Update stub — only .effective_user.id and
    .message.reply_text are touched by the handler."""
    reply_mock = AsyncMock()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=reply_mock),
    ), reply_mock


def _make_context(*, admin_id: int | None = 1234, engine=None):
    """Shim ``context.bot_data`` with a Settings stub whose ``is_admin``
    returns True only for ``admin_id``. ``engine=None`` exercises the
    sandbox fallback path."""
    settings = MagicMock()
    settings.is_admin = lambda uid: admin_id is not None and uid == admin_id
    ctx = SimpleNamespace(
        bot_data={
            "settings": settings,
            **({"engine": engine} if engine is not None else {}),
        }
    )
    return ctx


def _make_engine(
    *,
    total_spent=0.0,
    daily_pnl=0.0,
    daily_trades=0,
    ws_age_sec: float | None = None,
    kill_status: dict | None = None,
):
    """Build a fake engine exposing the attributes the handler reads.

    * ``live.get_status()`` → dict mirroring ``LiveTrader.get_status``.
    * ``kill_switch.get_status()`` → dict mirroring ``KillSwitch``.
    * ``scanner.ws._last_msg_ts`` (optional) → drives WS age rendering.
    """
    import time as _time

    live = MagicMock()
    live.get_status = MagicMock(
        return_value={
            "total_spent": total_spent,
            "daily_pnl": daily_pnl,
            "daily_trades": daily_trades,
            "remaining": max(0.0, 1.49 - total_spent),
        }
    )
    kill_switch = MagicMock()
    kill_switch.get_status = MagicMock(
        return_value=kill_status
        or {
            "killed": False,
            "reason": "",
            "file_exists": False,
            "file_path": "data_store/polypaper.stop",
            "memory_flag": False,
        }
    )
    # WS optional — stub only when ws_age_sec is provided
    if ws_age_sec is not None:
        ws = SimpleNamespace(_last_msg_ts=_time.time() - ws_age_sec, is_connected=True)
        scanner = SimpleNamespace(ws=ws)
    else:
        scanner = SimpleNamespace(ws=None)
    return SimpleNamespace(live=live, kill_switch=kill_switch, scanner=scanner)


# ═══════════════════════════════════════════════════════════════════════
# 1. Admin gate — non-admin sees deny only
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_guards_denies_non_admin():
    update, reply_mock = _make_update(user_id=9999)
    # admin_id=1234 but caller is 9999 → deny
    ctx = _make_context(admin_id=1234, engine=_make_engine())
    await live_guards_command(update, ctx)

    assert reply_mock.call_count == 1
    sent_text = reply_mock.call_args.args[0]
    assert "⛔" in sent_text
    # Must NOT render guard sections for non-admin
    assert "Live Guards" not in sent_text
    assert "G1" not in sent_text


@pytest.mark.asyncio
async def test_live_guards_denies_when_settings_missing():
    """Defensive: if Settings is not in bot_data (bot not finished boot
    / misconfig), handler must deny instead of falling through to True.
    """
    update, reply_mock = _make_update(user_id=1234)
    ctx = SimpleNamespace(bot_data={})  # no settings at all
    await live_guards_command(update, ctx)

    assert reply_mock.call_count == 1
    assert "⛔" in reply_mock.call_args.args[0]


# ═══════════════════════════════════════════════════════════════════════
# 2. Content shape — admin sees all 6 guards + master line
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_guards_renders_all_six_guards(monkeypatch):
    # Pin env so the snapshot is deterministic
    monkeypatch.setenv("LIVE_ENABLED", "true")
    monkeypatch.setenv("LIVE_BUDGET", "1.49")
    monkeypatch.setenv("LIVE_MAX_DAILY_LOSS", "1.00")
    monkeypatch.setenv("PNL_DIVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PNL_DIVERGENCE_WINDOW_H", "24")
    monkeypatch.setenv("PNL_DIVERGENCE_ALERT_PCT", "5.0")
    monkeypatch.setenv("PNL_DIVERGENCE_MIN_TRADES", "5")
    monkeypatch.setenv("ROLLING_WR_WINDOW", "20")
    monkeypatch.setenv("ROLLING_WR_KILL", "40.0")
    monkeypatch.setenv("WS_STALE_THRESHOLD", "60.0")

    update, reply_mock = _make_update(user_id=1234)
    ctx = _make_context(admin_id=1234, engine=_make_engine(ws_age_sec=5.0))

    await live_guards_command(update, ctx)

    assert reply_mock.call_count == 1
    call = reply_mock.call_args
    sent_text = call.args[0]
    kwargs = call.kwargs

    # Always HTML parse_mode (feedback_telegram_html)
    assert kwargs.get("parse_mode") == "HTML"

    # Master line present
    assert "LIVE_ENABLED" in sent_text
    # All 6 guards rendered (labels + keys)
    assert "G1 Kill Switch" in sent_text
    assert "G2 Live Budget" in sent_text
    assert "LIVE_BUDGET" in sent_text
    assert "G3 Daily Loss" in sent_text
    assert "LIVE_MAX_DAILY_LOSS" in sent_text
    assert "G4 PnL Divergence" in sent_text
    assert "G5 Rolling WR Kill" in sent_text
    assert "G6 WS Stale" in sent_text
    assert "WS_STALE_THRESHOLD" in sent_text
    # /envt hint so operator knows how to tune
    assert "/envt" in sent_text


# ═══════════════════════════════════════════════════════════════════════
# 3. Runtime env re-read — second call reflects env patch
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_guards_reflects_env_mutation_between_calls(monkeypatch):
    """The core T6.1 invariant at UI level: if /envt LIVE_BUDGET 5.00
    runs between two /live_guards calls, the second rendering must
    show 5.00 — NOT the value present at bot bootstrap time."""
    monkeypatch.setenv("LIVE_BUDGET", "1.49")
    update1, reply1 = _make_update(user_id=1234)
    ctx = _make_context(admin_id=1234, engine=_make_engine())

    await live_guards_command(update1, ctx)
    first_text = reply1.call_args.args[0]
    assert "$1.49" in first_text

    # Simulate /envt LIVE_BUDGET 5.00
    monkeypatch.setenv("LIVE_BUDGET", "5.00")
    update2, reply2 = _make_update(user_id=1234)
    await live_guards_command(update2, ctx)
    second_text = reply2.call_args.args[0]
    assert "$5.00" in second_text
    # And the old value is gone
    assert "LIVE_BUDGET = $1.49" not in second_text


# ═══════════════════════════════════════════════════════════════════════
# 4. Engine-absent fallback — thresholds still render, no crash
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_guards_renders_without_engine(monkeypatch):
    """Sandbox / bot boot race: bot_data has no 'engine' key yet.
    Handler must render env thresholds anyway (so operator can
    double-check /envt patches before engine comes up) and must NOT
    raise.
    """
    monkeypatch.setenv("LIVE_ENABLED", "false")
    monkeypatch.setenv("LIVE_BUDGET", "1.49")

    update, reply_mock = _make_update(user_id=1234)
    ctx = _make_context(admin_id=1234, engine=None)
    # engine key is omitted by _make_context when engine is None

    await live_guards_command(update, ctx)

    sent_text = reply_mock.call_args.args[0]
    # Guards still rendered
    assert "G2 Live Budget" in sent_text
    assert "$1.49" in sent_text
    assert "G5 Rolling WR Kill" in sent_text
    # WS age falls back to 'n/a' when scanner/ws unavailable
    assert "n/a" in sent_text
