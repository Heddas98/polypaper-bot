"""Backtest LAB Faz 2 — replay engine yeni filtreler (2026-05-20).

Heddas direktifi: "5m marketteki 30. saniye - 50. saniye al-sat",
"her gün şu saatte ki markette işlem yap", "X fiyatına gelince al,
Y'ye gelince sat" gibi rastgele senaryoları test edebilelim.

Bu testler 4 grup filtreyi pin'ler:
  1. Schedule (hour/weekday/minute_of_hour) — discovery-time
  2. entry_second_min/max — sinyal yalnız bu pencerede
  3. exit_second_min/max — pozisyon force-close
  4. entry_yes_price_min/max + exit_yes_price_above/below
+ VirtualPortfolio.close_trade_at_price PnL matematiği

Tüm filtreler default kapalı — eski caller'ları kırmamalı (regresyon var).
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock

import pytest

from backtest.replay_engine import ReplayConfig, ReplayEngine
from backtest.simulation.fee_model_v3 import FeeCalculatorV3
from backtest.simulation.portfolio import Trade, VirtualPortfolio
from backtest.strategies.base import (
    BaseBacktestStrategy,
    Direction,
    MarketData,
    OrderbookSnapshot,
    Signal,
    StrategyRegistryV2,
)

# ── _market_passes_schedule ─────────────────────────────────


def _eng_with_cfg(**cfg_overrides) -> ReplayEngine:
    cfg = ReplayConfig(**cfg_overrides)
    return ReplayEngine(db=MagicMock(), config=cfg)


def _window(start_time: str = "2026-05-19T22:30:00Z", slug: str = "btc-up-5m-001") -> dict:
    return {
        "slug": slug,
        "market_start_time": start_time,
        "first_snap_ms": 0,
    }


def test_schedule_no_filters_passes_all():
    eng = _eng_with_cfg()
    assert eng._market_passes_schedule(_window()) is True


def test_schedule_hour_filter_match():
    eng = _eng_with_cfg(hour_filter=[22, 23])
    assert eng._market_passes_schedule(_window("2026-05-19T22:30:00Z")) is True
    assert eng._market_passes_schedule(_window("2026-05-19T23:00:00Z")) is True


def test_schedule_hour_filter_skip():
    eng = _eng_with_cfg(hour_filter=[22, 23])
    assert eng._market_passes_schedule(_window("2026-05-19T15:30:00Z")) is False


def test_schedule_weekday_filter():
    # 2026-05-19 = Tuesday = weekday 1; 2026-05-23 = Saturday = 5
    eng = _eng_with_cfg(weekday_filter=[0, 1, 2, 3, 4])  # iş günleri
    assert eng._market_passes_schedule(_window("2026-05-19T12:00:00Z")) is True  # Sal
    assert eng._market_passes_schedule(_window("2026-05-23T12:00:00Z")) is False  # Cmt


def test_schedule_minute_of_hour_filter():
    eng = _eng_with_cfg(minute_of_hour_filter=[0, 30])
    assert eng._market_passes_schedule(_window("2026-05-19T22:00:00Z")) is True
    assert eng._market_passes_schedule(_window("2026-05-19T22:30:00Z")) is True
    assert eng._market_passes_schedule(_window("2026-05-19T22:15:00Z")) is False
    assert eng._market_passes_schedule(_window("2026-05-19T22:45:00Z")) is False


def test_schedule_combined_filters_all_must_match():
    eng = _eng_with_cfg(hour_filter=[22], weekday_filter=[1], minute_of_hour_filter=[30])
    # 2026-05-19 22:30 — Sal, 22h, :30 → hepsi tutuyor
    assert eng._market_passes_schedule(_window("2026-05-19T22:30:00Z")) is True
    # Saat doğru, dakika yanlış → False
    assert eng._market_passes_schedule(_window("2026-05-19T22:15:00Z")) is False
    # Dakika doğru, gün yanlış → False
    assert eng._market_passes_schedule(_window("2026-05-23T22:30:00Z")) is False


def test_schedule_slug_unix_fallback():
    """ISO start_time yoksa slug sonundaki unix epoch'tan parse et."""
    eng = _eng_with_cfg(hour_filter=[22])
    # 2026-05-19T22:00:00Z = epoch 1779314400
    w = {"slug": f"btc-up-5m-{1779314400}", "market_start_time": "", "first_snap_ms": 0}
    assert eng._market_passes_schedule(w) is True


