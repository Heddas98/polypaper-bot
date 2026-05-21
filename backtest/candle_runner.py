"""
PolyPaper Bot - Candle-based Market-Level Backtest (2026-05-21)
===============================================================

Heddas direktifi: "biz zaten candle'ları topluyorduk, kullanamayacağımız
şeyi neden topluyoruz?" — candles_ext (Binance OHLC) backtest'te
kullanılmıyordu. Bu motor onu değerlendirir.

TICK vs CANDLE backtest ayrımı:
  • backtest/runner.py (BacktestRunner): tick-level (ob_snapshots),
    saniye-aralığı kuralları için (sec_10_30_up). Az market.
  • backtest/candle_runner.py (BU DOSYA): market-level (candles_ext),
    her candle = bir market (yön: close>open). 2600+ BTC 5m candle.
    Yön/saat/weekday/STREAK/MARTİNGALE stratejileri için.

Veri kaynağı: candles_ext (Binance BTCUSDT 5m OHLC) — KESİN yön
(close>open → o periyotta fiyat yükseldi → UP market kazandı, çünkü
Polymarket up/down crypto market'leri Chainlink/Binance fiyatına settle
olur). 15m/1h: 5m candle'lardan aggregate edilir (kesin yön korunur).

Stratejiler (market-level):
  • bet_direction: up / down / follow_trend / fade_trend
  • hour_filter / weekday_filter: sadece bu zamanlarda işlem
  • martingale: stateful bet sizing (kaybedince katla, kazanınca reset)

Martingale matematiği (DÜRÜST): 50c adil para + fee = negatif EV. Bu motor
gerçeği gösterir (max streak, bust sayısı, max bet) — kumar tuzağını
sayılarla ortaya koyar.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger("polypaper.backtest.candle_runner")

# 5m'den türetme çarpanları (kaç 5m candle = 1 hedef TF)
_TF_AGG = {
    "5m": 1,
    "15m": 3,
    "30m": 6,
    "1h": 12,
    "4h": 48,
}

# asset → Binance symbol (candles_ext)
_BINANCE_SYMBOL = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}


@dataclass
class CandleMarket:
    """Tek bir market = bir candle (aggregate sonrası)."""

    ts: int  # open_ts (saniye)
    open_price: float
    close_price: float
    direction: str  # "up" (close>open) | "down" | "flat"
    hour_utc: int
    weekday: int  # 0=Pzt ... 6=Paz
    # 2026-05-21 sofistike sinyal alanları (candles_ext high/low/volume'dan)
    range_pct: float = 0.0  # (high−low)/open — bu candle'ın volatilitesi
    body_pct: float = 0.0  # (close−open)/open — işaretli hareket büyüklüğü
    volume: float = 0.0
    # Önceki market'ten taşınan sinyaller (karar ANINDA bilinebilir — leak yok)
    prev_body_pct: float = 0.0  # önceki candle hareketi (yön + büyüklük)
    prev_range_pct: float = 0.0  # önceki candle volatilitesi


@dataclass
class CandleRunConfig:
    """Candle backtest config — market-level + martingale."""

    asset: str = "BTC"
    timeframe: str = "5m"
    # Strateji (yön kararı)
    # up | down | follow_trend | fade_trend | rev_up | rev_down | rev
    # rev_*: 2026-05-21 mean-reversion — önceki candle büyük hareketse ters bahis.
    bet_direction: str = "up"
    rev_threshold: float = 0.0015  # rev_* için "büyük hareket" eşiği (|prev_body|)
    hour_filter: list[int] = field(default_factory=list)  # boş = tüm saatler
    weekday_filter: list[int] = field(default_factory=list)  # boş = tüm günler
    # Bahis
    base_bet: float = 1.0
    entry_price: float = 0.50  # giriş odds (limit @ X varsayımı)
    fee_rate: float = 0.07  # crypto taker fee katsayısı (docs)
    # Martingale
    martingale: bool = False
    max_levels: int = 6  # tavan katlama (0 = sınırsız — riskli)
    stop_after_streak: int = 0  # 0 = kapalı; N ardışık aynı yön sonrası dur
    # Limits
    last_n: int = 500  # son N market (0 = tümü)


@dataclass
class CandleRunSummary:
    config: CandleRunConfig
    n_markets: int = 0
    n_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    final_balance: float = 0.0
    # Martingale-spesifik
    max_bet: float = 0.0
    max_level_reached: int = 1
    busts: int = 0
    # Veri istatistiği
    max_streak: int = 0
    up_pct: float = 0.0
    streak_dist: dict = field(default_factory=dict)  # streak uzunluğu → kaç kez
    note: str = ""


class CandleBacktestRunner:
    """candles_ext üzerinde market-level + martingale backtest."""

    def __init__(self, db):
        self.db = db

    async def run(self, cfg: CandleRunConfig) -> CandleRunSummary:
        markets = await self._load_markets(cfg)
        if not markets:
            return CandleRunSummary(
                config=cfg,
                note=(
                    f"{cfg.asset} candle verisi yok (candles_ext). "
                    "Bot Binance OHLC topluyor mu? /candles ile kontrol et."
                ),
            )

        # Veri istatistiği (yön + streak)
        dirs = [m.direction for m in markets if m.direction != "flat"]
        up_count = dirs.count("up")
        up_pct = (100.0 * up_count / len(dirs)) if dirs else 0.0
        max_streak = _max_streak(dirs)

        if cfg.martingale:
            res = self._simulate_martingale(markets, cfg)
        else:
            res = self._simulate_flat(markets, cfg)

        res.config = cfg
        res.n_markets = len(markets)
        res.max_streak = max_streak
        res.up_pct = up_pct
        res.streak_dist = streak_distribution(dirs)
        res.final_balance = round(res.total_pnl, 4)
        return res

    # ─── Edge tarama (train/test split) ─────────────────────

    async def scan_edges(
        self,
        asset: str,
        timeframes: tuple[str, ...] = ("5m", "15m", "1h"),
        directions: tuple[str, ...] = ("up", "down", "follow_trend", "fade_trend"),
        train_ratio: float = 0.7,
        min_markets: int = 40,
    ) -> list[dict]:
        """2026-05-21 (Heddas: "kendi edge'imizi bulalım") — sinyal tarayıcı.

        Her (tf, direction) kombinasyonunu train/test split ile FLAT simüle
        eder. Gerçek edge = hem train hem test (OOS) pozitif. Çoğu kombinasyon
        OOS'ta çöker (fee + random) — overfit tuzağını sayılarla gösterir.

        Returns: dict listesi (tf, direction, n, train_pnl, test_pnl,
        train_wr, test_wr, is_edge).
        """
        results: list[dict] = []
        for tf in timeframes:
            cfg = CandleRunConfig(asset=asset, timeframe=tf, last_n=0)
            markets = await self._load_markets(cfg)
            if len(markets) < min_markets:
                results.append({"tf": tf, "direction": "-", "n": len(markets), "skip": True})
                continue
            sp = int(len(markets) * train_ratio)
            train, test = markets[:sp], markets[sp:]
            for d in directions:
                cfg.bet_direction = d
                tr = self._simulate_flat(train, cfg)
                te = self._simulate_flat(test, cfg)
                results.append(
                    {
                        "tf": tf,
                        "direction": d,
                        "n": len(markets),
                        "n_train": len(train),
                        "n_test": len(test),
                        "train_pnl": round(tr.total_pnl, 2),
                        "test_pnl": round(te.total_pnl, 2),
                        "train_wr": round(tr.win_rate, 1),
                        "test_wr": round(te.win_rate, 1),
                        "is_edge": tr.total_pnl > 0 and te.total_pnl > 0,
                        "skip": False,
                    }
                )
        return results

    async def scan_conditional_edges(
        self, asset: str, timeframe: str, train_ratio: float = 0.7, min_markets: int = 60
    ) -> list[dict]:
        """2026-05-21 (Heddas #1: sofistike sinyal) — koşullu edge tarayıcı.

        Basit yön sinyalinde edge yok (kanıtlandı). Bu, ÖNCEKİ candle'ın
        hareketine/volatilitesine bağlı 6 hipotezi train/test ile tarar:
          • reversal (büyük hareket sonrası ters) — mean-reversion
          • momentum (yön devam) — trend-following
          • volatilite-koşullu (düşük/yüksek vol'da farklı davranış)

        Karar ANINDA bilinen veriyle (prev_body, prev_range) — look-ahead
        leak YOK. Eşik/medyan TRAIN'den hesaplanır (test'e sızdırmaz).
        """
        cfg = CandleRunConfig(asset=asset, timeframe=timeframe, last_n=0)
        markets = await self._load_markets(cfg)
        if len(markets) < min_markets:
            return [{"skip": True, "n": len(markets)}]

        sp = int(len(markets) * train_ratio)
        train, test = markets[:sp], markets[sp:]

        # Eşikler TRAIN'den (leak yok)
        body_thr = 0.0015  # %0.15 "büyük hareket"
        train_ranges = sorted(m.prev_range_pct for m in train if m.prev_range_pct > 0)
        vol_med = train_ranges[len(train_ranges) // 2] if train_ranges else 0.003

        # 6 koşullu sinyal (her biri: market → bet_dir | None)
        signals = {
            "rev↑ prev↓big→UP": lambda m: "up" if m.prev_body_pct < -body_thr else None,
            "rev↓ prev↑big→DOWN": lambda m: "down" if m.prev_body_pct > body_thr else None,
            "mom↑ prev↑→UP": lambda m: "up" if m.prev_body_pct > 0 else None,
            "mom↓ prev↓→DOWN": lambda m: "down" if m.prev_body_pct < 0 else None,
            "lowvol-mom": lambda m: (
                ("up" if m.prev_body_pct > 0 else "down")
                if m.prev_range_pct < vol_med and m.prev_body_pct != 0
                else None
            ),
            "highvol-rev": lambda m: (
                ("down" if m.prev_body_pct > 0 else "up")
                if m.prev_range_pct > vol_med and m.prev_body_pct != 0
                else None
            ),
        }

        results: list[dict] = []
        for name, fn in signals.items():
            tr = _simulate_signal(train, fn, cfg)
            te = _simulate_signal(test, fn, cfg)
            results.append(
                {
                    "name": name,
                    "n_train": tr["n"],
                    "n_test": te["n"],
                    "train_pnl": round(tr["pnl"], 2),
                    "test_pnl": round(te["pnl"], 2),
                    "test_wr": round(te["wr"], 1),
                    "is_edge": tr["pnl"] > 0 and te["pnl"] > 0 and te["n"] >= 20,
                    "skip": False,
                }
            )
        return results

    # ─── Veri yükleme ───────────────────────────────────────

    async def _load_markets(self, cfg: CandleRunConfig) -> list[CandleMarket]:
        """candles_ext'ten OHLC çek, hedef TF'ye aggregate et."""
        symbol = _BINANCE_SYMBOL.get(cfg.asset.upper())
        if not symbol:
            return []
        # Binance candles_ext sadece 5m tutuyor — 5m çek (high/low/volume
        # dahil — sofistike sinyaller için), hedef TF'ye aggregate et
        rows = await self.db.conn.execute_fetchall(
            "SELECT open_ts, open, high, low, close, volume FROM candles_ext "
            "WHERE symbol=? AND interval='5m' ORDER BY open_ts ASC",
            (symbol,),
        )
        if not rows:
            return []

        agg = _TF_AGG.get(cfg.timeframe, 1)
        markets: list[CandleMarket] = []
        # agg'lik gruplar halinde birleştir (open=ilk, high=max, low=min,
        # close=son, volume=toplam)
        for i in range(0, len(rows) - agg + 1, agg):
            group = rows[i : i + agg]
            if len(group) < agg:
                break
            open_ts = int(group[0][0])
            if open_ts > 10_000_000_000:  # ms → saniye
                open_ts = open_ts // 1000
            open_price = float(group[0][1])
            close_price = float(group[-1][4])
            if open_price <= 0 or close_price <= 0:
                continue
            high = max(float(g[2]) for g in group)
            low = min(float(g[3]) for g in group)
            volume = sum(float(g[5] or 0) for g in group)
            if close_price > open_price:
                direction = "up"
            elif close_price < open_price:
                direction = "down"
            else:
                direction = "flat"
            try:
                dt = datetime.fromtimestamp(open_ts, tz=UTC)
            except (OSError, ValueError, OverflowError):
                continue
            markets.append(
                CandleMarket(
                    ts=open_ts,
                    open_price=open_price,
                    close_price=close_price,
                    direction=direction,
                    hour_utc=dt.hour,
                    weekday=dt.weekday(),
                    range_pct=(high - low) / open_price if open_price else 0.0,
                    body_pct=(close_price - open_price) / open_price if open_price else 0.0,
                    volume=volume,
                )
            )

        # prev_* sinyalleri — önceki market'in body/range (karar anında bilinir)
        for j in range(1, len(markets)):
            markets[j].prev_body_pct = markets[j - 1].body_pct
            markets[j].prev_range_pct = markets[j - 1].range_pct

        # Son N market
        if cfg.last_n > 0 and len(markets) > cfg.last_n:
            markets = markets[-cfg.last_n :]
        return markets

    # ─── Yön kararı ─────────────────────────────────────────

    def _decide_direction(
        self, market: CandleMarket, prev_dir: str | None, cfg: CandleRunConfig
    ) -> str | None:
        """Bu market'te hangi yöne bahis? None = işlem yok (filtre tutmadı)."""
        # Zaman filtreleri
        if cfg.hour_filter and market.hour_utc not in cfg.hour_filter:
            return None
        if cfg.weekday_filter and market.weekday not in cfg.weekday_filter:
            return None

        bd = cfg.bet_direction
        if bd == "up":
            return "up"
        if bd == "down":
            return "down"
        if bd == "follow_trend":
            return prev_dir  # önceki market yönü (None ilk market)
        if bd == "fade_trend":
            if prev_dir == "up":
                return "down"
            if prev_dir == "down":
                return "up"
            return None
        # 2026-05-21 mean-reversion modları (rev↑ edge): önceki candle
        # eşikten BÜYÜK hareketse ters yöne bahis, küçükse işlem yok (None).
        if bd == "rev_up":
            return "up" if market.prev_body_pct < -cfg.rev_threshold else None
        if bd == "rev_down":
            return "down" if market.prev_body_pct > cfg.rev_threshold else None
        if bd == "rev":  # iki yönlü reversal
            if market.prev_body_pct < -cfg.rev_threshold:
                return "up"
            if market.prev_body_pct > cfg.rev_threshold:
                return "down"
            return None
        return "up"

    # ─── Simülasyon: flat (sabit bahis) ─────────────────────

    def _simulate_flat(self, markets: list[CandleMarket], cfg: CandleRunConfig) -> CandleRunSummary:
        balance = 0.0
        wins = losses = 0
        prev_dir: str | None = None
        for m in markets:
            bet_dir = self._decide_direction(m, prev_dir, cfg)
            prev_dir = m.direction if m.direction != "flat" else prev_dir
            if bet_dir is None or m.direction == "flat":
                continue
            balance += _trade_pnl(bet_dir, m.direction, cfg.base_bet, cfg.entry_price, cfg.fee_rate)
            if bet_dir == m.direction:
                wins += 1
            else:
                losses += 1
        n = wins + losses
        return CandleRunSummary(
            config=cfg,
            n_trades=n,
            wins=wins,
            losses=losses,
            win_rate=(100.0 * wins / n) if n else 0.0,
            total_pnl=round(balance, 4),
            max_bet=cfg.base_bet,
            max_level_reached=1,
        )

    # ─── Simülasyon: martingale ─────────────────────────────

    def _simulate_martingale(
        self, markets: list[CandleMarket], cfg: CandleRunConfig
    ) -> CandleRunSummary:
        balance = 0.0
        bet = cfg.base_bet
        level = 1
        max_bet = 0.0
        max_level_reached = 1
        busts = 0
        wins = losses = 0
        prev_dir: str | None = None
        run_streak = 0  # mevcut ardışık aynı-yön sayacı (stop_after_streak için)

        for m in markets:
            # stop_after_streak: çok uzun trend → bahis durdur (martingale tehlikeli)
            if m.direction != "flat":
                if m.direction == prev_dir:
                    run_streak += 1
                else:
                    run_streak = 1

            bet_dir = self._decide_direction(m, prev_dir, cfg)
            prev_dir_for_next = m.direction if m.direction != "flat" else prev_dir

            skip = (
                bet_dir is None
                or m.direction == "flat"
                or (cfg.stop_after_streak > 0 and run_streak >= cfg.stop_after_streak)
            )
            if skip:
                prev_dir = prev_dir_for_next
                continue

            won = bet_dir == m.direction
            balance += _trade_pnl(bet_dir, m.direction, bet, cfg.entry_price, cfg.fee_rate)
            max_bet = max(max_bet, bet)
            if won:
                wins += 1
                bet = cfg.base_bet
                level = 1
            else:
                losses += 1
                level += 1
                max_level_reached = max(max_level_reached, level)
                bet *= 2
                if cfg.max_levels > 0 and level > cfg.max_levels:
                    busts += 1
                    bet = cfg.base_bet
                    level = 1
            prev_dir = prev_dir_for_next

        n = wins + losses
        return CandleRunSummary(
            config=cfg,
            n_trades=n,
            wins=wins,
            losses=losses,
            win_rate=(100.0 * wins / n) if n else 0.0,
            total_pnl=round(balance, 4),
            max_bet=round(max_bet, 2),
            max_level_reached=max_level_reached,
            busts=busts,
        )


# ─── Saf yardımcılar ────────────────────────────────────────


def _trade_pnl(bet_dir: str, actual_dir: str, bet: float, entry: float, fee_rate: float) -> float:
    """Bir trade'in net PnL'i (Polymarket binary + crypto fee).

    Giriş `entry` odds'ta `bet` $ yatır → bet/entry share. Kazanırsa her
    share 1$'a redeem (payout = shares×1). Kaybederse 0. Fee crypto modeli
    `fee_rate × (1−entry) × bet` (docs polymarket_taker_fee_v2, giriş fee).
    """
    if entry <= 0 or entry >= 1:
        return 0.0
    shares = bet / entry
    fee = fee_rate * (1.0 - entry) * bet
    if bet_dir == actual_dir:
        return shares * 1.0 - bet - fee  # payout − maliyet − fee
    return -(bet + fee)  # tüm bet + fee kaybı


def _simulate_signal(markets: list, signal_fn, cfg: CandleRunConfig) -> dict:
    """Koşullu sinyal FLAT simülasyonu — signal_fn(market)→bet_dir|None.

    None = o market'te işlem yok (segment dışı). Döner: {pnl, n, wr}.
    """
    pnl = 0.0
    wins = trades = 0
    for m in markets:
        if m.direction == "flat":
            continue
        bet_dir = signal_fn(m)
        if bet_dir is None:
            continue
        pnl += _trade_pnl(bet_dir, m.direction, cfg.base_bet, cfg.entry_price, cfg.fee_rate)
        trades += 1
        if bet_dir == m.direction:
            wins += 1
    return {"pnl": pnl, "n": trades, "wr": (100.0 * wins / trades) if trades else 0.0}


def _max_streak(dirs: list[str]) -> int:
    """En uzun ardışık aynı-yön serisi."""
    if not dirs:
        return 0
    best = cur = 1
    for i in range(1, len(dirs)):
        if dirs[i] == dirs[i - 1]:
            cur += 1
            best = max(best, cur)
        else:
            cur = 1
    return best


def streak_distribution(dirs: list[str]) -> dict[int, int]:
    """Streak uzunluğu → kaç kez. (UI'da 'üstüste kaç market' tablosu için.)"""
    if not dirs:
        return {}
    streaks = []
    cur = 1
    for i in range(1, len(dirs)):
        if dirs[i] == dirs[i - 1]:
            cur += 1
        else:
            streaks.append(cur)
            cur = 1
    streaks.append(cur)
    return dict(Counter(streaks))
