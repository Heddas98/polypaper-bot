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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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
    outcome: str = ""  # "Yes"/"No" or "Up"/"Down"
    side: str = ""  # "BUY" or "SELL"
    shares: float = 0.0  # current size
    avg_price: float = 0.0  # cost basis per share
    cost_basis_usd: float = 0.0  # total invested
    cur_price: float = 0.0  # current market price
    cur_value_usd: float = 0.0  # mark-to-market
    pnl_usd: float = 0.0  # unrealized PnL
    pnl_pct: float = 0.0  # unrealized PnL %
    end_date: str = ""  # market resolution time (ISO)
    # 2026-05-05 Heddas redeem flow: market resolution + redemption metadata
    condition_id: str = ""  # CTF conditionId (bytes32 hex) — redeem için
    closed: bool = False  # market.closed = resolved (Polymarket flag)
    is_winner: bool = False  # True if this token = winning outcome
    redeemable: bool = False  # closed + is_winner → eligible for redeem


@dataclass
class TradeRow:
    """Trade history entry."""

    trade_id: str
    market_slug: str = ""
    side: str = ""  # BUY / SELL
    role: str = ""  # MAKER / TAKER
    price: float = 0.0
    shares: float = 0.0
    fee_usd: float = 0.0
    status: str = ""  # CONFIRMED / MINED / RETRYING / FAILED
    matched_at: str = ""  # ISO timestamp


@dataclass
class ActivityRow:
    """Onchain activity entry from data-api/activity.

    type: TRADE | SPLIT | MERGE | REDEEM | REWARD | CONVERSION
    """

    timestamp: int  # unix seconds
    type: str  # TRADE / REDEEM / SPLIT / MERGE / REWARD
    condition_id: str = ""
    transaction_hash: str = ""  # polygonscan link
    title: str = ""  # market title (human readable)
    slug: str = ""
    outcome: str = ""  # "Up" / "Down" / "Yes" / "No"
    outcome_index: int = 0
    side: str = ""  # BUY / SELL (only for TRADE)
    size: float = 0.0  # shares
    price: float = 0.0  # USDC per share
    usdc_size: float = 0.0  # total USDC ($)
    asset: str = ""  # token_id (large int as string)


@dataclass
class ClosedPositionRow:
    """Settled/redeemed/closed position from data-api/closed-positions."""

    condition_id: str = ""
    asset: str = ""
    title: str = ""
    slug: str = ""
    outcome: str = ""
    outcome_index: int = 0
    size: float = 0.0
    avg_price: float = 0.0
    initial_value: float = 0.0
    current_value: float = 0.0
    realized_pnl: float = 0.0  # gerçekleşen kar/zarar
    percent_realized_pnl: float = 0.0
    cash_pnl: float = 0.0
    percent_pnl: float = 0.0
    total_bought: float = 0.0  # toplam alış miktarı (USDC)
    redeemed: bool = False
    end_date: str = ""
    icon: str = ""


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
    # 2026-05-06 Heddas direktifi — Live history detay + closed positions
    closed_positions: list[dict] = field(default_factory=list)
    activity: list[dict] = field(default_factory=list)
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


async def _http_get_json(client: httpx.AsyncClient, url: str, params: Optional[dict] = None) -> Any:
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
        # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
        from py_clob_client_v2 import AssetType, BalanceAllowanceParams

        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        loop = asyncio.get_running_loop()
        bal = await loop.run_in_executor(None, lambda: clob_client.get_balance_allowance(params))
        # 2026-05-05 V2 API fix (Heddas debug session):
        # V1: bal["allowance"] (string), V2: bal["allowances"] (dict per-spender)
        balance = float(bal.get("balance", 0) or 0) / 1e6
        if "allowances" in bal and isinstance(bal["allowances"], dict):
            max_raw = max(
                (int(v or 0) for v in bal["allowances"].values()),
                default=0,
            )
            allowance = float(max_raw) / 1e6
        else:
            allowance = float(bal.get("allowance", 0) or 0) / 1e6
        return balance, allowance, None
    except ImportError:
        # Older SDK fallback — try dict pattern
        try:
            loop = asyncio.get_running_loop()
            bal = await loop.run_in_executor(
                None, lambda: clob_client.get_balance_allowance({"asset_type": "COLLATERAL"})
            )
            balance = float(bal.get("balance", 0) or 0) / 1e6
            if "allowances" in bal and isinstance(bal["allowances"], dict):
                max_raw = max(
                    (int(v or 0) for v in bal["allowances"].values()),
                    default=0,
                )
                allowance = float(max_raw) / 1e6
            else:
                allowance = float(bal.get("allowance", 0) or 0) / 1e6
            return balance, allowance, None
        except Exception as e:  # noqa: BLE001
            return 0.0, 0.0, f"balance_allowance dict-fallback: {type(e).__name__}: {e}"
    except (AttributeError, KeyError, ValueError, TypeError) as e:
        return 0.0, 0.0, f"balance_allowance fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return 0.0, 0.0, f"balance_allowance unexpected: {type(e).__name__}: {e}"


