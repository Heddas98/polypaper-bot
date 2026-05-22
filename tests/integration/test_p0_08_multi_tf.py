"""
P0-08 Multi-TF + Event-Driven Data Layer Smoke Tests (2026-05-08)
==================================================================
End-to-end smoke tests covering:
  - P0-08-A: TF_DISCOVERY_MATRIX config layout
  - P0-08-B: Polymarket discovery dispatcher (slug_prefix vs series_id)
  - P0-08-C: Live trader execute_market_order with tf parameter
  - P0-08-D: Slug utils (TF/asset inference for 4 TF formats)
  - P0-08-E2: Schema migration v18 (ob_deltas, public_trades, external_prices)
  - P0-08-E3: Candle aggregation (5m → 15m/1h/24h runtime)
  - P0-08-E4: WSS price_change → ob_deltas + book → ob_snapshots
  - P0-08-E5: WSS last_trade_price → public_trades
  - P0-08-E6: External feed → external_prices persist
  - P0-08-F: MarketSnapshot.timeframe + TF-adaptive PennyContract

These run against a fresh in-memory DB. No external network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

# Ensure repo root in sys.path for direct script + pytest collection
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest


# ════ P0-08-A: Config matrix ═══════════════════════════════════════
def test_a_tf_discovery_matrix_default():
    from config.settings import Settings

    s = Settings()
    assert "5m" in s.TF_DISCOVERY_MATRIX
    assert "15m" in s.TF_DISCOVERY_MATRIX
    assert "1h" in s.TF_DISCOVERY_MATRIX
    assert "24h" in s.TF_DISCOVERY_MATRIX

    assert s.TF_DISCOVERY_MATRIX["5m"]["method"] == "slug_prefix"
    assert s.TF_DISCOVERY_MATRIX["5m"]["assets"] == ["BTC"]

    assert s.TF_DISCOVERY_MATRIX["15m"]["method"] == "slug_prefix"
    assert set(s.TF_DISCOVERY_MATRIX["15m"]["assets"]) == {"BTC", "ETH", "SOL", "XRP"}

    assert s.TF_DISCOVERY_MATRIX["1h"]["method"] == "series_id"
    assert s.TF_DISCOVERY_MATRIX["1h"]["series_map"] == {"BTC": 10114}

    assert s.TF_DISCOVERY_MATRIX["24h"]["method"] == "series_id"
    assert s.TF_DISCOVERY_MATRIX["24h"]["series_map"] == {"BTC": 41}


def test_a_supported_timeframes_property():
    from config.settings import Settings

    s = Settings()
    assert set(s.SUPPORTED_TIMEFRAMES) == {"5m", "15m", "1h", "24h"}
    assert set(s.SUPPORTED_ASSETS) == {"BTC", "ETH", "SOL", "XRP"}


# ════ P0-08-D: Slug utils ═════════════════════════════════════════
def test_d_slug_utils_tf_inference_5m_15m():
    from core.slug_utils import infer_asset_from_slug, infer_tf_from_slug

    assert infer_tf_from_slug("btc-updown-5m-1778268300") == "5m"
    assert infer_asset_from_slug("btc-updown-5m-1778268300") == "BTC"
    assert infer_tf_from_slug("eth-updown-15m-1778268900") == "15m"
    assert infer_asset_from_slug("eth-updown-15m-1778268900") == "ETH"


def test_d_slug_utils_tf_inference_1h_24h():
    from core.slug_utils import infer_asset_from_slug, infer_tf_from_slug

    assert infer_tf_from_slug("bitcoin-up-or-down-may-8-2026-12pm-et") == "1h"
    assert infer_asset_from_slug("bitcoin-up-or-down-may-8-2026-12pm-et") == "BTC"
    assert infer_tf_from_slug("bitcoin-up-or-down-on-may-9-2026") == "24h"
    assert infer_tf_from_slug("bitcoin-up-or-down-on-march-17") == "24h"  # eski format


def test_d_slug_utils_market_dict_tags_priority():
    from core.slug_utils import infer_tf_from_market

    # tags > series > slug
    mkt_tag_daily = {"slug": "x", "tags": [{"slug": "daily"}]}
    assert infer_tf_from_market(mkt_tag_daily) == "24h"

    mkt_series_hourly = {"slug": "y", "series": [{"slug": "btc-up-or-down-hourly"}]}
    assert infer_tf_from_market(mkt_series_hourly) == "1h"

    # Slug fallback
    mkt_legacy = {"slug": "btc-updown-5m-1778268300"}
    assert infer_tf_from_market(mkt_legacy) == "5m"


# ════ P0-08-E2: Schema migration v18 ══════════════════════════════
async def _make_fresh_db():
    """Create temp DB with v18 schema applied."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.unlink(tmp.name)  # Database() will create

    from db.database import Database

    db = Database(tmp.name)
    await db.initialize()
    return db, tmp.name


