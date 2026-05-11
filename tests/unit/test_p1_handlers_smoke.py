"""
P1-01-c2 (2026-05-09) — Smoke tests for handlers added in P0-07-f / P0-08-E7
/ P1-09-c. These handlers had ~7-10% coverage in baseline (no real
exercises, only import-loops). This module bumps each to ~50-70%.

Pattern: stub aiosqlite-backed db, stub Telegram Update + Context,
exercise async command, capture reply_text payload, assert key fragments.
"""

from __future__ import annotations

import sqlite3
import tempfile
import time
from datetime import UTC, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# ── shared fixtures ─────────────────────────────────────────────────────


class _StubMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, parse_mode=None, **kwargs):
        self.replies.append({"text": text, "parse_mode": parse_mode})


def _make_update():
    msg = _StubMessage()
    return SimpleNamespace(message=msg), msg


# ── data_status_handler ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_data_status_no_db():
    from telegram_bot.handlers.data_status_handler import data_status_command

    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={})  # no db
    await data_status_command(update, ctx)
    assert len(msg.replies) == 1
    assert "DB" in msg.replies[0]["text"] or "veri" in msg.replies[0]["text"].lower()


@pytest.mark.asyncio
async def test_data_status_full_panel():
    """Real DB with all v18 tables — exercise main render path."""
    import aiosqlite

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()

    # Minimal v18 schema (subset for handler)
    sync = sqlite3.connect(db_file.name)
    sync.executescript("""
    CREATE TABLE ob_deltas (ts_ms INTEGER, asset_id TEXT, side TEXT, price REAL, size REAL);
    CREATE TABLE public_trades (ts_ms INTEGER, asset_id TEXT, taker_side TEXT, price REAL, size REAL);
    CREATE TABLE external_prices (ts_ms INTEGER, symbol TEXT, source TEXT, price REAL,
        PRIMARY KEY (ts_ms, symbol, source));
    CREATE TABLE ob_snapshots (ts_ms INTEGER, asset_id TEXT, hash TEXT);
    CREATE TABLE candles_ext (open_ts INTEGER, symbol TEXT, asset TEXT, timeframe TEXT,
        o REAL, h REAL, l REAL, c REAL, v REAL);
    CREATE TABLE candles_poly (open_ts INTEGER, asset_id TEXT, slug TEXT,
        asset TEXT, timeframe TEXT, o REAL, h REAL, l REAL, c REAL, v REAL);
    """)
    now_ms = int(time.time() * 1000)
    sync.execute(
        "INSERT INTO external_prices VALUES (?,?,?,?)",
        (now_ms - 5000, "BTCUSD", "binance_spot_ws", 102000.0),
    )
    sync.commit()
    sync.close()

    db_conn = await aiosqlite.connect(db_file.name)
    db = SimpleNamespace(conn=db_conn, db_path=db_file.name)
    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={"db": db, "settings": None})

    from telegram_bot.handlers.data_status_handler import data_status_command

    await data_status_command(update, ctx)

    assert len(msg.replies) == 1
    text = msg.replies[0]["text"]
    # Panel must mention key sections
    assert any(kw in text for kw in ("Backtest", "Veri", "DB", "external_prices", "ob_deltas"))
    await db_conn.close()
    Path(db_file.name).unlink(missing_ok=True)


# ── ref_audit_handler ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ref_audit_no_db():
    from telegram_bot.handlers.ref_audit_handler import ref_audit_command

    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={})
    await ref_audit_command(update, ctx)
    assert any("DB" in r["text"] or "yok" in r["text"].lower() for r in msg.replies)