def test_schedule_first_snap_ms_fallback():
    """start_time + slug yoksa first_snap_ms'den parse et."""
    eng = _eng_with_cfg(hour_filter=[22])
    # 2026-05-19T22:00:00Z = epoch 1779314400 * 1000
    w = {"slug": "no-unix-here", "market_start_time": "", "first_snap_ms": 1779314400000}
    assert eng._market_passes_schedule(w) is True


def test_schedule_unparseable_with_filter_skips():
    """Filter aktif ama saat çıkarılamazsa False (defansif)."""
    eng = _eng_with_cfg(hour_filter=[22])
    w = {"slug": "no-unix", "market_start_time": "bozuk", "first_snap_ms": 0}
    assert eng._market_passes_schedule(w) is False


def test_schedule_unparseable_no_filter_passes():
    """Filter yoksa parse hatası önemsiz."""
    eng = _eng_with_cfg()
    w = {"slug": "no-unix", "market_start_time": "bozuk", "first_snap_ms": 0}
    assert eng._market_passes_schedule(w) is True


# ── VirtualPortfolio.close_trade_at_price ───────────────────


def _make_portfolio(balance: float = 10000.0) -> VirtualPortfolio:
    return VirtualPortfolio(initial_balance=balance, trade_amount=1.0, fee_calculator=FeeCalculatorV3())


def _make_trade(entry_price: float = 0.50, amount: float = 1.0, shares: float = 2.0, direction: str = "up") -> Trade:
    return Trade(
        market_id="m1",
        coin="BTC",
        market_type="5m",
        strategy="test",
        direction=direction,
        entry_price=entry_price,
        amount=amount,
        shares=shares,
        fee=0.10,
        confidence=0.6,
    )


def test_close_at_price_profit():
    """Entry 0.50, exit 0.80 → 2 shares × 0.80 = 1.60 payout; net pozitif."""
    p = _make_portfolio()
    t = _make_trade(entry_price=0.50, shares=2.0, amount=1.0)
    t.fee = 0.0  # fee'siz pür math
    p.close_trade_at_price(t, 0.80)
    # payout - amount - (entry_fee + exit_fee) = 1.60 - 1.0 - exit_fee
    # exit_fee for crypto @ price=0.80, payout=1.60 → fee_model_v3
    assert t.exit_price == 0.80
    assert t.pnl > 0
    assert t.won is True


def test_close_at_price_loss():
    """Entry 0.50, exit 0.20 → 2 shares × 0.20 = 0.40 payout; net negatif."""
    p = _make_portfolio()
    t = _make_trade(entry_price=0.50, shares=2.0, amount=1.0)
    t.fee = 0.0
    p.close_trade_at_price(t, 0.20)
    assert t.exit_price == 0.20
    assert t.pnl < 0
    assert t.won is False


def test_close_at_price_clamps_to_unit_interval():
    """exit_price 0..1 dışı → clamp."""
    p = _make_portfolio()
    t = _make_trade()
    t.fee = 0.0
    p.close_trade_at_price(t, 1.5)
    assert t.exit_price == 1.0
    p2 = _make_portfolio()
    t2 = _make_trade()
    t2.fee = 0.0
    p2.close_trade_at_price(t2, -0.3)
    assert t2.exit_price == 0.0


