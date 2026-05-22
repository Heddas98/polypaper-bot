"""RevMartingaleStrategy — rev↑ mean-reversion + martingale (2026-05-22).

Heddas direktifi: LAB'da bulunan rev↑ edge'i + martingale sizing'i CANLI
motor stratejisi olarak uygula (önce paper). Bu testler saf plugin
mantığını pin'ler: rev↑ sinyal kararı, martingale katlama, tavan, giriş
penceresi, config override + registry entegrasyonu.
"""

from __future__ import annotations

from core.live_strategies import (
    RevMartingaleStrategy,
    is_live_candidate,
    live_readiness,
    load_live_candidates,
    mark_live_candidate,
    unmark_live_candidate,
)
from core.strategy_plugins import MarketSnapshot, StrategyRegistry, StrategySignal


def _snap(direction_filter: str = "up", **meta) -> MarketSnapshot:
    return MarketSnapshot(direction_filter=direction_filter, metadata=dict(meta))


# ── Sinyal kararı ───────────────────────────────────────────


def test_no_prev_change_no_trade():
    """prev_window_change_pct yoksa → no-trade (mum verisi bekleniyor)."""
    sig = RevMartingaleStrategy().evaluate(_snap())
    assert sig.should_trade is False
    assert sig.direction is None
    assert "bekleniyor" in sig.reason


def test_small_drop_no_trade():
    """Önceki mum yeterince düşmedi (eşik altı) → no-trade."""
    # threshold 0.15 → -0.10% düşüş yetersiz
    sig = RevMartingaleStrategy().evaluate(_snap(prev_window_change_pct=-0.10))
    assert sig.should_trade is False
    assert "düşmedi" in sig.reason


def test_up_candle_no_trade():
    """Önceki mum YÜKSELDİ → rev↑ ateşlemez (sadece düşüşte dip-buy)."""
    sig = RevMartingaleStrategy().evaluate(_snap(prev_window_change_pct=0.50))
    assert sig.should_trade is False


def test_big_drop_buys_up():
    """Önceki mum büyük düştü (≤ -threshold) → UP al."""
    sig = RevMartingaleStrategy().evaluate(_snap(prev_window_change_pct=-0.40))
    assert sig.should_trade is True
    assert sig.direction == "up"
    assert 0.55 <= sig.confidence <= 0.80
    assert "rev↑" in sig.reason


# ── rev↓ (pump-fade) — direction_filter='down' ──────────────


def test_rev_down_buys_down_on_pump():
    """direction_filter='down' → rev↓: önceki mum büyük YÜKSELDİ → DOWN al."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(direction_filter="down", prev_window_change_pct=0.40)
    )
    assert sig.should_trade is True
    assert sig.direction == "down"
    assert "rev↓" in sig.reason


def test_rev_down_no_trade_on_drop():
    """rev↓ modunda önceki mum DÜŞTÜYSE ateşlemez (yükseliş bekler)."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(direction_filter="down", prev_window_change_pct=-0.40)
    )
    assert sig.should_trade is False


def test_rev_down_small_pump_no_trade():
    sig = RevMartingaleStrategy().evaluate(
        _snap(direction_filter="down", prev_window_change_pct=0.10)
    )
    assert sig.should_trade is False


def test_rev_up_no_trade_on_pump():
    """rev↑ modunda (up) önceki mum YÜKSELDİYSE ateşlemez."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(direction_filter="up", prev_window_change_pct=0.40)
    )
    assert sig.should_trade is False


# ── Martingale sizing ───────────────────────────────────────


def test_martingale_base_when_no_loss():
    """loss_streak=0 → bet = base_amount (katlama yok)."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(prev_window_change_pct=-0.40, base_amount=1.0, loss_streak=0)
    )
    assert sig.metadata["sized_amount"] == 1.0
    assert sig.metadata["level"] == 0


def test_martingale_doubles_per_loss():
    """loss_streak=3, base=1 → 2^3 = $8, level=3."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(prev_window_change_pct=-0.40, base_amount=1.0, loss_streak=3)
    )
    assert sig.metadata["sized_amount"] == 8.0
    assert sig.metadata["level"] == 3


def test_martingale_caps_at_max_levels():
    """loss_streak max_levels'i geçse bile level tavanlanır (sınırsız katlama YOK)."""
    sig = RevMartingaleStrategy(max_levels=6).evaluate(
        _snap(prev_window_change_pct=-0.40, base_amount=1.0, loss_streak=10)
    )
    assert sig.metadata["level"] == 6
    assert sig.metadata["sized_amount"] == 64.0  # 2^6