@pytest.mark.asyncio
async def test_ref_audit_empty():
    """Empty audit table → 'no entries' branch."""
    import aiosqlite

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    sync = sqlite3.connect(db_file.name)
    sync.executescript("""
    CREATE TABLE reference_price_audit (
        settle_ts_ms INTEGER NOT NULL, condition_id TEXT NOT NULL,
        asset_id TEXT, slug TEXT, asset TEXT, timeframe TEXT,
        official_resolution_price REAL,
        bot_binance_rest_price REAL, bot_binance_ws_price REAL,
        bot_chainlink_price REAL,
        dev_binance_bps REAL, dev_chainlink_bps REAL,
        settle_outcome TEXT, data_quality TEXT NOT NULL DEFAULT 'ok',
        created_at TEXT NOT NULL,
        PRIMARY KEY (condition_id, settle_ts_ms));
    """)
    sync.commit()
    sync.close()
    db_conn = await aiosqlite.connect(db_file.name)
    db = SimpleNamespace(conn=db_conn)
    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={"db": db})
    from telegram_bot.handlers.ref_audit_handler import ref_audit_command

    await ref_audit_command(update, ctx)
    assert len(msg.replies) == 1
    assert "Reference Price Audit" in msg.replies[0]["text"]
    assert "audit kaydi yok" in msg.replies[0]["text"]
    await db_conn.close()
    Path(db_file.name).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_ref_audit_with_data():
    """Populated audit table → bias table + worst-3 + alarm branches."""
    import aiosqlite

    db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_file.close()
    sync = sqlite3.connect(db_file.name)
    sync.executescript("""
    CREATE TABLE reference_price_audit (
        settle_ts_ms INTEGER NOT NULL, condition_id TEXT NOT NULL,
        asset_id TEXT, slug TEXT, asset TEXT, timeframe TEXT,
        official_resolution_price REAL,
        bot_binance_rest_price REAL, bot_binance_ws_price REAL,
        bot_chainlink_price REAL,
        dev_binance_bps REAL, dev_chainlink_bps REAL,
        settle_outcome TEXT, data_quality TEXT NOT NULL DEFAULT 'ok',
        created_at TEXT NOT NULL,
        PRIMARY KEY (condition_id, settle_ts_ms));
    """)
    now = int(time.time() * 1000)
    iso = datetime.fromtimestamp(now / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [
        # Healthy BTC: ~0.1 bps
        (
            now - 3600000,
            "btc-1",
            "0xt",
            "btc-1",
            "BTC",
            "5m",
            100000.0,
            None,
            100001.0,
            None,
            0.10,
            None,
            "UP",
            "ok",
            iso,
        ),
        (
            now - 7200000,
            "btc-2",
            "0xt",
            "btc-2",
            "BTC",
            "5m",
            101000.0,
            None,
            101000.5,
            None,
            0.05,
            None,
            "DOWN",
            "ok",
            iso,
        ),
        # Bad ETH: 10 bps systemic bias
        (
            now - 1800000,
            "eth-1",
            "0xt",
            "eth-1",
            "ETH",
            "15m",
            3500.0,
            None,
            3503.5,
            None,
            10.0,
            None,
            "UP",
            "ok",
            iso,
        ),
        (
            now - 5400000,
            "eth-2",
            "0xt",
            "eth-2",
            "ETH",
            "15m",
            3505.0,
            None,
            3508.5,
            None,
            9.99,
            None,
            "DOWN",
            "ok",
            iso,
        ),
    ]
    sync.executemany(
        "INSERT INTO reference_price_audit VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    sync.commit()
    sync.close()
    db_conn = await aiosqlite.connect(db_file.name)
    db = SimpleNamespace(conn=db_conn)
    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={"db": db})
    from telegram_bot.handlers.ref_audit_handler import ref_audit_command

    await ref_audit_command(update, ctx)
    text = msg.replies[0]["text"]
    assert "Toplam: <b>4</b>" in text
    assert "Worst 3 deviations" in text
    assert "ETH/15m/binance" in text
    # 10 bps systemic bias triggers RED alarm
    assert "EDGE ESTIMATE INVALID" in text
    await db_conn.close()
    Path(db_file.name).unlink(missing_ok=True)


# ── recon_handler ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recon_no_engine():
    from telegram_bot.handlers.recon_handler import recon_command

    update, msg = _make_update()
    ctx = SimpleNamespace(bot_data={})  # no engine
    await recon_command(update, ctx)
    assert "Engine baglantisi yok" in msg.replies[0]["text"]


@pytest.mark.asyncio
async def test_recon_no_task():
    from telegram_bot.handlers.recon_handler import recon_command

    update, msg = _make_update()
    engine = SimpleNamespace(recon_task=None)
    ctx = SimpleNamespace(bot_data={"engine": engine})
    await recon_command(update, ctx)
    text = msg.replies[0]["text"]
    assert "Reconciliation" in text
    assert "Task hic baslatilmamis" in text or "wire hatasi" in text


@pytest.mark.asyncio
async def test_recon_running(monkeypatch):
    """Active task with mismatches → full panel render."""
    monkeypatch.setenv("LIVE_ENABLED", "true")
    monkeypatch.delenv("RECON_ENABLED", raising=False)

    from core.reconciliation.onchain_sync import ReconciliationTask

    task = ReconciliationTask(db=None, wallet="0xA7e75855AAaa1234")
    # Inject some history without actually running the loop
    task._mismatches = [
        {"ts": "2026-05-09T12:00:00Z", "delta_usd": 1.5, "onchain_pusd": 9.5, "db_pusd": 11.0},
        {"ts": "2026-05-09T13:00:00Z", "delta_usd": -2.0, "onchain_pusd": 8.0, "db_pusd": 10.0},
    ]
    task._last_check_ts = time.time() - 60

    update, msg = _make_update()
    engine = SimpleNamespace(recon_task=task)
    ctx = SimpleNamespace(bot_data={"engine": engine})
    from telegram_bot.handlers.recon_handler import recon_command

    await recon_command(update, ctx)
    text = msg.replies[0]["text"]
    assert "Reconciliation" in text
    assert "wallet" in text
    assert "2 mismatch" in text
    assert "2026-05-09T12:00:00Z" in text


@pytest.mark.asyncio
async def test_recon_disabled(monkeypatch):
    """Paper mode with no override → DISABLED status."""
    monkeypatch.delenv("LIVE_ENABLED", raising=False)
    monkeypatch.delenv("RECON_ENABLED", raising=False)
    from core.reconciliation.onchain_sync import ReconciliationTask

    task = ReconciliationTask(db=None, wallet="0xtest")
    update, msg = _make_update()
    engine = SimpleNamespace(recon_task=task)
    ctx = SimpleNamespace(bot_data={"engine": engine})
    from telegram_bot.handlers.recon_handler import recon_command

    await recon_command(update, ctx)
    text = msg.replies[0]["text"]
    assert "DISABLED" in text
    assert "No mismatches" in text or "session" in text
