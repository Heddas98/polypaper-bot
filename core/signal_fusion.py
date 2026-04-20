"""
PolyPaper Bot - Signal Fusion Engine (Phase 33 → Phase 68)
6+3 signal weighted composite with drift-aware dynamic weights.

Signals (Original 6):
1. Odds Strength: How far above threshold are current odds?
2. EMA Trend: Is the short-term trend aligned with trade direction?
3. Momentum: Rate of odds change over last N samples
4. Volatility: Is the market active enough to trade?
5. Time Position: Where are we in the market window?
6. Orderbook Imbalance: Buy vs sell pressure from L2 book

Phase 60 Additions:
7. Calendar Multiplier: Weekend/time-of-day edge multiplier (Sat 2.4x, night 1.9x)
8. Round Number Gravity: Mispricing near round numbers (2.4c avg correction)

Phase 66 Addition:
9. BayesianUpdater: Real-time probability refinement via Bayes' theorem.
   Source: @mikita_crypto Game Theory analysis (86M trades, only 12.3% positive EV).

Phase 68 Additions:
10. Confluence Gate: Require K of N signals to agree → reduces false signals 20-30%.
    Source: TradeSight Confluence-Based Entry.
11. Technical Confidence: RSI + MACD + BB confirmation multiplier (1.3x boost / 0.7x penalty).
    Source: TradingView MCP (23 technical indicators).
12. BB Squeeze: Position size boost when volatility is compressed → breakout detection.

Drift-aware: If DriftDetector reduces a signal's weight, fusion respects it.
"""
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("polypaper.core.signals")


@dataclass
class SignalWeights:
    """Configurable weights for each signal source.

    Phase 79b: Rebalanced for "next candle prediction" focus.
    OLD: odds=0.25 ema=0.20 mom=0.18 vol=0.12 time=0.10 ob=0.15 whale=0.10
    NEW: odds=0.05 ema=0.25 mom=0.30 vol=0.00 time=0.10 ob=0.20 whale=0.00

    Rationale:
    - momentum is the strongest direction predictor (recent vs older odds movement)
    - ema confirms trend direction
    - orderbook shows buy/sell pressure
    - time tells us where we are in the 5m window
    - odds_strength only measures "how far from threshold" — not direction
    - volatility tells "is market active?" — not direction
    - whale has zero data
    All weights are ENV-overridable: SIGNAL_W_ODDS, SIGNAL_W_EMA, etc.
    """
    odds_strength: float = float(os.getenv("SIGNAL_W_ODDS", "0.05"))
    ema_trend: float = float(os.getenv("SIGNAL_W_EMA", "0.25"))
    momentum: float = float(os.getenv("SIGNAL_W_MOMENTUM", "0.30"))
    volatility: float = float(os.getenv("SIGNAL_W_VOLATILITY", "0.00"))
    time_position: float = float(os.getenv("SIGNAL_W_TIME", "0.10"))
    orderbook: float = float(os.getenv("SIGNAL_W_ORDERBOOK", "0.20"))
    whale_flow: float = float(os.getenv("SIGNAL_W_WHALE", "0.00"))
    min_composite: float = 0.18   # Phase 62: ENV override: MIN_COMPOSITE


