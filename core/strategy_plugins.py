"""
PolyPaper Bot - Strategy Plugin System (Phase 11)
Modular strategy types. Add new strategies without touching engine.
Inspired by crypto-toolkit's Protocol-based plugin architecture.

Usage:
    registry = StrategyRegistry()
    registry.register(MomentumStrategy())
    registry.register(ContrarianStrategy())

    result = registry.evaluate("momentum", odds_data)
"""

import logging
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC

logger = logging.getLogger("polypaper.core.strategy_plugins")


@dataclass
class StrategySignal:
    """Output of a strategy evaluation."""

    direction: str | None = None  # "up", "down", None
    confidence: float = 0.0  # 0.0 to 1.0
    should_trade: bool = False
    reason: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class MarketSnapshot:
    """Input data for strategy evaluation.

    P0-08-F (2026-05-08): `timeframe` field eklendi. Plugin'ler
    TF-adaptive logic için kullanabilir (örn. 5m'de "son 1 dk" mantıklı,
    24h'de değil; ratio-based `minutes_remaining/total_minutes` tercih).
    """

    up_odds: float = 0.5
    down_odds: float = 0.5
    threshold: float = 0.50
    direction_filter: str = "any"  # "up", "down", "any"
    odds_series: list = field(default_factory=list)  # Historical up_odds
    minutes_remaining: float = 2.5
    total_minutes: float = 5.0
    timeframe: str = "5m"  # P0-08-F: TF context (5m/15m/1h/24h)
    spread: float = 0.02
    best_ask: float = 0.5
    best_bid: float = 0.48
    metadata: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    """Protocol for all strategy plugins."""

    # Phase 81b: Strategy tags for categorization
    # Override in subclass to customize. Values: "core", "ported", "experimental"
    origin: str = "core"  # "core" = original live, "ported" = from backtest, "experimental"

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy name."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @abstractmethod
    def evaluate(self, snapshot: MarketSnapshot) -> StrategySignal:
        """Evaluate market conditions and return signal."""
        ...


class MomentumStrategy(BaseStrategy):
    """Trade in the direction of the current trend.
    If price is rising → buy UP. If falling → buy DOWN.
    Best for trending markets with clear direction."""

    name = "momentum"
    description = "📈 Trend-following. Buys in direction of price movement."

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series

        if len(series) < 5:
            result.reason = "Need 5+ data points"
            return result

        # Short-term trend (last 5 vs previous 5)
        recent = series[-3:]
        older = series[-6:-3] if len(series) >= 6 else series[:3]
        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)
        trend = recent_avg - older_avg

        # Direction from trend
        if trend > 0.02 and s.up_odds >= s.threshold:
            if s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = min(abs(trend) * 10, 1.0)
        elif trend < -0.02 and s.down_odds >= s.threshold:
            if s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = min(abs(trend) * 10, 1.0)

        # Time filter: don't enter too late
        pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if pct < 0.2:
            result.confidence *= 0.5

        result.should_trade = result.direction is not None and result.confidence >= 0.3
        result.reason = f"trend={trend:+.3f} conf={result.confidence:.2f}"
        result.metadata = {"trend": trend, "recent_avg": recent_avg, "older_avg": older_avg}
        return result


class ContrarianStrategy(BaseStrategy):
    """Trade against extreme moves. Mean-reversion logic.
    If odds spike to extreme → bet on reversal.
    Best for volatile markets with quick reversals."""

    name = "contrarian"
    description = "🔄 Mean-reversion. Bets against extreme moves."

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series

        if len(series) < 8:
            result.reason = "Need 8+ data points"
            return result

        # Calculate mean and deviation
        mean = sum(series[-20:]) / min(len(series), 20)
        current = series[-1]
        deviation = current - mean

        # Contrarian IGNORES standard threshold — it uses deviation as edge.
        # If UP odds spiked high → bet DOWN (expect mean reversion)
        # If UP odds dropped low → bet UP (expect bounce back)
        min_deviation = 0.08  # Minimum deviation to trigger

        if deviation > min_deviation:
            if s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = min(abs(deviation) * 5, 1.0)
        elif deviation < -min_deviation:
            if s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = min(abs(deviation) * 5, 1.0)

        # Volatility confirmation: contrarian works better in volatile markets
        if len(series) >= 5:
            vol = math.sqrt(sum((x - mean) ** 2 for x in series[-10:]) / min(len(series), 10))
            if vol > 0.05:
                result.confidence *= 1.2  # Boost in volatile markets
            result.metadata["volatility"] = vol

        result.should_trade = result.direction is not None and result.confidence >= 0.35
        result.reason = f"dev={deviation:+.3f} mean={mean:.3f} conf={result.confidence:.2f}"
        result.metadata.update({"deviation": deviation, "mean": mean})
        return result


class ScalperStrategy(BaseStrategy):
    """Quick in-and-out on small price movements.
    Targets tight spreads and small confident moves.
    Best for liquid markets with narrow spreads."""

    name = "scalper"
    description = "⚡ Quick trades on small movements. Needs tight spreads."

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # Scalping needs tight spread
        if s.spread > 0.10:
            result.reason = f"Spread too wide: {s.spread:.3f}"
            return result

        # Need at least some data
        if len(s.odds_series) < 3:
            result.reason = "Need 3+ data points"
            return result

        # Quick momentum check (last 2 ticks)
        last = s.odds_series[-1]
        prev = s.odds_series[-2]
        tick_move = last - prev

        # Any clear threshold cross
        if s.up_odds >= s.threshold and tick_move > 0.01:
            if s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = 0.5 + min(tick_move * 5, 0.4)
        elif s.down_odds >= s.threshold and tick_move < -0.01:
            if s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = 0.5 + min(abs(tick_move) * 5, 0.4)

        # Time: scalpers like the middle of the window
        pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if 0.3 < pct < 0.7:
            result.confidence *= 1.1

        result.should_trade = result.direction is not None and result.confidence >= 0.4
        result.reason = f"tick={tick_move:+.3f} spread={s.spread:.3f} conf={result.confidence:.2f}"
        result.metadata = {"tick_move": tick_move, "spread": s.spread}
        return result


