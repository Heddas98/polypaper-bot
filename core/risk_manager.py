"""
PolyPaper Bot - Risk Manager (v34)
=====================================
9 kapılı bağımsız pre-trade risk kontrol sistemi.
"Engine önerir, RiskManager karar verir."

Kapılar (sırayla):
  1. Halt durumu          — Günlük kayıp veya manuel kill
  2. Tek pozisyon boyutu  — max_position_size sınırı
  3. Açık pozisyon sayısı — max_open_positions
  4. Günlük kayıp         — daily_loss_limit (UTC sıfırlama)
  5. Günlük trade sayısı  — max_daily_trades
  6. Arka arkaya kayıp    — max_consecutive_losses
  7. Bakiye alt sınırı    — balance_floor
  8. Market konsantrasyon — tek market max exposure
  9. Toplam exposure      — tüm açık pozisyonlar

RiskState DB'ye kalıcı olarak kaydedilir (bot_settings tablosu).
Günlük sıfırlama: UTC 00:00, halt otomatik kaldırılır.

✅ Daily loss boundary uses <= operator (Phase 54 P0-04 fix, 2026-04-20 audited).
   All three boundary checks are consistent (check_trade L212, margin L217,
   record_trade_closed L391). Coverage:
   tests/test_phase55_critical.py::TestRiskDailyLossBoundary
   (test_daily_loss_at_exact_limit, test_daily_loss_just_below_limit).
"""
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict

import aiosqlite  # T1.4 Faz 1: narrow DB exception handling

logger = logging.getLogger("polypaper.core.risk")


@dataclass
class RiskLimits:
    """Configurable risk parameters. Persisted to DB via bot_settings.

    Per-asset & per-market tiered limits — initialization pattern notes
    ===================================================================
    `per_asset_limits` uses ``field(default_factory=lambda: {...})`` rather
    than a bare ``= {...}`` default. This is the canonical dataclass
    idiom for per-instance mutable defaults — a bare ``= {...}`` would
    share ONE dict across every ``RiskLimits()`` instance (the classic
    "mutable default argument" bug), so modifying ``lim1.per_asset_limits``
    would silently mutate ``lim2.per_asset_limits`` too.

    Baseline (Phase 36): BTC=$500, ETH=$300, SOL/XRP=$200. Unrecognized
    assets fall through ``per_asset_limits.get(asset_upper) is None`` →
    approved by default (see ``check_asset_exposure`` at L156+). Add a
    new asset via DB ``risk.per_asset.<ASSET>=<limit>`` — `from_dict`
    at L76+ flattens ``risk.per_asset.*`` keys back into the dict, and
    `to_dict` at L62+ reverses it for persistence.

    Round-trip invariant: ``RiskLimits.from_dict(lim.to_dict()) == lim``
    for any `lim` with known-numeric asset values (verified by Epic 3
    T3.4 regression tests). Stringified floats are re-parsed via the
    ``type_map[attr](val)`` coercion path at L95.
    """
    max_position_size: float = 10.0
    max_open_positions: int = 5
    max_total_exposure: float = 100.0
    max_daily_loss: float = 50.0
    max_daily_trades: int = 200
    max_loss_streak: int = 10
    min_balance_floor: float = 100.0
    max_single_market_exposure: float = 20.0
    # Phase 36: Tiered limits (per-asset and per-market) — see class docstring
    # above for the `field(default_factory=...)` rationale (NOT a bare `{}`).
    per_asset_limits: dict = field(default_factory=lambda: {
        "BTC": 500.0,
        "ETH": 300.0,
        "SOL": 200.0,
        "XRP": 200.0,
    })
    per_market_limit: float = 100.0

    def to_dict(self) -> dict:
        result = {}
        for k, v in self.__dict__.items():
            if k == "per_asset_limits":
                for asset, limit in v.items():
                    result[f"risk.per_asset.{asset}"] = str(limit)
            elif k == "per_market_limit":
                result[f"risk.{k}"] = str(v)
            else:
                result[f"risk.{k}"] = str(v)
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "RiskLimits":
        lim = cls()
        type_map = {f.name: f.type for f in cls.__dataclass_fields__.values()}

        # Handle per_asset_limits separately
        per_asset = {}
        for key, val in d.items():
            if key.startswith("risk.per_asset."):
                asset = key.replace("risk.per_asset.", "")
                try:
                    per_asset[asset] = float(val)
                except (ValueError, TypeError):
                    pass
        if per_asset:
            lim.per_asset_limits = per_asset

        # Handle other fields
        for key, val in d.items():
            if key.startswith("risk.per_asset.") or key == "risk.per_asset_limits":
                continue  # Already handled
            attr = key.replace("risk.", "")
            if attr in type_map and attr not in ("per_asset_limits",):
                try:
                    setattr(lim, attr, type_map[attr](val))
                except (ValueError, TypeError):
                    pass
        return lim