@dataclass
class SignalResult:
    """Result of signal fusion evaluation."""
    direction: Optional[str] = None
    composite_score: float = 0.0
    signals: dict = field(default_factory=dict)
    should_trade: bool = False
    reason: str = ""
    calendar_mult: float = 1.0       # Phase 60: weekend/time multiplier applied
    round_number_adj: float = 0.0    # Phase 60: round number gravity adjustment
    bayesian_posterior: float = 0.0  # Phase 66: Bayesian updated probability
    bayesian_edge: float = 0.0       # Phase 66: Edge vs market price
    confluence_count: int = 0         # Phase 68: how many signals agree
    confluence_required: int = 0      # Phase 68: how many required (K)
    confluence_passed: bool = True    # Phase 68: did confluence gate pass?
    technical_mult: float = 1.0       # Phase 68: RSI/MACD/BB confidence multiplier
    bb_squeeze: bool = False          # Phase 68: Bollinger squeeze active?
    mci_score: float = 1.0           # Phase 70: Market Coherence Index [0,1]
    mci_size_mult: float = 1.0       # Phase 70: MCI-based size multiplier
    whale_signal: float = 0.0        # Phase 60: Whale flow signal (new 7th signal)

    def summary(self) -> str:
        parts = [f"{k}={v:+.2f}" for k, v in self.signals.items()]
        extras = ""
        if self.calendar_mult != 1.0:
            extras += f" cal={self.calendar_mult:.2f}"
        if abs(self.round_number_adj) > 0.001:
            extras += f" rn={self.round_number_adj:+.3f}"
        if self.bayesian_posterior > 0:
            extras += f" bayes={self.bayesian_posterior:.3f}"
        if self.confluence_count > 0:
            extras += f" conf={self.confluence_count}/{self.confluence_required}"
        if self.technical_mult != 1.0:
            extras += f" tech={self.technical_mult:.2f}"
        if self.bb_squeeze:
            extras += " 🔥squeeze"
        if self.mci_score < 0.7:
            extras += f" mci={self.mci_score:.2f}"
        return f"score={self.composite_score:+.3f} [{', '.join(parts)}]{extras}"


