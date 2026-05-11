"""
PolyPaper Bot - Backtest v2 Fill Simulation
Realistic order fill modeling with orderbook depth walking.

Fill modes:
  1. SIMPLE:         Fill at best_ask (current behavior)
  2. MIDPOINT:       Fill at mid(bid, ask)
  3. ORDERBOOK:      Walk through orderbook levels (static slippage tiers)
  4. MARKET_IMPACT:  Apply √(size/volume) slippage model
  5. REAL_ORDERBOOK: Walk through ACTUAL L2 orderbook levels (Phase 37)
                     Uses real bid/ask JSON from ob_snapshots recordings.
                     VWAP fill against historical depth — most realistic mode.
  6. MAKER:          Phase 51 P51-06 — post limit at best_bid, probabilistic
                     fill based on queue position. Earns maker rebate, saves
                     spread cost. Returns filled=False if queue misses.
  7. MAKER_HYBRID:   Phase 51 P51-06 — try MAKER first; on miss fall back to
                     SIMPLE taker path. Reflects real bot behaviour where we
                     post for N seconds then cross the spread.

Liquidity filter: skip markets with volume < threshold.
"""

import logging
import math
import os
from dataclasses import dataclass
from enum import Enum

from backtest.strategies.base import Direction, OrderbookSnapshot

logger = logging.getLogger("polypaper.backtest.fill")


class FillMode(Enum):
    SIMPLE = "simple"
    MIDPOINT = "midpoint"
    ORDERBOOK = "orderbook"
    MARKET_IMPACT = "market_impact"
    REAL_ORDERBOOK = "real_orderbook"  # Phase 37: actual L2 depth walk
    MAKER = "maker"  # Phase 51 P51-06: post-at-bid, rebate
    MAKER_HYBRID = "maker_hybrid"  # Phase 51 P51-06: maker then taker


@dataclass
class FillResult:
    """Result of attempting to fill an order."""

    filled: bool = False
    fill_price: float = 0.0
    slippage: float = 0.0  # price impact from ideal
    fill_amount: float = 0.0  # USDC filled
    shares: float = 0.0  # tokens received
    reason: str = ""
    # Phase 51 P51-06 — maker/taker accounting for backtests
    is_maker: bool = False  # True if filled as maker (earned rebate)
    rebate: float = 0.0  # USDC maker rebate (negative = paid, positive = earned)


