"""Whale Flow Signal - Phase 60 Enhancement
Detects large order flow direction from whale trades table ($1000+ notional).

The whale_trades table captures OTC and large block orders that may not appear
in the L2 orderbook. This signal aggregates recent whale activity to detect
directional flow bias that retail traders miss.

Source: Phase 60 Ultra Analysis Report — "Whale Signal" section identifies that
whale flows often precede 5-30 minute price moves. Mining this signal improves
entry confidence by +12-18% in backtests.
"""

import logging
import os
import time

import aiosqlite

logger = logging.getLogger("polypaper.core.signals.whale_flow")


class WhaleFlowSignal:
    """Tracks recent whale trades and generates directional signal.

    Public Attributes:
        lookback_seconds (int): Time window to aggregate whale trades (default 300s = 5min)
        min_trades (int): Minimum trades required to generate signal (default 2)
        min_volume_usd (float): Minimum total whale volume to activate signal (default 100)
    """

    def __init__(
        self, lookback_seconds: int = 300, min_trades: int = 2, min_volume_usd: float = 100.0
    ):
        """Initialize whale flow signal tracker.

        Args:
            lookback_seconds: Time window in seconds (default 5 minutes)
            min_trades: Minimum number of whale trades to generate signal
            min_volume_usd: Minimum total notional volume in USD
        """
        self.lookback_seconds = lookback_seconds
        self.min_trades = min_trades
        self.min_volume_usd = min_volume_usd
        self._last_query_ts = 0.0
        self._cache = {}  # {slug: (timestamp, signal_value)}

        # ENV overrides (Phase 79: all signal params should be tunable)
        _env_lookback = os.getenv("WHALE_LOOKBACK_SECONDS")
        if _env_lookback:
            try:
                self.lookback_seconds = int(_env_lookback)
            except ValueError:
                pass

        _env_min_trades = os.getenv("WHALE_MIN_TRADES")
        if _env_min_trades:
            try:
                self.min_trades = int(_env_min_trades)
            except ValueError:
                pass

        _env_min_vol = os.getenv("WHALE_MIN_VOLUME_USD")
        if _env_min_vol:
            try:
                self.min_volume_usd = float(_env_min_vol)
            except ValueError:
                pass

    async def compute(self, db, slug: str, direction: str) -> float:
        """Compute whale flow signal asynchronously.

        Queries whale_trades table for recent activity and computes a directional
        signal. Positive signal = flow supports the trade direction.
        Negative signal = flow opposes it.

        Args:
            db: Database connection object
            slug: Market slug (e.g., "BTC-2025-01-10")
            direction: "up" or "down" (trade direction to align signal)

        Returns:
            Signal value in [-1.0, 1.0].
            0.0 if insufficient whale data.
            Positive = flow supports direction.
            Negative = flow opposes direction.

        Raises:
            No exceptions — logs errors and returns 0.0 on query failure.
        """
        # Extract asset from slug (e.g., "BTC-2025-01-10" -> "BTC")
        asset = slug.split("-")[0] if "-" in slug else slug

        # Check cache (5 second TTL to avoid hammering DB)
        now = time.time()
        cache_key = f"{slug}_{direction}"
        if cache_key in self._cache:
            ts, val = self._cache[cache_key]
            if now - ts < 5.0:
                logger.debug(f"[WHALE] Cache hit: {cache_key} = {val:.3f}")
                return val

        cutoff_ms = int((now - self.lookback_seconds) * 1000)

        try:
            # Query whale_trades for recent activity on this asset
            rows = await db.conn.execute_fetchall(
                """
                SELECT side, COUNT(*) as trade_count, SUM(notional_usd) as total_notional
                FROM whale_trades
                WHERE slug LIKE ? AND ts_ms > ?
                GROUP BY side
                """,
                (f"%{asset}%", cutoff_ms),
            )
        except aiosqlite.Error as e:
            # T1.4 Faz 3: try body is a single aiosqlite fetch — narrow to
            # DB error. whale_trades table missing / locked => 0.0 signal
            # (safe default, keeps engine running on fresh installs).
            logger.debug(f"[WHALE] Query error on {slug}: {e}")
            return 0.0

        if not rows:
            logger.debug(f"[WHALE] No whale trades for {slug} in last {self.lookback_seconds}s")
            return 0.0

        # Parse buy/sell volumes
        buy_vol = 0.0
        sell_vol = 0.0
        trade_count = 0

        for row in rows:
            side, count, total = row
            trade_count += count
            if side == "buy":
                buy_vol = total or 0.0
            elif side == "sell":
                sell_vol = total or 0.0

        # Check minimum constraints
        total_vol = buy_vol + sell_vol

        if trade_count < self.min_trades:
            logger.debug(
                f"[WHALE] Insufficient trades: {trade_count} < {self.min_trades} for {slug}"
            )
            return 0.0

        if total_vol < self.min_volume_usd:
            logger.debug(
                f"[WHALE] Insufficient volume: ${total_vol:.2f} < ${self.min_volume_usd} for {slug}"
            )
            return 0.0

        # Compute net flow ratio: buy_vol - sell_vol / total
        # Range: [-1, 1] where +1 = 100% buys, -1 = 100% sells
        net_flow = (buy_vol - sell_vol) / total_vol

        # Align with trade direction
        # If direction="down" and we see net selling (negative net_flow), that's GOOD (aligned)
        # If direction="down" and we see net buying (positive net_flow), that's BAD (opposed)
        if direction == "down":
            net_flow = -net_flow

        # Clamp to [-1, 1]
        signal = max(min(net_flow, 1.0), -1.0)

        # Cache the result
        self._cache[cache_key] = (now, signal)

        logger.debug(
            f"[WHALE] {slug} {direction}: buys=${buy_vol:.2f}, sells=${sell_vol:.2f}, "
            f"net_flow={net_flow:.3f}, signal={signal:.3f} ({trade_count} trades)"
        )

        return signal

    def clear_cache(self):
        """Clear the internal cache (useful for testing or manual reset)."""
        self._cache.clear()
