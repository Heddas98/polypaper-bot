"""
Phase 47f.10 P4#17 — ob_trades Historical Backfill
===================================================
Fetches historical trades for tracked markets from the Polymarket
CLOB /data/trades endpoint (requires L2 auth) and inserts size/side rows
missing from ob_trades (event_type='trade').

Phase 48 update: endpoint now returns 401 for unsigned requests. We now
use py-clob-client with the live trader's ApiCreds (same env vars as
core/live_trader.py). Falls back gracefully if creds are missing.

Runs STANDALONE (not inside the bot loop). Safe to run while the bot
is live because we INSERT OR IGNORE and use a deduped (ts_ms, token_id)
gate via an anti-duplicate SELECT.

Usage:
    py -3.11 scripts/backfill_ob_trades.py --days 7
    py -3.11 scripts/backfill_ob_trades.py --slug btc-up-down-7pm-et-2026-04-09

Env:
    DATABASE_PATH=data_store/polypaper.db
    POLYGON_PRIVATE_KEY + POLYGON_WALLET + POLYMARKET_API_KEY
        + POLYMARKET_API_SECRET + POLYMARKET_PASSPHRASE  (for L2 auth)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import aiosqlite

# Load .env from project root so CLOB creds are available
try:
    from dotenv import load_dotenv

    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(_here)
    load_dotenv(os.path.join(_root, ".env"))
except ImportError:
    pass

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backfill.ob_trades")

DB_PATH = os.getenv("DATABASE_PATH", "polypaper.db")


def _build_clob_client():
    """Build an authenticated py-clob-client. Returns None if creds missing.

    Phase 48: instead of trusting the stored POLYMARKET_API_* triplet
    (which may be stale / created for a different wallet), derive fresh
    L2 creds on the fly via `create_or_derive_api_creds()`. This method
    is idempotent on the Polymarket side — it returns existing creds
    for the wallet if present, or creates new ones.
    """
    pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
    wallet = os.getenv("POLYGON_WALLET", "").strip()
    missing = [
        k
        for k, v in [
            ("POLYGON_PRIVATE_KEY", pk),
            ("POLYGON_WALLET", wallet),
        ]
        if not v
    ]
    if missing:
        logger.error(
            f"Missing wallet creds: {', '.join(missing)} — "
            "cannot backfill (endpoint requires L2 auth)"
        )
        return None
    try:
        # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
        from py_clob_client_v2 import ApiCreds, ClobClient, TradeParams  # noqa: F401
    except ImportError:
        logger.error("py-clob-client-v2 not installed or missing required types")
        return None

    client = ClobClient(
        "https://clob.polymarket.com",
        key=pk,
        chain_id=137,
        signature_type=0,
        funder=wallet,
    )

    # Prefer fresh-derived creds over stored ones to sidestep stale triplets
    try:
        # 2026-04-30 P0.11 V2 fix: V1 `_creds` → V2 `_key`
        derived = client.create_or_derive_api_key()
        client.set_api_creds(derived)
        logger.info(
            f"derived L2 creds via wallet {wallet[:10]}... "
            f"key={str(getattr(derived, 'api_key', ''))[:8]}..."
        )
        return client
    except Exception as e:
        logger.warning(f"derive creds failed: {e} — falling back to stored triplet")

    api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
    api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
    api_pass = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
    if not all([api_key, api_secret, api_pass]):
        logger.error("fallback triplet also missing — abort")
        return None
    client.set_api_creds(
        ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_pass,
        )
    )
    return client


def _fetch_trades_sync(client, market_id: str) -> list[dict]:
    """Fetch trades via py-clob-client (blocking call).

    py-clob-client's `get_trades()` expects a `TradeParams` dataclass,
    NOT a dict. Passing a dict raises
    `'dict' object has no attribute 'market'`.
    """
    try:
        # 2026-04-30 P0.11: V1 → V2 migration
        from py_clob_client_v2 import TradeParams

        params = TradeParams(market=market_id)
        trades = client.get_trades(params)
        if isinstance(trades, list):
            return trades
        if isinstance(trades, dict):
            return trades.get("data") or []
        return []
    except Exception as e:
        logger.warning(f"fetch fail market={market_id[:16]}: {e}")
        return []


async def _fetch_trades(client, market_id: str, before_ts: int | None = None) -> list[dict]:
    """Async wrapper around the blocking py-clob-client call."""
    if client is None:
        return []
    return await asyncio.to_thread(_fetch_trades_sync, client, market_id)


async def _get_target_markets(conn: aiosqlite.Connection, slug_filter: str | None) -> list[tuple]:
    """Return [(slug, token_id)] for markets we want to backfill."""
    query = "SELECT DISTINCT slug, token_id FROM ob_trades " "WHERE token_id IS NOT NULL"
    params: tuple = ()
    if slug_filter:
        query += " AND slug = ?"
        params = (slug_filter,)
    async with conn.execute(query, params) as cur:
        return await cur.fetchall()


async def _latest_backfilled_ts(conn: aiosqlite.Connection, token_id: str) -> int:
    async with conn.execute(
        "SELECT COALESCE(MAX(ts_ms), 0) FROM ob_trades " "WHERE token_id=? AND event_type='trade'",
        (token_id,),
    ) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] else 0


async def backfill(days: int, slug_filter: str | None) -> None:
    logger.info(f"ob_trades backfill start — days={days} slug={slug_filter or 'ALL'}")
    cutoff_ms = int((datetime.now(UTC) - timedelta(days=days)).timestamp() * 1000)

    clob_client = _build_clob_client()
    if clob_client is None:
        logger.error("aborting: no authenticated CLOB client available")
        return
    logger.info("CLOB client built (L2 auth)")

    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        markets = await _get_target_markets(conn, slug_filter)
        logger.info(f"target markets: {len(markets)}")
        if not markets:
            logger.warning("no markets matched — exiting")
            return

        total_inserted = 0
        if True:
            client = clob_client
            for slug, token_id in markets:
                latest = await _latest_backfilled_ts(conn, token_id)
                floor_ms = max(latest, cutoff_ms)
                trades = await _fetch_trades(client, token_id)
                if not trades:
                    continue

                inserted = 0
                for t in trades:
                    try:
                        ts_ms = int(t.get("timestamp") or t.get("ts") or 0)
                        if ts_ms <= floor_ms:
                            continue
                        price = float(t.get("price") or 0.0)
                        size = float(t.get("size") or 0.0)
                        side = (t.get("side") or "").lower() or "unknown"
                        if price <= 0 or size <= 0:
                            continue
                        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).isoformat()
                        await conn.execute(
                            """INSERT INTO ob_trades
                               (slug, token_id, direction, price, prev_price,
                                price_change, ts_ms, ts_iso, source,
                                size, side, event_type)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                slug,
                                token_id,
                                "up",
                                price,
                                None,
                                0.0,
                                ts_ms,
                                ts_iso,
                                "backfill",
                                size,
                                side,
                                "trade",
                            ),
                        )
                        inserted += 1
                    except Exception as _ie:
                        logger.debug(f"row fail: {_ie}")
                if inserted:
                    await conn.commit()
                    total_inserted += inserted
                    logger.info(f"  {slug[:32]}: +{inserted} trades")
                # polite pacing
                await asyncio.sleep(0.2)

        logger.info(f"ob_trades backfill done — {total_inserted} rows inserted")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--slug", type=str, default=None)
    args = ap.parse_args()
    try:
        asyncio.run(backfill(args.days, args.slug))
    except KeyboardInterrupt:
        logger.warning("interrupted")
        sys.exit(1)


if __name__ == "__main__":
    main()
