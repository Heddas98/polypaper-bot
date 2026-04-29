"""
PolyPaper Bot - Replay Engine (Phase 37: Hardcore Backtest)
===========================================================

BACKTEST ENGINE MAP (Phase 50 clarification)
--------------------------------------------
  • backtest/engine_v2.py       → event-driven episodic backtest used by
                                  /backtest_v2 & /bt2. Owns its own fill
                                  sim, portfolio, fee model. Independent.
  • backtest/replay_engine.py   → THIS FILE. Base ReplayEngine; replays
                                  ob_snapshots from OUR OWN recorded DB.
                                  Used directly by /backtest_replay.
  • backtest/replay_engine_v3.py→ INHERITS from ReplayEngine; adds the
                                  Phase 47f calibration hooks. Used by
                                  /becker_build and ab_sweep_phase47f8.
  • backtest/becker_replay.py   → Phase 50 NEW. Walk-forward backtest
                                  against Jon-Becker parquet (via
                                  becker_calibration.db). No ob_snapshots
                                  needed — pure public trade stream.
                                  Exposed via /becker_replay.

None of the above is dead code. v3 REUSES v1 via inheritance.
Do NOT delete replay_engine.py — replay_engine_v3 depends on it.

Gerçek kaydedilmiş ob_snapshots verisini kullanarak backtest yapar.
PolyBackTest API'ye veya sentetik snapshot'lara GEREK YOK.

Akış:
  1. DB'den ob_snapshots tablosundan gerçek L2 orderbook verisi çeker
  2. Market slug'larını gruplar (slug + start_time → bir market window)
  3. Her market window için snapshot'ları kronolojik sırayla replay eder
  4. Stratejilere OrderbookSnapshot olarak besler
  5. Gerçek orderbook JSON'ını raw field'a koyar → REAL_ORDERBOOK fill mode kullanır
  6. Sonuç: Canlı trade ile BİREBİR aynı backtest sonucu

Kullanım:
  replay = ReplayEngine(db)
  stats = await replay.run(
      strategy_name="hour_edge",
      asset="BTC", timeframe="5m",
      fill_mode="real_orderbook",   # Gerçek L2 depth walk
  )
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, field

from backtest.strategies.base import (
    BacktestStrategy, BaseBacktestStrategy, MarketData, OrderbookSnapshot,
    Signal, Resolution, Direction, StrategyRegistryV2
)
from backtest.simulation.fill_model import FillSimulator, FillMode
from backtest.simulation.fee_model_v3 import FeeCalculatorV3 as FeeCalculator  # T4.1 unified
from backtest.simulation.portfolio import VirtualPortfolio, PortfolioStats

# Becker δ(p) helpers removed 2026-04-29 (Heddas direktifi: Becker tam silme).
# _apply_becker_boost no-op'a indirgendi, becker_curves attribute kalır
# (replay_engine_v3 wrapper hala set ediyor — Aşama 3.D'de o da silinecek).
_becker_delta_fn = None
_becker_boost_fn = None

logger = logging.getLogger("polypaper.backtest.replay")

# Duration in seconds for each market type
MARKET_DURATIONS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}


@dataclass
class ReplayConfig:
    """Configuration for a replay backtest run."""
    strategy_name: str = "hour_edge"
    strategy_params: dict = field(default_factory=dict)
    # Portfolio
    initial_balance: float = 10000.0
    trade_amount: float = 1.0
    # Fill simulation
    fill_mode: str = "real_orderbook"   # real_orderbook, midpoint, simple, orderbook
    min_liquidity: float = 0.0
    # Filters
    asset_filter: str = ""              # "" = all, "BTC", "ETH"
    timeframe_filter: str = ""          # "" = all, "5m", "15m"
    direction_filter: str = ""          # "" = all, "up", "down"
    min_confidence: float = 0.0
    # Time range (ms)
    start_ms: int = 0                   # 0 = no filter
    end_ms: int = 0
    # Phase 65: Latency injection (Gaussian μ±σ ms).
    # ⚠ Defaults (250ms / 75ms) are HEURISTIC, NOT empirically calibrated.
    # 50ms higher than live engine's REST_LATENCY_MS=200 (config/settings.py)
    # — kept higher in backtest as conservative pessimism. Pending Epic 4
    # T4.7 Faz B: align both defaults with measured live p50 once
    # `REST_TIMING_TELEMETRY=true` collects 24h of CLOB RTT samples.
    latency_mean_ms: int = 250          # average REST submit latency
    latency_std_ms: int = 75            # standard deviation
    # Limits
    max_markets: int = 0                # 0 = no limit
    # Phase 81: Market selection modes
    last_n: int = 0                     # >0 = only last N markets (most recent)
    random_n: int = 0                   # >0 = randomly sample N markets
    # Phase 82e Sprint B.2: Archive reader opt-in
    # When True, replay reads from SQLite hot tier + Parquet archive tier
    # via backtest.archive_reader.ArchiveReader. Lets backtest span WAY
    # beyond the DB retention window (e.g. 30-90 days), using Zstd L9
    # compressed parquets written by db_archive_job. Opt-in: default False
    # so existing callers behave identically.
    use_archive: bool = False


class ReplayEngine:
    """
    Event-driven backtest engine that replays REAL recorded data.

    Fark: BacktestEngineV2 sentetik/PolyBackTest verisi kullanır.
    ReplayEngine ise ob_snapshots tablosundaki gerçek L2 orderbook
    snapshot'larını kullanır. Fill simulation da gerçek depth'e karşı yapılır.
    """

    def __init__(self, db, config: Optional[ReplayConfig] = None):
        self.db = db
        self.config = config or ReplayConfig()
        self.portfolio: Optional[VirtualPortfolio] = None
        self.fill_sim: Optional[FillSimulator] = None
        self.fee_calc: Optional[FeeCalculator] = None
        self._strategy: Optional[BaseBacktestStrategy] = None
        self._markets_processed = 0
        self._markets_skipped = 0
        self._signals_generated = 0
        self._total_snapshots = 0
        # Becker δ(p) consumer + decision-mode removed 2026-04-29 (Heddas
        # direktifi: Becker tam silme). _becker_curves dict empty kept for
        # backward-compat with replay_engine_v3 wrapper (Aşama 3.D'de
        # wrapper da silinecek). Attributes ghost referans için kalır,
        # _apply_becker_* method'ları silindi.
        self._becker_curves: dict[str, list] = {}
        self._becker_boost_count = 0
        self._becker_boost_sum = 0.0
        self._becker_veto_count = 0
        self._becker_flip_count = 0
        # Phase 82e Sprint B.2 — optional archive reader (SQLite + Parquet)
        self._archive_reader = None

    def _get_archive_reader(self):
        """Lazy init of the ArchiveReader. Only used when use_archive=True."""
        if not getattr(self.config, "use_archive", False):
            return None
        if self._archive_reader is not None:
            return self._archive_reader
        try:
            from backtest.archive_reader import ArchiveReader
            self._archive_reader = ArchiveReader()
            logger.info(
                "ReplayEngine: archive reader ENABLED "
                "(SQLite hot + Parquet cold tier via DuckDB)"
            )
        except Exception as e:
            logger.warning(
                f"ReplayEngine: archive reader failed to init, "
                f"falling back to hot-only: {e}"
            )
            self._archive_reader = None
        return self._archive_reader

    def _setup(self):
        """Initialize components from config."""
        # Fee calculator — T4.1: for_market_type() removed with legacy fee_model.py.
        # Prior behavior was `FeeMode.STANDARD` in all branches (the `15m` branch
        # was hard-disabled with `and False`), so defaulting to FeeCalculatorV3's
        # V3 mode (taker-only, crypto) preserves identical backtest results.
        self.fee_calc = FeeCalculator()

        # Fill simulator (Phase 65: latency injection from config)
        fill_mode = FillMode(self.config.fill_mode)
        self.fill_sim = FillSimulator(
            mode=fill_mode,
            min_liquidity=self.config.min_liquidity,
            latency_mean_ms=getattr(self.config, "latency_mean_ms", 0),
            latency_std_ms=getattr(self.config, "latency_std_ms", 0),
        )

        # Portfolio
        self.portfolio = VirtualPortfolio(
            initial_balance=self.config.initial_balance,
            trade_amount=self.config.trade_amount,
            fee_calculator=self.fee_calc,
        )

        # Strategy
        self._debug_first_snap_logged = False  # Reset per run for price derivation debug
        self._strategy = StrategyRegistryV2.create(
            self.config.strategy_name,
            **self.config.strategy_params
        )
        if not self._strategy:
            raise ValueError(
                f"Strategy '{self.config.strategy_name}' not found. "
                f"Available: {StrategyRegistryV2.list_all()}"
            )

        logger.info(
            "ReplayEngine setup: strategy=%s, balance=$%.0f, "
            "trade=$%.2f, fill=%s",
            self.config.strategy_name, self.config.initial_balance,
            self.config.trade_amount, self.config.fill_mode
        )

    async def run(self, config: Optional[ReplayConfig] = None) -> PortfolioStats:
        """
        Run replay backtest using real recorded ob_snapshots data.

        Returns:
            PortfolioStats with full results
        """
        if config:
            self.config = config
        self._setup()
        self._markets_processed = 0
        self._markets_skipped = 0
        self._signals_generated = 0
        self._total_snapshots = 0

        # ── 1. Discover market windows from recorded data ──
        # Phase 82b.3: If the pipeline injected pre-discovered windows,
        # skip the expensive GROUP BY scan. This is critical for hyperopt
        # runs where _discover_market_windows was being re-executed for
        # every single Optuna trial.
        injected = getattr(self, "_injected_windows", None)
        if injected is not None:
            market_windows = list(injected)
            # Apply Phase 81 selection modes on the cached result
            if self.config.last_n > 0 and len(market_windows) > self.config.last_n:
                market_windows = market_windows[-self.config.last_n:]
            if (self.config.random_n > 0
                    and len(market_windows) > self.config.random_n):
                import random
                market_windows = random.sample(
                    market_windows, self.config.random_n)
            logger.debug(
                "ReplayEngine: using %d pre-injected windows (cached)",
                len(market_windows))
        else:
            market_windows = await self._discover_market_windows()

        if not market_windows:
            logger.warning("ReplayEngine: No market windows found in ob_snapshots")
            return self.portfolio.get_stats()

        logger.info("ReplayEngine: Found %d market windows to replay",
                     len(market_windows))

        # ── 2. Replay each market window ──
        for window in market_windows:
            if (self.config.max_markets > 0 and
                    self._markets_processed >= self.config.max_markets):
                break

            # Load full snapshot data for this window
            snapshots = await self._load_window_snapshots(window)

            if len(snapshots) < 2:
                self._markets_skipped += 1
                continue

            # Build MarketData from window metadata
            market = self._build_market_data(window, snapshots)

            # Determine winner (from final snapshot price direction)
            winner = self._determine_winner(snapshots)
            market.winner = winner

            # Run the market episode
            self._run_market(market, snapshots, winner)
            self._markets_processed += 1

        stats = self.portfolio.get_stats()
        logger.info(
            "ReplayEngine complete: %d markets (%d skipped), "
            "%d snapshots, %d trades, WR=%.1f%%, PnL=$%.2f",
            self._markets_processed, self._markets_skipped,
            self._total_snapshots, stats.total_trades,
            stats.win_rate, stats.total_pnl
        )
        return stats

    # ═══════════════════════════════════════════════
    #  MARKET WINDOW DISCOVERY
    # ═══════════════════════════════════════════════

    async def _discover_market_windows(self) -> list[dict]:
        """
        Discover distinct market windows from ob_snapshots.

        Her (slug, market_start_time) çifti bir market window.
        Bir slug birden fazla market window'a sahip olabilir
        (aynı market tekrar açılır).

        Phase 82b.3 fix:
          Before: GROUP BY scanned ENTIRE 10GB ob_snapshots table per trial →
                  each hyperopt trial hit 300s TRIAL_TIMEOUT before finishing
                  discovery → Best params always empty.
          After:  When cfg.last_n>0 and cfg.start_ms==0, we first fetch
                  MAX(ts_ms) (instant via idx_ob_snap_ts) and add a
                  WHERE ts_ms >= max_ts - buffer_hours filter. Uses the
                  ts_ms index to restrict the scan to the recent window
                  that could possibly contain last_n distinct markets.
                  Buffer heuristic: 2h per last_n unit, min 24h, max 720h.
                  For last_n=200 → 400h ≈ 17 days — very generous; actual
                  Polymarket rate of ~500 markets/day means 200 markets fit
                  in ~9.6h of data, so 400h has ~40x safety margin.
        """
        # ─── Phase 82b.3: compute ts_ms lower bound for last_n mode ───
        # Phase 82b.5: tighten the heuristic. Previous 2h/unit buffer meant
        # last_n=200 scanned 400h (~17 days, ~4M rows), pushing discovery
        # past the 300s hyperopt trial timeout. Empirical market rate is
        # ~500 markets/day across all (asset, timeframe) pairs, so 200
        # distinct markets fit in ~10 hours of recorded data. last_n//20
        # (min 6h, max 72h) gives us 200→10h with plenty of margin,
        # and scales gracefully for larger last_n values.
        ts_lower_bound = 0
        # Phase 82e Sprint B.2: when use_archive, MAX(ts_ms) must also look
        # at cold tier parquets. ArchiveReader unifies both.
        reader = self._get_archive_reader()
        if self.config.last_n > 0 and self.config.start_ms == 0:
            try:
                if reader is not None:
                    import asyncio as _asyncio
                    max_ts = await _asyncio.to_thread(reader.get_max_ts_ms)
                else:
                    cursor = await self.db.conn.execute(
                        "SELECT MAX(ts_ms) FROM ob_snapshots"
                    )
                    row = await cursor.fetchone()
                    max_ts = row[0] if row and row[0] else 0
            except Exception as e:
                logger.warning(
                    "ReplayEngine._discover: MAX(ts_ms) probe failed: %s", e)
                max_ts = 0
            if max_ts > 0:
                # Phase 82b.5: last_n//20, clamped [6h..72h] for ~40x speedup
                buffer_hours = min(72, max(6, self.config.last_n // 20))
                ts_lower_bound = max_ts - buffer_hours * 3600 * 1000
                logger.info(
                    "ReplayEngine._discover: last_n=%d → "
                    "ts_ms >= %d (~%dh back from max_ts=%d)",
                    self.config.last_n, ts_lower_bound, buffer_hours, max_ts)

        # Phase 82b.5: AVG(market_volume) and AVG(market_liquidity) were
        # expensive aggregates (SUM/COUNT over every snapshot in the group)
        # and hyperopt never reads them. Drop them here and zero-fill in
        # the output dict. Drops per-row work ~2x on typical windows.
        query = """
            SELECT slug, asset, timeframe,
                   up_token_id, down_token_id,
                   market_start_time, market_end_time,
                   MIN(ts_ms) as first_snap_ms,
                   MAX(ts_ms) as last_snap_ms,
                   COUNT(*) as snap_count
            FROM ob_snapshots
            WHERE 1=1
        """
        params = []

        # Phase 82b.3: prepend the ts_ms bound as the first filter so the
        # optimizer picks idx_ob_snap_ts.
        if ts_lower_bound > 0:
            query += " AND ts_ms >= ?"
            params.append(ts_lower_bound)

        if self.config.asset_filter:
            query += " AND asset = ?"
            params.append(self.config.asset_filter.upper())

        if self.config.timeframe_filter:
            query += " AND timeframe = ?"
            params.append(self.config.timeframe_filter)

        if self.config.start_ms > 0:
            query += " AND ts_ms >= ?"
            params.append(self.config.start_ms)

        if self.config.end_ms > 0:
            query += " AND ts_ms <= ?"
            params.append(self.config.end_ms)

        query += """
            GROUP BY slug, market_start_time
            HAVING snap_count >= 2
            ORDER BY first_snap_ms ASC
        """

        # Phase 82b.5: wrap the scan with timing so we can see in logs
        # how long the GROUP BY actually takes on the current DB size.
        _disc_t0 = datetime.utcnow()
        # Phase 82e Sprint B.2: route through ArchiveReader when opt-in.
        # The reader performs the same GROUP BY across hot+cold and
        # merges straddle windows (same slug+market_start_time across
        # tiers). Skips the hand-built SQL above and returns dicts
        # directly; we still apply last_n/random_n post-filters below.
        reader = self._get_archive_reader()
        if reader is not None:
            import asyncio as _asyncio
            windows = await _asyncio.to_thread(
                reader.discover_market_windows,
                ts_lower_bound=ts_lower_bound,
                ts_upper_bound=self.config.end_ms,
                asset_filter=self.config.asset_filter or None,
                timeframe_filter=self.config.timeframe_filter or None,
                min_snap_count=2,
            )
            _disc_elapsed = (datetime.utcnow() - _disc_t0).total_seconds()
            logger.info(
                "ReplayEngine._discover(archive): %d windows in %.1fs",
                len(windows), _disc_elapsed)
            # Apply last_n / random_n post-filters then return
            if self.config.last_n > 0:
                windows = windows[-self.config.last_n:]
                logger.info("ReplayEngine: --last %d → %d markets selected",
                            self.config.last_n, len(windows))
            if self.config.random_n > 0 and len(windows) > self.config.random_n:
                import random
                windows = random.sample(windows, self.config.random_n)
                logger.info("ReplayEngine: --random %d → %d markets sampled",
                            self.config.random_n, len(windows))
            return windows

        rows = await self.db.conn.execute_fetchall(query, params)
        _disc_elapsed = (datetime.utcnow() - _disc_t0).total_seconds()
        logger.info(
            "ReplayEngine._discover: GROUP BY returned %d rows in %.1fs",
            len(rows) if rows else 0, _disc_elapsed)
        if not rows:
            return []

        # Phase 82b.5: avg_volume/avg_liquidity removed from SELECT — kept
        # as 0.0 in the output dict for downstream consumers that still
        # reference the keys (e.g. display code in /report).
        windows = []
        for row in rows:
            windows.append({
                "slug": row[0],
                "asset": row[1],
                "timeframe": row[2],
                "up_token_id": row[3],
                "down_token_id": row[4],
                "market_start_time": row[5],
                "market_end_time": row[6],
                "first_snap_ms": row[7],
                "last_snap_ms": row[8],
                "snap_count": row[9],
                "avg_volume": 0.0,
                "avg_liquidity": 0.0,
            })

        # Phase 81: Market selection modes
        if self.config.last_n > 0:
            # Son N market (en yeni olanlar)
            windows = windows[-self.config.last_n:]
            logger.info("ReplayEngine: --last %d → %d markets selected",
                        self.config.last_n, len(windows))

        if self.config.random_n > 0 and len(windows) > self.config.random_n:
            import random
            windows = random.sample(windows, self.config.random_n)
            logger.info("ReplayEngine: --random %d → %d markets sampled",
                        self.config.random_n, len(windows))

        return windows

    async def _load_window_snapshots(self, window: dict) -> list[dict]:
        """Load all snapshots for a market window.

        Phase 82e Sprint B.2: when ReplayConfig.use_archive=True, load
        from SQLite hot + Parquet cold tier via ArchiveReader. For straddle
        windows (covering both tiers) we get the full union sorted by ts_ms.
        """
        reader = self._get_archive_reader()
        if reader is not None:
            import asyncio as _asyncio
            snapshots = await _asyncio.to_thread(
                reader.load_window_snapshots,
                window["slug"],
                window["first_snap_ms"],
                window["last_snap_ms"],
            )
            self._total_snapshots += len(snapshots)
            return snapshots

        query = """
            SELECT * FROM ob_snapshots
            WHERE slug = ?
              AND ts_ms >= ? AND ts_ms <= ?
            ORDER BY ts_ms ASC
        """
        rows = await self.db.conn.execute_fetchall(
            query,
            (window["slug"], window["first_snap_ms"], window["last_snap_ms"])
        )

        if not rows:
            return []

        # Get column names
        cursor = await self.db.conn.execute(
            "PRAGMA table_info(ob_snapshots)")
        columns_info = await cursor.fetchall()
        columns = [c[1] for c in columns_info]

        snapshots = [dict(zip(columns, row)) for row in rows]
        self._total_snapshots += len(snapshots)
        return snapshots

    # ═══════════════════════════════════════════════
    #  MARKET DATA CONSTRUCTION
    # ═══════════════════════════════════════════════

    def _build_market_data(self, window: dict,
                           snapshots: list[dict]) -> MarketData:
        """Build MarketData from window metadata."""
        # Detect hour from start time (with slug timestamp fallback)
        hour = 0
        start_time = window.get("market_start_time", "")
        if start_time:
            try:
                dt = datetime.fromisoformat(
                    start_time.replace("Z", "+00:00"))
                hour = dt.hour
            except Exception:
                pass
        # Phase 75-fix: Fallback — extract hour from slug Unix timestamp
        if hour == 0:
            slug = window.get("slug", "")
            parts = slug.rsplit("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                try:
                    dt = datetime.fromtimestamp(int(parts[1]), tz=timezone.utc)
                    hour = dt.hour
                except Exception:
                    pass
            # Also try first_snap_ms
            if hour == 0:
                first_ms = window.get("first_snap_ms", 0)
                if first_ms and int(first_ms) > 0:
                    try:
                        dt = datetime.fromtimestamp(
                            int(first_ms) / 1000, tz=timezone.utc)
                        hour = dt.hour
                    except Exception:
                        pass

        tf = window.get("timeframe", "5m")
        duration = MARKET_DURATIONS.get(tf, 300)

        return MarketData(
            market_id=f"{window['slug']}_{window.get('first_snap_ms', 0)}",
            coin=window.get("asset", "BTC").upper(),
            market_type=tf,
            question=f"Replay: {window['slug']}",
            start_time=start_time,
            end_time=window.get("market_end_time", ""),
            winner="",  # Set later by _determine_winner
            volume=float(window.get("avg_volume", 0)),
            liquidity=float(window.get("avg_liquidity", 0)),
            up_token_id=window.get("up_token_id", ""),
            down_token_id=window.get("down_token_id", ""),
            duration_seconds=duration,
            hour_utc=hour,
        )

    def _determine_winner(self, snapshots: list[dict]) -> str:
        """
        Determine market winner from snapshot price progression.

        Binary market: son snapshot'ta UP mid price > 0.5 ise UP kazanır.
        Daha güvenilir: ilk ve son snapshot arasındaki fiyat değişimine bak.
        """
        if not snapshots:
            return "UP"

        first = snapshots[0]
        last = snapshots[-1]

        # Use mid prices from last snapshot
        up_mid = last.get("mid_price_up", 0)
        down_mid = last.get("mid_price_down", 0)

        # If mid prices available
        if up_mid > 0 and down_mid > 0:
            return "UP" if up_mid > down_mid else "DOWN"

        # Fallback: use best_ask
        up_ask = last.get("up_best_ask", 0.5)
        down_ask = last.get("down_best_ask", 0.5)

        if up_ask > 0 and up_ask < 1:
            # UP token price > 0.5 means UP is winning
            return "UP" if up_ask > 0.5 else "DOWN"

        return "UP"  # Default

    # ═══════════════════════════════════════════════
    #  MARKET EPISODE REPLAY
    # ═══════════════════════════════════════════════

    # _apply_becker_boost + _apply_becker_decision removed 2026-04-29
    # (Heddas direktifi: Becker tam silme). ~190 satır dead method silindi.
    # Caller satırları (L823-846) zaten kaldırıldı, başka çağıran yok.

    def _run_market(self, market: MarketData,
                    raw_snapshots: list[dict], winner: str):
        """Process a single market episode with real data."""
        duration = MARKET_DURATIONS.get(market.market_type, 300)
        first_ts = raw_snapshots[0].get("ts_ms", 0)

        # Strategy: market open
        self._strategy.on_market_open(market)

        # Process snapshots
        signal: Optional[Signal] = None
        signal_snapshot: Optional[OrderbookSnapshot] = None

        for raw in raw_snapshots:
            snap = self._convert_snapshot(raw, first_ts, duration)

            # Call strategy
            result = self._strategy.on_snapshot(snap)

            # Becker boost + decision-mode removed 2026-04-29 (Heddas direktifi).
            # _becker_curves zaten {} (replay_engine_v3 wrapper artık inject
            # etmiyor — Aşama 3.D backlog). Live engine pure backtest path.
            if result and signal is None:
                if result.confidence >= self.config.min_confidence:
                    if self._direction_ok(result):
                        signal = result
                        signal_snapshot = snap
                        self._signals_generated += 1

        # Resolution
        resolution = Resolution(
            winner=Direction.UP if winner == "UP" else Direction.DOWN,
        )

        # Execute trade if signal was generated
        if signal and signal_snapshot and winner:
            fill = self.fill_sim.simulate_fill(
                direction=signal.direction,
                amount_usd=self.config.trade_amount,
                snapshot=signal_snapshot,
                market_volume=market.volume,
            )

            if fill.filled:
                trade = self.portfolio.open_trade(
                    signal=signal,
                    fill=fill,
                    market_id=market.market_id,
                    coin=market.coin,
                    market_type=market.market_type,
                    strategy=self.config.strategy_name,
                    hour_utc=market.hour_utc,
                    entry_time_pct=(signal_snapshot.elapsed_pct
                                   if signal_snapshot else 0),
                )
                if trade:
                    self.portfolio.close_trade(trade, winner)

        # Strategy: market close
        self._strategy.on_market_close(market, resolution)

    def _convert_snapshot(self, raw: dict, first_ts: int,
                          duration: int) -> OrderbookSnapshot:
        """
        Convert a DB ob_snapshots row into OrderbookSnapshot.

        KRITIK: raw field'a tam orderbook JSON'ını koyar.
        Bu sayede REAL_ORDERBOOK fill mode gerçek depth walk yapabilir.
        """
        ts = raw.get("ts_ms", 0)
        elapsed_ms = ts - first_ts if first_ts else 0
        elapsed_s = max(0, elapsed_ms / 1000.0)
        remaining_s = max(0, duration - elapsed_s)

        # Parse orderbook JSON for raw field (fill simulation uses this)
        up_bids = []
        up_asks = []
        down_bids = []
        down_asks = []
        try:
            up_bids_json = raw.get("up_bids_json", "[]")
            up_asks_json = raw.get("up_asks_json", "[]")
            down_bids_json = raw.get("down_bids_json", "[]")
            down_asks_json = raw.get("down_asks_json", "[]")

            if up_bids_json:
                up_bids = json.loads(up_bids_json)
            if up_asks_json:
                up_asks = json.loads(up_asks_json)
            if down_bids_json:
                down_bids = json.loads(down_bids_json)
            if down_asks_json:
                down_asks = json.loads(down_asks_json)
        except (json.JSONDecodeError, TypeError):
            pass

        # Binance price change estimate
        binance_change = raw.get("binance_price_change_pct", 0) or 0

        # Phase 75-fix: Derive bid/ask from mid_price if NULL
        _ub = float(raw.get("up_best_bid", 0) or 0)
        _ua = float(raw.get("up_best_ask", 0) or 0)
        _db = float(raw.get("down_best_bid", 0) or 0)
        _da = float(raw.get("down_best_ask", 0) or 0)
        _um = float(raw.get("mid_price_up", 0) or 0)
        _dm = float(raw.get("mid_price_down", 0) or 0)

        # Tier 1: Use mid_price to derive bid/ask (most reliable field)
        if _ub <= 0 and _um > 0:
            _ub = max(0.01, round(_um - 0.005, 4))
        if _ua <= 0 and _um > 0:
            _ua = min(0.99, round(_um + 0.005, 4))
        if _db <= 0 and _dm > 0:
            _db = max(0.01, round(_dm - 0.005, 4))
        if _da <= 0 and _dm > 0:
            _da = min(0.99, round(_dm + 0.005, 4))

        # Tier 2: Derive from bid if ask still missing
        if _ua <= 0 and _ub > 0:
            _ua = min(_ub + 0.01, 0.99)
        if _da <= 0 and _db > 0:
            _da = min(_db + 0.01, 0.99)

        # Tier 3: Cross-derive from complementary side (binary: up + down ≈ 1)
        if _ua <= 0 and _db > 0:
            _ua = round(1.0 - _db + 0.01, 4)
        if _da <= 0 and _ub > 0:
            _da = round(1.0 - _ub + 0.01, 4)
        if _ub <= 0 and _da > 0:
            _ub = max(0.01, round(1.0 - _da - 0.01, 4))
        if _db <= 0 and _ua > 0:
            _db = max(0.01, round(1.0 - _ua - 0.01, 4))

        # Phase 75-debug: Log first snapshot per run to verify derivation
        if not getattr(self, "_debug_first_snap_logged", False):
            self._debug_first_snap_logged = True
            logger.info(
                "PRICE_DEBUG first snap: raw_ub=%.4f raw_ua=%.4f raw_db=%.4f raw_da=%.4f "
                "mid_up=%.4f mid_down=%.4f → derived ub=%.4f ua=%.4f db=%.4f da=%.4f",
                float(raw.get("up_best_bid", 0) or 0),
                float(raw.get("up_best_ask", 0) or 0),
                float(raw.get("down_best_bid", 0) or 0),
                float(raw.get("down_best_ask", 0) or 0),
                _um, _dm, _ub, _ua, _db, _da,
            )

        return OrderbookSnapshot(
            timestamp_ms=int(ts),
            up_best_bid=_ub,
            up_best_ask=_ua,
            down_best_bid=_db,
            down_best_ask=_da,
            spread=float(raw.get("up_spread", 0) or 0),
            binance_price=float(raw.get("binance_price", 0) or 0),
            binance_price_change=binance_change,
            up_bid_depth=float(raw.get("up_bid_depth_usd", 0) or 0),
            up_ask_depth=float(raw.get("up_ask_depth_usd", 0) or 0),
            down_bid_depth=float(raw.get("down_bid_depth_usd", 0) or 0),
            down_ask_depth=float(raw.get("down_ask_depth_usd", 0) or 0),
            elapsed_seconds=elapsed_s,
            remaining_seconds=remaining_s,
            elapsed_pct=min(1.0, elapsed_s / duration) if duration > 0 else 0,
            # Raw data: full orderbook for REAL_ORDERBOOK fill simulation
            raw={
                "up_bids": up_bids,     # [[price, size], ...]
                "up_asks": up_asks,     # [[price, size], ...]
                "down_bids": down_bids,
                "down_asks": down_asks,
                "mid_price_up": raw.get("mid_price_up", 0),
                "mid_price_down": raw.get("mid_price_down", 0),
                "implied_prob_up": raw.get("implied_prob_up", 0),
                "implied_prob_down": raw.get("implied_prob_down", 0),
                "slug": raw.get("slug", ""),
                "elapsed_pct": raw.get("elapsed_pct", 0),
                "_real_data": True,  # Flag: gerçek veri, sentetik değil
            },
        )

    def _direction_ok(self, signal: Signal) -> bool:
        """Check if signal direction passes filter."""
        if not self.config.direction_filter:
            return True
        return signal.direction.value == self.config.direction_filter.lower()

    # ═══════════════════════════════════════════════
    #  SUMMARY
    # ═══════════════════════════════════════════════

    def get_summary(self) -> dict:
        """Return run summary dict."""
        stats = self.portfolio.get_stats() if self.portfolio else PortfolioStats()
        return {
            "strategy": self.config.strategy_name,
            "fill_mode": self.config.fill_mode,
            "data_source": "real_ob_snapshots",
            "markets_processed": self._markets_processed,
            "markets_skipped": self._markets_skipped,
            "total_snapshots": self._total_snapshots,
            "signals_generated": self._signals_generated,
            "total_trades": stats.total_trades,
            "wins": stats.wins,
            "losses": stats.losses,
            "win_rate": stats.win_rate,
            "total_pnl": stats.total_pnl,
            "total_fees": stats.total_fees,
            "total_slippage": stats.total_slippage,
            "sharpe": stats.sharpe_ratio,
            "sortino": stats.sortino_ratio,
            "max_drawdown": stats.max_drawdown,
            "profit_factor": stats.profit_factor,
            "avg_pnl": stats.avg_pnl,
        }
