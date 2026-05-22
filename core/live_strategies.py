"""PolyPaper Bot — Canlı/paper strateji: rev↑ + martingale (2026-05-22).

Heddas direktifi: LAB candle backtest'inde bulunan TEK OOS-dayanıklı edge'i
(rev↑ = mean-reversion: önceki TAMAMLANMIŞ mum büyük DÜŞTÜYSE → UP al,
dip-buying) + martingale sizing'i CANLI motor için strateji olarak uygula.
ÖNCE paper'da doğrula — `LIVE_ENABLED=false` iken motor yalnız paper trade
eder (executions tablosu), canlı ayna atlanır.

EDGE KANITI (LAB train/test split): rev↑ BTC 5m + 1h iki TF'de OOS-pozitif,
eşiğe duyarsız (overfit değil). Martingale: rev↑ WR yüksek → candle backtest'te
bust=0. **DÜRÜST UYARI**: candle backtest sabit $0.50 girişle test etti (adil
yazı-tura). Canlı paper GERÇEK Polymarket odds'uyla girer → backtest sonucunu
birebir tekrarlamayabilir. Tam da bu yüzden ÖNCE paper'da doğrulanıyor.

strategy_type = "martingale" — motor martingale plumbing'ini yeniden kullanır:
  • engine_signals.py:615  plugin_meta'ya loss_streak + base_amount enjekte
  • engine_signals.py:1339 psig.metadata["sized_amount"] → trade_amount override
  • engine_signals.py:1352 Kelly sizing atlanır (martingale kendi boyutlar)
  • engine_settlement.py    _mg_streak: kaybedince +1, kazanınca 0 (reset)

rev↑ girdisi (`prev_window_change_pct`) engine_signals plugin_meta'da motor
tarafından enjekte edilir (candle_collector → compute_price_deltas).
"""

from __future__ import annotations

import logging

from core.strategy_plugins import BaseStrategy, MarketSnapshot, StrategySignal

logger = logging.getLogger("polypaper.core.live_strategies")

# Varsayılanlar — LAB rev↑ araştırmasıyla hizalı (CLAUDE.md "rev↑ edge").
DEFAULT_REV_THRESHOLD_PCT = 0.15   # önceki mum ≤ -%0.15 → "büyük düşüş"
DEFAULT_MAX_LEVELS = 6             # martingale tavan (base × 2^6 = 64×)
DEFAULT_ENTRY_MAX_TIME_PCT = 0.30  # market'in ilk %30'unda gir (geç girme)


class RevMartingaleStrategy(BaseStrategy):
    """rev↑ mean-reversion girişi + martingale sizing (paper-first).

    Sinyal: önceki tamamlanmış mum eşikten fazla DÜŞTÜYSE UP al (dip-buy).
    Sizing: kaybedince katla (base × 2^loss_streak), max_levels'te tavanla.

    Parametreler `snapshot.metadata` üzerinden override edilebilir
    (DB strategy_params → plugin_meta): rev_threshold_pct, max_levels,
    entry_max_time_pct. Yoksa constructor varsayılanları kullanılır.
    """

    origin = "core"

    def __init__(
        self,
        rev_threshold_pct: float = DEFAULT_REV_THRESHOLD_PCT,
        max_levels: int = DEFAULT_MAX_LEVELS,
        entry_max_time_pct: float = DEFAULT_ENTRY_MAX_TIME_PCT,
    ):
        self.rev_threshold_pct = float(rev_threshold_pct)
        self.max_levels = int(max_levels)
        self.entry_max_time_pct = float(entry_max_time_pct)

    @property
    def name(self) -> str:
        # strategy_type ile EŞLEŞMELİ — motor plugins.get("martingale") ile bulur
        return "martingale"

    @property
    def description(self) -> str:
        return (
            "rev↑ mean-reversion (önceki mum büyük düştü → UP al) + "
            "martingale sizing (kaybedince 2× katla, kazanınca reset)"
        )

    def evaluate(self, snapshot: MarketSnapshot) -> StrategySignal:
        meta = snapshot.metadata or {}

        # Config override (strategy_params → plugin_meta), yoksa default
        try:
            rev_thr = float(meta.get("rev_threshold_pct", self.rev_threshold_pct))
            max_lv = int(meta.get("max_levels", self.max_levels))
            entry_max_t = float(meta.get("entry_max_time_pct", self.entry_max_time_pct))
        except (TypeError, ValueError):
            rev_thr, max_lv, entry_max_t = (
                self.rev_threshold_pct, self.max_levels, self.entry_max_time_pct,
            )

        # rev↑ girdisi: önceki TAMAMLANMIŞ mum % değişimi (motor enjekte eder).
        # Yoksa sessizce no-trade (mum verisi henüz hazır değil → güvenli).
        prev_chg = meta.get("prev_window_change_pct")
        if prev_chg is None:
            return StrategySignal(
                reason="rev↑: prev_window_change_pct yok (mum verisi bekleniyor)"
            )
        try:
            prev_chg = float(prev_chg)
        except (TypeError, ValueError):
            return StrategySignal(reason="rev↑: prev_window_change_pct sayısal değil")

        # Erken pencere kontrolü — market'in ilk %X'inde gir (geç girme).
        time_pct = meta.get("time_pct")
        if time_pct is not None:
            try:
                if float(time_pct) > entry_max_t:
                    return StrategySignal(
                        reason=f"rev↑: giriş penceresi geçti "
                        f"(time_pct {float(time_pct):.2f} > {entry_max_t})"
                    )
            except (TypeError, ValueError):
                pass

        # rev↑ sinyali: önceki mum eşikten fazla DÜŞTÜ mü? (prev_chg ≤ -rev_thr)
        if prev_chg > -rev_thr:
            return StrategySignal(
                reason=f"rev↑: önceki mum yeterince düşmedi "
                f"({prev_chg:.3f}% > -{rev_thr}%)"
            )

        # ── Martingale sizing ──
        try:
            loss_streak = int(meta.get("loss_streak", 0) or 0)
        except (TypeError, ValueError):
            loss_streak = 0
        try:
            base = float(meta.get("base_amount", 1.0) or 1.0)
        except (TypeError, ValueError):
            base = 1.0
        if base <= 0:
            base = 1.0
        # Sınırlı martingale: level max_lv'de tavanlanır (bet base×2^max_lv'yi
        # aşmaz). loss_streak max_lv'yi geçse bile bet sabit kalır (güvenli üst
        # sınır — sınırsız katlama YOK).
        level = max(0, min(loss_streak, max_lv))
        sized = round(base * (2 ** level), 2)

        # Güven: düşüş büyüklüğüyle hafif ölçekle (0.55–0.80 arası)
        drop = abs(prev_chg)
        confidence = max(0.55, min(0.80, 0.55 + drop * 0.05))

        return StrategySignal(
            direction="up",
            confidence=round(confidence, 4),
            should_trade=True,
            reason=(
                f"rev↑ dip-buy: önceki mum {prev_chg:.3f}% (≤ -{rev_thr}%), "
                f"martingale L{level} → ${sized:.2f}"
            ),
            metadata={
                "sized_amount": sized,
                "level": level,
                "rev_prev_change_pct": prev_chg,
            },
        )