async def fetch_positions(
    user_address: str, http_client: httpx.AsyncClient
) -> tuple[list[PositionRow], Optional[str]]:
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
            # 2026-05-05 Redeem support — condition_id + closed + is_winner
            cid = str(p.get("conditionId", p.get("condition_id", "")))
            closed = bool(p.get("closed", False))
            # Winning detection: cur_price ≈ 1.0 ± 0.001 (resolved winner = $1)
            is_winner = closed and (cur > 0.999)
            redeemable = closed and is_winner and shares > 0
            rows.append(
                PositionRow(
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
                    condition_id=cid,
                    closed=closed,
                    is_winner=is_winner,
                    redeemable=redeemable,
                )
            )
        return rows, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return [], f"positions fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"positions unexpected: {type(e).__name__}: {e}"


async def fetch_activity(
    user_address: str,
    http_client: httpx.AsyncClient,
    limit: int = 100,
    types: Optional[str] = None,
) -> tuple[list[ActivityRow], Optional[str]]:
    """Fetch onchain activity from data-api/activity.

    Polymarket spec: returns TRADE/SPLIT/MERGE/REDEEM/REWARD/CONVERSION events
    with timestamp, conditionId, transactionHash, usdcSize, etc.

    Args:
        user_address: Polymarket Profile/Safe Proxy address
        http_client: httpx async client
        limit: max records (default 100, max 500)
        types: comma-separated filter (e.g. "TRADE,REDEEM")

    Returns: (list[ActivityRow], error_or_None)
    """
    if not user_address:
        return [], "user_address empty"
    try:
        url = f"{DATA_API_BASE}/activity"
        params = {"user": user_address, "limit": min(limit, 500)}
        if types:
            params["type"] = types
        data = await _http_get_json(http_client, url, params)
        if data is None:
            return [], "activity API returned None"
        if not isinstance(data, list):
            data = data.get("activity", []) if isinstance(data, dict) else []

        rows: list[ActivityRow] = []
        for a in data:
            if not isinstance(a, dict):
                continue
            rows.append(
                ActivityRow(
                    timestamp=int(a.get("timestamp", 0) or 0),
                    type=str(a.get("type", "")),
                    condition_id=str(a.get("conditionId", "")),
                    transaction_hash=str(a.get("transactionHash", "")),
                    title=str(a.get("title", "")),
                    slug=str(a.get("slug", "")),
                    outcome=str(a.get("outcome", "")),
                    outcome_index=int(a.get("outcomeIndex", 0) or 0),
                    side=str(a.get("side", "")),
                    size=float(a.get("size", 0) or 0),
                    price=float(a.get("price", 0) or 0),
                    usdc_size=float(a.get("usdcSize", 0) or 0),
                    asset=str(a.get("asset", "")),
                )
            )
        return rows, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return [], f"activity fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"activity unexpected: {type(e).__name__}: {e}"