class SniperStrategy(BaseStrategy):
    """Only trades when multiple conditions align perfectly.
    High confidence, low frequency. Quality over quantity.
    Requires: strong odds + aligned trend + good timing + adequate volume."""

    name = "sniper"
    description = "🎯 High-confidence only. Waits for perfect setups."

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series
        checks_passed = 0
        total_checks = 5

        # Check 1: Strong odds (well above threshold)
        odds_strength = 0
        if s.up_odds >= s.threshold + 0.10:
            odds_strength = 1
            checks_passed += 1
        elif s.down_odds >= s.threshold + 0.10:
            odds_strength = -1
            checks_passed += 1

        # Check 2: Trend alignment
        if len(series) >= 6:
            recent = sum(series[-3:]) / 3
            older = sum(series[-6:-3]) / 3
            trend = recent - older
            if (odds_strength > 0 and trend > 0.02) or (odds_strength < 0 and trend < -0.02):
                checks_passed += 1

        # Check 3: Good timing (not too early, not too late)
        pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if 0.25 < pct < 0.75:
            checks_passed += 1

        # Check 4: Spread is reasonable
        if s.spread < 0.15:
            checks_passed += 1

        # Check 5: Volatility is moderate
        if len(series) >= 5:
            mean = sum(series[-10:]) / min(len(series), 10)
            vol = math.sqrt(sum((x - mean) ** 2 for x in series[-10:]) / min(len(series), 10))
            if 0.02 < vol < 0.12:
                checks_passed += 1

        # Direction
        if odds_strength > 0 and s.direction_filter in ("up", "any"):
            result.direction = "up"
        elif odds_strength < 0 and s.direction_filter in ("down", "any"):
            result.direction = "down"

        result.confidence = checks_passed / total_checks
        result.should_trade = checks_passed >= 4 and result.direction is not None
        result.reason = f"{checks_passed}/{total_checks} checks | conf={result.confidence:.2f}"
        result.metadata = {"checks_passed": checks_passed, "total_checks": total_checks}
        return result


class MartingaleStrategy(BaseStrategy):
    """Kelly-Filtered Fractional DCA Martingale.
    Phase 18.5: NOT pure 2x doubling — uses adaptive multiplier.

    Core logic:
    1. Base signal from contrarian mean-reversion
    2. On consecutive losses, increase amount by multiplier
    3. Kelly Criterion pre-filter: skip if EV negative
    4. Hard cap at MAX_LEVEL (8) — never exceed
    5. Circuit breaker at max_exposure

    Amount progression (1.5x multiplier, 8 levels):
    L0=$1 → L1=$1.5 → L2=$2.25 → L3=$3.38 → L4=$5.06
    → L5=$7.59 → L6=$11.39 → L7=$17.09
    Total worst case: ~$49.26 (not $255 like pure 2x×8)
    """

    name = "martingale"
    description = "🎰 Kelly-filtered DCA. Increases size on losses, resets on win."

    # Configurable constants
    MULTIPLIER = 1.3  # Phase 19: 1.3x (was 1.5x — too aggressive at 0-30c)
    MAX_LEVEL = 8  # Hard cap: 8 levels max
    MAX_TOTAL_EXPOSURE = 50.0  # Circuit breaker: max $ across all levels
    MIN_KELLY = 0.05  # Minimum Kelly fraction to enter
    MIN_ENTRY_PRICE = 0.35  # Phase 19: NEVER enter below 35c (0-30c was 0% WR)

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series

        if len(series) < 8:
            result.reason = "Need 8+ data points"
            return result

        # ═══ Step 1: Contrarian base signal (mean-reversion) ═══
        mean = sum(series[-20:]) / min(len(series), 20)
        current = series[-1]
        deviation = current - mean
        min_deviation = 0.06  # Slightly more sensitive than pure contrarian

        target_dir = None
        if deviation > min_deviation and s.direction_filter in ("down", "any"):
            target_dir = "down"
        elif deviation < -min_deviation and s.direction_filter in ("up", "any"):
            target_dir = "up"

        if not target_dir:
            result.reason = f"No deviation signal: dev={deviation:+.3f}"
            return result

        # ═══ Step 2: Kelly pre-filter ═══
        # Estimate win probability from signal strength
        signal_strength = min(abs(deviation) * 5, 0.8)
        est_win_prob = 0.5 + signal_strength * 0.2  # 50-66% range

        price = s.up_odds if target_dir == "up" else s.down_odds
        if not price or price <= 0.05 or price >= 0.95:
            result.reason = f"Price extreme: {price}"
            return result

        # Phase 19: Price floor — 0-30c zone had 0% WR
        if price < self.MIN_ENTRY_PRICE:
            result.reason = f"Below floor: {price:.2f} < {self.MIN_ENTRY_PRICE}"
            return result

        # Kelly fraction
        b = (1.0 / price) - 1.0
        kelly_f = (b * est_win_prob - (1 - est_win_prob)) / b if b > 0 else 0
        if kelly_f < self.MIN_KELLY:
            result.reason = f"Kelly reject: f={kelly_f:.3f} < {self.MIN_KELLY}"
            return result

        # ═══ Step 3: Martingale level from metadata ═══
        # Engine passes loss_streak via snapshot metadata or defaults to 0
        loss_streak = s.metadata.get("loss_streak", 0) if hasattr(s, "metadata") else 0
        level = min(loss_streak, self.MAX_LEVEL - 1)
        multiplier = self.MULTIPLIER**level
        base_amount = s.metadata.get("base_amount", 1.0) if hasattr(s, "metadata") else 1.0
        sized_amount = round(base_amount * multiplier, 2)

        # Circuit breaker
        total_exposure = s.metadata.get("total_exposure", 0) if hasattr(s, "metadata") else 0
        if total_exposure + sized_amount > self.MAX_TOTAL_EXPOSURE:
            result.reason = (
                f"Circuit breaker: ${total_exposure}+${sized_amount} > ${self.MAX_TOTAL_EXPOSURE}"
            )
            return result

        # Volatility boost (same as contrarian)
        vol = 0.0
        if len(series) >= 5:
            vol = math.sqrt(sum((x - mean) ** 2 for x in series[-10:]) / min(len(series), 10))

        confidence = signal_strength * (1.1 if vol > 0.05 else 0.9)
        confidence = min(confidence, 1.0)

        result.direction = target_dir
        result.confidence = confidence
        result.should_trade = confidence >= 0.30
        result.reason = (
            f"L{level} dev={deviation:+.3f} kelly={kelly_f:.2f} "
            f"size=${sized_amount:.2f} vol={vol:.3f}"
        )
        result.metadata = {
            "level": level,
            "multiplier": multiplier,
            "sized_amount": sized_amount,
            "kelly_f": kelly_f,
            "deviation": deviation,
            "est_win_prob": est_win_prob,
        }
        return result