@pytest.mark.asyncio
async def test_e2_schema_v18_tables_exist():
    db, path = await _make_fresh_db()
    try:
        async with db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {r[0] async for r in cur}
        for t in (
            "ob_deltas",
            "public_trades",
            "external_prices",
            "ob_snapshots",
            "candles_ext",
            "candles_poly",
        ):
            assert t in tables, f"Missing table: {t}"
        async with db.conn.execute("SELECT MAX(version) FROM schema_version") as cur:
            ver = (await cur.fetchone())[0]
        assert ver >= 18, f"Expected schema_version >= 18, got {ver}"
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ════ P0-08-E3: Candle aggregation ════════════════════════════════
@pytest.mark.asyncio
async def test_e3_candle_aggregation_5m_to_1h():
    db, path = await _make_fresh_db()
    try:
        from data.candle_collector import CandleCollector

        cc = CandleCollector(db=db, scanner=None)

        # Insert 12 × 5m candles (1 hour worth)
        base = int(time.time() * 1000) - 60 * 60 * 1000
        rows = []
        for i in range(12):
            ot = base + i * 5 * 60 * 1000
            rows.append(("BTCUSDT", "5m", ot, 100.0 + i, 110.0 + i, 90.0 + i, 105.0 + i, 1000.0))
        await db.conn.executemany(
            """INSERT INTO candles_ext
               (symbol, interval, open_ts, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        await db.conn.commit()

        # Aggregate to 1h
        c1h = await cc.get_ext_candles("BTCUSDT", "1h", limit=5)
        assert len(c1h) >= 1
        # The full 1h bucket should contain 12 5m candles
        full = [c for c in c1h if c.get("_n_5m_periods", 0) == 12]
        if full:
            # First open should be base candle's open, last close should be highest close
            agg = full[0]
            assert agg["high"] == 121.0  # 110 + 11
            assert agg["low"] == 90.0
            assert agg["volume"] == 12000.0
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ════ P0-08-E4: WSS price_change → ob_deltas ══════════════════════
@pytest.mark.asyncio
async def test_e4_wss_price_change_persists_deltas():
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)

        ev = {
            "event_type": "price_change",
            "market": "0xMARKET",
            "price_changes": [
                {
                    "asset_id": "AID1",
                    "price": "0.5",
                    "size": "100",
                    "side": "BUY",
                    "hash": "h1",
                    "best_bid": "0.5",
                    "best_ask": "0.51",
                },
                {
                    "asset_id": "AID2",
                    "price": "0.5",
                    "size": "0",  # level removed
                    "side": "SELL",
                    "hash": "h2",
                    "best_bid": "0.49",
                    "best_ask": "0.5",
                },
            ],
            "timestamp": str(int(time.time() * 1000)),
        }
        ws._handle_price_change_event(ev)
        await ws.flush()  # 2026-05-22: batched writes → explicit flush

        async with db.conn.execute("SELECT COUNT(*), side FROM ob_deltas GROUP BY side") as cur:
            rows = {r[1]: r[0] async for r in cur}
        assert rows.get("BUY") == 1
        assert rows.get("SELL") == 1
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ════ P0-08-E5: WSS last_trade_price → public_trades ══════════════
@pytest.mark.asyncio
async def test_e5_wss_last_trade_price_persists_public_trades():
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)

        ev = {
            "event_type": "last_trade_price",
            "asset_id": "AID",
            "market": "0xMARKET",
            "fee_rate_bps": "7.2",
            "price": "0.456",
            "side": "BUY",
            "size": "219.21",
            "timestamp": str(int(time.time() * 1000)),
        }
        ws._extract_trade(ev)
        await ws.flush()  # 2026-05-22: batched writes → explicit flush

        async with db.conn.execute(
            "SELECT taker_side, price, size, fee_rate_bps FROM public_trades"
        ) as cur:
            rows = [r async for r in cur]
        assert len(rows) == 1
        assert rows[0]["taker_side"] == "BUY"
        assert abs(rows[0]["price"] - 0.456) < 1e-6
        assert abs(rows[0]["fee_rate_bps"] - 7.2) < 1e-6
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ════ P0-08-F: MarketSnapshot.timeframe + TF-adaptive plugin ══════
def test_f_marketsnapshot_has_timeframe_field():
    from core.strategy_plugins import MarketSnapshot

    s = MarketSnapshot()
    assert hasattr(s, "timeframe")
    assert s.timeframe == "5m"  # default

    s2 = MarketSnapshot(timeframe="1h", total_minutes=60.0)
    assert s2.timeframe == "1h"
    assert s2.total_minutes == 60.0


@pytest.mark.skip(
    reason="PennyContractStrategy 2026-05-21 radikal strateji temizliğinde silindi "
    "(test_phase70 TestPennyContract ile aynı; pre-existing stale test)"
)
def test_f_pennycontract_tf_adaptive_threshold():
    """PennyContract too_close_to_close uses ratio, not absolute minutes."""
    from core.strategy_plugins import MarketSnapshot, PennyContractStrategy

    strat = PennyContractStrategy()

    # 1h market, 10 dk kalan = 16% (< 20% threshold) → too_close
    s_1h_close = MarketSnapshot(
        timeframe="1h",
        total_minutes=60.0,
        minutes_remaining=10.0,
        up_odds=0.05,
        threshold=0.10,
        spread=0.02,
    )
    sig = strat.evaluate(s_1h_close)
    assert sig.reason == "too_close_to_close"

    # 1h market, 30 dk kalan = 50% → NOT too_close
    s_1h_ok = MarketSnapshot(
        timeframe="1h",
        total_minutes=60.0,
        minutes_remaining=30.0,
        up_odds=0.05,
        threshold=0.10,
        spread=0.02,
    )
    sig2 = strat.evaluate(s_1h_ok)
    assert sig2.reason != "too_close_to_close"


# ════ P0-08-G: AI Brain BRAIN_SYSTEM TF context ═══════════════════
def test_g_brain_system_has_tf_matrix():
    from core import ai_brain

    assert "TF MATRIX" in ai_brain.BRAIN_SYSTEM
    assert "5m" in ai_brain.BRAIN_SYSTEM
    assert "15m" in ai_brain.BRAIN_SYSTEM
    assert "1h" in ai_brain.BRAIN_SYSTEM
    assert "24h" in ai_brain.BRAIN_SYSTEM
    assert "series_id=10114" in ai_brain.BRAIN_SYSTEM
    assert "series_id=41" in ai_brain.BRAIN_SYSTEM


# ════ 2026-05-22: WS BATCHED WRITES (sessiz mod) ══════════════════
# Per-mesaj commit → buffer + periyodik flush. Kanit: VERI AYNI kalir
# (her event, dogru alanlarla persist), yalniz commit sayisi duser.
class _FakeScannerMeta:
    """get_token_meta sabit metadata dondurur (ob_snapshots asset/tf/slug)."""

    def get_token_meta(self, asset_id):
        return ("BTC", "5m", "btc-updown-5m-123")


async def _count(db, table) -> int:
    async with db.conn.execute(f"SELECT COUNT(*) FROM {table}") as cur:
        row = await cur.fetchone()
    return row[0]


@pytest.mark.asyncio
async def test_batch_book_event_buffered_then_flushed():
    """book event flush ÖNCESİ buffer'da (DB'de 0), flush SONRASI tam satır."""
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)
        ws._handle_book_event(
            {
                "event_type": "book",
                "asset_id": "AIDBOOK",
                "market": "0xCOND",
                "bids": [{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "50"}],
                "asks": [{"price": "0.52", "size": "80"}],
                "timestamp": str(int(time.time() * 1000)),
                "hash": "bookhash",
            }
        )
        # flush ÖNCESİ: buffer'da, DB'de değil
        assert len(ws._buf_snapshots) == 1
        assert await _count(db, "ob_snapshots") == 0

        await ws.flush()

        async with db.conn.execute(
            "SELECT asset_id, condition_id, best_bid, best_ask, mid_price FROM ob_snapshots"
        ) as cur:
            rows = [r async for r in cur]
        assert len(rows) == 1
        assert rows[0]["asset_id"] == "AIDBOOK"
        assert rows[0]["condition_id"] == "0xCOND"
        assert abs(rows[0]["best_bid"] - 0.48) < 1e-6
        assert abs(rows[0]["best_ask"] - 0.52) < 1e-6
        assert abs(rows[0]["mid_price"] - 0.50) < 1e-6
        assert len(ws._buf_snapshots) == 0  # flush sonrası buffer boş
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_batch_data_parity_no_loss():
    """N event → flush → TAM N satır (hiçbiri düşmez, çoğalmaz)."""
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)
        N = 25
        ts = int(time.time() * 1000)
        for i in range(N):
            ws._handle_price_change_event(
                {
                    "event_type": "price_change",
                    "market": "0xC",
                    "timestamp": str(ts + i),
                    "price_changes": [
                        {
                            "asset_id": f"A{i}", "price": "0.5", "size": "10", "side": "BUY",
                            "hash": f"h{i}b", "best_bid": "0.5", "best_ask": "0.51",
                        },
                        {
                            "asset_id": f"A{i}", "price": "0.49", "size": "5", "side": "SELL",
                            "hash": f"h{i}s", "best_bid": "0.49", "best_ask": "0.5",
                        },
                    ],
                }
            )
            ws._handle_book_event(
                {
                    "event_type": "book", "asset_id": f"A{i}", "market": "0xC",
                    "bids": [{"price": "0.48", "size": "100"}],
                    "asks": [{"price": "0.52", "size": "80"}],
                    "timestamp": str(ts + i), "hash": f"bh{i}",
                }
            )
            ws._extract_trade(
                {
                    "event_type": "last_trade_price", "asset_id": f"A{i}", "market": "0xC",
                    "price": "0.5", "size": "3", "side": "BUY", "fee_rate_bps": "7",
                    "timestamp": str(ts + i),
                }
            )

        await ws.flush()

        assert await _count(db, "ob_deltas") == N * 2  # her event 2 delta
        assert await _count(db, "ob_snapshots") == N
        assert await _count(db, "public_trades") == N
        assert ws._flush_count >= 1
        assert ws._rows_persisted == N * 2 + N + N
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_batch_metadata_from_scanner_preserved():
    """Scanner bağlıysa ob_snapshots asset/timeframe/slug doldurulur (NULL bug yok)."""
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)
        ws.attach_scanner(_FakeScannerMeta())
        ws._handle_book_event(
            {
                "event_type": "book", "asset_id": "AIDM", "market": "0xC",
                "bids": [{"price": "0.4", "size": "10"}],
                "asks": [{"price": "0.6", "size": "10"}],
                "timestamp": str(int(time.time() * 1000)), "hash": "h",
            }
        )
        await ws.flush()
        async with db.conn.execute(
            "SELECT asset, timeframe, slug FROM ob_snapshots WHERE asset_id='AIDM'"
        ) as cur:
            row = await cur.fetchone()
        assert row["asset"] == "BTC"
        assert row["timeframe"] == "5m"
        assert row["slug"] == "btc-updown-5m-123"
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_batch_burst_autoflush_no_data_loss():
    """Buffer eşiği aşılınca otomatik flush; sonda explicit flush → tam sayı."""
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)
        ws._flush_max_rows = 5  # düşük eşik → burst tetiklensin
        ts = int(time.time() * 1000)
        for i in range(10):
            ws._extract_trade(
                {
                    "event_type": "last_trade_price", "asset_id": f"B{i}", "market": "0xC",
                    "price": "0.5", "size": "3", "side": "BUY", "fee_rate_bps": "7",
                    "timestamp": str(ts + i),
                }
            )
        await asyncio.sleep(0.1)  # burst flush task'inin koşmasına izin ver
        # eşik aşıldığında en az bir otomatik flush olmalı
        assert await _count(db, "public_trades") >= 5

        await ws.flush()  # kalanı yaz
        assert await _count(db, "public_trades") == 10
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_batch_flush_empty_is_noop():
    """Boş buffer flush → hata yok, satır yok, sayaç artmaz."""
    db, path = await _make_fresh_db()
    try:
        from data.websocket_client import PolymarketWebSocket

        ws = PolymarketWebSocket(db=db)
        await ws.flush()
        assert await _count(db, "ob_deltas") == 0
        assert ws._flush_count == 0
    finally:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass


# ════ P0-08-H: LIVE_STRATEGIES whitelist intact ═══════════════════
def test_h_live_strategies_whitelist_only_5m_baseline():
    from core.live_trader import LIVE_STRATEGIES

    assert "M_BTC_5m_any_0.92" in LIVE_STRATEGIES
    assert "BTC High-Threshold Pure" in LIVE_STRATEGIES
    assert "AI_F_BTC_5m_up_0.38" in LIVE_STRATEGIES
    # New TF combos should NOT be whitelisted yet (paper-only)
    new_tf_strats = [
        s for s in LIVE_STRATEGIES if any(tf in s for tf in ("_15m_", "_1h_", "_24h_"))
    ]
    assert (
        len(new_tf_strats) == 0
    ), f"New TF strategies should require manual whitelist: {new_tf_strats}"


# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Standalone runner — `python tests/integration/test_p0_08_multi_tf.py`
    import sys

    print("Running P0-08 multi-TF smoke tests standalone...\n")
    # Sync tests
    sync_tests = [
        test_a_tf_discovery_matrix_default,
        test_a_supported_timeframes_property,
        test_d_slug_utils_tf_inference_5m_15m,
        test_d_slug_utils_tf_inference_1h_24h,
        test_d_slug_utils_market_dict_tags_priority,
        test_f_marketsnapshot_has_timeframe_field,
        test_f_pennycontract_tf_adaptive_threshold,
        test_g_brain_system_has_tf_matrix,
        test_h_live_strategies_whitelist_only_5m_baseline,
    ]
    # Async tests
    async_tests = [
        test_e2_schema_v18_tables_exist,
        test_e3_candle_aggregation_5m_to_1h,
        test_e4_wss_price_change_persists_deltas,
        test_e5_wss_last_trade_price_persists_public_trades,
    ]
    passed = failed = 0
    for t in sync_tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    loop = asyncio.new_event_loop()
    for t in async_tests:
        try:
            loop.run_until_complete(t())
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    loop.close()
    total = len(sync_tests) + len(async_tests)
    print(f"\n{passed}/{total} pass, {failed} fail")
    sys.exit(0 if failed == 0 else 1)