def compute_live_pnl(
    activity: list[dict],
    since_epoch: int,
    now_epoch: int | None = None,
    settle_grace_sec: int = 900,
) -> dict:
    """Bot LIVE PnL — data-api/activity TRADE+REDEEM event'lerinden hesap.

    2026-05-18 (Heddas direktifi): `live_trader._total_pnl` $0 kalıyordu —
    `check_settlement` yalnız otomatik-mirror (`_open` set eden) trade'leri
    yakalıyor, manuel `/live` trade'lerini değil. Gerçek LIVE PnL bu yüzden
    hiç hesaplanmıyordu. Çözüm: Polymarket'in on-chain `activity` feed'i
    (gerçek kaynak — TRADE = alım maliyeti, REDEEM = kazanan payout).

    Saf fonksiyon — cache'lenmiş `activity` listesini alır, ekstra API/DB
    çağrısı YOK (snapshot zaten periyodik güncelleniyor).

    Args:
        activity: snapshot'ın 'activity' listesi (ActivityRow asdict'leri —
                  type / usdc_size / price / size / timestamp / condition_id).
        since_epoch: unix saniye — bot mainnet başlangıcı (LIVE_START_DATE).
                     Öncesi (operatörün bot-öncesi kişisel geçmişi) ELENİR.
        now_epoch: "şimdi" referansı (test için enjekte edilebilir). None →
                   gerçek zaman.
        settle_grace_sec: bir TRADE'in REDEEM'i hâlâ gelmemişse, trade bu
                          süreden yeni ise market "pending" (settle bekliyor)
                          sayılır, "loss" değil. 5dk BTC market'leri için
                          900s (15dk) güvenli pay — 60s snapshot job'ının
                          in-flight bir trade'i geçici "loss" göstermesini
                          engeller.

    Hesap (market-bazlı filtre — bir market bot-döneminde TRADE edildiyse,
    o market'in TÜM event'leri sayılır; event-bazlı filtre sınırdaki
    market'in trade'ini eler ama redeem'ini sayar → PnL şişer):
        TRADE  → maliyet (usdc_size — share değeri + taker fee dahil)
        REDEEM → payout (usdc_size — kazanan share'lar $1'a redeem, fee yok)
        net_pnl = Σ payout − Σ cost  (gerçekleşen nakit PnL, fee düşülmüş)
        fee     = Σ max(0, usdc_size − price×size)  TRADE'lerde — 2026-05-18
                  9 canlı trade'de `fees_v2.polymarket_taker_fee_v2` ile
                  cent-cent doğrulandı (crypto 0.07×(1−p) modeli).
        win market     = REDEEM almış conditionId
        loss market    = REDEEM almamış + son trade'i grace'ten eski
        pending market = REDEEM almamış + son trade'i grace içinde (settle
                         bekliyor — henüz kazanç/kayıp belli değil)

    Returns: dict (error key yok — saf hesap, exception fırlatmaz).
    """
    if now_epoch is None:
        now_epoch = int(datetime.now(UTC).timestamp())

    def _f(d: dict, k: str) -> float:
        try:
            return float(d.get(k, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _ts(d: dict) -> int:
        try:
            return int(d.get("timestamp", 0) or 0)
        except (TypeError, ValueError):
            return 0

    bot_cids = {
        str(a.get("condition_id", ""))
        for a in activity
        if str(a.get("type", "")).upper() == "TRADE"
        and _ts(a) >= since_epoch
        and a.get("condition_id")
    }
    rel = [a for a in activity if str(a.get("condition_id", "")) in bot_cids]
    trades = [a for a in rel if str(a.get("type", "")).upper() == "TRADE"]
    redeems = [a for a in rel if str(a.get("type", "")).upper() == "REDEEM"]

    cost = sum(_f(t, "usdc_size") for t in trades)
    payout = sum(_f(r, "usdc_size") for r in redeems)
    fee = sum(
        max(0.0, _f(t, "usdc_size") - _f(t, "price") * _f(t, "size"))
        for t in trades
    )

    traded_cids = {str(t.get("condition_id", "")) for t in trades}
    redeemed_cids = {str(r.get("condition_id", "")) for r in redeems}
    no_redeem = traded_cids - redeemed_cids
    # En son trade'i grace içinde olan market'ler hâlâ settle bekliyor —
    # kayıp sayma. Grace'ten eski + redeem yok → gerçek kayıp.
    pending_cids = {
        cid
        for cid in no_redeem
        if max(
            (_ts(t) for t in trades if str(t.get("condition_id", "")) == cid),
            default=0,
        )
        > now_epoch - settle_grace_sec
    }
    loss_cids = no_redeem - pending_cids

    return {
        "trades": len(trades),
        "redeems": len(redeems),
        "markets": len(traded_cids),
        "win_markets": len(traded_cids & redeemed_cids),
        "loss_markets": len(loss_cids),
        "pending_markets": len(pending_cids),
        "cost": round(cost, 4),
        "payout": round(payout, 4),
        "net_pnl": round(payout - cost, 4),
        "fee": round(fee, 4),
        "roi_pct": round((payout - cost) / cost * 100, 2) if cost > 0 else 0.0,
    }


async def fetch_closed_positions(
    user_address: str,
    http_client: httpx.AsyncClient,
    limit: int = 50,
) -> tuple[list[ClosedPositionRow], Optional[str]]:
    """Fetch closed (settled/redeemed) positions from data-api/closed-positions.

    Polymarket spec: positions that have been resolved.
    Returns realized PnL, redemption status.

    Args:
        user_address: Polymarket Profile/Safe Proxy address
        http_client: httpx async client
        limit: max records (default 50, max 500)

    Returns: (list[ClosedPositionRow], error_or_None)
    """
    if not user_address:
        return [], "user_address empty"
    try:
        url = f"{DATA_API_BASE}/closed-positions"
        params = {"user": user_address, "limit": min(limit, 500)}
        data = await _http_get_json(http_client, url, params)
        if data is None:
            return [], "closed-positions API returned None"
        if not isinstance(data, list):
            data = data.get("positions", []) if isinstance(data, dict) else []

        rows: list[ClosedPositionRow] = []
        for p in data:
            if not isinstance(p, dict):
                continue
            rows.append(
                ClosedPositionRow(
                    condition_id=str(p.get("conditionId", "")),
                    asset=str(p.get("asset", "")),
                    title=str(p.get("title", "")),
                    slug=str(p.get("slug", "")),
                    outcome=str(p.get("outcome", "")),
                    outcome_index=int(p.get("outcomeIndex", 0) or 0),
                    size=float(p.get("size", 0) or 0),
                    avg_price=float(p.get("avgPrice", 0) or 0),
                    initial_value=float(p.get("initialValue", 0) or 0),
                    current_value=float(p.get("currentValue", 0) or 0),
                    realized_pnl=float(p.get("realizedPnl", 0) or 0),
                    percent_realized_pnl=float(p.get("percentRealizedPnl", 0) or 0),
                    cash_pnl=float(p.get("cashPnl", 0) or 0),
                    percent_pnl=float(p.get("percentPnl", 0) or 0),
                    total_bought=float(p.get("totalBought", 0) or 0),
                    redeemed=bool(p.get("redeemed", False)),
                    end_date=str(p.get("endDate", "")),
                    icon=str(p.get("icon", "")),
                )
            )
        return rows, None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        return [], f"closed-positions fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"closed-positions unexpected: {type(e).__name__}: {e}"


async def fetch_portfolio_value(
    user_address: str, http_client: httpx.AsyncClient
) -> tuple[float, Optional[str]]:
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
        # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
        from py_clob_client_v2 import TradeParams

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
            rows.append(
                TradeRow(
                    trade_id=str(t.get("id", t.get("trade_id", ""))),
                    market_slug=str(t.get("market", t.get("slug", "")))[:40],
                    side=str(t.get("side", "")).upper(),
                    role=str(t.get("trader_side", t.get("role", ""))).upper(),
                    price=price,
                    shares=shares,
                    fee_usd=fee_usd,
                    status=str(t.get("status", "")),
                    matched_at=str(t.get("match_time", t.get("matched_at", ""))),
                )
            )
        return rows, None
    except ImportError as e:
        return [], f"py-clob-client-v2 not installed: {e}"
    except (AttributeError, KeyError, ValueError, TypeError) as e:
        return [], f"trades fetch: {type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return [], f"trades unexpected: {type(e).__name__}: {e}"


# 2026-04-30 P1.X Cloudflare 403 fix:
# Module-level client cache. Derive sadece 1 kere yapılır; sonraki job
# çağrıları cache'den döner. Cache TTL = CLOB_CLIENT_CACHE_TTL_S (default 3600s).
# Cloudflare 403 alınırsa cache invalidate + 1h cooldown.
_CLOB_CLIENT_CACHE = {
    "client": None,
    "creds": None,
    "fetched_at": 0.0,
    "cooldown_until": 0.0,
}


def _build_clob_client():
    """Construct (or fetch from cache) authenticated CLOB client.

    Cache strategy:
    - First: try SHARED_CREDS_CACHE from core.live_trader (boot derive)
    - Else: local cache hit (1h TTL)
    - Else: derive (Cloudflare risk) → cache or cooldown

    Returns: client or None
    """
    import time

    now = time.time()
    ttl = int(os.getenv("CLOB_CLIENT_CACHE_TTL_S", "3600"))

    # Cooldown active (Cloudflare 403 backoff)
    if _CLOB_CLIENT_CACHE["cooldown_until"] > now:
        remaining = int(_CLOB_CLIENT_CACHE["cooldown_until"] - now)
        logger.debug(f"clob_client: cooldown active ({remaining}s remaining)")
        return None

    # Local cache hit
    if _CLOB_CLIENT_CACHE["client"] is not None:
        age = now - _CLOB_CLIENT_CACHE["fetched_at"]
        if age < ttl:
            return _CLOB_CLIENT_CACHE["client"]
        # Expired
        _CLOB_CLIENT_CACHE["client"] = None
        _CLOB_CLIENT_CACHE["creds"] = None

    # 2026-04-30 P1.X: Cross-module shared cache (live_trader boot derive)
    # check ÖNCE — kendimiz derive etmek zorunda kalmayalım, Cloudflare 403 risk yok.
    try:
        from core.live_trader import get_shared_creds

        shared_creds, shared_ts = get_shared_creds()
        if shared_creds and (now - shared_ts) < ttl:
            try:
                from py_clob_client_v2 import ClobClient as _CC

                pk_local = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
                wallet_local = os.getenv("POLYGON_WALLET", "").strip()
                sig_local = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
                if pk_local and wallet_local:
                    _client = _CC(
                        CLOB_HOST,
                        key=pk_local,
                        chain_id=137,
                        signature_type=sig_local,
                        funder=wallet_local,
                    )
                    _client.set_api_creds(shared_creds)
                    _CLOB_CLIENT_CACHE["client"] = _client
                    _CLOB_CLIENT_CACHE["creds"] = shared_creds
                    _CLOB_CLIENT_CACHE["fetched_at"] = now
                    logger.debug(
                        f"clob_client: REUSED shared cache from live_trader "
                        f"(age={int(now-shared_ts)}s, key={str(getattr(shared_creds,'api_key',''))[:8]}...)"
                    )
                    return _client
            except Exception as _share_err:  # noqa: BLE001
                logger.debug(f"clob_client: shared cache reuse fail: {_share_err}")
    except ImportError:
        pass

    try:
        # 2026-04-30 P0.11: V1 → V2 migration (Heddas direktifi "en güncel ol")
        from py_clob_client_v2 import ClobClient

        pk = os.getenv("POLYGON_PRIVATE_KEY", "").strip()
        wallet = os.getenv("POLYGON_WALLET", "").strip()
        if not pk or not wallet:
            return None
        sig_type = int(os.getenv("CLOB_SIGNATURE_TYPE", "2"))
        client = ClobClient(
            CLOB_HOST,
            key=pk,
            chain_id=137,
            signature_type=sig_type,
            funder=wallet,
        )
        # 2026-04-30 P1.X v2: Stored ENV creds (eski Phase A, V1 ile derive)
        # V2 ile incompatible (verify 401). Direkt derive yap, verify et,
        # başarısızsa stored last-resort fallback.
        # 2026-04-30 P0.11 V2 fix: V1 `_creds` → V2 `_key`
        try:
            creds = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            # Verify cheap call (401 verify fail varsa düşer)
            try:
                from py_clob_client_v2 import TradeParams as _TP

                _ = client.get_trades(_TP())
            except Exception as _verify_err:  # noqa: BLE001
                logger.warning(
                    f"clob_client derive verify warn ({type(_verify_err).__name__}); cache anyway"
                )
            _CLOB_CLIENT_CACHE["client"] = client
            _CLOB_CLIENT_CACHE["creds"] = creds
            _CLOB_CLIENT_CACHE["fetched_at"] = now
            logger.debug(
                f"clob_client: derived & cached (key={str(getattr(creds,'api_key',''))[:8]}..., TTL {ttl}s)"
            )
            return client
        except Exception as derive_err:  # noqa: BLE001
            err_str = str(derive_err)
            # Cloudflare 403 → 1h cooldown
            if "403" in err_str or "Cloudflare" in err_str or "blocked" in err_str.lower():
                _CLOB_CLIENT_CACHE["cooldown_until"] = now + 3600
                logger.warning("clob_client: Cloudflare 403 → 1h cooldown (derive bloke)")
            else:
                logger.warning(f"clob_client derive: {type(derive_err).__name__}: {err_str[:120]}")
            # Last-resort: stored ENV creds (V2 uyumluysa belki çalışır)
            api_key = os.getenv("POLYMARKET_API_KEY", "").strip()
            api_secret = os.getenv("POLYMARKET_API_SECRET", "").strip()
            api_pass = os.getenv("POLYMARKET_PASSPHRASE", "").strip()
            if all([api_key, api_secret, api_pass]):
                try:
                    from py_clob_client_v2 import ApiCreds

                    stored_creds = ApiCreds(
                        api_key=api_key,
                        api_secret=api_secret,
                        api_passphrase=api_pass,
                    )
                    client.set_api_creds(stored_creds)
                    _CLOB_CLIENT_CACHE["client"] = client
                    _CLOB_CLIENT_CACHE["creds"] = stored_creds
                    _CLOB_CLIENT_CACHE["fetched_at"] = now
                    logger.debug(f"clob_client: stored ENV last-resort (key={api_key[:8]}...)")
                    return client
                except Exception as _se:  # noqa: BLE001
                    logger.debug(f"stored last-resort fail: {_se}")
            return None
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
    t0 = datetime.now(UTC)
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
        # 2026-05-06 Heddas direktifi — Live history detay
        closed_task = fetch_closed_positions(user, http_client, limit=50)
        activity_task = fetch_activity(
            user,
            http_client,
            limit=100,
            types="TRADE,REDEEM,SPLIT,MERGE",
        )

        results = await asyncio.gather(
            bal_task
            if bal_task
            else asyncio.sleep(0, result=(0.0, 0.0, "clob_client unavailable")),
            pos_task,
            val_task,
            trades_task
            if trades_task
            else asyncio.sleep(0, result=([], "clob_client unavailable")),
            closed_task,
            activity_task,
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

        # Closed positions (2026-05-06)
        if isinstance(results[4], tuple) and len(results[4]) == 2:
            closed_positions, err = results[4]
            snap.closed_positions = [asdict(c) for c in closed_positions]
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[4], Exception):
            snap.fetch_errors.append(f"closed: {type(results[4]).__name__}")

        # Activity (TRADE/REDEEM/SPLIT/MERGE) (2026-05-06)
        if isinstance(results[5], tuple) and len(results[5]) == 2:
            activity, err = results[5]
            snap.activity = [asdict(a) for a in activity]
            if err:
                snap.fetch_errors.append(err)
        elif isinstance(results[5], Exception):
            snap.fetch_errors.append(f"activity: {type(results[5]).__name__}")

    snap.fetch_latency_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)
    return snap


# ════════════════════════════════════════════════════════════════════════
# 2026-04-29 Aşama 3.A — Cüzdan tutarlılığı helpers
# Tüm Telegram handler'ları (live, dashboard, start, portfolio) AYNI
# kaynaktan okur: polymarket_portfolio_cache table (60s job ile güncelli).
# ════════════════════════════════════════════════════════════════════════
async def read_cached_snapshot(db) -> Optional[dict]:
    """DB cache'ten son snapshot oku. Returns dict or None.

    Cache var ama stale (>5dk) ise yine döner — caller cache yaşına bakar.
    Cache hiç yoksa None döner.
    """
    if db is None or getattr(db, "conn", None) is None:
        return None
    try:
        import json

        async with db.conn.execute(
            "SELECT snapshot_json, fetched_at FROM polymarket_portfolio_cache " "WHERE id=1"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        snap = json.loads(row[0])
        snap["_fetched_at"] = row[1]  # caller'a yaş hesabı için
        return snap
    except Exception as e:  # noqa: BLE001
        logger.debug(f"read_cached_snapshot: {type(e).__name__}: {e}")
        return None


def cache_age_seconds(snap: Optional[dict]) -> int:
    """Cache yaşı (saniye). None / parse hatası → 99999."""
    if not snap or not snap.get("_fetched_at"):
        return 99999
    try:
        fetched = datetime.fromisoformat(snap["_fetched_at"].replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        return int((datetime.now(UTC) - fetched).total_seconds())
    except (ValueError, TypeError):
        return 99999
