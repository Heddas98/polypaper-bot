"""
PolyPaper Bot - Automated Data Collector
Runs periodically (via cron/bat/Telegram) to build up historical data archive.

Strategy: "Collect & Cache"
  - PolyBackTest free tier gives rolling window (last 50 markets)
  - Each run captures new markets + their snapshots
  - Over time we build a deep archive:
    * 1 month  → ~1500 markets
    * 3 months → ~4500 markets
    * 6 months → ~9000 markets (PolyBackTest-level)

  - Binance data is unlimited and free — backfill any range
  - Gamma gives market outcomes for validation

Usage:
  py -3.11 -m backtest.data_sources.collector          # full collection
  py -3.11 -m backtest.data_sources.collector --quick   # only new markets
  py -3.11 -m backtest.data_sources.collector --stats   # show cache stats
  py -3.11 -m backtest.data_sources.collector --binance # binance only

Schedule: Windows Task Scheduler or start_collector.bat (every 8 hours)
"""
import os
import sys
import asyncio
import logging
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from backtest.data_sources.cache import BacktestCache
from backtest.data_sources.polybacktest import PolyBackTestClient
from backtest.data_sources.binance_hist import BinanceHistClient
from backtest.data_sources.gamma_hist import GammaHistClient

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("polypaper.collector")