# ═══════════════════════════════════════
# STRATEGY REGISTRY
# ═══════════════════════════════════════
# Phase 26: Flash Crash + Streak Reversal
# ═══════════════════════════════════════


class FlashCrashStrategy(BaseStrategy):
    """Phase 26: Detects sudden odds drops and buys the crash (mean-reversion).
    Based on discountry/polymarket-trading-bot flash_crash_strategy.
    If odds dropped 0.15+ recently → BUY (odds will revert)."""

    name = "flashcrash"
    description = "💥 Buys sudden drops. Mean-reversion on crashes."
    drop_threshold = 0.15  # Min drop to trigger
    min_series_len = 5  # Need history to detect drop

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series
        if len(series) < self.min_series_len:
            result.reason = f"Need {self.min_series_len}+ data points"
            return result

        # Check recent drop in UP odds
        recent_max = max(series[-self.min_series_len :])
        current = series[-1]
        drop = recent_max - current

        # Check recent drop in DOWN odds (1-up)
        down_current = 1 - current
        down_recent_max = 1 - min(series[-self.min_series_len :])
        down_drop = down_recent_max - down_current

        if drop >= self.drop_threshold and s.direction_filter in ("up", "any"):
            # UP odds crashed → BUY UP (mean-reversion)
            result.direction = "up"
            result.confidence = min(drop / 0.30, 1.0)
            result.should_trade = True
            result.reason = f"FLASH_CRASH UP drop={drop:.2f} (max={recent_max:.2f}→{current:.2f})"
        elif down_drop >= self.drop_threshold and s.direction_filter in ("down", "any"):
            # DOWN odds crashed → BUY DOWN
            result.direction = "down"
            result.confidence = min(down_drop / 0.30, 1.0)
            result.should_trade = True
            result.reason = f"FLASH_CRASH DOWN drop={down_drop:.2f}"
        else:
            result.reason = (
                f"No crash (up_drop={drop:.2f} down_drop={down_drop:.2f} thr={self.drop_threshold})"
            )
        return result


class StreakReversalStrategy(BaseStrategy):
    """Phase 26: Monitors consecutive same-direction market results.
    Based on 0xrsydn/polymarket-crypto-toolkit streak reversal.
    After N consecutive same-direction wins → bet on reversal."""

    name = "streak"
    description = "🔄 Bets against streaks. Reversal play."
    streak_threshold = 3  # Consecutive same-direction to trigger

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        series = s.odds_series
        if len(series) < 8:
            result.reason = "Need 8+ data points"
            return result

        # Analyze recent trend: how many consecutive points trending same direction
        ups = 0
        downs = 0
        for i in range(len(series) - 1, max(0, len(series) - 8), -1):
            if series[i] > 0.55:
                ups += 1
            elif series[i] < 0.45:
                downs += 1
            else:
                break  # Neutral zone breaks streak

        if ups >= self.streak_threshold and s.direction_filter in ("down", "any"):
            # Consecutive UP → bet DOWN (reversal)
            result.direction = "down"
            result.confidence = min(ups / 6, 1.0)
            result.should_trade = True
            result.reason = f"STREAK_REV: {ups} consecutive UP → bet DOWN"
        elif downs >= self.streak_threshold and s.direction_filter in ("up", "any"):
            # Consecutive DOWN → bet UP (reversal)
            result.direction = "up"
            result.confidence = min(downs / 6, 1.0)
            result.should_trade = True
            result.reason = f"STREAK_REV: {downs} consecutive DOWN → bet UP"
        else:
            result.reason = f"No streak (ups={ups} downs={downs} thr={self.streak_threshold})"
        return result


# ═══════════════════════════════════════


class HighThresholdStrategy(BaseStrategy):
    """Phase 25: Only trades in the 80c+ zone where 1027 trades show %84 WR.
    Pure odds-based — no complex signals needed at extreme odds.
    Trades both UP and DOWN when either side crosses 0.80+.
    Zone data: 87t %84 WR +2.40 PnL at 80c+."""

    name = "highthreshold"
    description = "🏔️ Ultra-safe 80c+ zone only. High WR, low frequency."

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        min_odds = max(s.threshold, 0.75)  # Never below 0.75

        # Direction: whichever side is above threshold
        if s.up_odds >= min_odds and s.direction_filter in ("up", "any"):
            result.direction = "up"
            strength = (s.up_odds - min_odds) / (1.0 - min_odds)
        elif s.down_odds >= min_odds and s.direction_filter in ("down", "any"):
            result.direction = "down"
            strength = (s.down_odds - min_odds) / (1.0 - min_odds)
        else:
            result.should_trade = False
            result.reason = f"No side >= {min_odds}"
            return result

        # Timing: prefer middle of market window (not first/last minute)
        timing_ok = True
        if s.minutes_remaining is not None and s.total_minutes > 0:
            pct = s.minutes_remaining / s.total_minutes
            if pct < 0.15 or pct > 0.85:
                timing_ok = False

        # Spread check: at extreme odds, spread should be small
        spread_ok = s.spread < 0.08

        result.confidence = min(0.5 + strength * 0.5, 1.0)
        result.should_trade = timing_ok and spread_ok and result.direction is not None
        result.reason = f"HT odds>={min_odds} str={strength:.2f} timing={'✓' if timing_ok else '✗'} spread={'✓' if spread_ok else '✗'}"
        return result