def test_martingale_base_default_when_missing():
    """base_amount yoksa 1.0 varsayılır (crash yok)."""
    sig = RevMartingaleStrategy().evaluate(
        _snap(prev_window_change_pct=-0.40, loss_streak=2)
    )
    assert sig.metadata["sized_amount"] == 4.0  # 1.0 × 2^2


# ── Giriş penceresi ─────────────────────────────────────────


def test_entry_window_blocks_late():
    """time_pct giriş penceresini geçtiyse → no-trade (geç girme)."""
    s = RevMartingaleStrategy(entry_max_time_pct=0.30)
    # erken (time_pct 0.1) → trade
    assert s.evaluate(_snap(prev_window_change_pct=-0.40, time_pct=0.1)).should_trade
    # geç (time_pct 0.8) → no-trade
    late = s.evaluate(_snap(prev_window_change_pct=-0.40, time_pct=0.8))
    assert late.should_trade is False
    assert "pencere" in late.reason


# ── Config override (strategy_params → metadata) ────────────


def test_metadata_overrides_threshold():
    """rev_threshold_pct metadata'dan override edilir."""
    s = RevMartingaleStrategy(rev_threshold_pct=0.15)
    # default 0.15 ile -0.10 ateşlemez; ama override 0.05 ile ateşler
    assert s.evaluate(_snap(prev_window_change_pct=-0.10)).should_trade is False
    fired = s.evaluate(_snap(prev_window_change_pct=-0.10, rev_threshold_pct=0.05))
    assert fired.should_trade is True


def test_metadata_overrides_max_levels():
    """max_levels metadata'dan override edilir."""
    s = RevMartingaleStrategy(max_levels=6)
    sig = s.evaluate(
        _snap(prev_window_change_pct=-0.40, base_amount=1.0, loss_streak=10, max_levels=3)
    )
    assert sig.metadata["level"] == 3
    assert sig.metadata["sized_amount"] == 8.0  # 2^3


# ── Registry entegrasyonu ───────────────────────────────────


def test_registry_register_and_route():
    """register → get/evaluate name='martingale' ile yönlenir."""
    reg = StrategyRegistry()
    assert reg.get("martingale") is None  # bare registry boş
    reg.register(RevMartingaleStrategy())
    assert reg.get("martingale") is not None
    sig = reg.evaluate("martingale", _snap(prev_window_change_pct=-0.40, base_amount=1.0))
    assert isinstance(sig, StrategySignal)
    assert sig.should_trade is True
    assert sig.direction == "up"


def test_bad_prev_change_type_no_crash():
    """prev_window_change_pct sayısal değilse → no-trade, exception YOK."""
    sig = RevMartingaleStrategy().evaluate(_snap(prev_window_change_pct="oops"))
    assert sig.should_trade is False


# ── Adım 4: live readiness + candidate store ────────────────


def test_live_readiness_ready():
    r = live_readiness(120, 75, 45, 12.5)
    assert r["ready"] is True
    assert r["wr"] == 62.5
    assert r["missing"] == []


def test_live_readiness_not_ready_all():
    r = live_readiness(40, 20, 20, -5.0)
    assert r["ready"] is False
    assert len(r["missing"]) == 3  # trade + WR + PnL hepsi eksik


def test_live_readiness_partial_wr_fails():
    # 150 trade (ok), PnL +5 (ok) ama WR %50 (<60 fail)
    r = live_readiness(150, 75, 75, 5.0)
    assert r["ready"] is False
    assert any("WR" in m for m in r["missing"])


def test_live_candidate_roundtrip(tmp_path):
    p = tmp_path / "cands.json"
    assert is_live_candidate("rev↑ BTC 5m", path=p) is False
    mark_live_candidate("rev↑ BTC 5m", {"trades": 120}, path=p)
    assert is_live_candidate("rev↑ BTC 5m", path=p) is True
    assert "rev↑ BTC 5m" in load_live_candidates(p)
    unmark_live_candidate("rev↑ BTC 5m", path=p)
    assert is_live_candidate("rev↑ BTC 5m", path=p) is False


def test_live_candidates_corrupt_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ broken json", encoding="utf-8")
    assert load_live_candidates(p) == {}


def test_mark_live_candidate_empty_label_noop(tmp_path):
    p = tmp_path / "c.json"
    mark_live_candidate("", path=p)
    assert load_live_candidates(p) == {}