@dataclass
class RiskState:
    """Tracks current risk exposure. Updated by engine on every trade/settlement."""
    open_position_count: int = 0
    total_exposure: float = 0.0
    daily_pnl: float = 0.0
    daily_trade_count: int = 0
    consecutive_losses: int = 0
    daily_reset_date: str = ""
    halted: bool = False
    halt_reason: str = ""
    per_market_exposure: dict = field(default_factory=dict)  # slug → $ amount
    # Phase 74b: strategy_id→slug tracking — same strat can't re-enter same market
    strategy_market_open: dict = field(default_factory=dict)  # "strat_id:slug" → True
    # Phase 49 A-02: last loss timestamp (ISO) — used for auto-cooldown of
    # loss-streak gate. Without this, streak >= max_loss_streak becomes a
    # permanent deadlock because no new trades can win to reset the streak.
    last_loss_ts: str = ""
    # P2-03 FIX: Formal dataclass fields for alert flags (were set via setattr)
    alert_flag: str = ""             # Phase 47f.9: early-warning flag for heartbeat
    _pnl_soft_flag: bool = False     # Phase 47f.9: daily PnL soft-limit crossed


class RiskVerdict:
    """Result of a risk check."""
    def __init__(self, approved: bool, reason: str = ""):
        self.approved = approved
        self.reason = reason

    def __bool__(self):
        return self.approved

    def __repr__(self):
        return f"RiskVerdict(approved={self.approved}, reason='{self.reason}')"