class DataCollector:
    """
    Orchestrates data collection from all sources.
    Designed to run periodically via cron/scheduler.
    """

    def __init__(self):
        self.cache = BacktestCache()
        self.pbt = PolyBackTestClient(cache=self.cache)
        self.binance = BinanceHistClient(cache=self.cache)
        self.gamma = GammaHistClient(cache=self.cache)
        self.stats = {
            "new_markets": 0,
            "new_snapshots": 0,
            "new_klines": 0,
            "new_gamma_markets": 0,
            "errors": 0,
            "start_time": 0,
        }

    async def init(self):
        """Initialize all clients."""
        await self.cache.init()
        await self.pbt.init()
        await self.binance.init()
        await self.gamma.init()
        self.stats["start_time"] = time.time()
        logger.info("=" * 60)
        logger.info("Data Collector initialized — %s",
                     datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        logger.info("=" * 60)

    async def close(self):
        """Close all clients."""
        await self.pbt.close()
        await self.binance.close()
        await self.gamma.close()
        await self.cache.close()

    # ── Main Collection Routines ─────────────────────────────

    async def collect_polybacktest(self, coins: list = None,
                                    market_types: list = None):
        """
        Collect markets + snapshots from PolyBackTest API.
        Free tier: 50 BTC 5m/15m, 24 1h/4h, 5 24h.
        """
        if coins is None:
            coins = ["btc"]
        if market_types is None:
            market_types = ["5m", "15m", "1h"]

        logger.info("--- PolyBackTest Collection ---")

        for coin in coins:
            for mtype in market_types:
                try:
                    logger.info("Fetching %s %s markets...", coin, mtype)
                    result = await self.pbt.fetch_backtest_data(
                        coin=coin, market_type=mtype
                    )
                    n_markets = len(result.get("markets", []))
                    n_snaps = result.get("total_snapshots", 0)
                    errors = result.get("errors", 0)

                    self.stats["new_markets"] += n_markets
                    self.stats["new_snapshots"] += n_snaps
                    self.stats["errors"] += errors

                    logger.info(
                        "  %s %s: %d markets, %d snapshots, %d errors",
                        coin.upper(), mtype, n_markets, n_snaps, errors
                    )
                except Exception as e:
                    logger.error("  %s %s failed: %s", coin, mtype, e)
                    self.stats["errors"] += 1

    async def collect_binance(self, coins: list = None,
                               intervals: list = None,
                               lookback_hours: int = 24):
        """
        Collect Binance kline data for backtesting.
        This is unlimited and free — we collect more aggressively.
        """
        if coins is None:
            coins = ["btc", "eth", "sol"]
        if intervals is None:
            intervals = ["1m", "5m", "15m"]

        logger.info("--- Binance Kline Collection ---")

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (lookback_hours * 3600 * 1000)

        for coin in coins:
            for interval in intervals:
                try:
                    logger.info("Fetching %s %s klines (last %dh)...",
                                coin, interval, lookback_hours)
                    klines = await self.binance.get_klines_range(
                        coin=coin, interval=interval,
                        start_ms=start_ms, end_ms=end_ms
                    )
                    self.stats["new_klines"] += len(klines)
                    logger.info("  %s %s: %d klines",
                                coin.upper(), interval, len(klines))
                except Exception as e:
                    logger.error("  %s %s failed: %s", coin, interval, e)
                    self.stats["errors"] += 1

    async def collect_gamma(self, coins: list = None, max_pages: int = 5):
        """
        Collect resolved market metadata from Gamma API.
        Gives us market outcomes (winner) for backtest validation.
        """
        if coins is None:
            coins = ["btc", "eth", "sol"]

        logger.info("--- Gamma Resolved Markets ---")

        for coin in coins:
            try:
                logger.info("Fetching %s resolved markets...", coin)
                markets = await self.gamma.get_all_resolved(
                    coin=coin, max_pages=max_pages
                )
                self.stats["new_gamma_markets"] += len(markets)
                logger.info("  %s: %d resolved markets", coin.upper(),
                             len(markets))
            except Exception as e:
                logger.error("  %s failed: %s", coin, e)
                self.stats["errors"] += 1

    async def collect_funding_rates(self, coins: list = None):
        """Collect Binance funding rate history."""
        if coins is None:
            coins = ["btc", "eth", "sol"]

        logger.info("--- Binance Funding Rates ---")

        for coin in coins:
            try:
                rates = await self.binance.get_funding_rate(coin=coin,
                                                            limit=500)
                logger.info("  %s: %d funding rates", coin.upper(),
                             len(rates))
            except Exception as e:
                logger.error("  %s funding failed: %s", coin, e)

    # ── Full Collection ──────────────────────────────────────

    async def run_full(self):
        """Run complete data collection cycle."""
        logger.info("Starting FULL collection cycle...")
        await self.collect_polybacktest()
        await self.collect_binance(lookback_hours=48)
        await self.collect_gamma()
        await self.collect_funding_rates()
        self._print_summary()

    async def run_quick(self):
        """Quick collection — only PolyBackTest new markets."""
        logger.info("Starting QUICK collection (PBT only)...")
        await self.collect_polybacktest(
            coins=["btc"], market_types=["5m", "15m"]
        )
        self._print_summary()

    async def run_binance_only(self):
        """Binance-only collection with deep backfill."""
        logger.info("Starting BINANCE-ONLY collection...")
        await self.collect_binance(
            coins=["btc", "eth", "sol"],
            intervals=["1m", "5m", "15m", "1h"],
            lookback_hours=168  # 7 days
        )
        await self.collect_funding_rates()
        self._print_summary()

    async def show_stats(self):
        """Show current cache statistics."""
        stats = await self.cache.stats()
        expired = await self.cache.cleanup_expired()

        print("\n" + "=" * 50)
        print("  BACKTEST DATA CACHE STATISTICS")
        print("=" * 50)
        print(f"  API Cache entries:    {stats.get('api_cache', 0)}")
        print(f"  Markets cached:       {stats.get('market_cache', 0)}")
        print(f"  Snapshots cached:     {stats.get('snapshot_cache', 0)}")
        print(f"  Klines cached:        {stats.get('kline_cache', 0)}")
        print(f"  Expired cleaned:      {expired}")
        print(f"  DB path:              {self.cache.db_path}")
        print(f"  DB size:              {self._db_size()}")
        print("=" * 50 + "\n")

    # ── Helpers ──────────────────────────────────────────────

    def _print_summary(self):
        """Print collection summary."""
        elapsed = time.time() - self.stats["start_time"]
        print("\n" + "=" * 50)
        print("  COLLECTION SUMMARY")
        print("=" * 50)
        print(f"  Duration:          {elapsed:.1f}s")
        print(f"  New markets (PBT): {self.stats['new_markets']}")
        print(f"  New snapshots:     {self.stats['new_snapshots']}")
        print(f"  New klines:        {self.stats['new_klines']}")
        print(f"  Gamma markets:     {self.stats['new_gamma_markets']}")
        print(f"  Errors:            {self.stats['errors']}")
        print("=" * 50 + "\n")

    def _db_size(self) -> str:
        """Get DB file size as human-readable string."""
        try:
            size = self.cache.db_path.stat().st_size
            if size < 1024:
                return f"{size}B"
            elif size < 1024 * 1024:
                return f"{size / 1024:.1f}KB"
            else:
                return f"{size / (1024 * 1024):.1f}MB"
        except Exception:
            return "N/A"


# ── CLI Entry Point ──────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(
        description="PolyPaper Bot - Backtest Data Collector"
    )
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode: only new PBT markets")
    parser.add_argument("--binance", action="store_true",
                        help="Binance-only mode with deep backfill")
    parser.add_argument("--stats", action="store_true",
                        help="Show cache statistics only")
    parser.add_argument("--full", action="store_true", default=True,
                        help="Full collection (default)")
    args = parser.parse_args()

    collector = DataCollector()
    try:
        await collector.init()

        if args.stats:
            await collector.show_stats()
        elif args.quick:
            await collector.run_quick()
        elif args.binance:
            await collector.run_binance_only()
        else:
            await collector.run_full()

    except KeyboardInterrupt:
        logger.info("Collection interrupted by user")
    except Exception as e:
        logger.error("Collection failed: %s", e)
        raise
    finally:
        await collector.close()


if __name__ == "__main__":
    asyncio.run(main())