class LateConvergenceStrategy(BaseStrategy):
    """Phase 47f.7: Late-window dominant-direction play.
    Ported from backtest/strategies/late_convergence.py.

    Backtest 6,140 BTC 5m markets:
      Min 4:00 → 98.9% WR, Min 4:30 → 96.4% WR.
    Strategy: wait until late in the market window, bet whichever side
    is clearly winning. The later you wait, the higher the WR but the
    more expensive the entry.

    47f.8 sweep proved this is the only Becker-FRIENDLY strategy
    (flip@0.01 → +$19.52 PnL, +105% on baseline).

    Live MarketSnapshot lacks per-side bid/ask; we use up_odds/down_odds
    as the dominant prices and let the engine fetch best_ask later. The
    engine's _open_positions / _pending dedupe blocks repeat fires per
    market, so this plugin does not need to track _signal_emitted.
    """

    name = "late_convergence"
    description = "🕓 Late-window momentum. Bets the dominant side after 80% elapsed."

    # Configurable parameters (mirror backtest defaults)
    min_elapsed_pct = 0.80  # 80% of window = minute 4 of 5m
    max_entry_price = 0.95  # don't buy above 95c (EV guard)
    min_spread_threshold = 0.02  # min distance from 50/50 to call dominance

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # Compute elapsed_pct from minutes_remaining/total_minutes.
        if s.total_minutes <= 0:
            result.reason = "no total_minutes"
            return result
        elapsed_pct = 1.0 - (s.minutes_remaining / s.total_minutes)

        if elapsed_pct < self.min_elapsed_pct:
            result.reason = f"too early: {elapsed_pct:.0%} < {self.min_elapsed_pct:.0%}"
            return result

        up_price = s.up_odds if s.up_odds and s.up_odds > 0 else 0.0
        down_price = s.down_odds if s.down_odds and s.down_odds > 0 else 0.0
        if up_price <= 0 and down_price <= 0:
            result.reason = "no prices"
            return result

        # Determine dominant side: which is clearly above 0.5 + min_spread,
        # falling back to the simply-larger side.
        spread = self.min_spread_threshold
        if up_price > 0.5 + spread:
            direction = "up"
            dominant_price = up_price
        elif down_price > 0.5 + spread:
            direction = "down"
            dominant_price = down_price
        elif up_price > down_price:
            direction = "up"
            dominant_price = up_price
        else:
            direction = "down"
            dominant_price = down_price

        if dominant_price > self.max_entry_price:
            result.reason = f"price too high: {dominant_price:.2f} > {self.max_entry_price}"
            return result

        # Direction filter
        if s.direction_filter not in ("any", direction):
            result.reason = f"direction_filter={s.direction_filter} blocks {direction}"
            return result

        # Confidence: time + price clarity (mirror backtest formula).
        time_conf = min(0.99, 0.55 + (elapsed_pct - 0.5) * 0.88)
        price_conf = abs(dominant_price - 0.5) * 2.0  # 0→1
        confidence = min(0.99, (time_conf + price_conf) / 2.0)

        result.direction = direction
        result.confidence = confidence
        result.should_trade = True
        result.reason = (
            f"late_conv {direction.upper()} @ {dominant_price:.2f} "
            f"({elapsed_pct:.0%} elapsed) conf={confidence:.2f}"
        )
        result.metadata = {
            "elapsed_pct": elapsed_pct,
            "dominant_price": dominant_price,
            "time_conf": time_conf,
            "price_conf": price_conf,
        }
        return result


class PennyContractStrategy(BaseStrategy):
    """Phase 70: 1¢ Contract Zone Strategy (A1: taker mispricing -57% on 1¢).

    Exploits the systematic taker mispricing on penny contracts (1-5¢):
    - Takers on 1¢ contracts lose ~57% more than makers
    - NO side on 1-5¢ contracts has positive EV for makers
    - YES side at 95-99¢ similarly mispriced

    Strategy:
    1. Only activates in penny zone (1-5¢ or 95-99¢)
    2. Always uses maker orders (never taker in penny zone)
    3. Favors NO side at low prices (1-5¢) — contrarian to crowd
    4. Small position sizes (high risk, high reward)

    ENV:
        PENNY_ZONE_ENABLED=true
        PENNY_ZONE_MAX_PRICE=0.05       # Max price for low-side
        PENNY_ZONE_MIN_HIGH_PRICE=0.95   # Min price for high-side
        PENNY_ZONE_MIN_SPREAD=0.01       # Minimum bid-ask spread
        PENNY_ZONE_MAX_CONFIDENCE=0.70   # Cap confidence (risky)
    """

    _ENABLED = os.getenv("PENNY_ZONE_ENABLED", "true").lower() == "true"
    _MAX_LOW = float(os.getenv("PENNY_ZONE_MAX_PRICE", "0.05"))
    _MIN_HIGH = float(os.getenv("PENNY_ZONE_MIN_HIGH_PRICE", "0.95"))
    _MIN_SPREAD = float(os.getenv("PENNY_ZONE_MIN_SPREAD", "0.01"))
    _MAX_CONF = float(os.getenv("PENNY_ZONE_MAX_CONFIDENCE", "0.70"))

    @property
    def name(self) -> str:
        return "penny_contract"

    @property
    def description(self) -> str:
        return "1-5¢ contract zone: exploit taker mispricing via maker NO orders"

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        if not self._ENABLED:
            result.reason = "penny_disabled"
            return result

        up_odds = s.up_odds

        # Check if we're in the penny zone
        in_low_zone = up_odds <= self._MAX_LOW  # UP at 1-5¢
        in_high_zone = up_odds >= self._MIN_HIGH  # UP at 95-99¢

        if not in_low_zone and not in_high_zone:
            result.reason = f"not_penny_zone({up_odds:.2f})"
            return result

        # Need minimum spread for maker profitability
        if s.spread < self._MIN_SPREAD:
            result.reason = f"spread_too_tight({s.spread:.3f}<{self._MIN_SPREAD})"
            return result

        # Need some time remaining (avoid last-minute penny trades).
        # P0-08-F (2026-05-08): ratio-based (son %20 zaman) → TF-adaptive.
        # 5m: <1.0 dk, 15m: <3.0 dk, 1h: <12 dk, 24h: <4.8 saat eşdeğeri.
        if s.total_minutes > 0 and s.minutes_remaining < 0.2 * s.total_minutes:
            result.reason = "too_close_to_close"
            return result

        if in_low_zone:
            # UP is at 1-5¢ → most crowd bets on NO (DOWN)
            # Contrarian: buy YES (UP) at very low price = high payout if hit
            # But data shows NO side is usually right...
            # A1 finding: taker mispricing is on NO side → makers on NO have edge
            # So we go DOWN (which means buying NO token at 95-99¢... that's high_zone)
            # Actually: if UP=0.03, DOWN=0.97. The mispricing is that
            # takers overpay for YES at 0.03. Makers should sell YES at 0.03 = buy NO.
            # But in our system, we buy the opposite token:
            # If we think UP=bad bet → go DOWN
            direction = "down"
            # Confidence based on how extreme the price is
            # 1¢ = highest confidence, 5¢ = lower
            price_extremity = 1.0 - (up_odds / self._MAX_LOW)  # 0.01→0.80, 0.05→0.00
            confidence = min(self._MAX_CONF, 0.40 + price_extremity * 0.30)
        else:
            # UP is at 95-99¢ → mirror logic
            # Takers overpay for NO at 1-5¢ → go UP (buy YES at 95-99¢)
            direction = "up"
            price_extremity = (up_odds - self._MIN_HIGH) / (1.0 - self._MIN_HIGH)
            confidence = min(self._MAX_CONF, 0.40 + price_extremity * 0.30)

        # Direction filter
        if s.direction_filter not in ("any", direction):
            result.reason = f"direction_filter={s.direction_filter} blocks {direction}"
            return result

        result.direction = direction
        result.confidence = round(confidence, 3)
        result.should_trade = True
        result.reason = (
            f"penny {'low' if in_low_zone else 'high'} "
            f"up={up_odds:.2f} → {direction.upper()} "
            f"conf={confidence:.2f} spread={s.spread:.3f}"
        )
        result.metadata = {
            "zone": "low" if in_low_zone else "high",
            "price_extremity": round(price_extremity, 3),
            "force_maker": True,  # Signal to engine: always use maker
        }
        return result