class SignalFusion:
    """7-signal fusion engine with drift-aware weighting (Phase 60: +whale_flow)."""

    def __init__(self, weights: Optional[SignalWeights] = None, drift_detector=None,
                 whale_flow_signal=None):
        self.weights = weights or SignalWeights()
        # Phase 62: ENV override for min_composite
        _env_min = os.getenv("MIN_COMPOSITE")
        if _env_min:
            try:
                self.weights.min_composite = float(_env_min)
            except ValueError:
                pass
        # Phase 60: ENV override for whale signal weight
        _env_whale_weight = os.getenv("WHALE_SIGNAL_WEIGHT")
        if _env_whale_weight:
            try:
                self.weights.whale_flow = float(_env_whale_weight)
            except ValueError:
                pass
        self.drift = drift_detector  # Phase 33: DriftDetector reference
        self.whale_flow_signal = whale_flow_signal  # Phase 60: WhaleFlowSignal instance

    def evaluate(self, up_odds: float, down_odds: float,
                 threshold: float, direction: str,
                 odds_series: list[float] = None,
                 minutes_remaining: float = None,
                 total_minutes: float = 5.0,
                 orderbook: dict = None,
                 whale_signal: float = 0.0) -> SignalResult:
        result = SignalResult()
        odds_series = odds_series or []

        # Phase 79: Diagnostic logging for signal evaluation
        if len(odds_series) == 0:
            logger.debug(f"[SIGNAL_DIAG] evaluate called with empty odds_series. "
                        f"up={up_odds}, down={down_odds}, threshold={threshold}, direction={direction}")

        # Determine target direction (Phase 79: fixed — allow sub-threshold trades with reduced strength)
        target_dir = None
        target_odds = None

        # Phase 79: Allow ANY direction even if below threshold (just lower signal strength).
        # This prevents score=0.000 when threshold is set conservatively (e.g., 0.80).
        # Old logic required odds >= threshold; new logic only uses threshold as a soft gate.
        if direction == "up":
            target_dir, target_odds = "up", up_odds
        elif direction == "down":
            target_dir, target_odds = "down", down_odds
        elif direction == "any":
            # Pick the stronger side
            if up_odds >= down_odds:
                target_dir, target_odds = "up", up_odds
            else:
                target_dir, target_odds = "down", down_odds

        # Sanity check (should never fail now)
        if not target_dir:
            result.reason = "no_direction"
            logger.debug(f"[SIGNAL_DIAG] no_direction: direction={direction}")
            return result

        result.direction = target_dir

        # ═══ Signal 1: Odds Strength ═══
        # Phase 79: Fixed — measure how far target_odds is from the threshold.
        # Positive if above threshold, negative if below. Normalized to [-1, 1].
        diff = target_odds - threshold
        denom = max(abs(0.5 - threshold), 0.1)  # Use absolute distance from 0.5
        strength = max(min(diff / denom, 1.0), -1.0)  # Clamp to [-1, 1]
        result.signals["odds"] = strength

        # ═══ Signal 2: EMA Trend ═══
        ema_signal = 0.0
        if len(odds_series) >= 8:
            ema_short = self._ema(odds_series[-8:], 5)
            ema_long = self._ema(odds_series[-12:] if len(odds_series) >= 12 else odds_series, 10)
            if ema_short and ema_long:
                trend = (ema_short - ema_long) / max(ema_long, 0.01)
                if target_dir == "up":
                    ema_signal = min(max(trend * 10, -1), 1)
                else:
                    ema_signal = min(max(-trend * 10, -1), 1)
        result.signals["ema"] = ema_signal

        # ═══ Signal 3: Momentum ═══
        mom_signal = 0.0
        if len(odds_series) >= 4:
            recent = odds_series[-3:]
            older = odds_series[-6:-3] if len(odds_series) >= 6 else odds_series[:3]
            recent_avg = sum(recent) / len(recent)
            older_avg = sum(older) / len(older)
            momentum = (recent_avg - older_avg) / max(older_avg, 0.01)
            if target_dir == "up":
                mom_signal = min(max(momentum * 5, -1), 1)
            else:
                mom_signal = min(max(-momentum * 5, -1), 1)
        result.signals["momentum"] = mom_signal

        # ═══ Signal 4: Volatility ═══
        vol_signal = 0.0
        if len(odds_series) >= 5:
            mean = sum(odds_series[-10:]) / min(len(odds_series), 10)
            variance = sum((x - mean) ** 2 for x in odds_series[-10:]) / min(len(odds_series), 10)
            volatility = math.sqrt(variance)
            if 0.02 < volatility < 0.15:
                vol_signal = 0.5 + min(volatility * 5, 0.5)
            elif volatility >= 0.15:
                vol_signal = -0.3
            else:
                vol_signal = -0.5
        result.signals["volatility"] = vol_signal

        # ═══ Signal 5: Time Position ═══
        time_signal = 0.0
        if minutes_remaining is not None and total_minutes > 0:
            pct_remaining = minutes_remaining / total_minutes
            if 0.3 < pct_remaining < 0.8:
                time_signal = 0.5
            elif pct_remaining >= 0.8:
                time_signal = 0.3
            elif pct_remaining <= 0.1:
                time_signal = -0.8
            else:
                time_signal = 0.0
        result.signals["time"] = time_signal

        # ═══ Signal 6: Orderbook Imbalance (Phase 33) ═══
        ob_signal = 0.0
        if orderbook:
            ob_signal = self._orderbook_signal(orderbook, target_dir)
        result.signals["orderbook"] = ob_signal

        # ═══ Signal 7: Whale Flow (Phase 60) ═══
        # Pre-computed by caller as it's async. Only active if WHALE_SIGNAL_ENABLED.
        whale_sig = 0.0
        if _WHALE_SIGNAL_ENABLED and abs(whale_signal) > 0.001:
            whale_sig = whale_signal
        result.signals["whale"] = whale_sig
        result.whale_signal = whale_sig

        # ═══ Drift-Aware Weighted Composite ═══
        w = self.weights
        raw_weights = {
            "odds": w.odds_strength,
            "ema": w.ema_trend,
            "momentum": w.momentum,
            "volatility": w.volatility,
            "time": w.time_position,
            "orderbook": w.orderbook,
            "whale": w.whale_flow,
        }

        # Apply drift multipliers
        if self.drift:
            for sig_name in raw_weights:
                drift_mult = self.drift.get_weight(sig_name)
                raw_weights[sig_name] *= drift_mult

        # Normalize weights to sum to 1.0
        total_w = sum(raw_weights.values())
        if total_w > 0:
            for k in raw_weights:
                raw_weights[k] /= total_w

        result.composite_score = sum(
            raw_weights[k] * result.signals[k] for k in raw_weights
        )

        # ═══ Phase 60 Signal 7: Calendar Multiplier (Weekend/Time Edge) ═══
        # MiroFish crowd behavior research: Sat 2.4x, Sun 2.1x, night 1.9x edge
        # because fewer participants → more mispricing → bigger opportunities.
        if _CALENDAR_MULT_ENABLED:
            cal = self._calendar_multiplier()
            if cal != 1.0:
                result.composite_score *= cal
                result.calendar_mult = cal
                result.signals["calendar"] = cal

        # ═══ Phase 60 Signal 8: Round Number Gravity Correction ═══
        # Markets gravitate toward round numbers (10c, 25c, 50c, 75c etc).
        # Average mispricing near rounds: 2.4c. Correct signal accordingly.
        if _ROUND_NUMBER_ENABLED and target_odds is not None:
            rn_adj = self._round_number_correction(target_odds)
            if abs(rn_adj) > 0.001:
                result.composite_score += rn_adj
                result.round_number_adj = rn_adj
                result.signals["round_num"] = rn_adj

        # ═══ Phase 66 Signal 9: Bayesian Probability Update ═══
        # Feed each signal into BayesianUpdater to refine the true probability.
        # prior = market-implied (target_odds), then each signal updates posterior.
        if _BAYESIAN_ENABLED and target_odds is not None:
            try:
                bayes = BayesianUpdater(prior=target_odds)
                sig_accuracy = _BAYESIAN_SIGNAL_ACCURACY
                # Feed the 6 core signals as observations
                for sig_name in ("odds", "ema", "momentum", "volatility", "time", "orderbook"):
                    sig_val = result.signals.get(sig_name, 0.0)
                    if abs(sig_val) > 0.01:
                        bayes.update(sig_val, accuracy=sig_accuracy)
                result.bayesian_posterior = bayes.posterior
                result.bayesian_edge = bayes.get_edge(target_odds)
                result.signals["bayes_edge"] = round(result.bayesian_edge, 4)
                # Boost/penalize composite if Bayesian edge is significant
                if abs(result.bayesian_edge) > 0.02:
                    _bayes_boost = result.bayesian_edge * 0.15  # 15% weight to Bayesian
                    result.composite_score += _bayes_boost
            except Exception as _be:
                logger.debug(f"BayesianUpdater error: {_be}")

        # ═══ Phase 68 Signal 10: Confluence Gate ═══
        # Require K of N signals to be positive → reduces false signals
        if _CONFLUENCE_ENABLED:
            core_signals = ["odds", "ema", "momentum", "volatility", "time", "orderbook"]
            positive_count = sum(
                1 for s in core_signals
                if result.signals.get(s, 0) > _CONFLUENCE_SIGNAL_THRESHOLD
            )
            result.confluence_count = positive_count
            result.confluence_required = _CONFLUENCE_K
            result.confluence_passed = positive_count >= _CONFLUENCE_K
            result.signals["confluence"] = positive_count / len(core_signals)

            if not result.confluence_passed:
                result.composite_score *= _CONFLUENCE_PENALTY
                # Don't zero out — just penalize heavily

        # ═══ Phase 68 Signal 11+12: Technical Confidence (RSI + MACD + BB) ═══
        if _TECHNICAL_ENABLED and odds_series and len(odds_series) >= 15:
            try:
                from indicators.technical import compute_technicals
                tech = compute_technicals(odds_series)
                result.technical_mult = tech.confidence_mult
                result.bb_squeeze = tech.bb.is_squeeze if tech.bb else False

                # Apply confidence multiplier to composite score
                result.composite_score *= tech.confidence_mult

                # Store sub-signals
                if tech.rsi and abs(tech.rsi.signal) > 0.1:
                    result.signals["rsi"] = round(tech.rsi.signal, 3)
                if tech.macd and abs(tech.macd.signal) > 0.1:
                    result.signals["macd"] = round(tech.macd.signal, 3)
                if tech.bb:
                    if tech.bb.is_squeeze:
                        result.signals["bb_squeeze"] = round(tech.bb.squeeze_strength, 3)
                    if abs(tech.bb.signal) > 0.1:
                        result.signals["bb"] = round(tech.bb.signal, 3)
            except Exception as _te:
                logger.debug(f"Technical indicators error: {_te}")

        # ── Phase 70: MCI gate — reduce size or skip if market poorly calibrated ──
        _mci_enabled = os.getenv("MCI_ENABLED", "true").lower() == "true"
        if _mci_enabled and hasattr(self, "_mci_result") and self._mci_result is not None:
            result.mci_score = self._mci_result.score
            result.mci_size_mult = self._mci_result.size_multiplier
            if not self._mci_result.should_trade:
                result.composite_score *= 0.3  # Heavily penalize

        result.should_trade = result.composite_score >= w.min_composite
        if result.should_trade:
            result.reason = f"SIGNAL_OK: {result.summary()}"
        else:
            result.reason = f"SIGNAL_WEAK: {result.summary()}"

        return result

    # Phase 79 S3-04: OB depth levels + decay factor (ENV-tunable)
    _OB_LEVELS = int(os.getenv("OB_SIGNAL_LEVELS", "20"))
    _OB_DECAY = float(os.getenv("OB_SIGNAL_DECAY", "0.85"))

    @classmethod
    def _orderbook_signal(cls, orderbook: dict, direction: str) -> float:
        """Calculate orderbook imbalance signal with depth decay.

        Phase 79 S3-04: Uses top 20 levels (was 5) with exponential decay
        so nearer levels matter more. ENV: OB_SIGNAL_LEVELS, OB_SIGNAL_DECAY.

        Buy imbalance > 0 = more buy pressure = UP likely
        Sell imbalance > 0 = more sell pressure = DOWN likely
        """
        n = cls._OB_LEVELS
        decay = cls._OB_DECAY
        asks = orderbook.get("asks", [])[:n]
        bids = orderbook.get("bids", [])[:n]

        if not asks or not bids:
            return 0.0

        # Depth-weighted dollar volume: closer levels count more
        ask_vol = sum((p * s) * (decay ** i) for i, (p, s) in enumerate(asks))
        bid_vol = sum((p * s) * (decay ** i) for i, (p, s) in enumerate(bids))
        total = ask_vol + bid_vol

        if total < 1.0:
            return 0.0

        # Imbalance: positive = more bids (buy pressure)
        imbalance = (bid_vol - ask_vol) / total  # [-1, 1]

        # Align with trade direction
        if direction == "up":
            return min(max(imbalance * 2, -1), 1)
        else:
            return min(max(-imbalance * 2, -1), 1)

    @staticmethod
    def _ema(data: list[float], period: int) -> Optional[float]:
        if len(data) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = data[0]
        for val in data[1:]:
            ema = (val - ema) * multiplier + ema
        return ema

    # ═══════════════════════════════════════════════════════════════════
    # Phase 60 Signal 7: Calendar Multiplier
    # ═══════════════════════════════════════════════════════════════════
    @staticmethod
    def _calendar_multiplier() -> float:
        """Weekend + time-of-day edge multiplier.

        Based on MiroFish crowd behavior research (Doc #4 Bias #5):
        - Saturday: 2.4x edge (minimum liquidity, maximum mispricing)
        - Sunday pre-18:00 UTC: 2.1x
        - Friday 20:00+ UTC: 1.8x
        - Night 02:00-06:00 UTC: 1.9x (global minimum activity)
        - Late evening 20:00-02:00 UTC: 1.6x
        - Business hours 09:00-16:00 UTC: 1.0x (baseline)
        - Early morning 06:00-09:00 UTC: 1.2x

        Returns a multiplier applied to composite_score.
        Clamped to MAX_CALENDAR_MULT to prevent over-aggressive sizing.
        """
        now = datetime.now(timezone.utc)
        day = now.weekday()  # 0=Mon, 5=Sat, 6=Sun
        hour = now.hour

        # Weekend factor
        weekend_factor = 1.0
        if day == 5:  # Saturday
            weekend_factor = _WEEKEND_SAT_MULT
        elif day == 6:  # Sunday
            weekend_factor = _WEEKEND_SUN_MULT if hour < 18 else 1.0
        elif day == 4 and hour >= 20:  # Friday evening
            weekend_factor = 1.8

        # Time-of-day factor (UTC)
        time_factor = 1.0
        if 2 <= hour < 6:
            time_factor = 1.9    # Global minimum activity
        elif 6 <= hour < 9:
            time_factor = 1.2    # Early morning
        elif 9 <= hour < 16:
            time_factor = 1.0    # US business hours
        elif 20 <= hour or hour < 2:
            time_factor = 1.6    # Late evening

        # Combined: use the larger of the two (don't double-compound)
        # When weekend AND night overlap (Sat 3AM UTC): take max, not product
        combined = max(weekend_factor, time_factor)
        return min(combined, _MAX_CALENDAR_MULT)

    # ═══════════════════════════════════════════════════════════════════
    # Phase 60 Signal 8: Round Number Gravity Correction
    # ═══════════════════════════════════════════════════════════════════
    @staticmethod
    def _round_number_correction(price: float) -> float:
        """Correct for round number magnetism bias.

        Based on MiroFish crowd behavior research (Doc #4 Bias #2):
        Markets near round numbers (10c, 25c, 50c, 75c, etc.) are pulled
        toward those numbers. Average mispricing: 2.4 cents.

        gravity_pull(price, round) = alpha × sign(price - round) × e^(-|price - round| / beta)

        If price is NEAR a round number from above → market is slightly
        overpriced → signal correction is negative (conservative).
        If price is NEAR from below → slightly underpriced → positive correction.

        Returns a small additive adjustment to composite_score.
        """
        alpha = _ROUND_NUM_ALPHA
        beta = _ROUND_NUM_BETA
        # Key round numbers in prediction markets
        round_numbers = [0.10, 0.20, 0.25, 0.30, 0.40, 0.50,
                         0.60, 0.70, 0.75, 0.80, 0.90]

        total_pull = 0.0
        for r in round_numbers:
            dist = price - r
            if abs(dist) < 0.08:  # Only consider nearby rounds
                pull = alpha * (1.0 if dist > 0 else -1.0) * math.exp(-abs(dist) / beta)
                total_pull += pull

        # Clamp to ±0.05 to prevent excessive adjustment
        return max(min(total_pull, 0.05), -0.05)


