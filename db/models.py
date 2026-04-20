"""
PolyPaper Bot - Data Models (Phase 4.5 FIX)
FIXED: minutes_before_end default = 0.5 (not 5.0)
"""
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Asset(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    XRP = "XRP"


class Timeframe(str, Enum):
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    H24 = "24h"


class Direction(str, Enum):
    UP = "up"
    DOWN = "down"
    ANY = "any"


# Phase 50 hotfix: legacy aliases for StrategyStatus. Kept at module scope
# because Enum wraps class-level attrs in a descriptor that makes dict lookup
# awkward inside _missing_. Production log showed status='running' blowing up
# /strategies — mapping normalizes all known legacy values.
_STRATEGY_STATUS_ALIASES = {
    "running": "active",
    "run": "active",
    "on": "active",
    "enabled": "active",
    "live": "active",
    "off": "stopped",
    "disabled": "stopped",
    "halt": "stopped",
    "halted": "stopped",
    "pause": "paused",
}


class StrategyStatus(str, Enum):
    ACTIVE = "active"
    STOPPED = "stopped"
    PAUSED = "paused"

    # Phase 49 P0-07 + Phase 50 hotfix: case-insensitive coercion + legacy
    # alias normalization so "ACTIVE", " Active ", "running", "on", etc.
    # do not explode with `ValueError: '...' is not a valid StrategyStatus`.
    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            norm = value.strip().lower()
            # Direct match
            for member in cls:
                if member.value == norm:
                    return member
            # Legacy alias match
            alias = _STRATEGY_STATUS_ALIASES.get(norm)
            if alias:
                for member in cls:
                    if member.value == alias:
                        return member
        return None


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    BET_PLACED = "bet_placed"
    CLAIMED = "claimed"
    FAILED = "failed"


def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(BaseModel):
    id: str = Field(default_factory=_uid)
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    accepted_terms: bool = False
    default_wallet_id: Optional[str] = None
    notify_buy: bool = True
    notify_stop_loss: bool = True
    notify_take_profit: bool = True
    notify_claim: bool = True
    notify_no_buy: bool = False
    created_at: datetime = Field(default_factory=_now)


class Wallet(BaseModel):
    id: str = Field(default_factory=_uid)
    user_id: str
    label: str = "primary"
    balance: float = 10000.0
    is_primary: bool = True
    created_at: datetime = Field(default_factory=_now)


class Strategy(BaseModel):
    id: str = Field(default_factory=_uid)
    user_id: str
    wallet_id: str
    label: Optional[str] = None
    asset: Asset = Asset.BTC
    timeframe: Timeframe = Timeframe.M5
    direction: Direction = Direction.ANY
    trade_amount: float = 1.0
    odds_threshold: Optional[float] = 0.50
    price_difference: Optional[float] = None
    # FIXED: Was 5.0 → impossible for 5m markets. Now 0.5 (30 sec buffer)
    minutes_before_end: Optional[float] = 0.5
    minutes_after_start: Optional[float] = 0.0
    stop_loss_percent: Optional[float] = None
    stop_loss_odds: Optional[float] = None
    take_profit_percent: Optional[float] = None
    take_profit_odds: Optional[float] = None
    max_executions_per_event: Optional[int] = 1
    max_losses_per_event: Optional[int] = None
    max_entry_slippage: Optional[float] = None
    ma_filter_enabled: bool = False
    min_volatility: Optional[float] = None
    strategy_type: str = "fusion"  # "fusion", "momentum", "contrarian", "scalper", "sniper", "martingale"
    deploy_stage: str = "canary"   # Phase 47f.10 P3#15: canary | promoted
    status: StrategyStatus = StrategyStatus.STOPPED
    started_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def summary_line(self, index: int = 1) -> str:
        status_emoji = "🟢" if self.status == StrategyStatus.ACTIVE else "⚫"
        type_emoji = {"momentum": "📈", "contrarian": "🔄", "scalper": "⚡",
                       "sniper": "🎯", "fusion": "🔬", "martingale": "🎰"}.get(self.strategy_type, "🔬")
        name = self.label or f"{self.asset.value} {self.timeframe.value} {self.direction.value.title()}"
        return (
            f"{index}. {status_emoji} <b>{name}</b> "
            f"{type_emoji} | ${self.trade_amount} @ {self.odds_threshold}"
        )

    def auto_label(self) -> str:
        """Generate descriptive label if none set."""
        t = {"fusion": "F", "contrarian": "C", "sniper": "N",
             "momentum": "M", "scalper": "S", "martingale": "MG", "highthreshold": "HT", "flashcrash": "FC", "streak": "SR"}.get(self.strategy_type, "?")
        return f"{t}_{self.asset.value}_{self.timeframe.value}_{self.direction.value}_{self.odds_threshold}"


class Execution(BaseModel):
    id: str = Field(default_factory=_uid)
    user_id: str
    wallet_id: str
    strategy_id: Optional[str] = None
    event_slug: str
    market_token_id: Optional[str] = None
    direction: Direction
    trade_amount: float
    fee_amount: float = 0.0
    odds_threshold: Optional[float] = None
    execution_price: Optional[float] = None
    status: ExecutionStatus = ExecutionStatus.PENDING
    is_maker: int = 0  # 1 = maker fill, 0 = taker fill (Phase 79 BUG-02)
    signal_score: float = 0.0  # Original signal score at entry time (Phase 79 BUG-03)
    stop_loss_percent: Optional[float] = None
    stop_loss_odds: Optional[float] = None
    take_profit_percent: Optional[float] = None
    take_profit_odds: Optional[float] = None
    pnl: float = 0.0
    payout: float = 0.0
    result: Optional[str] = None
    closed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MarketEvent(BaseModel):
    slug: str
    asset: str
    timeframe: str
    condition_id: Optional[str] = None
    up_token_id: Optional[str] = None
    down_token_id: Optional[str] = None
    up_odds: Optional[float] = None
    down_odds: Optional[float] = None
    price_to_beat: Optional[float] = None
    current_price: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    resolved: bool = False
    resolution: Optional[str] = None
    last_updated: datetime = Field(default_factory=_now)