class BondingYieldLiveStrategy(BaseStrategy):
    """Phase 76: Bond-like returns from high-probability contracts (90-99c).
    Buy contracts near resolution with 90%+ probability for 1-10% yield.
    Works on both UP and DOWN sides — whichever is in the bonding range."""

    name = "bonding_yield"
    description = "🏦 Bond-like 1-10% yield from 90-99c contracts near resolution."

    # ENV-tunable
    MIN_PRICE = float(os.getenv("BONDING_MIN_PRICE", "0.90"))
    MAX_PRICE = float(os.getenv("BONDING_MAX_PRICE", "0.99"))
    MIN_YIELD = float(os.getenv("BONDING_MIN_YIELD", "0.01"))
    MAX_HOURS = float(os.getenv("BONDING_MAX_HOURS_LEFT", "48"))
    CONF_BASE = float(os.getenv("BONDING_CONFIDENCE_BASE", "0.80"))

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # Check both sides for bonding range
        candidates = []
        fee_est = 0.02  # ~2% fee estimate

        if self.MIN_PRICE <= s.up_odds <= self.MAX_PRICE:
            exp_yield = 1.0 - s.up_odds - fee_est
            if exp_yield >= self.MIN_YIELD and s.direction_filter in ("up", "any"):
                candidates.append(("up", s.up_odds, exp_yield))

        if self.MIN_PRICE <= s.down_odds <= self.MAX_PRICE:
            exp_yield = 1.0 - s.down_odds - fee_est
            if exp_yield >= self.MIN_YIELD and s.direction_filter in ("down", "any"):
                candidates.append(("down", s.down_odds, exp_yield))

        if not candidates:
            result.reason = "no contract in bonding range"
            return result

        # Pick best yield
        candidates.sort(key=lambda x: x[2], reverse=True)
        direction, price, exp_yield = candidates[0]

        # Time check
        hours_left = s.minutes_remaining / 60.0
        if hours_left > self.MAX_HOURS:
            result.reason = f"too_far: {hours_left:.1f}h > {self.MAX_HOURS}h"
            return result

        # Spread check: tight spread needed for small yields
        if s.spread > exp_yield * 0.5:
            result.reason = f"spread_wide: {s.spread:.3f} > yield/2={exp_yield/2:.3f}"
            return result

        # Confidence: higher price = higher confidence
        confidence = self.CONF_BASE + (price - self.MIN_PRICE) * 2.0
        confidence = min(confidence, 0.99)

        # Time boost: closer to resolution = more confident
        if s.total_minutes > 0:
            time_factor = 1.0 - (s.minutes_remaining / s.total_minutes)
            confidence = min(confidence + time_factor * 0.1, 0.99)

        result.direction = direction
        result.confidence = round(confidence, 4)
        result.should_trade = True
        result.reason = (
            f"bonding {direction} @ {price:.2f}c " f"yield={exp_yield:.1%} {hours_left:.1f}h left"
        )
        result.metadata = {
            "entry_price": price,
            "expected_yield": exp_yield,
            "hours_remaining": round(hours_left, 1),
            "force_maker": True,
        }
        return result


# ═══════════════════════════════════════════════════════════════
# Phase 81b: Backtest strategies ported to live plugin system
# These were previously only available in backtest/strategies/.
# Now unified: same logic runs in backtest, paper, shadow, live.
# ═══════════════════════════════════════════════════════════════


class HourEdgeLiveStrategy(BaseStrategy):
    """Bet based on hour-of-day directional bias.
    Source: PolyBackTest analysis of 9,075 BTC 5m + 744 1h markets.
    6am UTC: 57.8% UP, 10pm: 68% DOWN, 14h: 81.8% UP."""

    name = "hour_edge"
    description = "🕐 Hour-of-day directional edge (time-based, no orderbook needed)"
    origin = "ported"

    # Default edges: {hour_utc: ("up"|"down", win_rate)}
    EDGES = {
        6: ("up", 0.578),
        14: ("up", 0.818),
        17: ("down", 0.650),
        22: ("down", 0.680),
    }
    min_win_rate: float = 0.55

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        meta = s.metadata or {}

        # Get current hour UTC from metadata or system time
        hour = meta.get("hour_utc")
        if hour is None:
            from datetime import datetime

            hour = datetime.now(UTC).hour

        edge = self.EDGES.get(hour)
        if not edge or edge[1] < self.min_win_rate:
            result.reason = f"no edge at hour {hour}"
            return result

        direction, win_rate = edge

        # Direction filter check
        if s.direction_filter not in (direction, "any"):
            result.reason = f"filtered: {direction} vs {s.direction_filter}"
            return result

        # Time position: enter early
        time_pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if time_pct < 0.15:
            result.reason = "too late in window"
            return result

        result.direction = direction
        result.confidence = win_rate
        result.should_trade = True
        result.reason = f"hour_edge: {hour}h UTC → {direction.upper()} WR={win_rate:.1%}"
        result.metadata = {"hour_utc": hour, "hist_wr": win_rate}
        return result