# ── Phase 60: ENV controls for new signals ──
_CALENDAR_MULT_ENABLED = os.getenv("CALENDAR_MULT_ENABLED", "true").lower() == "true"
_MAX_CALENDAR_MULT = float(os.getenv("MAX_CALENDAR_MULT", "2.5"))
_WEEKEND_SAT_MULT = float(os.getenv("WEEKEND_SAT_MULT", "2.4"))
_WEEKEND_SUN_MULT = float(os.getenv("WEEKEND_SUN_MULT", "2.1"))

_ROUND_NUMBER_ENABLED = os.getenv("ROUND_NUMBER_ENABLED", "true").lower() == "true"
_ROUND_NUM_ALPHA = float(os.getenv("ROUND_NUM_ALPHA", "0.024"))
_ROUND_NUM_BETA = float(os.getenv("ROUND_NUM_BETA", "0.03"))

# ── Phase 60: Whale Flow Signal (7th signal) ──
_WHALE_SIGNAL_ENABLED = os.getenv("WHALE_SIGNAL_ENABLED", "true").lower() == "true"

# ── Phase 66: Bayesian Updater ──
_BAYESIAN_ENABLED = os.getenv("BAYESIAN_UPDATER_ENABLED", "true").lower() == "true"
_BAYESIAN_SIGNAL_ACCURACY = float(os.getenv("BAYESIAN_SIGNAL_ACCURACY", "0.60"))

