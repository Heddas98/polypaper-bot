"""
PolyPaper Bot - Backtest Runner (2026-05-21 Heddas direktifi)
==============================================================

Eski replay_engine.py (1101 satır, schema-broken):
  - up_token_id / market_start_time eski schema bekliyordu
  - 3+ gün boyunca her run "no such column" hatasiyla patliyordu
  - mevcut ob_snapshots schema'sina (modern: ts_ms + asset_id +
    condition_id + slug + asset + tf, 13 kolon) UYUMSUZ

Bu YENI minimal runner:
  • Modern schema'ya doğal (condition_id ile gruplama, asset_id ile
    UP/DOWN ayrımı, scanner.get_token_meta() ile asset/tf/slug).
  • SADECE RuleBasedStrategy + base API (BaseBacktestStrategy).
  • 11 hazır Python class (hour_edge/taker_flow/...) silindi —
    Heddas LAB no-code rule_based ile kendi kurallarini yaziyor.
  • Settle: binary market — son snapshot'taki UP fiyatı > 0.5 → UP kazanır.

UI bağlantıları:
  • backtest_v2.py _run_replay → BacktestRunner.run(cfg, "rule_based")
  • backtest_v2.py _run_compare → çoklu ruleset için tek tek koş
  • backtest_lab.py _build_quick + _build_compare paneller

Performans:
  • Discovery: 1 GROUP BY query (condition_id, asset, tf filtreli)
  • Per-market: condition_id'ye 1 select (UP+DOWN merged) — N+1 ama
    market sayısı genelde 100-1000 mertebesinde, kabul edilebilir
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from backtest.simulation.fee_model_v3 import FeeCalculatorV3
from backtest.simulation.fill_model import FillResult
from backtest.simulation.portfolio import VirtualPortfolio
from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Resolution,
    StrategyRegistryV2,
)

logger = logging.getLogger("polypaper.backtest.runner")


# Market window süre tablosu (timeframe → saniye)
_TF_DURATION = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "24h": 86400,
}


@dataclass
class RunConfig:
    """Backtest run config — minimal knob set (Heddas direktifi)."""

    asset: str = "BTC"  # "" = all
    timeframe: str = "5m"  # "" = all
    strategy_name: str = "rule_based"
    strategy_params: dict = field(default_factory=dict)
    initial_balance: float = 10000.0
    trade_amount: float = 1.0
    last_n: int = 100  # son N market (0 = unlimited)
    max_markets: int = 0  # 0 = unlimited (last_n'den sonra ek limit)
    # min_snapshots: bir market'in dahil edilmesi için en az snap sayısı
    # (UP+DOWN merged sonrası 2+ snap gerek — entry + settle).
    min_snapshots: int = 4


@dataclass
class RunSummary:
    """Backtest sonuç özeti — Telegram için flat."""

    config: RunConfig
    n_markets_discovered: int = 0
    n_markets_processed: int = 0
    n_markets_skipped: int = 0
    n_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    fees_total: float = 0.0
    final_balance: float = 0.0
    note: str = ""  # özel mesaj (örn. "0 market — veri yok")


class BacktestRunner:
    """Modern schema runner — RuleBasedStrategy ile çalışır."""

    def __init__(self, db, fee_calc: FeeCalculatorV3 | None = None):
        self.db = db
        self.fee_calc = fee_calc or FeeCalculatorV3()
        self.portfolio: VirtualPortfolio | None = None
        self._strategy: BaseBacktestStrategy | None = None

    async def run(self, cfg: RunConfig) -> RunSummary:
        """Backtest çalıştır, RunSummary döner."""
        self.portfolio = VirtualPortfolio(
            initial_balance=cfg.initial_balance,
            trade_amount=cfg.trade_amount,
            fee_calculator=self.fee_calc,
        )
        # 2026-05-21: RuleBasedStrategy ruleset dict'i from_ruleset() ile
        # yüklenir — generic create(name, **params) `name` argüman çakışması
        # verir (ruleset'in kendi "name" alanı var). Diğer stratejiler (yok
        # artık ama API korunur) generic create ile.
        if cfg.strategy_name == "rule_based" and cfg.strategy_params:
            from backtest.strategies.rule_based import RuleBasedStrategy

            self._strategy = RuleBasedStrategy.from_ruleset(cfg.strategy_params)
        else:
            self._strategy = StrategyRegistryV2.create(cfg.strategy_name)
        if not self._strategy:
            available = StrategyRegistryV2.list_all()
            raise ValueError(
                f"Strategy '{cfg.strategy_name}' bulunamadı. "
                f"Kayıtlı: {available}. /lab → Strateji Kurucu ile yeni kural yaz."
            )

        # 1. Market window discovery
        windows = await self._discover_markets(cfg)
        if not windows:
            logger.warning(
                "BacktestRunner: 0 market discovered — bot yeni başladıysa "
                "1-2 saat veri toplanmasını bekle, ya da asset/timeframe "
                "filtresi çok dar."
            )
            return RunSummary(
                config=cfg,
                note=(
                    f"0 market bulundu — asset={cfg.asset} tf={cfg.timeframe}. "
                    "Bot yeni başladıysa 1-2 saat veri toplaması gerek."
                ),
            )

        logger.info(
            "BacktestRunner: %d market discovered (asset=%s tf=%s last_n=%d)",
            len(windows),
            cfg.asset,
            cfg.timeframe,
            cfg.last_n,
        )

        # 2. Per-market execution
        max_n = cfg.max_markets if cfg.max_markets > 0 else len(windows)
        processed = 0
        skipped = 0
        for window in windows[:max_n]:
            try:
                ok = await self._run_market(window, cfg)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "BacktestRunner._run_market %s failed: %s",
                    window.get("slug", "?"),
                    e,
                )
                ok = False
            if ok:
                processed += 1
            else:
                skipped += 1

        # 3. Aggregate stats
        stats = self.portfolio.get_stats()
        n_trades = stats.total_trades
        return RunSummary(
            config=cfg,
            n_markets_discovered=len(windows),
            n_markets_processed=processed,
            n_markets_skipped=skipped,
            n_trades=n_trades,
            wins=stats.wins,
            losses=stats.losses,
            win_rate=stats.win_rate,
            total_pnl=stats.total_pnl,
            avg_pnl=getattr(stats, "avg_pnl", 0.0),
            fees_total=getattr(stats, "total_fees", 0.0),
            # PortfolioStats'te final_balance yok — initial + pnl hesapla
            final_balance=cfg.initial_balance + stats.total_pnl,
        )

    # ─── Discovery ──────────────────────────────────────────

    async def _discover_markets(self, cfg: RunConfig) -> list[dict]:
        """Modern schema GROUP BY condition_id.

        Filtre: asset (BTC/ETH/...), timeframe (5m/15m/...). NULL slug
        olan eski snapshot'lar atlanır (Adım 1 fix öncesi 50k row).
        """
        query = """
            SELECT condition_id,
                   MAX(slug) AS slug,
                   MAX(asset) AS asset,
                   MAX(timeframe) AS timeframe,
                   MIN(ts_ms) AS first_ts,
                   MAX(ts_ms) AS last_ts,
                   COUNT(*) AS snap_count,
                   COUNT(DISTINCT asset_id) AS token_count
            FROM ob_snapshots
            WHERE slug IS NOT NULL
        """
        params: list = []
        if cfg.asset:
            query += " AND asset = ?"
            params.append(cfg.asset.upper())
        if cfg.timeframe:
            query += " AND timeframe = ?"
            params.append(cfg.timeframe)
        # 2026-05-21: token_count >= 1 (eskiden >= 2). Mevcut veride her
        # condition_id'de genelde TEK token (UP) snapshot'i var — DOWN tarafi
        # WS'de ayri subscribe edilmiyor/kaydedilmiyor. Binary market oldugu
        # icin DOWN fiyati UP'tan turetilir (_load_merged_snapshots'ta).
        query += f"""
            GROUP BY condition_id
            HAVING snap_count >= {cfg.min_snapshots} AND token_count >= 1
            ORDER BY first_ts DESC
        """
        if cfg.last_n > 0:
            query += f" LIMIT {cfg.last_n}"

        rows = await self.db.conn.execute_fetchall(query, params)
        windows: list[dict] = []
        for r in rows:
            windows.append(
                {
                    "condition_id": r[0],
                    "slug": r[1],
                    "asset": r[2],
                    "timeframe": r[3],
                    "first_ts": r[4],
                    "last_ts": r[5],
                    "snap_count": r[6],
                }
            )
        return windows

    # ─── Per-market execution ───────────────────────────────

    async def _run_market(self, window: dict, cfg: RunConfig) -> bool:
        """Tek market'i koş — UP+DOWN snapshot merge, strategy, settle."""
        snapshots = await self._load_merged_snapshots(window)
        if len(snapshots) < 2:
            return False

        # Market metadata
        market = MarketData(
            market_id=window.get("condition_id", ""),
            coin=(window.get("asset") or "BTC").upper(),
            market_type=window.get("timeframe") or "5m",
            duration_seconds=_TF_DURATION.get(window.get("timeframe", "5m"), 300),
        )

        # Strategy lifecycle
        self._strategy.on_market_open(market)

        # İlk sinyali yakala, sonra market sonuna kadar tut (binary settle)
        signal = None
        entry_snap = None
        for snap in snapshots:
            sig = self._strategy.on_snapshot(snap)
            if sig is not None and signal is None:
                signal = sig
                entry_snap = snap
                # binary: tek sinyal yeter (Faz 2 backlog'da multi-entry).
                break

        if signal is None or entry_snap is None:
            self._strategy.on_market_close(
                market, Resolution(winner=Direction.UP, final_up_price=0.5)
            )
            return True  # market koşuldu, sinyal yok — yine de processed

        last_snap = snapshots[-1]
        await self._open_and_settle(market, signal, entry_snap, last_snap, cfg)

        # Close
        winner_dir = (
            Direction.UP if last_snap.up_best_ask > last_snap.down_best_ask else Direction.DOWN
        )
        self._strategy.on_market_close(
            market,
            Resolution(
                winner=winner_dir,
                final_up_price=last_snap.up_best_ask or 0.5,
                final_down_price=last_snap.down_best_ask or 0.5,
            ),
        )
        return True

    async def _load_merged_snapshots(self, window: dict) -> list[OrderbookSnapshot]:
        """condition_id'deki UP+DOWN snapshot'larını ts_ms'de merge et.

        Modern schema her token_id (UP veya DOWN) ayrı row. Strategy'nin
        gördüğü OrderbookSnapshot UP+DOWN bid/ask birleştirilmiş — bu
        merge'i biz yapıyoruz.
        """
        rows = await self.db.conn.execute_fetchall(
            "SELECT ts_ms, asset_id, best_bid, best_ask, mid_price, spread "
            "FROM ob_snapshots WHERE condition_id=? ORDER BY ts_ms ASC",
            (window["condition_id"],),
        )
        if not rows:
            return []

        # 2026-05-21: token sayisini belirle. Mevcut veride genelde TEK
        # token (UP) var; nadiren iki (UP+DOWN). İlk gördüğümüz = UP varsay.
        seen_tokens: list[str] = []
        for r in rows:
            if r[1] not in seen_tokens:
                seen_tokens.append(r[1])
            if len(seen_tokens) == 2:
                break
        up_token = seen_tokens[0]
        down_token = seen_tokens[1] if len(seen_tokens) >= 2 else None

        first_ts = rows[0][0]
        merged: dict[int, dict] = {}
        for ts, aid, bid, ask, _mid, spread in rows:
            d = merged.setdefault(
                ts,
                {
                    "timestamp_ms": ts,
                    "elapsed_seconds": (ts - first_ts) / 1000.0,
                    "remaining_seconds": 0.0,
                    "elapsed_pct": 0.0,
                    "spread": spread or 0.0,
                    "raw": {},
                },
            )
            if aid == up_token:
                d["up_best_bid"] = bid or 0.0
                d["up_best_ask"] = ask or 0.0
            elif aid == down_token:
                d["down_best_bid"] = bid or 0.0
                d["down_best_ask"] = ask or 0.0

        total_dur = (rows[-1][0] - first_ts) / 1000.0 if rows[-1][0] > first_ts else 1.0
        snaps: list[OrderbookSnapshot] = []
        for ts in sorted(merged.keys()):
            d = merged[ts]
            d["remaining_seconds"] = max(0.0, total_dur - d["elapsed_seconds"])
            d["elapsed_pct"] = (d["elapsed_seconds"] / total_dur) if total_dur > 0 else 0.0
            # UP tarafı yoksa snapshot atla (entry referansı yok)
            if "up_best_ask" not in d:
                continue
            # 2026-05-21: DOWN tarafı yoksa binary market matematiğiyle türet.
            # Polymarket binary: UP_token + DOWN_token ≈ 1.0 (spread hariç).
            # down_ask ≈ 1 − up_bid, down_bid ≈ 1 − up_ask (karşı taraf).
            if "down_best_ask" not in d:
                up_bid = d.get("up_best_bid", 0.0)
                up_ask = d.get("up_best_ask", 0.0)
                d["down_best_ask"] = max(0.0, 1.0 - up_bid) if up_bid > 0 else 0.0
                d["down_best_bid"] = max(0.0, 1.0 - up_ask) if up_ask > 0 else 0.0
            if "up_best_bid" not in d:
                d["up_best_bid"] = 0.0
            snaps.append(OrderbookSnapshot(**d))
        return snaps

    async def _open_and_settle(
        self, market, signal, entry_snap, exit_snap, cfg: RunConfig
    ) -> None:
        """Trade aç + binary settle (market sonunda)."""
        # Entry price: signal yönündeki ask
        if signal.direction == Direction.UP:
            entry_price = entry_snap.up_best_ask or 0.5
            # Binary settle: market sonunda UP "kazandı mı" → exit price 1.0/0.0
            up_won = exit_snap.up_best_ask > exit_snap.down_best_ask
            exit_price = 1.0 if up_won else 0.0
        else:
            entry_price = entry_snap.down_best_ask or 0.5
            down_won = exit_snap.down_best_ask > exit_snap.up_best_ask
            exit_price = 1.0 if down_won else 0.0

        if entry_price <= 0.0 or entry_price >= 1.0:
            return

        # FillResult: midpoint giriş (basit). Faz ileri: real_orderbook depth walk.
        fill = FillResult(
            filled=True,
            fill_price=entry_price,
            slippage=0.0,
            fill_amount=cfg.trade_amount,
            shares=cfg.trade_amount / entry_price,
            reason="midpoint",
            is_maker=False,
            rebate=0.0,
        )
        trade = self.portfolio.open_trade(
            signal=signal,
            fill=fill,
            market_id=market.market_id,
            coin=market.coin,
            market_type=market.market_type,
            strategy=cfg.strategy_name,
            hour_utc=0,
            entry_time_pct=entry_snap.elapsed_pct,
        )
        if trade is None:
            return

        # Settle
        self.portfolio.close_trade_at_price(trade, exit_price)