class OrderbookImbalanceLiveStrategy(BaseStrategy):
    """Bid/ask depth asymmetry → directional signal.
    Source: 57.6% hit rate from orderbook analysis."""

    name = "orderbook_imbalance"
    description = "📊 Orderbook bid/ask depth imbalance → direction signal"
    origin = "ported"

    imbalance_threshold: float = 1.30
    min_depth: float = 100.0

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        meta = s.metadata or {}

        up_bid = meta.get("up_bid_depth", 0)
        up_ask = meta.get("up_ask_depth", 0)
        down_bid = meta.get("down_bid_depth", 0)
        down_ask = meta.get("down_ask_depth", 0)

        # Check UP token: heavy bid = buy pressure → UP signal
        if up_bid > self.min_depth and up_ask > 0:
            ratio = up_bid / up_ask
            if ratio >= self.imbalance_threshold and s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = min(0.90, 0.55 + (ratio - 1.0) * 0.15)
                result.should_trade = True
                result.reason = f"ob_imbalance: UP bid/ask={ratio:.2f}"
                result.metadata = {"up_ratio": ratio, "up_bid": up_bid, "up_ask": up_ask}
                return result

        # Check DOWN token
        if down_bid > self.min_depth and down_ask > 0:
            ratio = down_bid / down_ask
            if ratio >= self.imbalance_threshold and s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = min(0.90, 0.55 + (ratio - 1.0) * 0.15)
                result.should_trade = True
                result.reason = f"ob_imbalance: DOWN bid/ask={ratio:.2f}"
                result.metadata = {"down_ratio": ratio, "down_bid": down_bid, "down_ask": down_ask}
                return result

        # Net imbalance across both tokens
        total_bid = up_bid + down_bid
        total_ask = up_ask + down_ask
        if total_bid > self.min_depth and total_ask > 0:
            net = (up_bid - down_bid) / (total_bid + 1)
            if abs(net) > 0.3:
                direction = "up" if net > 0 else "down"
                if s.direction_filter in (direction, "any"):
                    result.direction = direction
                    result.confidence = min(0.85, 0.50 + abs(net) * 0.5)
                    result.should_trade = True
                    result.reason = f"ob_imbalance: net={net:.2f}"
                    result.metadata = {"net_imbalance": net}
                    return result

        result.reason = "no significant imbalance"
        return result


class FadeRipLiveStrategy(BaseStrategy):
    """Fade large BTC price moves (mean reversion).
    Source: BTC +0.3% → DOWN bet is profitable. Asymmetric edge."""

    name = "fade_rip"
    description = "🔄 Fade large BTC moves (mean reversion after rips)"
    origin = "ported"

    rip_threshold_pct: float = 0.3
    fade_up_only: bool = True

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        meta = s.metadata or {}

        btc_change = meta.get("btc_price_change", 0.0)
        if btc_change == 0:
            result.reason = "no BTC price data"
            return result

        # Time filter: need some elapsed time for price to develop
        time_pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if time_pct > 0.85:
            result.reason = "too early for fade"
            return result

        # Fade UP rip → bet DOWN
        if btc_change >= self.rip_threshold_pct:
            if s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = min(0.85, 0.55 + (btc_change - self.rip_threshold_pct) * 0.2)
                result.should_trade = True
                result.reason = f"fade_rip: BTC +{btc_change:.3f}% → DOWN"
                result.metadata = {"btc_pct_change": btc_change}
                return result

        # Fade DOWN rip → bet UP (only if enabled)
        if not self.fade_up_only and btc_change <= -self.rip_threshold_pct:
            if s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = min(
                    0.75, 0.50 + (abs(btc_change) - self.rip_threshold_pct) * 0.15
                )
                result.should_trade = True
                result.reason = f"fade_rip: BTC {btc_change:.3f}% → UP"
                result.metadata = {"btc_pct_change": btc_change}
                return result

        result.reason = f"BTC {btc_change:+.3f}% below threshold"
        return result


class OpeningBreakoutLiveStrategy(BaseStrategy):
    """First-minute BTC breakout → directional bet.
    Source: $10+ BTC move in first 60s → 57% hit rate."""

    name = "opening_breakout"
    description = "📈 First-minute BTC price breakout → directional bet"
    origin = "ported"

    breakout_usd: float = 10.0

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        meta = s.metadata or {}

        btc_move = meta.get("btc_move_usd", 0.0)
        if btc_move == 0:
            result.reason = "no BTC move data"
            return result

        # Time filter: only early in window
        time_pct = s.minutes_remaining / s.total_minutes if s.total_minutes > 0 else 0.5
        if time_pct < 0.65:
            result.reason = "past entry window"
            return result

        if abs(btc_move) < self.breakout_usd:
            result.reason = f"BTC ${btc_move:+.1f} below breakout threshold"
            return result

        direction = "up" if btc_move > 0 else "down"
        if s.direction_filter not in (direction, "any"):
            result.reason = f"filtered: {direction}"
            return result

        result.direction = direction
        result.confidence = min(0.85, 0.55 + (abs(btc_move) / self.breakout_usd - 1) * 0.05)
        result.should_trade = True
        result.reason = f"opening_breakout: BTC ${btc_move:+.1f}"
        result.metadata = {"btc_move_usd": btc_move}
        return result


class FundingRateLiveStrategy(BaseStrategy):
    """Binance funding rate → contrarian directional signal.
    High positive funding = overleveraged longs → fade → DOWN."""

    name = "funding_rate"
    description = "💰 Binance funding rate contrarian signal"
    origin = "ported"

    rate_threshold: float = 0.0005
    contrarian: bool = True

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()
        meta = s.metadata or {}

        rate = meta.get("funding_rate")
        if rate is None:
            result.reason = "no funding rate data"
            return result

        if abs(rate) < self.rate_threshold:
            result.reason = f"funding rate {rate:.6f} below threshold"
            return result

        if self.contrarian:
            direction = "down" if rate > 0 else "up"
        else:
            direction = "up" if rate > 0 else "down"

        if s.direction_filter not in (direction, "any"):
            result.reason = f"filtered: {direction}"
            return result

        result.direction = direction
        result.confidence = min(0.80, 0.55 + abs(rate) * 200)
        result.should_trade = True
        mode = "contrarian" if self.contrarian else "follow"
        result.reason = f"funding_rate: {rate:.6f} ({mode}) → {direction.upper()}"
        result.metadata = {"funding_rate": rate, "mode": mode}
        return result