# ── Phase 68: Confluence Gate ──
_CONFLUENCE_ENABLED = os.getenv("CONFLUENCE_MODE", "true").lower() == "true"
_CONFLUENCE_K = int(os.getenv("CONFLUENCE_K", "4"))       # Need K of 6 signals positive
_CONFLUENCE_SIGNAL_THRESHOLD = float(os.getenv("CONFLUENCE_SIGNAL_THRESHOLD", "0.05"))
_CONFLUENCE_PENALTY = float(os.getenv("CONFLUENCE_PENALTY", "0.5"))  # Score multiplier when gate fails

# ── Phase 68: Technical Indicators (RSI + MACD + BB) ──
_TECHNICAL_ENABLED = os.getenv("TECHNICAL_INDICATORS_ENABLED", "true").lower() == "true"


class BayesianUpdater:
    """Phase 66: Real-time Bayesian probability refinement.

    Source: @mikita_crypto Game Theory analysis (A7) — 86M trades.
    Only 12.3% of trades are positive EV. BayesianUpdater helps filter
    by updating prior probability with each signal layer.

    Usage:
        updater = BayesianUpdater(prior=0.50)
        updater.update(signal_strength=0.8, accuracy=0.65)  # strong bullish signal
        updater.update(signal_strength=-0.3, accuracy=0.55)  # weak bearish signal
        final_prob = updater.posterior  # refined probability
    """

    def __init__(self, prior: float = 0.50):
        """Initialize with market-implied prior probability.

        Args:
            prior: Initial probability estimate (typically from market odds).
                   e.g., if UP odds = 0.60, prior = 0.60.
        """
        self.posterior = max(0.001, min(0.999, prior))
        self._updates: list[dict] = []

    def update(self, signal_strength: float, accuracy: float = 0.60) -> float:
        """Update posterior with a new signal observation.

        Args:
            signal_strength: Signal value in [-1, 1]. Positive = supports direction.
            accuracy: Historical accuracy of this signal type (0.5 = coin flip, 1.0 = perfect).

        Returns:
            Updated posterior probability.
        """
        accuracy = max(0.50, min(0.95, accuracy))  # clamp to sane range

        # Convert signal_strength to a binary-like observation
        # Positive signal → evidence FOR the hypothesis
        # Negative signal → evidence AGAINST
        if signal_strength > 0:
            # Signal supports direction — likelihood = accuracy scaled by strength
            likelihood = 0.5 + (accuracy - 0.5) * min(abs(signal_strength), 1.0)
        else:
            # Signal opposes direction — inverse likelihood
            likelihood = 0.5 - (accuracy - 0.5) * min(abs(signal_strength), 1.0)

        # Bayes update: P(H|E) = P(E|H)*P(H) / [P(E|H)*P(H) + P(E|~H)*P(~H)]
        p_evidence_given_h = likelihood
        p_evidence_given_not_h = 1.0 - likelihood
        p_h = self.posterior
        p_not_h = 1.0 - p_h

        numerator = p_evidence_given_h * p_h
        denominator = numerator + p_evidence_given_not_h * p_not_h

        if denominator > 0:
            self.posterior = max(0.001, min(0.999, numerator / denominator))

        self._updates.append({
            "signal": signal_strength,
            "accuracy": accuracy,
            "posterior": self.posterior,
        })
        return self.posterior

    def get_edge(self, market_price: float) -> float:
        """Calculate edge: how much our posterior differs from market price.

        Positive edge = we think the market is underpriced → BUY signal.
        Negative edge = overpriced → avoid.

        Args:
            market_price: Current market-implied probability (e.g., 0.60 for 60c).

        Returns:
            Edge value (posterior - market_price). Positive = opportunity.
        """
        return self.posterior - market_price

    @property
    def confidence(self) -> float:
        """How far the posterior has moved from 0.50 (maximum uncertainty).
        Returns 0.0 (no confidence) to 1.0 (very confident)."""
        return abs(self.posterior - 0.50) * 2.0

    @property
    def update_count(self) -> int:
        return len(self._updates)

    def summary(self) -> str:
        return (f"BayesPost={self.posterior:.3f} conf={self.confidence:.2f} "
                f"updates={self.update_count}")