class FillSimulator:
    """Simulates order execution against orderbook state."""

    # Bid-ask spread cost in Polymarket (cost of crossing the spread for
    # immediate execution).
    #
    # T4.7-C (2026-04-24): Default raised 0.005 → 0.023 based on empirical
    # calibration. T4.5 slippage analysis (1082 trades) reported weighted
    # p90 = +2.3% adverse slippage, and T4.6-B sweep confirmed the heuristic
    # was ~4.6x too optimistic (classic strategy 199 trades: HEURISTIC PnL
    # -$4.87 vs EMPIRICAL PnL -$6.51, delta_pnl_pct=-33.68% FAIL). Using
    # 0.023 makes backtest defaults match live reality without needing ENV
    # overrides in production decision runs.
    #
    # Override via ENV `FILL_SPREAD_COST` for sensitivity sweeps (e.g. 0.005
    # to reproduce legacy pre-T4.7-C heuristic behavior).
    SPREAD_COST: float = float(os.getenv("FILL_SPREAD_COST", "0.023"))

    def __init__(
        self,
        mode: FillMode = FillMode.SIMPLE,
        min_liquidity: float = 0.0,
        market_impact_factor: float = 1.0,
        maker_queue_probability: float = 0.45,
        maker_rebate_bps: float = 0.0,
        latency_mean_ms: int = 0,
        latency_std_ms: int = 0,
    ):
        """
        Args:
            mode: fill simulation mode
            min_liquidity: minimum market volume to accept trade
            market_impact_factor: multiplier for market impact model
            maker_queue_probability: Phase 51 P51-06 — probability a posted
                limit order fills before the window closes. 0.45 mirrors the
                observed fill rate of the live maker_stats dashboard.
            maker_rebate_bps: Phase 51 P51-06 — maker rebate in basis points
                of notional (default 0 — Polymarket doesn't pay maker rebates
                by default, but this is env-configurable for forecasting).
            latency_mean_ms: Phase 65 — average signal→fill latency in ms.
                When > 0, adds Gaussian noise to fill price simulating REST
                submit delay. Price drifts during latency window.
            latency_std_ms: Phase 65 — standard deviation of latency.
        """
        self.mode = mode
        self.min_liquidity = min_liquidity
        self.impact_factor = market_impact_factor
        self.maker_queue_probability = max(0.0, min(1.0, maker_queue_probability))
        self.maker_rebate_bps = max(0.0, maker_rebate_bps)
        self.latency_mean_ms = max(0, latency_mean_ms)
        self.latency_std_ms = max(0, latency_std_ms)
        self._latency_slippage_applied = 0.0  # last computed latency slippage

    def simulate_fill(
        self,
        direction: Direction,
        amount_usd: float,
        snapshot: OrderbookSnapshot,
        market_volume: float = 0.0,
    ) -> FillResult:
        """
        Simulate filling an order at the current orderbook state.

        Args:
            direction: UP or DOWN
            amount_usd: trade size in USDC
            snapshot: current orderbook state
            market_volume: total market volume (for impact model)
        Returns:
            FillResult with fill details
        """
        # Liquidity filter
        if self.min_liquidity > 0 and market_volume < self.min_liquidity:
            return FillResult(
                filled=False,
                reason=f"Low liquidity: ${market_volume:.0f} < ${self.min_liquidity:.0f}",
            )

        # Get relevant prices
        if direction == Direction.UP:
            best_bid = snapshot.up_best_bid
            best_ask = snapshot.up_best_ask
            bid_depth = snapshot.up_bid_depth
            ask_depth = snapshot.up_ask_depth
        else:
            best_bid = snapshot.down_best_bid
            best_ask = snapshot.down_best_ask
            bid_depth = snapshot.down_bid_depth
            ask_depth = snapshot.down_ask_depth

        # Phase 75-fix: Derive ask from bid if missing (binary market: ask ≈ bid + spread)
        if best_ask <= 0 and best_bid > 0:
            best_ask = best_bid + 0.01  # 1c spread estimate
        # For binary markets: if ask=0 and bid=0, try deriving from opposite side
        if best_ask <= 0:
            opp_bid = snapshot.down_best_bid if direction == Direction.UP else snapshot.up_best_bid
            if opp_bid > 0:
                best_ask = round(1.0 - opp_bid + 0.01, 4)  # complementary price

        # Validate prices
        if best_ask <= 0 or best_ask >= 1.0:
            return FillResult(filled=False, reason="Invalid ask price")

        # Calculate fill price based on mode
        if self.mode == FillMode.SIMPLE:
            fill_price = best_ask + self.SPREAD_COST
            slippage = self.SPREAD_COST

        elif self.mode == FillMode.MIDPOINT:
            if best_bid > 0:
                fill_price = (best_bid + best_ask) / 2
            else:
                fill_price = best_ask
            # Add spread cost for crossing the spread
            fill_price = fill_price + self.SPREAD_COST
            slippage = fill_price - best_ask if best_ask > 0 else self.SPREAD_COST

        elif self.mode == FillMode.ORDERBOOK:
            fill_price = self._orderbook_walk(amount_usd, best_ask, ask_depth)
            # Add spread cost for immediate execution
            fill_price = fill_price + self.SPREAD_COST
            slippage = fill_price - best_ask

        elif self.mode == FillMode.MARKET_IMPACT:
            fill_price = self._market_impact_fill(amount_usd, best_ask, market_volume)
            # Add spread cost for immediate execution
            fill_price = fill_price + self.SPREAD_COST
            slippage = fill_price - best_ask

        elif self.mode == FillMode.MAKER:
            maker = self._maker_fill(amount_usd, best_bid, best_ask, bid_depth, direction, snapshot)
            return maker

        elif self.mode == FillMode.MAKER_HYBRID:
            maker = self._maker_fill(amount_usd, best_bid, best_ask, bid_depth, direction, snapshot)
            if maker.filled:
                return maker
            # Maker miss — fall through to SIMPLE taker path
            fill_price = best_ask + self.SPREAD_COST
            slippage = self.SPREAD_COST

        elif self.mode == FillMode.REAL_ORDERBOOK:
            # Phase 37: Walk through ACTUAL recorded L2 orderbook levels
            raw = snapshot.raw if hasattr(snapshot, "raw") else {}
            if direction == Direction.UP:
                asks = raw.get("up_asks", [])
            else:
                asks = raw.get("down_asks", [])

            if asks and len(asks) > 0:
                fill_price = self._real_orderbook_walk(amount_usd, asks)
                slippage = fill_price - best_ask if best_ask > 0 else 0
            else:
                # Fallback to MIDPOINT if no real orderbook data
                if best_bid > 0:
                    fill_price = (best_bid + best_ask) / 2 + self.SPREAD_COST
                else:
                    fill_price = best_ask + self.SPREAD_COST
                slippage = fill_price - best_ask if best_ask > 0 else self.SPREAD_COST

        else:
            fill_price = best_ask + self.SPREAD_COST
            slippage = self.SPREAD_COST

        # Phase 65: Latency-induced price drift.
        # During REST submit delay (typically 200-300ms), price moves against us
        # because the orderbook updates between signal and fill.
        # Model: latency_ms ~ N(μ, σ²); drift = latency × LATENCY_DRIFT_BPS_PER_MS.
        #
        # T4.7-C (2026-04-24): Default lowered 0.08 → 0.04 bps/ms. T4.6-B sweep
        # pair'ed this with spread/impact bumps; combined EMPIRICAL set matches
        # live fill telemetry better than legacy heuristic. Half-heuristic (0.04)
        # reflects that median latency drift is smaller than the heuristic
        # assumed while the spread component carries most adverse slippage.
        # Override via ENV `FILL_LATENCY_DRIFT_BPS_PER_MS` for sensitivity sweeps
        # (e.g. 0.08 to reproduce legacy pre-T4.7-C behavior).
        latency_drift = 0.0
        if self.latency_mean_ms > 0:
            import random

            lat_ms = max(50, random.gauss(self.latency_mean_ms, self.latency_std_ms))
            drift_bps_per_ms_env = float(os.getenv("FILL_LATENCY_DRIFT_BPS_PER_MS", "0.04"))
            drift_bps_per_ms = drift_bps_per_ms_env / 10000  # bps → fraction
            latency_drift = fill_price * lat_ms * drift_bps_per_ms
            fill_price += latency_drift
            slippage += latency_drift
            self._latency_slippage_applied = latency_drift

        # Calculate shares
        if fill_price <= 0:
            return FillResult(filled=False, reason="Zero fill price")

        shares = amount_usd / fill_price

        lat_tag = f" +lat={latency_drift*10000:.1f}bps" if latency_drift > 0.0001 else ""
        return FillResult(
            filled=True,
            fill_price=round(fill_price, 4),
            slippage=round(slippage, 6),
            fill_amount=amount_usd,
            shares=round(shares, 4),
            reason=f"{self.mode.value} fill @ {fill_price:.4f}{lat_tag}",
        )

    def _orderbook_walk(self, amount_usd: float, best_ask: float, total_ask_depth: float) -> float:
        """
        Walk through orderbook levels with depth-bucketed slippage tiers.
        Slippage increases with fill ratio (order size / available depth).

        Tier table (SYNTHETIC — NOT calibrated against live data):
          <10% depth:  0.2% slippage
          10-30%:      0.5%
          30-70%:      1.5%
          >70%:        3.0%

        These were chosen as plausible defaults during Phase 34 backtests
        without an empirical reference set. For real fills the canonical
        path is `REAL_ORDERBOOK` (VWAP against recorded L2 depth) — this
        simpler tier walk is a fallback when raw L2 isn't available.
        Calibration against the 1417-trade live realized_slippage history
        is tracked under Epic 4 T4.2 Faz B (see TASKS.md).
        """
        if total_ask_depth <= 0 or amount_usd <= 0:
            return best_ask

        fill_ratio = amount_usd / total_ask_depth

        if fill_ratio <= 0.1:
            # Small order — minimal impact
            slippage = 0.002
        elif fill_ratio <= 0.3:
            # Medium order — moderate impact
            slippage = 0.005
        elif fill_ratio <= 0.7:
            # Large order — significant impact
            slippage = 0.015
        else:
            # Very large order — deep impact
            slippage = 0.03

        return min(best_ask * (1 + slippage), 0.99)

    def _real_orderbook_walk(self, amount_usd: float, ask_levels: list) -> float:
        """
        Walk through REAL recorded L2 orderbook ask levels (VWAP fill).

        Phase 37: En gerçekçi fill simulation.
        Kaydedilmiş orderbook JSON'ındaki her seviyeden geçer.
        Küçük order → best_ask'ta fill. Büyük order → derinliğe göre VWAP.

        Args:
            amount_usd: USDC amount to fill
            ask_levels: [[price, size], [price, size], ...]
                        sorted by price ascending (best first)
        Returns:
            Volume-weighted average fill price
        """
        if not ask_levels or amount_usd <= 0:
            return 0.5  # fallback

        remaining_usd = amount_usd
        total_shares = 0.0
        total_cost = 0.0

        for level in ask_levels:
            if remaining_usd <= 0:
                break

            # Level format: [price, size] where size is in shares
            try:
                price = float(level[0])
                size_shares = float(level[1])
            except (IndexError, TypeError, ValueError):
                continue

            if price <= 0 or price >= 1.0 or size_shares <= 0:
                continue

            # How much USD can we fill at this level?
            level_usd_capacity = price * size_shares

            if remaining_usd <= level_usd_capacity:
                # Fill remaining at this level
                shares_at_level = remaining_usd / price
                total_shares += shares_at_level
                total_cost += remaining_usd
                remaining_usd = 0
            else:
                # Consume entire level, move to next
                total_shares += size_shares
                total_cost += level_usd_capacity
                remaining_usd -= level_usd_capacity

        if remaining_usd > 0:
            # Not enough depth — fill remainder at worst price + 2% penalty
            if ask_levels:
                try:
                    worst_price = float(ask_levels[-1][0])
                except (IndexError, TypeError, ValueError):
                    worst_price = 0.5
                penalty_price = min(worst_price * 1.02, 0.99)
                if penalty_price > 0:
                    extra_shares = remaining_usd / penalty_price
                    total_shares += extra_shares
                    total_cost += remaining_usd

        if total_shares <= 0:
            # No fill possible — return best ask
            try:
                return float(ask_levels[0][0])
            except (IndexError, TypeError, ValueError):
                return 0.5

        # VWAP = total_cost / total_shares
        vwap = total_cost / total_shares
        return min(round(vwap, 6), 0.99)

    def _maker_fill(
        self,
        amount_usd: float,
        best_bid: float,
        best_ask: float,
        bid_depth: float,
        direction: Direction,
        snapshot: OrderbookSnapshot,
    ) -> FillResult:
        """Phase 51 P51-06 — Probabilistic maker fill.

        Model:
          1. Target price = best_bid (post as maker one tick ahead is
             simulated as "at best_bid" for simplicity — conservative).
          2. Fill probability = base queue prob × size_adjustment × spread_adjustment
             - size_adjustment: penalise orders > 20% of bid_depth (queue jumping)
             - spread_adjustment: wider spread → higher fill chance (stale book)
          3. If filled → pay best_bid, earn rebate (if configured), no spread cost.
          4. If missed → return filled=False with reason so callers can fall back.

        This is deliberately simple and opinionated — it is NOT a market
        microstructure simulator. Its job is to give backtest a realistic
        "what if we only posted maker orders?" counterfactual.
        """
        if best_bid <= 0 or best_bid >= 1.0:
            return FillResult(filled=False, reason="Invalid bid price for maker")
        if best_ask <= 0 or best_ask >= 1.0:
            return FillResult(filled=False, reason="Invalid ask price for maker")

        # Phase 57: Depth-calibrated maker fill probability.
        #
        # OLD: static base 45% × simple size bucket × spread bucket.
        # NEW: base probability is driven by bid_depth itself (how deep
        #      the queue is ahead of us), then modulated by size and spread.
        #
        # Intuition: if $10K is resting on the bid, your $1 order sits
        # behind ~$5K on average — very unlikely to fill before market moves.
        # If only $50 is on the bid, you're near the front.
        #
        # Depth → base fill probability (empirical buckets):
        #   depth > $10K  → 15%  (deep queue, you're far back)
        #   depth > $5K   → 25%
        #   depth > $1K   → 35%
        #   depth > $500  → 50%
        #   depth > $100  → 65%
        #   depth ≤ $100  → 80%  (thin book, you're near front)
        if bid_depth > 10_000:
            depth_base_prob = 0.15
        elif bid_depth > 5_000:
            depth_base_prob = 0.25
        elif bid_depth > 1_000:
            depth_base_prob = 0.35
        elif bid_depth > 500:
            depth_base_prob = 0.50
        elif bid_depth > 100:
            depth_base_prob = 0.65
        else:
            depth_base_prob = 0.80

        # Size adjustment: penalise orders that are large relative to depth.
        size_adj = 1.0
        if bid_depth > 0:
            ratio = amount_usd / bid_depth
            if ratio <= 0.05:
                size_adj = 1.0
            elif ratio <= 0.2:
                size_adj = 0.85
            elif ratio <= 0.5:
                size_adj = 0.55
            else:
                size_adj = 0.25

        # Spread adjustment: wide spreads mean the book is stale, so a
        # posted order is more likely to get picked off by aggressive flow.
        spread = max(0.0, best_ask - best_bid)
        if spread >= 0.02:
            spread_adj = 1.15
        elif spread >= 0.01:
            spread_adj = 1.05
        else:
            spread_adj = 0.95

        fill_prob = min(1.0, depth_base_prob * size_adj * spread_adj)

        # Deterministic "fill or not": we use a hash of the snapshot clock
        # (or id) so the same backtest run produces the same result — crucial
        # for reproducibility. Not true randomness, but unbiased enough.
        seed_source = getattr(snapshot, "ts", None) or getattr(snapshot, "timestamp", 0)
        try:
            seed = hash(
                (
                    float(seed_source),
                    float(amount_usd),
                    direction.value if hasattr(direction, "value") else str(direction),
                )
            )
        except Exception:
            seed = hash(str(seed_source))
        roll = (seed & 0xFFFF) / 0xFFFF  # 0..1

        if roll > fill_prob:
            return FillResult(
                filled=False,
                reason=f"maker miss (p={fill_prob:.2f}, roll={roll:.2f})",
                is_maker=True,
            )

        fill_price = best_bid  # posted at bid, filled at bid
        shares = amount_usd / fill_price
        rebate = amount_usd * (self.maker_rebate_bps / 10_000.0)

        return FillResult(
            filled=True,
            fill_price=round(fill_price, 4),
            slippage=round(best_ask - fill_price, 6),  # negative slippage vs ask
            fill_amount=amount_usd,
            shares=round(shares, 4),
            reason=f"maker fill @ {fill_price:.4f} (p={fill_prob:.2f})",
            is_maker=True,
            rebate=round(rebate, 6),
        )

    # Default impact scale — what fraction of best_ask the √-impact term
    # contributes.
    #
    # T4.7-C (2026-04-24): Default raised 0.01 → 0.025 based on T4.5
    # realized_slippage empirical mean+1σ. Pre-T4.7-C value (0.01) landed a
    # $1000 order in $100k volume at ~10bps impact, but live SOL/ETH markets
    # (lower depth than BTC) showed ~2-3x that in the 1082-trade sample.
    # Override via ENV `FILL_IMPACT_SCALE` for sensitivity sweeps
    # (e.g. 0.01 to reproduce legacy pre-T4.7-C behavior).
    IMPACT_SCALE: float = float(os.getenv("FILL_IMPACT_SCALE", "0.025"))
    # Minimum spread floor — even tiny orders pay this. Polymarket UI
    # observation; not a hard fee, just a slippage proxy.
    IMPACT_MIN_FLOOR: float = float(os.getenv("FILL_IMPACT_MIN_FLOOR", "0.001"))

    def _market_impact_fill(
        self, amount_usd: float, best_ask: float, market_volume: float
    ) -> float:
        """
        Market impact model: slippage ∝ √(order_size / market_volume).

        Approximation of the standard square-root impact law (used by
        Almgren-Chriss / Kyle's lambda style models). The constant scaling
        factor `IMPACT_SCALE` (default 0.01 → ~1% max impact for orders
        equal to total volume) is heuristic, NOT calibrated against
        Polymarket fills. Pending Epic 4 T4.2 Faz B empirical calibration.

        Formula: slippage = max(IMPACT_MIN_FLOOR,
                                √(size/volume) × impact_factor × IMPACT_SCALE)
        """
        if market_volume <= 0 or amount_usd <= 0:
            return best_ask

        # √(size/volume) × impact_factor × IMPACT_SCALE
        impact = math.sqrt(amount_usd / market_volume) * self.impact_factor
        impact = impact * self.IMPACT_SCALE

        # Spread floor — even small orders pay this minimum.
        impact = max(self.IMPACT_MIN_FLOOR, impact)

        return min(best_ask * (1 + impact), 0.99)
