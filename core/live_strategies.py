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

import json
import logging
import re
import time
from pathlib import Path
from types import SimpleNamespace

from core.strategy_plugins import BaseStrategy, MarketSnapshot, StrategySignal

logger = logging.getLogger("polypaper.core.live_strategies")

# Varsayılanlar — LAB rev↑ araştırmasıyla hizalı (CLAUDE.md "rev↑ edge").
DEFAULT_REV_THRESHOLD_PCT = 0.15   # önceki mum ≤ -%0.15 → "büyük düşüş"
DEFAULT_MAX_LEVELS = 6             # martingale tavan (base × 2^6 = 64×)
# 2026-05-22 FIX: 0.30 idi → motor market'leri ZATEN ilerlemişken (time_pct
# 0.4-0.7) değerlendiriyor (açılışta değil), bu yüzden %30 penceresi HER ŞEYİ
# blokluyordu (210 SIG_WEAK/cycle, hiç trade yok). rev edge'i SETTLE-YÖNÜ
# bazlı (önceki muma bağlı) → giriş zamanı edge'i değiştirmez. 0.90 = son
# %10 hariç (settle anı) her an gir → stratejiler artık trade eder.
DEFAULT_ENTRY_MAX_TIME_PCT = 0.90


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
            "rev mean-reversion (rev↑: önceki mum düştü→UP · rev↓: yükseldi→DOWN, "
            "row.direction'a göre) + martingale sizing (kaybedince 2× katla, reset)"
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

        # Sinyal modu:
        #   min_volatility>0 → highvol-rev (yüksek-vol adaptif reversal: prev↑→
        #     DOWN, prev↓→UP; vol eşiği = motor-enjekte rolling vol_med).
        #   else direction_filter "down" → rev↓ (pump-fade) · else rev↑ (dip-buy).
        # LAB OOS: rev↑ BTC 5m/1h · rev↓ ETH 15m · highvol-rev SOL 15m.
        _df = (snapshot.direction_filter or "up").lower()
        if _df == "any":
            # highvol-rev (direction=ANY): yüksek-vol rejiminde ADAPTİF reversal
            # (prev↑→DOWN, prev↓→UP). vol eşiği = motor-enjekte rolling vol_med.
            prev_range = meta.get("prev_range_pct")
            vol_med = meta.get("vol_med_pct")
            if prev_range is None or vol_med is None:
                return StrategySignal(reason="highvol-rev: range/vol_med verisi yok")
            try:
                if float(prev_range) < float(vol_med):
                    return StrategySignal(
                        reason=f"highvol-rev: vol düşük "
                        f"({float(prev_range):.3f}% < med {float(vol_med):.3f}%)"
                    )
            except (TypeError, ValueError):
                return StrategySignal(reason="highvol-rev: range/vol_med sayısal değil")
            if prev_chg == 0:
                return StrategySignal(reason="highvol-rev: önceki mum flat")
            trade_dir = "down" if prev_chg > 0 else "up"  # adaptif reversal
            _lbl = "highvol-rev"
        elif _df == "down":
            # rev↓: önceki mum büyük YÜKSELDİ mi? (prev_chg ≥ +rev_thr → DOWN)
            if prev_chg < rev_thr:
                return StrategySignal(
                    reason=f"rev↓: önceki mum yeterince yükselmedi "
                    f"({prev_chg:.3f}% < +{rev_thr}%)"
                )
            trade_dir = "down"
            _lbl = "rev↓ pump-fade"
        else:
            # rev↑: önceki mum büyük DÜŞTÜ mü? (prev_chg ≤ -rev_thr → UP)
            if prev_chg > -rev_thr:
                return StrategySignal(
                    reason=f"rev↑: önceki mum yeterince düşmedi "
                    f"({prev_chg:.3f}% > -{rev_thr}%)"
                )
            trade_dir = "up"
            _lbl = "rev↑ dip-buy"

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
            direction=trade_dir,
            confidence=round(confidence, 4),
            should_trade=True,
            reason=(
                f"{_lbl}: önceki mum {prev_chg:.3f}%, "
                f"martingale L{level} → ${sized:.2f}"
            ),
            metadata={
                "sized_amount": sized,
                "level": level,
                "rev_prev_change_pct": prev_chg,
            },
        )