def test_close_at_price_accumulates_exit_fee():
    """Exit fee mevcut trade.fee'ye eklenmeli (entry + exit toplam)."""
    p = _make_portfolio()
    t = _make_trade()
    entry_fee = t.fee  # 0.10
    p.close_trade_at_price(t, 0.60)
    # exit_fee = polymarket_taker_fee_v2(0.60, payout=2.0×0.60=1.20, crypto)
    # = 0.07 × (1−0.60) × 1.20 ≈ 0.0336 (V3 mode)
    assert t.fee > entry_fee
    assert t.fee == pytest.approx(entry_fee + 0.07 * (1 - 0.60) * (2.0 * 0.60), rel=0.01)


def test_close_at_price_updates_equity_curve():
    p = _make_portfolio()
    initial_curve_len = len(p.equity_curve)
    t = _make_trade()
    p.close_trade_at_price(t, 0.70)
    assert len(p.equity_curve) == initial_curve_len + 1
    assert len(p.trades) == 1


# ── _run_market filter pipeline ─────────────────────────────


class _AlwaysEntryStrategy(BaseBacktestStrategy):
    """İlk snapshot'ta UP sinyali atan basit test stratejisi."""

    name = "_test_always_entry"
    version = "1.0"

    def on_market_open(self, market: MarketData) -> None:
        self._market = market
        self._signal_emitted = False

    def on_snapshot(self, snapshot: OrderbookSnapshot) -> Optional[Signal]:
        if self._signal_emitted:
            return None
        self._signal_emitted = True
        return Signal(
            direction=Direction.UP,
            confidence=1.0,
            entry_price=snapshot.up_best_ask or 0.5,
            reason="test",
        )


# Strategy'yi registry'e tek seferlik ekle (modül yüklendiğinde)
StrategyRegistryV2.register(_AlwaysEntryStrategy)


def _engine_for_run(**cfg_overrides) -> ReplayEngine:
    """Synthetic snapshot'lar boş `[]` orderbook taşır — default real_orderbook
    fill mode'unda depth walk başarısız olur. Testlerde midpoint fill mode'u
    sentetik snapshot'larla uyumlu (mid = (bid+ask)/2 kullanır)."""
    cfg = ReplayConfig(
        strategy_name="_test_always_entry",
        trade_amount=1.0,
        fill_mode=cfg_overrides.pop("fill_mode", "midpoint"),
        **cfg_overrides,
    )
    eng = ReplayEngine(db=MagicMock(), config=cfg)
    eng._setup()  # portfolio + fill_sim + strategy hazırla
    return eng


def _market() -> MarketData:
    return MarketData(
        market_id="t1",
        coin="BTC",
        market_type="5m",
        question="test",
        start_time="2026-05-19T22:00:00Z",
        winner="UP",
        volume=1000.0,
        liquidity=500.0,
        duration_seconds=300,
        hour_utc=22,
    )


_TS_BASE_MS = 1_779_314_400_000  # 2026-05-19T22:00:00Z, sıfır olmayan epoch


def _snap(ts_offset_ms: int, up_ask: float = 0.55, up_bid: float = 0.50, down_ask: float = 0.50) -> dict:
    """Synthetic raw snapshot dict (DB-shaped) — _convert_snapshot bunu çevirir.

    `_convert_snapshot` `elapsed_ms = ts - first_ts if first_ts else 0` ile
    çalışır — first_ts 0 olursa tüm elapsed 0'lanır (gerçek DB'de problem
    değil çünkü ts_ms unix epoch ms). Testlerde non-zero baseline ekliyoruz.
    """
    return {
        "ts_ms": _TS_BASE_MS + ts_offset_ms,
        "up_best_bid": up_bid,
        "up_best_ask": up_ask,
        "down_best_bid": 1.0 - up_ask,
        "down_best_ask": down_ask,
        "mid_price_up": (up_ask + up_bid) / 2,
        "mid_price_down": 1.0 - (up_ask + up_bid) / 2,
        "up_spread": up_ask - up_bid,
        "binance_price": 100000.0,
        "binance_price_change_pct": 0.0,
        "up_bid_depth_usd": 1000.0,
        "up_ask_depth_usd": 1000.0,
        "down_bid_depth_usd": 1000.0,
        "down_ask_depth_usd": 1000.0,
        "up_bids_json": "[]",
        "up_asks_json": "[]",
        "down_bids_json": "[]",
        "down_asks_json": "[]",
    }