class RiskManager:
    """
    Pre-trade gate. Every proposed trade MUST pass through here.
    Engine generates signals → RiskManager validates → only then execute.
    """

    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self.state = RiskState()
        self.per_asset_exposure: Dict[str, float] = {}  # asset → total $ exposure

    def _extract_asset_from_slug(self, market_slug: str) -> str:
        """Extract asset (BTC, ETH, SOL, XRP) from slug like 'BTC-USDC-15m'."""
        parts = market_slug.split("-")
        return parts[0].upper() if parts else "?"

    def check_asset_limit(self, asset: str, pending_amount: float) -> tuple[bool, str]:
        """
        Check if adding this trade would exceed per-asset limit.
        Returns (approved: bool, reason: str)
        """
        asset_upper = asset.upper()
        limit = self.limits.per_asset_limits.get(asset_upper)

        # If no limit defined for this asset, allow it
        if limit is None:
            return True, ""

        current_exposure = self.per_asset_exposure.get(asset_upper, 0.0)
        new_exposure = current_exposure + pending_amount

        if new_exposure > limit:
            return False, f"ASSET_LIMIT_{asset_upper}: ${new_exposure:.2f} > ${limit:.2f}"
        return True, ""

    def check_market_limit(self, market_slug: str, pending_amount: float) -> tuple[bool, str]:
        """
        Check if adding this trade would exceed per-market limit.
        Returns (approved: bool, reason: str)
        """
        limit = self.limits.per_market_limit
        current_exposure = self.state.per_market_exposure.get(market_slug, 0.0)
        new_exposure = current_exposure + pending_amount

        if new_exposure > limit:
            return False, f"MARKET_LIMIT: ${new_exposure:.2f} > ${limit:.2f}"
        return True, ""

    def check_trade(self, trade_amount: float, market_slug: str,
                    wallet_balance: float, strategy_id: str = "") -> RiskVerdict:
        """Check if a proposed trade passes all risk gates."""
        self._maybe_reset_daily()

        # Gate 1: Is trading halted?
        if self.state.halted:
            return RiskVerdict(False, f"HALTED: {self.state.halt_reason}")

        # Gate 2: Position size limit
        if trade_amount > self.limits.max_position_size:
            return RiskVerdict(False,
                f"POSITION_SIZE: ${trade_amount} > max ${self.limits.max_position_size}")

        # Gate 3: Open position count
        if self.state.open_position_count >= self.limits.max_open_positions:
            return RiskVerdict(False,
                f"MAX_POSITIONS: {self.state.open_position_count} >= {self.limits.max_open_positions}")

        # Gate 4: Total exposure
        new_exposure = self.state.total_exposure + trade_amount
        if new_exposure > self.limits.max_total_exposure:
            return RiskVerdict(False,
                f"EXPOSURE: ${new_exposure:.2f} > max ${self.limits.max_total_exposure}")

        # Gate 5: Daily loss limit  (Phase 54 P0-04: <= consistent with record_trade_closed)
        # Phase 58: worst-case margin — assume this trade could be a full loss.
        # This prevents the boundary race where daily_pnl is just above -limit
        # and multiple concurrent trades all pass the check before any settles.
        worst_case_pnl = self.state.daily_pnl - trade_amount
        if self.state.daily_pnl <= -self.limits.max_daily_loss:
            self.state.halted = True
            self.state.halt_reason = f"Daily loss ${self.state.daily_pnl:.2f} hit limit"
            return RiskVerdict(False,
                f"DAILY_LOSS: ${self.state.daily_pnl:.2f} <= -${self.limits.max_daily_loss}")
        # Note (2026-04-20 T3.5 audit): margin check REJECTS this trade but does
        # NOT set halted=True — daily_pnl itself is still within limits, only
        # this specific (potentially oversized) trade would breach. Smaller
        # concurrent trades may still pass. Hard halt only triggers when
        # daily_pnl reaches -max_daily_loss (L215 here / L391+ record_trade_closed).
        if worst_case_pnl <= -self.limits.max_daily_loss:
            return RiskVerdict(False,
                f"DAILY_LOSS_MARGIN: pnl={self.state.daily_pnl:.2f} - pending={trade_amount:.2f} "
                f"would breach -${self.limits.max_daily_loss}")

        # Gate 6: Daily trade count
        if self.state.daily_trade_count >= self.limits.max_daily_trades:
            return RiskVerdict(False,
                f"DAILY_TRADES: {self.state.daily_trade_count} >= {self.limits.max_daily_trades}")

        # Gate 7: Consecutive loss streak — Phase 49 A-02 auto-cooldown
        # If enough time has passed since the last loss, auto-reset the streak
        # instead of deadlocking. Default 6h, configurable via STREAK_COOLDOWN_HOURS.
        if self.state.consecutive_losses >= self.limits.max_loss_streak:
            cooldown_h = float(os.getenv("STREAK_COOLDOWN_HOURS", "6"))
            cooled_down = False
            try:
                if self.state.last_loss_ts:
                    from datetime import datetime as _dt, timezone as _tz
                    last_dt = _dt.fromisoformat(self.state.last_loss_ts)
                    delta_h = (_dt.now(_tz.utc) - last_dt).total_seconds() / 3600.0
                    if delta_h >= cooldown_h:
                        logger.warning(
                            f"⚙️ LOSS_STREAK cooldown elapsed "
                            f"({delta_h:.1f}h >= {cooldown_h:.1f}h) — "
                            f"auto-resetting streak {self.state.consecutive_losses}→0"
                        )
                        self.state.consecutive_losses = 0
                        cooled_down = True
                else:
                    # No last_loss_ts recorded (pre-Phase49 state): stamp now
                    # so future cooldowns work, and keep the gate closed for now.
                    from datetime import datetime as _dt, timezone as _tz
                    self.state.last_loss_ts = _dt.now(_tz.utc).isoformat()
            except (ValueError, TypeError, AttributeError) as _e:
                # T1.4 Faz 1: fromisoformat raises ValueError on bad ISO,
                # TypeError if last_loss_ts is non-str, AttributeError if
                # RiskState shape drifts. Gate stays closed on failure
                # (cooled_down=False) — fail-safe toward halt.
                logger.debug(f"streak cooldown check failed: {type(_e).__name__}: {_e}")

            if not cooled_down:
                return RiskVerdict(False,
                    f"LOSS_STREAK: {self.state.consecutive_losses} >= "
                    f"{self.limits.max_loss_streak} "
                    f"(cooldown {cooldown_h:.0f}h)")

        # Gate 8: Balance floor
        if wallet_balance - trade_amount < self.limits.min_balance_floor:
            return RiskVerdict(False,
                f"BALANCE_FLOOR: ${wallet_balance - trade_amount:.2f} < ${self.limits.min_balance_floor}")

        # Gate 8b (Phase 74b): Same strategy can't re-enter same market slug
        # This is a safety net — engine._open_positions also dedup's, but
        # risk_manager catches edge cases (race conditions, restart recovery).
        if strategy_id:
            _strat_key = f"{strategy_id}:{market_slug}"
            if self.state.strategy_market_open.get(_strat_key):
                return RiskVerdict(False,
                    f"STRAT_ALREADY_OPEN: {strategy_id[:8]} already in {market_slug[:30]}")

        # Gate 9: Per-market concentration
        current_market = self.state.per_market_exposure.get(market_slug, 0)
        if current_market + trade_amount > self.limits.max_single_market_exposure:
            return RiskVerdict(False,
                f"MARKET_CONCENTRATION: ${current_market + trade_amount:.2f} > ${self.limits.max_single_market_exposure}")

        # ═══ Phase 36: Tiered Limits ═══
        # Gate 10: Per-asset limit (graduated cascade)
        asset = self._extract_asset_from_slug(market_slug)
        approved, reason = self.check_asset_limit(asset, trade_amount)
        if not approved:
            return RiskVerdict(False, reason)

        # Gate 11: Per-market limit (NEW — tighter than per-market concentration)
        approved, reason = self.check_market_limit(market_slug, trade_amount)
        if not approved:
            return RiskVerdict(False, reason)

        # Gate 12 (Phase 59): Cross-asset correlated exposure limit
        # All crypto assets are correlated — if BTC+ETH+SOL all open, total
        # directional risk is higher than the sum suggests. This gate caps
        # the combined crypto exposure across all assets.
        _cross_limit = float(os.getenv("MAX_CROSS_ASSET_EXPOSURE", "0"))
        if _cross_limit > 0:
            total_cross = sum(self.per_asset_exposure.values()) + trade_amount
            if total_cross > _cross_limit:
                return RiskVerdict(False,
                    f"CROSS_ASSET: total ${total_cross:.2f} > ${_cross_limit:.2f} "
                    f"({', '.join(f'{a}=${v:.1f}' for a, v in self.per_asset_exposure.items())})")

        return RiskVerdict(True, "ALL_GATES_PASSED")

    def record_trade_opened(self, trade_amount: float, market_slug: str,
                            strategy_id: str = ""):
        """Update state when a trade is opened."""
        self.state.open_position_count += 1
        self.state.total_exposure += trade_amount
        self.state.daily_trade_count += 1
        self.state.per_market_exposure[market_slug] = \
            self.state.per_market_exposure.get(market_slug, 0) + trade_amount
        # Phase 74b: Track strategy→market open status
        if strategy_id:
            self.state.strategy_market_open[f"{strategy_id}:{market_slug}"] = True
        # Phase 36: Track per-asset exposure
        asset = self._extract_asset_from_slug(market_slug)
        self.per_asset_exposure[asset] = self.per_asset_exposure.get(asset, 0) + trade_amount
        logger.debug(f"Risk: opened ${trade_amount} on {market_slug[:30]} | "
                     f"exposure=${self.state.total_exposure:.2f} | "
                     f"positions={self.state.open_position_count} | "
                     f"{asset}=${self.per_asset_exposure[asset]:.2f}")

    def record_trade_closed(self, trade_amount: float, pnl: float, market_slug: str,
                            strategy_id: str = ""):
        """Update state when a trade is closed."""
        self.state.open_position_count = max(0, self.state.open_position_count - 1)
        self.state.total_exposure = max(0, self.state.total_exposure - trade_amount)
        self.state.daily_pnl += pnl
        self.state.per_market_exposure[market_slug] = \
            max(0, self.state.per_market_exposure.get(market_slug, 0) - trade_amount)
        # Phase 74b: Release strategy→market lock
        if strategy_id:
            self.state.strategy_market_open.pop(f"{strategy_id}:{market_slug}", None)
        # Phase 36: Update per-asset exposure
        asset = self._extract_asset_from_slug(market_slug)
        self.per_asset_exposure[asset] = max(0, self.per_asset_exposure.get(asset, 0) - trade_amount)

        if pnl >= 0:
            self.state.consecutive_losses = 0
        else:
            self.state.consecutive_losses += 1
            # Phase 49 A-02: stamp last_loss_ts so the auto-cooldown in Gate 7
            # can detect how long we've been stuck in a losing streak.
            self.state.last_loss_ts = datetime.now(timezone.utc).isoformat()

        # Phase 47f.9: Alertmanager-style early warning on loss streak.
        # ALERT_LOSS_STREAK (default 5) fires a single WARNING log when the
        # streak crosses the threshold — heartbeat job can pick this up and
        # page the admin without hard-killing the bot yet (hard kill still
        # happens at self.limits.max_loss_streak).
        try:
            alert_thresh = int(os.getenv("ALERT_LOSS_STREAK", "5"))
            if (self.state.consecutive_losses == alert_thresh
                    and alert_thresh < self.limits.max_loss_streak):
                logger.warning(
                    f"⚠️ ALERT loss_streak={self.state.consecutive_losses} "
                    f"crossed early-warning threshold ({alert_thresh}); "
                    f"hard halt at {self.limits.max_loss_streak}.")
                # Expose for heartbeat / /rs to surface the alert.
                self.state.alert_flag = f"LOSS_STREAK_{alert_thresh}"
        except (ValueError, TypeError, AttributeError) as _e:
            # T1.4 Faz 1: ALERT_LOSS_STREAK env parse or state attr access;
            # alerting is advisory, never blocks the actual hard-halt gate.
            logger.debug(f"loss-streak alert: {type(_e).__name__}: {_e}")

        # Phase 47f.9: daily_pnl soft alert at 40% of max (quieter cousin of
        # the 80% heartbeat warning). Logs once per crossing.
        try:
            soft_pct = float(os.getenv("ALERT_DAILY_PNL_PCT", "0.4"))
            soft_limit = -self.limits.max_daily_loss * soft_pct
            prev_flag = getattr(self.state, "_pnl_soft_flag", False)
            if self.state.daily_pnl <= soft_limit and not prev_flag:
                logger.warning(
                    f"⚠️ ALERT daily_pnl={self.state.daily_pnl:+.2f} "
                    f"crossed {soft_pct*100:.0f}% soft limit "
                    f"({soft_limit:.2f}); hard halt at "
                    f"{-self.limits.max_daily_loss:.2f}.")
                self.state._pnl_soft_flag = True
        except (ValueError, TypeError, AttributeError) as _e:
            # T1.4 Faz 1: ALERT_DAILY_PNL_PCT env parse or state attr access;
            # advisory only — doesn't affect the daily_pnl hard limit below.
            logger.debug(f"pnl soft alert: {type(_e).__name__}: {_e}")

        # Check if daily loss limit hit
        if self.state.daily_pnl <= -self.limits.max_daily_loss:
            self.state.halted = True
            self.state.halt_reason = f"Daily loss ${self.state.daily_pnl:.2f}"
            logger.warning(f"🛑 RISK HALT: Daily loss limit hit ({self.state.daily_pnl:.2f})")

    def reset_halt(self):
        """Manual reset by user (e.g., /risk_reset command)."""
        self.state.halted = False
        self.state.halt_reason = ""
        self.state.consecutive_losses = 0
        self.state.last_loss_ts = ""
        logger.info("✅ Risk halt reset by user")

    def reset_streak(self):
        """Manual streak reset without clearing halt state."""
        old = self.state.consecutive_losses
        self.state.consecutive_losses = 0
        self.state.last_loss_ts = ""
        logger.info(f"✅ Loss streak reset: {old}→0")
        return old

    def get_status(self) -> dict:
        """Return current risk state for display."""
        return {
            "halted": self.state.halted,
            "halt_reason": self.state.halt_reason,
            "open_positions": self.state.open_position_count,
            "total_exposure": self.state.total_exposure,
            "daily_pnl": self.state.daily_pnl,
            "daily_trades": self.state.daily_trade_count,
            "loss_streak": self.state.consecutive_losses,
            "limits": {
                "max_position": self.limits.max_position_size,
                "max_positions": self.limits.max_open_positions,
                "max_exposure": self.limits.max_total_exposure,
                "max_daily_loss": self.limits.max_daily_loss,
                "balance_floor": self.limits.min_balance_floor,
            },
            # Phase 36: Tiered limits display
            "tiered_limits": {
                "per_asset": {
                    asset: {
                        "limit": limit,
                        "current": self.per_asset_exposure.get(asset, 0),
                    }
                    for asset, limit in self.limits.per_asset_limits.items()
                },
                "per_market": {
                    "limit": self.limits.per_market_limit,
                    "markets": self.state.per_market_exposure,
                }
            }
        }

    def _maybe_reset_daily(self):
        """Reset daily counters at UTC midnight."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.state.daily_reset_date != today:
            self.state.daily_pnl = 0.0
            self.state.daily_trade_count = 0
            self.state.daily_reset_date = today
            if self.state.halted and "Daily loss" in self.state.halt_reason:
                self.state.halted = False
                self.state.halt_reason = ""
                logger.info("🔄 Daily reset: halt cleared")

    # ═══ Phase 66: LIQUIDITY + UNSELLABLE TOKEN CHECKS ═══
    # Source: A9 (Claude $47 bot — sovereign2013 lost $23→$1.50 on unsellable token)
    # These are PRE-ENTRY and PRE-EXIT gates to prevent getting stuck.

    def check_liquidity_for_exit(self, position_size: float,
                                  orderbook: dict) -> RiskVerdict:
        """Phase 66: Check if there's enough liquidity to exit a position.

        Called BEFORE market close or when smart_exit triggers.
        If bid depth < position size, we can't exit at reasonable price.
        Source: A9 — sovereign2013's $23 position became $1.50 due to no bids.

        Args:
            position_size: Dollar amount we need to sell.
            orderbook: L2 orderbook dict with 'bids' and 'asks' lists.

        Returns:
            RiskVerdict(approved=True) if we can safely exit.
            RiskVerdict(approved=False, reason=...) if liquidity insufficient.
        """
        if not orderbook:
            # No orderbook data → conservative: allow exit but warn
            return RiskVerdict(True, "NO_OB_DATA: exit allowed (no orderbook)")

        bids = orderbook.get("bids", [])
        if not bids:
            return RiskVerdict(False,
                f"NO_BIDS: orderbook has no bids — cannot exit ${position_size:.2f}")

        # Sum bid-side dollar volume (top 10 levels)
        bid_depth = sum(p * s for p, s in bids[:10])

        # Minimum: we need at least 50% of position size in bids
        # (some slippage is acceptable, but not total illiquidity)
        min_depth_pct = float(os.getenv("LIQUIDITY_MIN_DEPTH_PCT", "0.50"))
        min_depth = position_size * min_depth_pct

        if bid_depth < min_depth:
            return RiskVerdict(False,
                f"LOW_LIQUIDITY: bid_depth=${bid_depth:.2f} < "
                f"required=${min_depth:.2f} ({min_depth_pct:.0%} of ${position_size:.2f})")

        # Also check: is the best bid price reasonable (not a 90% discount)?
        best_bid_price = bids[0][0] if bids else 0
        if best_bid_price < 0.02:
            return RiskVerdict(False,
                f"PENNY_BID: best_bid=${best_bid_price:.4f} — likely illiquid market")

        return RiskVerdict(True,
            f"LIQUIDITY_OK: bid_depth=${bid_depth:.2f} >= ${min_depth:.2f}")

    def check_unsellable_risk(self, market_odds: float,
                               orderbook: dict,
                               minutes_to_close: Optional[float] = None) -> RiskVerdict:
        """Phase 66: Pre-entry check for unsellable token risk.

        Don't enter a position if:
        1. Orderbook bid depth is very thin (< threshold)
        2. Market is near close and liquidity typically drops
        3. Market odds are extreme (>95c or <5c) → one-sided market

        Source: A9 — losing money not from wrong prediction but from
                inability to sell the token.

        Args:
            market_odds: Current market price (0.0-1.0).
            orderbook: L2 orderbook dict.
            minutes_to_close: Minutes until market closes.

        Returns:
            RiskVerdict indicating if entry is safe.
        """
        min_entry_depth = float(os.getenv("UNSELLABLE_MIN_ENTRY_DEPTH", "5.0"))
        close_warning_mins = float(os.getenv("UNSELLABLE_CLOSE_WARNING_MINS", "2.0"))

        # Check 1: Extreme odds → one-sided market = low exit liquidity
        if market_odds is not None and (market_odds > 0.95 or market_odds < 0.05):
            return RiskVerdict(False,
                f"EXTREME_ODDS: {market_odds:.3f} → one-sided market, exit liquidity risk")

        # Check 2: Orderbook depth
        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            bid_depth = sum(p * s for p, s in bids[:5]) if bids else 0
            ask_depth = sum(p * s for p, s in asks[:5]) if asks else 0
            total_depth = bid_depth + ask_depth

            if total_depth < min_entry_depth:
                return RiskVerdict(False,
                    f"THIN_BOOK: total_depth=${total_depth:.2f} < ${min_entry_depth:.2f}")

        # Check 3: Too close to market close
        if minutes_to_close is not None and minutes_to_close < close_warning_mins:
            return RiskVerdict(False,
                f"NEAR_CLOSE: {minutes_to_close:.1f}min < {close_warning_mins:.1f}min warning")

        return RiskVerdict(True, "ENTRY_SAFE")

    # ═══ Phase 28: RISK STATE PERSISTENCE ═══

    async def save_state(self, db):
        """Save critical risk state to DB to survive restarts."""
        try:
            state_data = {
                "risk_state.daily_pnl": str(self.state.daily_pnl),
                "risk_state.daily_trade_count": str(self.state.daily_trade_count),
                "risk_state.consecutive_losses": str(self.state.consecutive_losses),
                "risk_state.halted": str(int(self.state.halted)),
                "risk_state.halt_reason": self.state.halt_reason,
                "risk_state.daily_reset_date": self.state.daily_reset_date,
                # Phase 49 A-02
                "risk_state.last_loss_ts": self.state.last_loss_ts or "",
                # P1-03 FIX: Persist per-market exposure so restarts don't reset Gate 9
                "risk_state.per_market_exposure": json.dumps(self.state.per_market_exposure),
            }
            # Phase 36: Save tiered limits
            for asset, limit in self.limits.per_asset_limits.items():
                state_data[f"risk.per_asset.{asset}"] = str(limit)
            state_data["risk.per_market_limit"] = str(self.limits.per_market_limit)

            now = datetime.now(timezone.utc).isoformat()
            for key, val in state_data.items():
                await db.conn.execute(
                    "INSERT OR REPLACE INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, val, now))
            await db.conn.commit()
        except (aiosqlite.Error, TypeError, ValueError) as e:
            # T1.4 Faz 1: DB write failure or json.dumps TypeError on
            # per_market_exposure. Elevated to WARNING — if this silently
            # fails, a crash-restart cycle loses halted state and daily_pnl,
            # letting the bot re-hit limits that should have stayed locked.
            logger.warning(f"Risk save FAILED ({type(e).__name__}: {e}) — "
                           f"state will not survive restart")

    async def load_state(self, db):
        """Restore risk state from DB after restart."""
        try:
            rows = await db.conn.execute_fetchall(
                "SELECT key, value FROM bot_settings WHERE key LIKE 'risk_state.%'")
            if not rows:
                return
            d = {r[0]: r[1] for r in rows}
            self.state.daily_pnl = float(d.get("risk_state.daily_pnl", 0))
            self.state.daily_trade_count = int(d.get("risk_state.daily_trade_count", 0))
            self.state.consecutive_losses = int(d.get("risk_state.consecutive_losses", 0))
            self.state.halted = bool(int(d.get("risk_state.halted", 0)))
            self.state.halt_reason = d.get("risk_state.halt_reason", "")
            self.state.daily_reset_date = d.get("risk_state.daily_reset_date", "")
            # Phase 49 A-02
            self.state.last_loss_ts = d.get("risk_state.last_loss_ts", "") or ""
            # If we restarted while in a full streak block, apply cooldown
            # check NOW so the bot isn't silently deadlocked at boot.
            try:
                if (self.state.consecutive_losses >= self.limits.max_loss_streak
                        and self.state.last_loss_ts):
                    cooldown_h = float(os.getenv("STREAK_COOLDOWN_HOURS", "6"))
                    last_dt = datetime.fromisoformat(self.state.last_loss_ts)
                    delta_h = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600.0
                    if delta_h >= cooldown_h:
                        logger.warning(
                            f"⚙️ Boot cooldown: {delta_h:.1f}h since last loss "
                            f"(>= {cooldown_h:.1f}h) — auto-resetting "
                            f"streak {self.state.consecutive_losses}→0"
                        )
                        self.state.consecutive_losses = 0
            except (ValueError, TypeError, AttributeError) as _e:
                # T1.4 Faz 1: same shape as Gate 7 cooldown check (L248).
                # Gate stays active if parse fails — fail-safe toward halt.
                logger.debug(f"boot streak cooldown check failed: "
                             f"{type(_e).__name__}: {_e}")
            # Recount open positions from DB
            open_count = await db.conn.execute_fetchall(
                "SELECT COUNT(*) FROM executions WHERE status='bet_placed'")
            if open_count:
                self.state.open_position_count = open_count[0][0]
            exposure = await db.conn.execute_fetchall(
                "SELECT COALESCE(SUM(trade_amount),0) FROM executions WHERE status='bet_placed'")
            if exposure:
                self.state.total_exposure = exposure[0][0]
            # P1-03 FIX: Restore per-market exposure from saved JSON
            try:
                _pme_raw = d.get("risk_state.per_market_exposure", "{}")
                _pme = json.loads(_pme_raw) if _pme_raw else {}
                self.state.per_market_exposure = {k: float(v) for k, v in _pme.items()}
            except (json.JSONDecodeError, TypeError, ValueError, AttributeError) as _pme_err:
                # T1.4 Faz 1: corrupted/legacy JSON blob. Fallback: empty map
                # — Gate 9 (market concentration) will rebuild organically as
                # new trades open. Not critical for short-term correctness.
                logger.debug(f"per_market_exposure restore: "
                             f"{type(_pme_err).__name__}: {_pme_err}")
            # Phase 36: Rebuild per-asset exposure from open positions
            await self._rebuild_per_asset_exposure(db)
            # P1-04 FIX: Rebuild strategy_market_open from open positions
            await self._rebuild_strategy_market_open(db)
            logger.info(f"⚙️ Risk state restored: PnL={self.state.daily_pnl:+.2f} "
                        f"streak={self.state.consecutive_losses} halted={self.state.halted} "
                        f"per_market={len(self.state.per_market_exposure)} "
                        f"strat_market_locks={len(self.state.strategy_market_open)}")
        except (aiosqlite.Error, ValueError, TypeError, KeyError) as e:
            # T1.4 Faz 1: DB read, float/int coercion, or missing key. This
            # is the critical boot path — if it fails, daily_pnl starts at 0
            # and the bot could re-hit limits that should have stayed
            # locked. Emit full traceback so boot-time issues surface.
            logger.exception(f"Risk load FAILED [{type(e).__name__}]: {e} — "
                             f"state starting from defaults")

    async def _rebuild_per_asset_exposure(self, db):
        """Rebuild per-asset exposure from open positions in DB."""
        try:
            rows = await db.conn.execute_fetchall(
                "SELECT event_slug, trade_amount FROM executions WHERE status='bet_placed'")
            if not rows:
                return
            for row in rows:
                event_slug = row[0]
                trade_amount = row[1]
                asset = self._extract_asset_from_slug(event_slug)
                self.per_asset_exposure[asset] = \
                    self.per_asset_exposure.get(asset, 0) + trade_amount
        except (aiosqlite.Error, ValueError, TypeError, IndexError) as e:
            # T1.4 Faz 1: DB read, type coerce, or row shape mismatch.
            # Phase 36 per-asset exposure rebuilds next cycle from trade
            # opens, so a one-boot miss is acceptable.
            logger.debug(f"Per-asset exposure rebuild: {type(e).__name__}: {e}")

    async def _rebuild_strategy_market_open(self, db):
        """P1-04: Rebuild strategy_market_open from open positions in DB.

        Prevents the same strategy from re-entering the same market after
        restart (Gate 8b bypass). Queries open executions and reconstructs
        the 'strategy_id:slug' → True mapping.
        """
        try:
            rows = await db.conn.execute_fetchall(
                "SELECT strategy_id, event_slug FROM executions WHERE status='bet_placed'")
            if not rows:
                return
            for row in rows:
                key = f"{row[0]}:{row[1]}"
                self.state.strategy_market_open[key] = True
            logger.debug(f"strategy_market_open rebuilt: {len(rows)} locks")
        except (aiosqlite.Error, IndexError) as e:
            # T1.4 Faz 1: DB read or missing row columns. P1-04 lock map
            # rebuilds next cycle as strategies reopen — non-blocking.
            logger.debug(f"strategy_market_open rebuild: {type(e).__name__}: {e}")