class CalibrationArbLiveStrategy(BaseStrategy):
    """Price-probability miscalibration detection.
    Token $0.50 zone is most efficient. Deviation = edge."""

    name = "calibration_arb"
    description = "⚖️ Price-probability miscalibration arb"
    origin = "ported"

    deviation_threshold: float = 0.08
    target_zone_low: float = 0.35
    target_zone_high: float = 0.65

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # UP token mid price
        current_price = (
            (s.best_bid + s.best_ask) / 2 if s.best_bid > 0 and s.best_ask > 0 else s.up_odds
        )

        # Only trade in target zone
        if not (self.target_zone_low <= current_price <= self.target_zone_high):
            result.reason = f"outside zone: {current_price:.3f}"
            return result

        # Deviation from fair value (0.50)
        deviation = current_price - 0.50

        # UP token cheap → buy UP
        if deviation < -self.deviation_threshold:
            if s.direction_filter in ("up", "any"):
                result.direction = "up"
                result.confidence = min(0.80, 0.55 + abs(deviation) * 2)
                result.should_trade = True
                result.reason = f"calibration_arb: UP undervalued {current_price:.3f} (fair=0.50)"
                result.metadata = {"deviation": deviation, "mid_price": current_price}
                return result

        # UP token expensive → buy DOWN
        if deviation > self.deviation_threshold:
            if s.direction_filter in ("down", "any"):
                result.direction = "down"
                result.confidence = min(0.80, 0.55 + abs(deviation) * 2)
                result.should_trade = True
                result.reason = f"calibration_arb: DOWN undervalued (up={current_price:.3f})"
                result.metadata = {"deviation": deviation, "mid_price": current_price}
                return result

        result.reason = f"no miscalibration: dev={deviation:+.3f}"
        return result


class FusionStrategy(BaseStrategy):
    """Phase 81: Weighted signal fusion — wraps SignalFusion as a plugin.

    The original "fusion" strategy type used a separate code path in
    engine_signals.py → signal_fusion.py.  This plugin bridges that logic
    into the unified BaseStrategy.evaluate(MarketSnapshot) interface so
    fusion strategies can be backtested, hyperopt'd, and treated identically
    to every other live strategy.

    Uses: odds_strength, ema_trend, momentum, time_position, orderbook
    weights (12 total signals including calendar, round-number, Bayesian,
    confluence, technical, BB squeeze).
    """

    name = "fusion"
    description = "🔀 Weighted multi-signal composite (12 signals). Default strategy type."

    # HyperOpt-tunable signal weights (None = use ENV defaults)
    SIGNAL_W_ODDS: float | None = None
    SIGNAL_W_EMA: float | None = None
    SIGNAL_W_MOMENTUM: float | None = None
    SIGNAL_W_TIME: float | None = None
    SIGNAL_W_ORDERBOOK: float | None = None
    MIN_COMPOSITE: float | None = None

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # Lazy import to avoid circular dependency
        from core.signal_fusion import SignalFusion, SignalWeights

        # Build custom weights if HyperOpt params are set
        weights = SignalWeights()
        if self.SIGNAL_W_ODDS is not None:
            weights.odds_strength = self.SIGNAL_W_ODDS
        if self.SIGNAL_W_EMA is not None:
            weights.ema_trend = self.SIGNAL_W_EMA
        if self.SIGNAL_W_MOMENTUM is not None:
            weights.momentum = self.SIGNAL_W_MOMENTUM
        if self.SIGNAL_W_TIME is not None:
            weights.time_position = self.SIGNAL_W_TIME
        if self.SIGNAL_W_ORDERBOOK is not None:
            weights.orderbook = self.SIGNAL_W_ORDERBOOK
        if self.MIN_COMPOSITE is not None:
            weights.min_composite = self.MIN_COMPOSITE

        fusion = SignalFusion(weights=weights)

        # Determine direction to evaluate
        # If direction_filter is set, use it; otherwise pick the stronger side
        direction = s.direction_filter
        if direction == "any":
            if s.up_odds >= s.down_odds:
                direction = "up"
            else:
                direction = "down"

        # Build orderbook dict from metadata (if available)
        meta = s.metadata or {}
        orderbook = None
        if meta.get("up_bid_depth") or meta.get("down_bid_depth"):
            orderbook = {
                "up_bid_depth": meta.get("up_bid_depth", 0),
                "up_ask_depth": meta.get("up_ask_depth", 0),
                "down_bid_depth": meta.get("down_bid_depth", 0),
                "down_ask_depth": meta.get("down_ask_depth", 0),
            }

        # Call SignalFusion.evaluate()
        sig = fusion.evaluate(
            up_odds=s.up_odds,
            down_odds=s.down_odds,
            threshold=s.threshold,
            direction=direction,
            odds_series=s.odds_series,
            minutes_remaining=s.minutes_remaining,
            total_minutes=s.total_minutes,
            orderbook=orderbook,
            whale_signal=0.0,
        )

        # Convert SignalResult → StrategySignal
        result.direction = sig.direction
        result.confidence = abs(sig.composite_score)
        result.should_trade = sig.should_trade
        result.reason = sig.reason if sig.reason else sig.summary()
        result.metadata = {
            "composite_score": sig.composite_score,
            "signals": sig.signals,
            "calendar_mult": sig.calendar_mult,
            "confluence_count": sig.confluence_count,
            "confluence_passed": sig.confluence_passed,
            "technical_mult": sig.technical_mult,
            "mci_score": sig.mci_score,
        }
        return result


class ClassicStrategy(BaseStrategy):
    """Phase 82e Sprint 4.6: Klasik / no-algorithm strategy type.

    Hiçbir ek filtre/sinyal yok. Sadece Strategy Builder'daki temel
    parametreleri kullanır:
      • direction_filter ("up" | "down" | "any")
      • threshold (trigger odds)
      • take_profit / stop_loss engine tarafında uygulanır

    Kullanıcı "fiyat X'e gelince al, TP=%Y, SL=%Z" gibi klasik bir
    limit emri davranışı istediğinde bu plugin seçilir. Algoritmik
    bir edge hesaplamaz — kapıdaki threshold fire eder. Confidence
    sabit (0.75) çünkü karar tamamen kullanıcının.

    Not: PARAM_SPACES'e EKLENMEZ — hyperopt tune edilecek hiçbir
    algoritmik parametresi yoktur. Kullanıcı zaten tüm değerleri
    manuel ayarlıyor.
    """

    name = "classic"
    description = "🎯 Klasik (no-algo). Sadece trigger/TP/SL — ek filtre yok."
    origin = "core"

    # Sabit confidence — kullanıcı niyeti kesin kabul edilir.
    CONFIDENCE = 0.75

    def evaluate(self, s: MarketSnapshot) -> StrategySignal:
        result = StrategySignal()

        # UP yönü: direction_filter "up" veya "any" ve up_odds >= threshold
        if s.direction_filter in ("up", "any"):
            if s.up_odds is not None and s.up_odds >= s.threshold:
                result.direction = "up"
                result.confidence = self.CONFIDENCE
                result.should_trade = True
                result.reason = f"classic UP @ {s.up_odds:.3f} >= trigger {s.threshold:.3f}"
                result.metadata = {
                    "trigger": s.threshold,
                    "entry_price": s.up_odds,
                    "no_algo": True,
                }
                return result

        # DOWN yönü: direction_filter "down" veya "any" ve down_odds >= threshold
        if s.direction_filter in ("down", "any"):
            if s.down_odds is not None and s.down_odds >= s.threshold:
                result.direction = "down"
                result.confidence = self.CONFIDENCE
                result.should_trade = True
                result.reason = f"classic DOWN @ {s.down_odds:.3f} >= trigger {s.threshold:.3f}"
                result.metadata = {
                    "trigger": s.threshold,
                    "entry_price": s.down_odds,
                    "no_algo": True,
                }
                return result

        # Trigger'a ulaşılmadı — sessiz bekle
        result.reason = (
            f"classic not-at-trigger: up={s.up_odds:.3f} down={s.down_odds:.3f} "
            f"trigger={s.threshold:.3f} dir={s.direction_filter}"
        )
        return result


