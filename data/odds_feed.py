"""
PolyPaper Bot - Odds Feed (Phase 10.5 FIX)
ACTUALLY collects odds data for indicator calculations.

3 data sources:
1. DB startup: loads recent odds_history on boot
2. Scanner: record_odds() called every scan cycle
3. WS callback: on_ws_price() called on every WS price update

The odds_series is what Signal Fusion uses for EMA, momentum, volatility.
"""

import logging
from collections import defaultdict, deque

import aiosqlite

logger = logging.getLogger("polypaper.data.odds_feed")


class OddsFeed:
    def __init__(self, client=None, window_size: int = 200):
        self.client = client
        self.window_size = window_size
        # slug → deque of float (up_odds values, newest last)
        self._series: dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._count = 0

    async def load_from_db(self, db):
        """Load recent odds_history from DB on startup."""
        try:
            async with db.conn.execute(
                """SELECT event_slug, up_odds FROM odds_history
                   WHERE up_odds IS NOT NULL
                   ORDER BY timestamp DESC LIMIT 2000"""
            ) as c:
                rows = []
                async for row in c:
                    rows.append((row["event_slug"], row["up_odds"]))

            # Reverse to chronological order (oldest first)
            rows.reverse()
            for slug, up_odds in rows:
                self._series[slug].append(float(up_odds))
                self._count += 1

            logger.info(
                f"OddsFeed: loaded {self._count} records for {len(self._series)} slugs from DB"
            )
        except (aiosqlite.Error, ValueError, TypeError) as e:
            # T11.8-B (2026-04-24): narrow from bare Exception. DB read +
            # float() parse on `up_odds`. AttributeError covered by code path.
            logger.error(f"OddsFeed DB load: {type(e).__name__}: {e}")

    def record_odds(self, slug: str, up_odds: float, down_odds: float = None):
        """Record a new odds snapshot. Called by Scanner on every scan."""
        if up_odds is not None and 0.01 < up_odds < 0.99:
            self._series[slug].append(up_odds)
            self._count += 1

    def on_ws_price(self, token_id: str, price: float, slug: str = ""):
        """Called by WS on every price update (optional, for sub-second resolution)."""
        if slug and price and 0.01 < price < 0.99:
            self._series[slug].append(price)

    def get_odds_series(self, slug: str, side: str = "up") -> list[float]:
        """Get chronological list of up_odds for indicator calculation."""
        return list(self._series.get(slug, []))

    def get_data_count(self, slug: str) -> int:
        return len(self._series.get(slug, []))

    def get_last(self, slug: str) -> dict | None:
        """Phase 82c — Return most recent odds as dict.

        engine.py:653 ORPHAN settlement path'i bu imzayı bekliyor:
            last_odds = self.odds_feed.get_last(slug)
            if last_odds: up = last_odds.get("up", 0.5)

        Returns:
            {"up": float, "down": float}  — son snapshot varsa
            None                           — slug için kayıt yoksa

        Not: down = 1.0 - up (binary market, fee ihmal).
        """
        series = self._series.get(slug)
        if not series:
            return None
        try:
            up = float(series[-1])
        except (TypeError, ValueError, IndexError):
            return None
        # Clamp 0-1 safety
        up = max(0.0, min(1.0, up))
        return {"up": up, "down": 1.0 - up}

    def get_status(self) -> dict:
        return {
            "total_records": self._count,
            "tracked_slugs": len(self._series),
            "slug_sizes": {
                k: len(v) for k, v in sorted(self._series.items(), key=lambda x: -len(x[1]))[:5]
            },
        }