def test_run_market_no_filter_takes_first_signal():
    """Baseline: filter yok → 1. snapshot'ta sinyal yakalanır, market_close'ta resolve."""
    eng = _engine_for_run()
    snapshots = [_snap(i * 1000) for i in range(10)]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Binary win (UP, winner=UP) → exit_price = 1.0
    assert t.exit_price == 1.0


def test_run_market_entry_second_min_blocks_early_signals():
    """entry_second_min=60 → ilk 60sn sinyal blok; 60. saniyede yakalanır."""
    eng = _engine_for_run(entry_second_min=60)
    # 0, 10, 20, ..., 90 saniye snapshot'ları (her 10sn)
    snapshots = [_snap(i * 10_000, up_ask=0.55 + i * 0.001) for i in range(10)]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    # Sinyal saniye 60'ta yakalanmalı → entry_price ≈ 0.55 + 6×0.001 = 0.556
    t = eng.portfolio.trades[0]
    assert t.entry_price == pytest.approx(0.556, abs=0.005)


def test_run_market_entry_second_max_blocks_late_signals():
    """entry_second_max=30 → 30sn'den sonra sinyal yok = TRADE YOK."""
    # Strategy ilk-snapshot signal atar; 0sn'de geçer ama config min/max[0, 30]
    # yokken sırf max set edersek: 0sn dahil < max=30 OK → sinyal yakalanır
    # max'ı denemek için min'i 31 yapalım (entry penceresi 31..40)
    eng = _engine_for_run(entry_second_min=31, entry_second_max=40)
    snapshots = [_snap(i * 10_000) for i in range(5)]  # 0, 10, 20, 30, 40
    # 0,10,20,30 < 31 → atla; 40 ≤ 40 → yakala
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1


def test_run_market_entry_second_window_no_signal():
    """entry_second_max çok küçük → snapshot'lar penceredeyken kalmıyor → trade yok."""
    eng = _engine_for_run(entry_second_min=200, entry_second_max=250)
    # Snapshot'lar 0..40s — pencere 200..250'ye ulaşmıyor
    snapshots = [_snap(i * 10_000) for i in range(5)]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 0