# ─── Live aday (candidate) işareti — Adım 4 (2026-05-22) ─────
# SADECE NİYET KAYDI. maybe_mirror bunu OKUMAZ — gerçek canlı trade için
# core/live_trader.py LIVE_STRATEGIES whitelist + LIVE_ENABLED=true + 2-tık
# toggle MANUEL gerekir. Bu işaret "Heddas bu stratejiyi canlıya aday gördü"
# der; readiness kriterleri sağlanınca LAB'dan işaretlenir. Ayrı JSON dosya
# (data_store/live_candidates.json) — DB/real-money kodu DEĞİŞMEZ.

_LIVE_CANDIDATES_PATH = Path("data_store/live_candidates.json")

# Canlıya geçiş readiness kriterleri (code doctrine live_trader.py:147):
# "100+ paper trade + WR>=60% + PnL>0 + Heddas manuel onayı".
LIVE_READY_MIN_TRADES = 100
LIVE_READY_MIN_WR = 60.0
LIVE_READY_MIN_PNL = 0.0


def load_live_candidates(path: Path | None = None) -> dict:
    """İşaretli canlı adayları oku (label → meta). Bozuk/yok → {}."""
    p = path or _LIVE_CANDIDATES_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("load_live_candidates bozuk: %s", e)
        return {}


def is_live_candidate(label: str, path: Path | None = None) -> bool:
    return bool(label) and label in load_live_candidates(path)


def mark_live_candidate(label: str, stats: dict | None = None, path: Path | None = None) -> None:
    """Stratejiyi canlı aday işaretle (yalnız niyet — trade AÇMAZ)."""
    if not label:
        return
    p = path or _LIVE_CANDIDATES_PATH
    data = load_live_candidates(p)
    data[label] = {"marked_ts": int(time.time()), "stats": stats or {}}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning("mark_live_candidate yazılamadı: %s", e)


def unmark_live_candidate(label: str, path: Path | None = None) -> None:
    p = path or _LIVE_CANDIDATES_PATH
    data = load_live_candidates(p)
    if label in data:
        del data[label]
        try:
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            logger.warning("unmark_live_candidate yazılamadı: %s", e)


def live_readiness(completed: int, wins: int, losses: int, pnl: float) -> dict:
    """Paper track-record'a göre canlıya hazırlık değerlendir.

    Returns: {ready: bool, wr: float, checks: [(ad, ok, detay)...], missing: [...]}.
    """
    decided = wins + losses
    wr = (wins / decided * 100.0) if decided > 0 else 0.0
    checks = [
        (
            f"≥{LIVE_READY_MIN_TRADES} paper trade",
            completed >= LIVE_READY_MIN_TRADES,
            f"{completed}",
        ),
        (f"WR ≥ %{LIVE_READY_MIN_WR:.0f}", wr >= LIVE_READY_MIN_WR, f"%{wr:.0f}"),
        ("PnL > 0", pnl > LIVE_READY_MIN_PNL, f"${pnl:+.2f}"),
    ]
    missing = [name for name, ok, _ in checks if not ok]
    return {"ready": not missing, "wr": round(wr, 1), "checks": checks, "missing": missing}


# ─── RuleBasedLiveStrategy — tick rule_based paper port (2026-05-22) ───
# Heddas: LAB Strateji Kurucu'daki rule_based ruleset'lerini paper auto-trade'e
# bağla. RuleSet koşulları (elapsed_seconds, up_best_ask, hour_utc...) motor
# MarketSnapshot.metadata'sına EŞLENİR; eval LOGİĞİ backtest `_eval_entry_block`
# ile AYNI (operator/AND-OR birebir → faithful). strategy_type="rule_based",
# label=ruleset adı (plugin onunla yükler).
# DÜRÜST UYARI: tick backtest ob_snapshots TINY-sample → düşük güven; ayrıca
# canlı metadata ↔ backtest snapshot alan eşlemesi (units) birebir olmayabilir
# → paper'da doğrula, abartma.

_RS_NAME_RX = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_RULESET_DIR = Path("data_store/bt_strategies")


