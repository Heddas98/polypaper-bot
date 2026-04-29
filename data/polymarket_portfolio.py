"""Polymarket Portfolio — Real wallet read-only integration.

2026-04-29 Aşama 1: Polymarket Proxy cüzdanından canlı veri çekme. Heddas'ın
gerçek pUSD bakiyesi, açık pozisyonlar, portfolio NAV, son trade'ler.

Endpoint topology:
  - CLOB SDK: get_balance_allowance(COLLATERAL)  → pUSD balance + allowance
             get_trades(TradeParams)             → son N trade
  - REST data-api.polymarket.com:
      GET /positions?user={addr}                  → açık pozisyonlar
      GET /value?user={addr}                      → portfolio NAV
      GET /activity?user={addr}                   → user activity feed (opsiyonel)

Polymarket docs:
  - https://docs.polymarket.com/api-reference/core/get-current-positions-for-a-user
  - https://docs.polymarket.com/api-reference/core/get-total-value-of-a-users-positions
  - https://docs.polymarket.com/trading/clients/l2#getbalanceallowance-5
  - Rate limits: positions 200 req/10s, value 200 req/10s, closed 150 req/10s

ENV requirements:
  POLYGON_PRIVATE_KEY        — Rabby PK (signing)
  POLYGON_WALLET             — Polymarket Gnosis Safe Proxy (`funder`)
  CLOB_SIGNATURE_TYPE        — 2 (GNOSIS_SAFE) default

This module is READ-ONLY. Withdraw / transfer / allowance approve operations
will live in a separate `polymarket_actions.py` module (Aşama 2).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger("polypaper.data.polymarket_portfolio")

# Data API base — different from CLOB. See:
# https://docs.polymarket.com/api-reference/introduction#apis
DATA_API_BASE = os.getenv("POLYMARKET_DATA_API", "https://data-api.polymarket.com")
CLOB_HOST = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
HTTP_TIMEOUT = float(os.getenv("PORTFOLIO_HTTP_TIMEOUT", "10.0"))


@dataclass
class PositionRow:
    """Single open position from data-api /positions endpoint."""
    token_id: str
    market_slug: str = ""
    outcome: str = ""           # "Yes"/"No" or "Up"/"Down"
    side: str = ""              # "BUY" or "SELL"
    shares: float = 0.0         # current size
    avg_price: float = 0.0      # cost basis per share
    cost_basis_usd: float = 0.0 # total invested
    cur_price: float = 0.0      # current market price
    cur_value_usd: float = 0.0  # mark-to-market
    pnl_usd: float = 0.0        # unrealized PnL
    pnl_pct: float = 0.0        # unrealized PnL %
    end_date: str = ""          # market resolution time (ISO)


@dataclass
class TradeRow:
    """Trade history entry."""
    trade_id: str
    market_slug: str = ""
    side: str = ""              # BUY / SELL
    role: str = ""              # MAKER / TAKER
    price: float = 0.0
    shares: float = 0.0
    fee_usd: float = 0.0
    status: str = ""            # CONFIRMED / MINED / RETRYING / FAILED
    matched_at: str = ""        # ISO timestamp


@dataclass
class PortfolioSnapshot:
    """Cache-able snapshot of Polymarket Proxy wallet state."""
    fetched_at: str
    user_address: str = ""
    # Balance (from CLOB SDK get_balance_allowance)
    pusd_balance: float = 0.0
    pusd_allowance: float = 0.0
    # Portfolio (from data-api)
    portfolio_value_usd: float = 0.0
    positions_count: int = 0
    positions: list[dict] = field(default_factory=list)
    # Recent trades (from CLOB SDK get_trades)
    recent_trades: list[dict] = field(default_factory=list)
    # Diagnostic
    fetch_errors: list[str] = field(default_factory=list)
    fetch_latency_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _proxy_address() -> str:
    """Funder/Proxy wallet — POLYGON_WALLET (= Polymarket Gnosis Safe Proxy)."""
    return os.getenv("POLYGON_WALLET", "").strip()


async def _http_get_json(client: httpx.AsyncClient, url: str,
                         params: Optional[dict] = None) -> Any:
    """GET JSON from Polymarket data-api with retry on 429."""
    for attempt in range(3):
        try:
            r = await client.get(url, params=params or {}, timeout=HTTP_TIMEOUT)
            if r.status_code == 429:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5 * (attempt + 1))
            logger.debug(f"data-api retry {attempt+1} {url}: {e}")
    return None


async def fetch_balance_allowance(clob_client) -> tuple[float, float, Optional[str]]:
    """Fetch pUSD balance + allowance via CLOB SDK.

    Returns (balance_usd, allowance_usd, error_str_or_None).
    py-clob-client 0.34.6: `BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)`
    dataclass + enum bekler. Raw values USDC.e units (6 decimals) → /1e6.

    Phase D Bulgu 9'dan farklı SDK signature (live_trader hâlâ dict, aynı
    upgrade lazım — Aşama 2'de unify edilecek).
    """
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        loop = asyncio.get_running_loop()
        bal = await loop.run_in_executor(
            None, lambda: clob_client.get_balance_allowance(params)
        )
        # Response dict: {"balance": "...", "allowance": "..."}
        balance = float(bal.get("balance", 0) or 0) / 1e6
        allowance = float(bal.get("allowance", 0) or 0) / 1e6
        return balance, allowance, None
    except ImportError:
        # Older SDK fallback — try dict pattern
        try:
            loop = asyncio.get_running_loop()
            bal = await loop.run_in_executor(
                None,
                lambda: clob_client.get_balance_allowance({"asset_type": "COLLATERAL"})
            )
            balance = float(bal.get("balance", 0) or 0) / 1e6
            allowance = float(bal.get("allowance", 0) or 0) / 1e6
            return balance, allowance, None
        except Exception as e:  # noqa: BLE001
            return 0.0, 0.0, f"balance_allowance dict-fallback: {type(e).__name__}: {e}"
    except (AttributeError, KeyError, ValueError, TypeError) as e:
        return 0.0, 0.0, f"balance_allowance fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return 0.0, 0.0, f"balance_allowance unexpected: {type(e).__name__}: {e}"


async def fetch_positions(user_address: str,
                          http_client: httpx.AsyncClient) -> tuple[list[PositionRow], Optional[str]]:
    """Fetch current open positions from data-api/positions.

    Polymarket data-api returns rich response; we project to PositionRow.
    Rate limit: 200 req/10s. Cache 60s (job interval).
    """
    if not user_address:
        return [], "user_address empty"
    try:
        url = f"{DATA_API_BASE}/positions"
        data = await _http_get_json(http_client, url, {"user": user_address})
        if data is None:
            return [], "positions API returned None"
        if not isinstance(data, list):
            data = data.get("positions", []) if isinstance(data, dict) else []
        rows: list[PositionRow] = []
        for p in data:
            if not isinstance(p, dict):
                continue
            avg = float(p.get("avgPrice", 0) or 0)
            cur = float(p.get("curPrice", 0) or 0)
            shares = float(p.get("size", p.get("shares", 0)) or 0)
            cost = avg * shares
            value = cur * shares
            pnl = value - cost
            pnl_pct = (pnl / cost * 100) if cost > 0.001 else 0.0
            rows.append(PositionRow(
                token_id=str(p.get("asset", p.get("tokenId", p.get("token_id", "")))),
                market_slug=p.get("slug", p.get("eventSlug", "")),
                outcome=p.get("outcome", ""),
                side="BUY",  # data-api returns user's holding side
                shares=shares,
                avg_price=avg,
                cost_basis_usd=cost,
                cur_price=cur,
                cur_value_usd=value,
                pnl_usd=pnl,
                pnl_pct=pnl_pct,
                end_date=p.get("endDate", "") or p.get("end_date_iso", ""),
            ))
        return rows, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return [], f"positions fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"positions unexpected: {type(e).__name__}: {e}"


async def fetch_portfolio_value(user_address: str,
                                http_client: httpx.AsyncClient) -> tuple[float, Optional[str]]:
    """Fetch total portfolio value from data-api/value.

    Returns (value_usd, error_or_None). NAV = mark-to-market sum across all
    open positions (excludes pUSD cash bakiyesi).
    """
    if not user_address:
        return 0.0, "user_address empty"
    try:
        url = f"{DATA_API_BASE}/value"
        data = await _http_get_json(http_client, url, {"user": user_address})
        if data is None:
            return 0.0, "value API returned None"
        # Response shape: [{"user": "0x...", "value": 12.34}] or {"value": ...}
        if isinstance(data, list) and data:
            value = float(data[0].get("value", 0) or 0)
        elif isinstance(data, dict):
            value = float(data.get("value", 0) or 0)
        else:
            return 0.0, f"value API unexpected shape: {type(data).__name__}"
        return value, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return 0.0, f"value fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return 0.0, f"value unexpected: {type(e).__name__}: {e}"


async def fetch_recent_trades(clob_client, limit: int = 20) -> tuple[list[TradeRow], Optional[str]]:
    """Fetch recent trades via CLOB SDK get_trades.

    SDK returns list of trade objects. We project to TradeRow.
    """
    try:
        from py_clob_client.clob_types import TradeParams
        loop = asyncio.get_running_loop()
        # Note: SDK get_trades signature varies; pass empty TradeParams for all
        params = TradeParams()
        trades = await loop.run_in_executor(None, lambda: clob_client.get_trades(params))
        if not trades:
            return [], None
        # Polymarket may return list or dict with "trades"
        if isinstance(trades, dict):
            trades = trades.get("trades", []) or trades.get("data", [])
        rows: list[TradeRow] = []
        for t in (trades or [])[:limit]:
            if not isinstance(t, dict):
                continue
            # Field name variants per SDK version
            shares = float(t.get("size", t.get("shares", 0)) or 0)
            price = float(t.get("price", 0) or 0)
            fee_bps = float(t.get("fee_rate_bps", 0) or 0)
            fee_usd = (price * shares * fee_bps) / 10000 if fee_bps else 0.0
            rows.append(TradeRow(
                trade_id=str(t.get("id", t.get("trade_id", ""))),
                market_slug=str(t.get("market", t.get("slug", "")))[:40],
                side=str(t.get("side", "")).upper(),
                role=str(t.get("trader_side", t.get("role", ""))).upper(),
                price=price,
                shares=shares,
                fee_usd=fee_usd,
                status=str(t.get("status", "")),
                matched_at=str(t.get("match_time", t.get("matched_at", ""))),
            ))
        return rows, None
    except ImportError as e:
        return [], f"py-clob-client not installed: {e}"
    except (AttributeError, KeyError, ValueError, TypeError) as e:
        return [], f"trades fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"trades unexpected: {type(e).__name__}: {e}"


def _build_clob_client():
    """Construct authenticated CLOB client from .env. Returns client or None."""
    try:
        from py_clob_client.client import ClobClient
        pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
        wallet = os.getenv("POLYGON_WALLET", "").strip()
        if not pk or not wallet:
            return None
        sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
        client = ClobClient(
            CLOB_HOST,
            key=pk, chain_id=137,
            signature_type=sig_type,
            funder=wallet,
        )
        # Derive L2 creds (idempotent)
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        return client
    except (ImportError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"clob_client build: {type(e).__name__}: {e}")
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"clob_client unexpected: {type(e).__name__}: {e}")
        return None


async def build_snapshot() -> PortfolioSnapshot:
    """Tek seferde tüm Polymarket portfolio verisini çek.

    Returns ``PortfolioSnapshot`` dataclass — cache-friendly (asdict +
    json.dumps OK). fetch_errors listesi her bir alt-çağrının hata
    durumunu taşır; tek bir endpoint fail etse bile partial data döner.
    """
    t0 = datetime.now(timezone.utc)
    user = _proxy_address()
    snap = PortfolioSnapshot(fetched_at=t0.isoformat(), user_address=user)

    if not user:
        snap.fetch_errors.append("POLYGON_WALLET env var empty — fund Proxy first")
        return snap

    clob = _build_clob_client()
    async with httpx.AsyncClient() as http_client:
        # Run independent fetches in parallel
        if clob is not None:
            bal_task = fetch_balance_allowance(clob)
            trades_task = fetch_recent_trades(clob, limit=20)
        else:
            bal_task = None
            trades_task = None
        pos_task = fetch_positions(user, http_client)
        val_task = fetch_portfolio_value(user, http_client)

        results = await asyncio.gather(
            bal_task if bal_task else asyncio.sleep(0, result=(0.0, 0.0, "clob_client unavailable")),
            pos_task,
            val_task,
            trades_task if trades_task else asyncio.sleep(0, result=([], "clob_client unavailable")),
            return_exceptions=True,
        )

        # Balance + allowance
        if isinstance(results[0], tuple) and len(results[0]) == 3:
            snap.pusd_balance, snap.pusd_allowance, err = results[0]
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[0], Exception):
            snap.fetch_errors.append(f"balance: {type(results[0]).__name__}")

        # Positions
        if isinstance(results[1], tuple) and len(results[1]) == 2:
            positions, err = results[1]
            snap.positions = [asdict(p) for p in positions]
            snap.positions_count = len(positions)
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[1], Exception):
            snap.fetch_errors.append(f"positions: {type(results[1]).__name__}")

        # Portfolio value
        if isinstance(results[2], tuple) and len(results[2]) == 2:
            snap.portfolio_value_usd, err = results[2]
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[2], Exception):
            snap.fetch_errors.append(f"value: {type(results[2]).__name__}")

        # Recent trades
        if isinstance(results[3], tuple) and len(results[3]) == 2:
            trades, err = results[3]
            snap.recent_trades = [asdict(t) for t in trades]
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[3], Exception):
            snap.fetch_errors.append(f"trades: {type(results[3]).__name__}")

    snap.fetch_latency_ms = int(
        (datetime.now(timezone.utc) - t0).total_seconds() * 1000
    )
    return snap
