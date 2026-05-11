"""
PolyPaper Bot - Trade Memory System (Phase 77)
===============================================
Persistent learning from wins/losses. Remembers what works, what doesn't.
Feeds back into signal evaluation via pattern matching.

Key concept: Every closed trade creates a "memory" with:
  - Context: strategy, direction, price zone, time zone, market conditions
  - Outcome: won/lost, PnL, edge vs expected

The system builds pattern statistics:
  - "momentum + BTC + 60-70c zone + morning = 72% WR (18 trades)"
  - "contrarian + ETH + 40-50c zone + weekend = 38% WR (8 trades) ⚠️"

These feed back as confidence multipliers in signal evaluation.

DB Table: trade_memory (created via migration)
ENV:
    TRADE_MEMORY_ENABLED=true
    TRADE_MEMORY_MIN_TRADES=5      # Min trades to form a pattern
    TRADE_MEMORY_LOOKBACK_DAYS=30  # How far back to look
    TRADE_MEMORY_BOOST_MAX=0.15    # Max confidence boost from memory
    TRADE_MEMORY_PENALTY_MAX=0.20  # Max confidence penalty from memory
"""

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Dict, List, Optional

import aiosqlite

from telegram_bot.templates.safe_html import esc

logger = logging.getLogger("polypaper.core.trade_memory")

# ── ENV ──
MEMORY_ENABLED = os.getenv("TRADE_MEMORY_ENABLED", "true").lower() == "true"
MIN_PATTERN_TRADES = int(os.getenv("TRADE_MEMORY_MIN_TRADES", "5"))
LOOKBACK_DAYS = int(os.getenv("TRADE_MEMORY_LOOKBACK_DAYS", "30"))
BOOST_MAX = float(os.getenv("TRADE_MEMORY_BOOST_MAX", "0.15"))
PENALTY_MAX = float(os.getenv("TRADE_MEMORY_PENALTY_MAX", "0.20"))


@dataclass
class PatternStats:
    """Statistics for a specific trading pattern."""

    pattern_key: str  # e.g. "momentum:BTC:60-70:morning"
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0
    confidence_mult: float = 1.0  # >1 = boost, <1 = penalty
    last_updated: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.pattern_key,
            "trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "pnl": round(self.total_pnl, 2),
            "wr": round(self.win_rate, 1),
            "mult": round(self.confidence_mult, 3),
        }


@dataclass
class TradeLesson:
    """A single lesson learned from a trade."""

    strategy_id: str
    asset: str  # BTC, ETH, SOL, etc.
    price_zone: str  # "0-10", "10-20", ..., "90-100"
    time_zone: str  # "morning", "afternoon", "evening", "night"
    day_type: str  # "weekday", "weekend"
    direction: str  # "up", "down"
    result: str  # "won", "lost"
    pnl: float
    signal_score: float
    entry_price: float
    created_at: str


def _price_zone(price: float) -> str:
    """Bucket price into 10c zones."""
    if price <= 0:
        return "0-10"
    zone = int(price * 100) // 10 * 10
    return f"{zone}-{min(zone + 10, 100)}"


def _time_zone(dt: Optional[datetime] = None) -> str:
    """Categorize UTC hour into time zone."""
    if dt is None:
        dt = datetime.now(UTC)
    h = dt.hour
    if 6 <= h < 12:
        return "morning"
    elif 12 <= h < 18:
        return "afternoon"
    elif 18 <= h < 24:
        return "evening"
    return "night"


def _day_type(dt: Optional[datetime] = None) -> str:
    if dt is None:
        dt = datetime.now(UTC)
    return "weekend" if dt.weekday() >= 5 else "weekday"


def _asset_from_slug(slug: str) -> str:
    """Extract asset name from event slug."""
    slug_lower = slug.lower()
    for a in ("btc", "eth", "sol", "xrp", "doge", "ada", "matic", "avax", "link", "dot"):
        if a in slug_lower:
            return a.upper()
    return "OTHER"


