"""
UMA Dispute Window Awareness (P3.Y) — 2026-05-03 docs re-audit
================================================================

Polymarket markets resolve through UMA Optimistic Oracle. Settlement
timeline (https://docs.polymarket.com/concepts/resolution):

    Phase                          Duration
    ─────────────────────────────────────────
    Challenge period                2 hours
    Debate period (if disputed)     24-48 hours
    UMA voting (if disputed)        ~48 hours
    ─────────────────────────────────────────
    Undisputed resolution           ~2h after proposal
    Disputed resolution             4-6 days total

**Bot risk:** Yeni pozisyon açmak için market `endDate`'e çok yakın
olmak ÇOK RİSKLİ:
  1. Trading might stop mid-position (resolution finalized)
  2. Liquidity collapses near end (taker sweeps blow past limits)
  3. Last-minute UMA dispute → 4-6 gün locked equity
  4. Crypto Up/Down 5m/15m strategy: 2h buffer ortalama signal lifespan'i
     aşar, signal yenisi gelmeden settlement başlar

**Bu modül saf fonksiyon:** market metadata (Gamma API'den fetched dict)
girer, "yeni pozisyon aç/açma" kararı verir. Web3.py / RPC çağrısı YOK
(forward work — `query_uma_contract_state()` v2'de eklenecek).

Engine integration (Sprint 4 wire — şu an opsiyonel):
    from core.uma_dispute import should_block_new_position

    block, reason = should_block_new_position(market_dict)
    if block:
        logger.info(f"⏸ UMA gate: {reason}")
        return SkipResult(reason="UMA_SETTLEMENT_WINDOW")
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("polypaper.uma_dispute")

# ─── Default thresholds (ENV-overridable) ────────────────────────────

# Challenge period buffer — `endDate - now` < bu kadar dakika ise engelle.
# Default 120dk (=2h, UMA challenge period) +30dk safety = 150dk.
DEFAULT_SETTLEMENT_BUFFER_MIN = 150

# Disputed market'e yeni pozisyon — KESİNLİKLE hayır.
# Disputed = 4-6 gün lock riski.

# Already-resolved market — yeni pozisyon imkansız (closed=true)
# ama defansif kontrol yapıyoruz.


def _get_buffer_min() -> int:
    """ENV-overridable settlement buffer (T6.1 hot-tune pattern)."""
    try:
        v = int(os.getenv("UMA_SETTLEMENT_BUFFER_MIN", str(DEFAULT_SETTLEMENT_BUFFER_MIN)))
        return max(0, min(v, 1440))  # clamp 0..1440 (24h)
    except (TypeError, ValueError):
        return DEFAULT_SETTLEMENT_BUFFER_MIN


def _parse_end_date(market: dict[str, Any]) -> int | None:
    """Extract market end-date as Unix epoch from various Gamma API shapes.

    Gamma API uses these field shapes (observed):
      - endDate: ISO8601 string ("2026-05-15T20:00:00Z")
      - end_date_iso: ISO8601 string (alt key)
      - endDateTs: Unix epoch int (rare)
      - closeTime: Unix epoch int (alt key)

    Returns: Unix epoch int (UTC) or None if unparseable.
    """
    if not isinstance(market, dict):
        return None

    # Try epoch fields first (cheap)
    for key in ("endDateTs", "closeTime", "end_time"):
        v = market.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)

    # Try ISO8601 strings
    for key in ("endDate", "end_date_iso", "end_date", "closeDate"):
        v = market.get(key)
        if not isinstance(v, str) or not v:
            continue
        try:
            # Polymarket uses both "Z" suffix and "+00:00" offset
            iso = v.replace("Z", "+00:00")
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return int(dt.timestamp())
        except (TypeError, ValueError):
            continue

    return None


# ─── Decision API ──────────────────────────────────────────────────


@dataclass
class GateDecision:
    """Result of UMA gate check."""

    block: bool
    reason: str  # short tag for logging/metrics
    detail: str  # human-readable explanation
    minutes_to_settlement: int | None = None  # negative if already past end


def is_market_closed(market: dict[str, Any]) -> bool:
    """Market trading already stopped?"""
    if not isinstance(market, dict):
        return False
    # Gamma fields (observed):
    if market.get("closed") is True:
        return True
    if market.get("active") is False:
        return True
    # CLOB endpoint: `acceptingOrders` False = stopped
    if market.get("acceptingOrders") is False:
        return True
    # Resolution status string
    rs = str(market.get("resolutionStatus", "")).lower()
    if rs in {"resolved", "settled", "closed"}:
        return True
    return False


def is_market_disputed(market: dict[str, Any]) -> bool:
    """Market currently in UMA dispute (proposal challenged)?

    Gamma API exposes:
      - resolutionStatus: "proposed" / "disputed" / "challenged" / "resolved"
      - umaDispute / uma_dispute: boolean (some endpoints)
      - state: "DISPUTED" enum string
    """
    if not isinstance(market, dict):
        return False
    rs = str(market.get("resolutionStatus", "")).lower()
    if rs in {"disputed", "challenged", "in_dispute"}:
        return True
    for key in ("umaDispute", "uma_dispute", "isDisputed", "is_disputed"):
        if market.get(key) is True:
            return True
    state = str(market.get("state", "")).upper()
    if "DISPUTE" in state:
        return True
    return False


def is_in_settlement_window(
    market: dict[str, Any],
    buffer_min: int | None = None,
    now_ts: int | None = None,
) -> bool:
    """endDate - now < buffer_min ise True (yeni pozisyon engellenmeli).

    `now_ts` testte deterministic clock için override.
    `buffer_min` None → ENV (default 150dk).
    """
    end_ts = _parse_end_date(market)
    if end_ts is None:
        return False  # bilinmiyorsa engelleme (false negative kabul, false positive değil)
    if buffer_min is None:
        buffer_min = _get_buffer_min()
    now = now_ts if now_ts is not None else int(time.time())
    minutes_left = (end_ts - now) / 60
    return minutes_left < buffer_min


def minutes_to_settlement(
    market: dict[str, Any],
    now_ts: int | None = None,
) -> int | None:
    """Dakika cinsinden endDate'e kalan süre. None = parse hatası.

    Negatif değer = endDate geçmişte kalmış (zaten settlement).
    """
    end_ts = _parse_end_date(market)
    if end_ts is None:
        return None
    now = now_ts if now_ts is not None else int(time.time())
    return int((end_ts - now) / 60)


def should_block_new_position(
    market: dict[str, Any],
    buffer_min: int | None = None,
    now_ts: int | None = None,
) -> GateDecision:
    """Tek-call decision API for engine integration.

    Order of precedence (most severe first):
      1. closed/resolved → block (BLOCK_CLOSED)
      2. disputed → block (BLOCK_DISPUTED, 4-6 gün lock riski)
      3. in settlement window → block (BLOCK_SETTLEMENT_WINDOW)
      4. otherwise → allow

    Returns: GateDecision(block, reason, detail, minutes_to_settlement).

    Engine usage:
        decision = should_block_new_position(market_meta)
        if decision.block:
            return SkipResult(reason=decision.reason, detail=decision.detail)
    """
    if not isinstance(market, dict) or not market:
        return GateDecision(
            block=False,
            reason="NO_DATA",
            detail="Market metadata yok — UMA gate atlandı (default allow).",
            minutes_to_settlement=None,
        )

    minutes_left = minutes_to_settlement(market, now_ts=now_ts)

    if is_market_closed(market):
        return GateDecision(
            block=True,
            reason="BLOCK_CLOSED",
            detail="Market trading kapalı (closed/resolved/acceptingOrders=False).",
            minutes_to_settlement=minutes_left,
        )

    if is_market_disputed(market):
        return GateDecision(
            block=True,
            reason="BLOCK_DISPUTED",
            detail="UMA dispute aktif — 4-6 gün lock riski. Yeni pozisyon engellendi.",
            minutes_to_settlement=minutes_left,
        )

    if is_in_settlement_window(market, buffer_min=buffer_min, now_ts=now_ts):
        buf = buffer_min if buffer_min is not None else _get_buffer_min()
        ml = minutes_left if minutes_left is not None else 0
        return GateDecision(
            block=True,
            reason="BLOCK_SETTLEMENT_WINDOW",
            detail=(
                f"Market endDate {ml} dk uzakta < {buf} dk buffer "
                f"(UMA challenge 2h + safety). Yeni pozisyon engellendi."
            ),
            minutes_to_settlement=minutes_left,
        )

    return GateDecision(
        block=False,
        reason="ALLOW",
        detail="Market açık, dispute yok, settlement window dışında.",
        minutes_to_settlement=minutes_left,
    )


# ─── Forward work ──────────────────────────────────────────────────
#
# v2 (Sprint 5+): on-chain UMA Optimistic Oracle direct query.
# Implementation sketch (commented — not wired):
#
#   from web3 import Web3
#   from core.allowance_preflight import ADDR_UMA_OPTIMISTIC_ORACLE
#
#   def query_uma_oracle_assertion_state(w3: Web3, assertion_id: bytes) -> dict:
#       """Direct assertionLiveness + currentVotePhase query.
#
#       Returns assertion timing fields:
#         - liveness: timestamp when challenge period ends
#         - settled: bool
#         - disputed: bool
#       """
#       contract = w3.eth.contract(
#           address=ADDR_UMA_OPTIMISTIC_ORACLE,
#           abi=[...],  # IOptimisticOracleV3.assertions(bytes32) tuple
#       )
#       result = contract.functions.assertions(assertion_id).call()
#       return {...}
#
# Şu an Gamma API metadata yeterli — on-chain direct query nadiren gerekir
# (Gamma 30s-2dk gecikme, ama bot pacing 5m/15m için sorun değil).