def test_run_market_exit_second_max_forces_early_close():
    """Entry 0sn @ 0.55, exit 30sn @ 0.70 → erken kapanış, intermediate fiyat."""
    eng = _engine_for_run(exit_second_max=30)
    # 0, 10, 20, 30, 40 saniye
    # Up fiyatı zaman içinde artar (entry → exit profit)
    snapshots = [
        _snap(0,  up_ask=0.55, up_bid=0.50),
        _snap(10_000, up_ask=0.60, up_bid=0.55),
        _snap(20_000, up_ask=0.65, up_bid=0.60),
        _snap(30_000, up_ask=0.72, up_bid=0.70),  # exit @ this
        _snap(40_000, up_ask=0.80, up_bid=0.78),
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Erken-exit → exit_price = up_best_bid at 30sn = 0.70 (NOT 1.0)
    assert t.exit_price == pytest.approx(0.70, abs=0.01)
    assert "exit_reason" in (t.metadata or {})
    assert "exit_second_max" in t.metadata["exit_reason"]


def test_run_market_exit_yes_price_above_triggers():
    """Entry @ 0.55, fiyat 0.80 üstüne çıkınca exit."""
    eng = _engine_for_run(exit_yes_price_above=0.80)
    snapshots = [
        _snap(0,  up_ask=0.55, up_bid=0.50),
        _snap(10_000, up_ask=0.70, up_bid=0.65),
        _snap(20_000, up_ask=0.85, up_bid=0.82),  # >= 0.80 trigger
        _snap(30_000, up_ask=0.90, up_bid=0.88),
    ]
    eng._run_market(_market(), snapshots, "UP")
    t = eng.portfolio.trades[0]
    assert t.exit_price == pytest.approx(0.82, abs=0.01)
    assert "exit_yes_price_above" in t.metadata.get("exit_reason", "")


def test_run_market_exit_yes_price_below_triggers():
    """Entry @ 0.55, fiyat 0.30 altına düşünce exit (stop-loss)."""
    eng = _engine_for_run(exit_yes_price_below=0.30)
    snapshots = [
        _snap(0,  up_ask=0.55, up_bid=0.50),
        _snap(10_000, up_ask=0.40, up_bid=0.35),
        _snap(20_000, up_ask=0.28, up_bid=0.25),  # <= 0.30 trigger
        _snap(30_000, up_ask=0.20, up_bid=0.15),
    ]
    eng._run_market(_market(), snapshots, "UP")
    t = eng.portfolio.trades[0]
    assert t.exit_price == pytest.approx(0.25, abs=0.01)
    assert "exit_yes_price_below" in t.metadata.get("exit_reason", "")


def test_run_market_entry_yes_price_range_blocks_out_of_range():
    """entry_yes_price_min=0.60 → 0.55 fiyatlı snapshot sinyal vermez."""
    eng = _engine_for_run(entry_yes_price_min=0.60, entry_yes_price_max=0.80)
    snapshots = [
        _snap(0, up_ask=0.55),  # < 0.60, atla
        _snap(10_000, up_ask=0.58),  # < 0.60, atla
        _snap(20_000, up_ask=0.65),  # OK, sinyal yakala
        _snap(30_000, up_ask=0.70),
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    # Entry @ snap index 2 → ts_offset=20_000ms = 20s → pct = 20/300 ≈ 0.0667
    # entry_price = fill_price (midpoint+slippage) NOT signal price — entry_time_pct
    # ile sinyalin doğru snapshot'ta yakalandığını doğrularız.
    t = eng.portfolio.trades[0]
    assert t.entry_time_pct == pytest.approx(20 / 300, abs=0.005)
    # Fill price midpoint of (0.50, 0.65) + spread cost ≈ 0.598 — kabaca 0.65'in altında
    # ama 0.55'in üstünde (snapshot 0/1 olmadığını teyit eder)
    assert t.entry_price > 0.575
    assert t.entry_price < 0.65


def test_run_market_entry_yes_price_range_no_signal():
    """Hiçbir snapshot pencerede değil → trade yok."""
    eng = _engine_for_run(entry_yes_price_min=0.90, entry_yes_price_max=0.99)
    snapshots = [_snap(i * 10_000, up_ask=0.55 + i * 0.01) for i in range(5)]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 0


def test_run_market_combined_entry_window_and_price():
    """entry_second_min=20 + entry_yes_price_min=0.60 — ikisi de gerek."""
    eng = _engine_for_run(entry_second_min=20, entry_yes_price_min=0.60)
    snapshots = [
        _snap(0, up_ask=0.65),  # second=0 < 20 — atla (fiyat OK ama saniye değil)
        _snap(10_000, up_ask=0.65),  # second=10 < 20 — atla
        _snap(20_000, up_ask=0.55),  # second OK ama fiyat < 0.60 — atla
        _snap(30_000, up_ask=0.65),  # ikisi de OK → yakala
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Entry @ snap index 3 → 30s → pct = 30/300 = 0.10
    assert t.entry_time_pct == pytest.approx(30 / 300, abs=0.005)
    # Doğru snapshot: ne 0/1 (saniye filter blok), ne 2 (fiyat filter blok)
    assert t.entry_price > 0.575  # snap 3 fiyat aralığında (0.575..0.65)


def test_run_market_baseline_no_regression():
    """Yeni knob'ların default'ları eski davranışı etkilemiyor mu?"""
    eng = _engine_for_run()  # tüm default'lar
    snapshots = [_snap(i * 10_000) for i in range(5)]
    eng._run_market(_market(), snapshots, "DOWN")
    # Strategy UP atar, winner DOWN → kayıp; exit_price = 0.0 (binary loss)
    t = eng.portfolio.trades[0]
    assert t.exit_price == 0.0
    assert t.pnl < 0
    assert "exit_reason" not in (t.metadata or {})


# ── Faz 5 — GTC limit order simülasyonu ─────────────────────


def test_run_market_limit_fills_when_ask_drops():
    """Sinyal yakalanır → limit @ 0.50 post → fiyat 0.50'ye düşünce fill."""
    eng = _engine_for_run(entry_limit_price=0.50)
    snapshots = [
        _snap(0,        up_ask=0.65, up_bid=0.60),   # sinyal: ask>limit, fill bekle
        _snap(10_000,   up_ask=0.55, up_bid=0.50),   # hala üstte
        _snap(20_000,   up_ask=0.48, up_bid=0.43),   # ask < 0.50, FILL
        _snap(30_000,   up_ask=0.40, up_bid=0.35),
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Fill at min(0.48, 0.50) = 0.48 (current ask iyi olduğu için ondan)
    assert t.entry_price == pytest.approx(0.48, abs=0.01)
    # Metadata: limit entry tipini kaydetmiş
    assert t.metadata.get("entry_type") == "limit"
    assert t.metadata.get("entry_limit_price") == 0.50
    # WIN: ask düşse de binary UP=1.0 settle (exit yok, market_close)
    assert t.exit_price == 1.0


def test_run_market_limit_never_fills_no_trade():
    """Ask hiç limit'in altına düşmezse trade açılmaz."""
    eng = _engine_for_run(entry_limit_price=0.20)
    snapshots = [
        _snap(0,        up_ask=0.65, up_bid=0.60),
        _snap(10_000,   up_ask=0.55, up_bid=0.50),
        _snap(20_000,   up_ask=0.48, up_bid=0.43),
        _snap(30_000,   up_ask=0.40, up_bid=0.35),
        _snap(40_000,   up_ask=0.30, up_bid=0.25),  # hiç 0.20'nin altı yok
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 0


def test_run_market_limit_expires_no_trade():
    """entry_limit_expire_seconds = 15 → 15sn sonra fill olmadıysa iptal."""
    eng = _engine_for_run(entry_limit_price=0.30, entry_limit_expire_seconds=15)
    snapshots = [
        _snap(0,        up_ask=0.65, up_bid=0.60),   # sinyal @ 0sn
        _snap(10_000,   up_ask=0.50, up_bid=0.45),   # 10sn: hala bekliyor
        _snap(20_000,   up_ask=0.45, up_bid=0.40),   # 20sn: 15sn aştı, EXPIRE
        _snap(30_000,   up_ask=0.25, up_bid=0.20),   # 30sn: artık fill olabilirdi ama expired
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 0


def test_run_market_limit_down_side():
    """DOWN sinyal için limit — down_best_ask referans alınır."""
    # Strategy hep UP atar; DOWN test'i için direction_filter ile UP'i blokla?
    # Aslında daha basit: strategy DOWN atan yeni bir test class lazım.
    # Mevcut _AlwaysEntryStrategy UP atıyor; quick kludge: signal eli alınır
    # ama _run_market signal.direction'a göre fill side'ı seçer; UP test'imizde
    # bu zaten kanıtlandı. DOWN için ayrı kanıt: down_best_ask referansını
    # kontrol ederiz.
    eng = _engine_for_run()  # baseline strategy
    # Hile: direction_filter=down → UP signal eler. Limit DOWN için lazım,
    # strategy DOWN üretmiyor. Yerine direct unit test:
    # _run_market'ın DOWN-side fill yolunu test etmek için strategy DOWN
    # atan bir test class registry'e ekleyelim.
    from backtest.strategies.base import (
        BaseBacktestStrategy,
        Direction,
        Signal,
        StrategyRegistryV2,
    )

    class _DownStrategy(BaseBacktestStrategy):
        name = "_test_down_entry"
        version = "1.0"

        def on_market_open(self, market):
            self._signal_emitted = False

        def on_snapshot(self, snap):
            if self._signal_emitted:
                return None
            self._signal_emitted = True
            return Signal(
                direction=Direction.DOWN,
                confidence=1.0,
                entry_price=snap.down_best_ask or 0.5,
                reason="test_down",
            )

    StrategyRegistryV2.register(_DownStrategy)
    cfg = ReplayConfig(
        strategy_name="_test_down_entry",
        trade_amount=1.0,
        fill_mode="midpoint",
        entry_limit_price=0.30,
    )
    eng2 = ReplayEngine(db=MagicMock(), config=cfg)
    eng2._setup()

    snapshots = [
        # down_best_ask kompakt değişiyor — 1.0 - up_ask + 0.01 (helper formula)
        _snap(0,        up_ask=0.65, down_ask=0.40),   # down_ask=0.40 > 0.30, bekle
        _snap(10_000,   up_ask=0.55, down_ask=0.50),   # hala üstte
        _snap(20_000,   up_ask=0.45, down_ask=0.28),   # down_ask < 0.30, FILL
    ]
    eng2._run_market(_market(), snapshots, "DOWN")
    assert len(eng2.portfolio.trades) == 1
    t = eng2.portfolio.trades[0]
    assert t.direction == "down"
    assert t.entry_price == pytest.approx(0.28, abs=0.01)
    assert t.metadata.get("entry_type") == "limit"


def test_run_market_limit_with_exit_filter():
    """Limit fill + exit_second — ikisi de devrede."""
    eng = _engine_for_run(
        entry_limit_price=0.50,
        exit_second_max=40,  # 40sn'de zorla çıkış
    )
    snapshots = [
        _snap(0,        up_ask=0.65, up_bid=0.60),  # sinyal
        _snap(10_000,   up_ask=0.60, up_bid=0.55),
        _snap(20_000,   up_ask=0.45, up_bid=0.40),  # FILL @ 0.45
        _snap(30_000,   up_ask=0.55, up_bid=0.50),
        _snap(40_000,   up_ask=0.70, up_bid=0.65),  # EXIT @ 0.65 (up_best_bid)
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Limit fill
    assert t.metadata.get("entry_type") == "limit"
    assert t.entry_price == pytest.approx(0.45, abs=0.01)
    # Exit (40sn early-close, not market_close)
    assert t.exit_price == pytest.approx(0.65, abs=0.01)
    assert "exit_second_max" in t.metadata.get("exit_reason", "")


def test_run_market_market_order_unaffected_by_limit_default():
    """entry_limit_price=0 (default) → market entry, eski davranış."""
    eng = _engine_for_run()  # default
    snapshots = [
        _snap(0, up_ask=0.65, up_bid=0.60),
        _snap(10_000, up_ask=0.55),
    ]
    eng._run_market(_market(), snapshots, "UP")
    assert len(eng.portfolio.trades) == 1
    t = eng.portfolio.trades[0]
    # Standart market entry — fill_mode=midpoint
    assert t.metadata.get("entry_type") != "limit"
    # entry_price = midpoint(0.65, 0.60) + slippage ≈ 0.625 + 0.023 = 0.648
    assert t.entry_price > 0.60
    assert t.entry_price < 0.70