class TradeMemory:
    """
    Persistent trade memory system.

    Records patterns from closed trades and builds statistical models
    that feed back into signal evaluation.
    """

    def __init__(self):
        self.db = None
        self._cache: Dict[str, PatternStats] = {}
        self._last_refresh = 0.0
        self._refresh_interval = 300  # 5 min cache
        self._mistakes: List[dict] = []  # recent mistakes for /mistakes command

    async def initialize(self, db):
        """Initialize with DB reference and ensure table exists."""
        self.db = db
        try:
            await db.conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    price_zone TEXT NOT NULL,
                    time_zone TEXT NOT NULL,
                    day_type TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    result TEXT NOT NULL,
                    pnl REAL DEFAULT 0.0,
                    signal_score REAL DEFAULT 0.0,
                    entry_price REAL DEFAULT 0.0,
                    pattern_key TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            await db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tm_pattern ON trade_memory(pattern_key)"
            )
            await db.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tm_created ON trade_memory(created_at)"
            )
            await db.conn.commit()
            logger.info("🧠 Phase 77: Trade Memory initialized")
        except (aiosqlite.Error, AttributeError) as e:
            # T1.4 Faz 3: CREATE TABLE + 2 CREATE INDEX + commit. Realistic
            # modes: aiosqlite.Error (DDL syntax error, locked DB, disk full),
            # AttributeError (db.conn missing during shutdown race).
            logger.warning(f"trade_memory init: {e}")

    async def record(
        self,
        strategy_id: str,
        slug: str,
        direction: str,
        result: str,
        pnl: float,
        signal_score: float = 0.0,
        entry_price: float = 0.0,
    ):
        """Record a completed trade into memory."""
        if not MEMORY_ENABLED or self.db is None:
            return

        asset = _asset_from_slug(slug)
        pz = _price_zone(entry_price)
        tz = _time_zone()
        dt = _day_type()
        pattern_key = f"{strategy_id}:{asset}:{pz}:{tz}:{dt}"
        now = datetime.now(UTC).isoformat()

        try:
            await self.db.conn.execute(
                """INSERT INTO trade_memory
                   (strategy_id, asset, price_zone, time_zone, day_type,
                    direction, result, pnl, signal_score, entry_price,
                    pattern_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_id,
                    asset,
                    pz,
                    tz,
                    dt,
                    direction,
                    result,
                    pnl,
                    signal_score,
                    entry_price,
                    pattern_key,
                    now,
                ),
            )
            await self.db.conn.commit()

            # Track mistakes (losses with high signal score = overconfident)
            if result == "lost" and signal_score > 0.5:
                self._mistakes.append(
                    {
                        "strategy": strategy_id,
                        "asset": asset,
                        "zone": pz,
                        "time": tz,
                        "score": round(signal_score, 2),
                        "pnl": round(pnl, 2),
                        "when": now[:16],
                    }
                )
                if len(self._mistakes) > 50:
                    self._mistakes = self._mistakes[-50:]

            # Invalidate cache for this pattern
            self._cache.pop(pattern_key, None)

        except (aiosqlite.Error, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: INSERT INTO trade_memory + commit, plus self._mistakes
            # list mutation. Realistic modes:
            #   - aiosqlite.Error: INSERT failed (table missing / locked /
            #     constraint violation).
            #   - TypeError: numeric bind edge cases (pnl/signal_score None).
            #   - ValueError: coerce failures on numeric columns.
            #   - AttributeError: self.db.conn missing during shutdown.
            logger.debug(f"trade_memory.record: {e}")

    async def get_pattern(
        self, strategy_id: str, slug: str, entry_price: float = 0.0
    ) -> Optional[PatternStats]:
        """Look up pattern statistics for current trade context."""
        if not MEMORY_ENABLED or self.db is None:
            return None

        asset = _asset_from_slug(slug)
        pz = _price_zone(entry_price)
        tz = _time_zone()
        dt = _day_type()
        pattern_key = f"{strategy_id}:{asset}:{pz}:{tz}:{dt}"

        # Check cache
        if pattern_key in self._cache and time.time() - self._last_refresh < self._refresh_interval:
            return self._cache[pattern_key]

        try:
            cutoff = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).isoformat()
            rows = await self.db.conn.execute_fetchall(
                """SELECT result, pnl FROM trade_memory
                   WHERE pattern_key = ? AND created_at > ?""",
                (pattern_key, cutoff),
            )

            if not rows or len(rows) < MIN_PATTERN_TRADES:
                return None

            wins = sum(1 for r in rows if r[0] == "won")
            losses = sum(1 for r in rows if r[0] == "lost")
            total = len(rows)
            total_pnl = sum(r[1] for r in rows)
            wr = (wins / total * 100) if total > 0 else 50.0

            # Confidence multiplier:
            # WR > 60% → boost (max BOOST_MAX)
            # WR < 45% → penalty (max PENALTY_MAX)
            # 45-60% → neutral (1.0)
            if wr >= 60:
                mult = 1.0 + min((wr - 60) / 40 * BOOST_MAX, BOOST_MAX)
            elif wr <= 45:
                mult = 1.0 - min((45 - wr) / 45 * PENALTY_MAX, PENALTY_MAX)
            else:
                mult = 1.0

            stats = PatternStats(
                pattern_key=pattern_key,
                total_trades=total,
                wins=wins,
                losses=losses,
                total_pnl=round(total_pnl, 2),
                avg_pnl=round(total_pnl / total, 2) if total > 0 else 0.0,
                win_rate=round(wr, 1),
                confidence_mult=round(mult, 3),
                last_updated=datetime.now(UTC).isoformat()[:16],
            )

            self._cache[pattern_key] = stats
            self._last_refresh = time.time()
            return stats

        except (aiosqlite.Error, IndexError, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: SELECT + per-row r[0]/r[1] access + comprehension
            # sum/aggregate + WR multiplier arithmetic + PatternStats
            # construct. Realistic modes:
            #   - aiosqlite.Error: SELECT failed (table missing / locked).
            #   - IndexError: row shape drift (result, pnl columns dropped).
            #   - TypeError: None in sum(r[1]) when pnl column is NULL.
            #   - ValueError: numeric coerce in round/min/max.
            #   - AttributeError: self.db.conn missing during shutdown.
            logger.debug(f"trade_memory.get_pattern: {e}")
            return None

    async def get_worst_patterns(self, limit: int = 10) -> List[PatternStats]:
        """Get worst-performing patterns (mistakes to avoid)."""
        if not MEMORY_ENABLED or self.db is None:
            return []

        try:
            cutoff = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).isoformat()
            rows = await self.db.conn.execute_fetchall(
                """SELECT pattern_key, COUNT(*) as cnt,
                          SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) as wins,
                          SUM(pnl) as total_pnl
                   FROM trade_memory
                   WHERE created_at > ?
                   GROUP BY pattern_key
                   HAVING cnt >= ?
                   ORDER BY (CAST(wins AS REAL) / cnt) ASC, total_pnl ASC
                   LIMIT ?""",
                (cutoff, MIN_PATTERN_TRADES, limit),
            )

            results = []
            for r in rows:
                pk, cnt, wins, tpnl = r
                wr = (wins / cnt * 100) if cnt > 0 else 50
                results.append(
                    PatternStats(
                        pattern_key=pk,
                        total_trades=cnt,
                        wins=wins,
                        losses=cnt - wins,
                        total_pnl=round(tpnl, 2),
                        avg_pnl=round(tpnl / cnt, 2) if cnt > 0 else 0,
                        win_rate=round(wr, 1),
                    )
                )
            return results
        except (aiosqlite.Error, IndexError, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: SELECT GROUP BY HAVING + tuple unpack
            # (pk, cnt, wins, tpnl = r) + WR arithmetic + PatternStats.
            # Realistic modes:
            #   - aiosqlite.Error: SELECT failed.
            #   - ValueError: tuple unpack on row shape drift.
            #   - IndexError: defensive for future column reorder.
            #   - TypeError: None aritmetik (tpnl/cnt when SUM is NULL).
            #   - AttributeError: self.db.conn shutdown race.
            logger.debug(f"trade_memory.get_worst: {e}")
            return []

    async def get_best_patterns(self, limit: int = 10) -> List[PatternStats]:
        """Get best-performing patterns (strengths to exploit)."""
        if not MEMORY_ENABLED or self.db is None:
            return []

        try:
            cutoff = (datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)).isoformat()
            rows = await self.db.conn.execute_fetchall(
                """SELECT pattern_key, COUNT(*) as cnt,
                          SUM(CASE WHEN result='won' THEN 1 ELSE 0 END) as wins,
                          SUM(pnl) as total_pnl
                   FROM trade_memory
                   WHERE created_at > ?
                   GROUP BY pattern_key
                   HAVING cnt >= ?
                   ORDER BY (CAST(wins AS REAL) / cnt) DESC, total_pnl DESC
                   LIMIT ?""",
                (cutoff, MIN_PATTERN_TRADES, limit),
            )

            results = []
            for r in rows:
                pk, cnt, wins, tpnl = r
                wr = (wins / cnt * 100) if cnt > 0 else 50
                results.append(
                    PatternStats(
                        pattern_key=pk,
                        total_trades=cnt,
                        wins=wins,
                        losses=cnt - wins,
                        total_pnl=round(tpnl, 2),
                        avg_pnl=round(tpnl / cnt, 2) if cnt > 0 else 0,
                        win_rate=round(wr, 1),
                    )
                )
            return results
        except (aiosqlite.Error, IndexError, TypeError, ValueError, AttributeError) as e:
            # T1.4 Faz 3: identical aggregate pattern to get_worst_patterns
            # (SELECT GROUP BY HAVING, only ORDER BY differs). Exception
            # surface identical — same narrow tuple.
            logger.debug(f"trade_memory.get_best: {e}")
            return []

    def format_telegram(self, patterns: List[PatternStats], title: str = "Patterns") -> str:
        """Format pattern list for Telegram."""
        if not patterns:
            return f"<i>Henüz yeterli {esc(title.lower())} verisi yok.</i>"

        lines = [f"🧠 <b>{esc(title)}</b>\n"]
        for p in patterns:
            parts = p.pattern_key.split(":")
            label = ":".join(parts[:2]) if len(parts) >= 2 else p.pattern_key
            zone = parts[2] if len(parts) > 2 else "?"
            emoji = "🟢" if p.win_rate >= 60 else ("🔴" if p.win_rate < 45 else "🟡")

            lines.append(
                f"{emoji} <b>{esc(label)}</b> [{esc(zone)}c]\n"
                f"   {p.total_trades}t | WR {p.win_rate:.0f}% | "
                f"PnL ${p.total_pnl:+.2f} (avg ${p.avg_pnl:+.2f})"
            )

        return "\n".join(lines)

    def format_mistakes_telegram(self) -> str:
        """Format recent mistakes for Telegram."""
        if not self._mistakes:
            return "<i>Henüz overconfident hata kaydı yok.</i>"

        lines = ["🚫 <b>Son Hatalar</b> (yüksek sinyal, kayıp)\n"]
        for m in self._mistakes[-10:]:
            lines.append(
                f"⚠️ {esc(m['strategy'])}:{esc(m['asset'])} [{esc(m['zone'])}c] "
                f"score={m['score']} → ${m['pnl']}"
            )
        return "\n".join(lines)


# ── Singleton ──
_instance: Optional[TradeMemory] = None


def get_trade_memory() -> TradeMemory:
    global _instance
    if _instance is None:
        _instance = TradeMemory()
    return _instance