class StrategyRegistry:
    """Registry for all available strategy plugins + global config."""

    # Phase 19.5: Editable plugin parameters
    # P1-07 Round-3 (2026-05-11): explicit `dict[str, dict[str, type]]`
    # annotation. Was `dict[str, object]` (mypy default narrow) → 3× errors
    # on get_config/set_config when iterating + indexing.
    CONFIGURABLE: dict[str, dict[str, type]] = {
        "contrarian": {"min_deviation": float, "min_confidence": float},
        "martingale": {
            "MULTIPLIER": float,
            "MAX_LEVEL": int,
            "MAX_TOTAL_EXPOSURE": float,
            "MIN_KELLY": float,
            "MIN_ENTRY_PRICE": float,
            "min_deviation": float,
        },
        "momentum": {"trend_threshold": float, "min_confidence": float},
        "scalper": {"max_spread": float, "tick_threshold": float},
        "sniper": {"min_checks": int, "odds_margin": float},
        "flashcrash": {"drop_threshold": float, "min_series_len": int},
        "streak": {"streak_threshold": int},
        # Phase 47f.7: late_convergence live plugin port.
        "late_convergence": {
            "min_elapsed_pct": float,
            "max_entry_price": float,
            "min_spread_threshold": float,
        },
        # Phase 70: penny contract zone
        "penny_contract": {"_MAX_LOW": float, "_MIN_HIGH": float, "_MIN_SPREAD": float},
        # Phase 76: bonding yield
        "bonding_yield": {
            "MIN_PRICE": float,
            "MAX_PRICE": float,
            "MIN_YIELD": float,
            "MAX_HOURS": float,
            "CONF_BASE": float,
        },
        # Phase 81: fusion signal weights
        "fusion": {
            "SIGNAL_W_ODDS": float,
            "SIGNAL_W_EMA": float,
            "SIGNAL_W_MOMENTUM": float,
            "SIGNAL_W_TIME": float,
            "SIGNAL_W_ORDERBOOK": float,
            "MIN_COMPOSITE": float,
        },
        # Phase 81b: ported backtest strategies
        "hour_edge": {"min_win_rate": float},
        "orderbook_imbalance": {"imbalance_threshold": float, "min_depth": float},
        "fade_rip": {"rip_threshold_pct": float, "fade_up_only": bool},
        "opening_breakout": {"breakout_usd": float},
        "funding_rate": {"rate_threshold": float, "contrarian": bool},
        "calibration_arb": {
            "deviation_threshold": float,
            "target_zone_low": float,
            "target_zone_high": float,
        },
    }

    def __init__(self):
        self._strategies: dict[str, BaseStrategy] = {}
        for cls in [
            MomentumStrategy,
            ContrarianStrategy,
            ScalperStrategy,
            SniperStrategy,
            MartingaleStrategy,
            FlashCrashStrategy,
            StreakReversalStrategy,
            HighThresholdStrategy,
            LateConvergenceStrategy,
            PennyContractStrategy,
            BondingYieldLiveStrategy,
            FusionStrategy,
            # Phase 81b: Ported from backtest-only
            HourEdgeLiveStrategy,
            OrderbookImbalanceLiveStrategy,
            FadeRipLiveStrategy,
            OpeningBreakoutLiveStrategy,
            FundingRateLiveStrategy,
            CalibrationArbLiveStrategy,
            # Phase 82e Sprint 4.6: Classic no-algo strategy
            ClassicStrategy,
        ]:  # Phase 47f.7 + Phase 70 + Phase 76 + Phase 81 + Phase 81b + Phase 82e
            s = cls()
            self._strategies[s.name] = s

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.name] = strategy
        logger.info(f"Registered strategy plugin: {strategy.name}")

    def get(self, name: str) -> BaseStrategy | None:
        return self._strategies.get(name)

    def evaluate(self, name: str, snapshot: MarketSnapshot) -> StrategySignal:
        strategy = self._strategies.get(name)
        if not strategy:
            return StrategySignal(reason=f"Unknown strategy: {name}")
        return strategy.evaluate(snapshot)

    def list_all(self) -> list[dict]:
        return [
            {"name": s.name, "description": s.description, "origin": getattr(s, "origin", "core")}
            for s in self._strategies.values()
        ]

    @property
    def names(self) -> list[str]:
        return list(self._strategies.keys())

    def get_config(self, plugin_name: str) -> dict:
        """Phase 19.5: Get current configurable parameters for a plugin."""
        s = self._strategies.get(plugin_name)
        if not s:
            return {}
        result = {}
        for attr in self.CONFIGURABLE.get(plugin_name, {}):
            result[attr] = getattr(s, attr, getattr(s, attr.upper(), "?"))
        return result

    def set_config(self, plugin_name: str, param: str, value) -> bool:
        """Phase 19.5: Set a plugin parameter at runtime."""
        s = self._strategies.get(plugin_name)
        if not s:
            return False
        allowed = self.CONFIGURABLE.get(plugin_name, {})
        if param not in allowed:
            return False
        cast = allowed[param]
        try:
            typed_val = cast(value)
        except (ValueError, TypeError):
            return False
        # Try instance attr first, then class attr (for UPPER constants)
        if hasattr(s, param):
            setattr(s, param, typed_val)
        elif hasattr(s, param.upper()):
            setattr(s, param.upper(), typed_val)
        else:
            setattr(s, param, typed_val)
        logger.info(f"Plugin config: {plugin_name}.{param} = {typed_val}")
        return True