def _load_ruleset_json(name: str, dir_path: Path | None = None) -> dict | None:
    """rule_based ruleset'i JSON dosyasından yükle (path-güvenli, name regex)."""
    if not name or not _RS_NAME_RX.match(name):
        return None
    p = (dir_path or _RULESET_DIR) / f"{name}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _engine_meta_to_ns(snapshot: MarketSnapshot) -> SimpleNamespace:
    """Motor MarketSnapshot+metadata → rule_based alanlı SimpleNamespace.

    backtest OrderbookSnapshot/MarketData alan adlarıyla birebir (eval reuse).
    Eksik alan → makul default (defansif; koşul False'a düşer).
    """
    m = snapshot.metadata or {}

    def _f(v, d: float = 0.0) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return d

    total_min = _f(snapshot.total_minutes, 5.0) or 5.0
    rem = _f(snapshot.minutes_remaining, 0.0)
    up_ask = _f(m.get("up_best_ask"), _f(snapshot.best_ask, 0.5))
    up_bid = _f(m.get("up_best_bid"), _f(snapshot.best_bid, 0.5))
    return SimpleNamespace(
        up_best_ask=up_ask,
        up_best_bid=up_bid,
        down_best_ask=_f(m.get("down_best_ask"), round(1.0 - up_bid, 4)),
        down_best_bid=_f(m.get("down_best_bid"), round(1.0 - up_ask, 4)),
        spread=_f(m.get("up_spread"), _f(snapshot.spread, 0.0)),
        binance_price=_f(m.get("binance_mid"), _f(m.get("asset_spot_price"), 0.0)),
        binance_price_change=_f(
            m.get("asset_price_change"), _f(m.get("btc_price_change"), 0.0)
        ),
        up_bid_depth=_f(m.get("up_bid_depth")),
        up_ask_depth=_f(m.get("up_ask_depth")),
        down_bid_depth=_f(m.get("down_bid_depth")),
        down_ask_depth=_f(m.get("down_ask_depth")),
        elapsed_seconds=max(0.0, (total_min - rem) * 60.0),
        remaining_seconds=rem * 60.0,
        elapsed_pct=_f(m.get("time_pct"), 0.0),  # 0..1 (backtest ile aynı)
        coin=str(m.get("coin", "BTC")),
        market_type=str(snapshot.timeframe or "5m"),
        hour_utc=int(_f(m.get("hour_utc"), 0)),
    )


class RuleBasedLiveStrategy(BaseStrategy):
    """LAB rule_based ruleset'ini canlı motorda değerlendirir (paper port)."""

    origin = "core"

    @property
    def name(self) -> str:
        return "rule_based"

    @property
    def description(self) -> str:
        return "LAB Strateji Kurucu rule_based ruleset (tick → paper port)"

    def evaluate(self, snapshot: MarketSnapshot) -> StrategySignal:
        meta = snapshot.metadata or {}
        rs_name = meta.get("ruleset_name")
        if not rs_name:
            return StrategySignal(reason="rule_based: ruleset_name (label) yok")
        ruleset = _load_ruleset_json(str(rs_name))
        if not ruleset:
            return StrategySignal(reason=f"rule_based: '{rs_name}' yüklenemedi")
        entry = ruleset.get("entry") or {}
        try:
            # backtest eval logiği AYNI → operator/AND-OR birebir (faithful)
            from backtest.strategies.rule_based import _eval_entry_block

            ns = _engine_meta_to_ns(snapshot)
            fired = _eval_entry_block(entry, ns, None)
        except Exception as e:  # noqa: BLE001
            logger.debug("rule_based live eval err: %s", e)
            return StrategySignal(reason="rule_based: eval hatası")
        if not fired:
            return StrategySignal(reason=f"rule_based '{rs_name}': koşullar sağlanmadı")
        direction = (
            "down" if str(ruleset.get("direction", "up")).lower() == "down" else "up"
        )
        try:
            conf = max(0.5, min(0.85, float(ruleset.get("confidence", 0.7))))
        except (TypeError, ValueError):
            conf = 0.7
        return StrategySignal(
            direction=direction,
            confidence=round(conf, 4),
            should_trade=True,
            reason=f"rule_based '{rs_name}': koşullar sağlandı → {direction.upper()}",
        )
