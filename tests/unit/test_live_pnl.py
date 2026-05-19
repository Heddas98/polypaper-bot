"""Bot LIVE PnL — `compute_live_pnl` saf fonksiyon testleri.

2026-05-18 (Heddas direktifi): `live_trader._total_pnl` manuel `/live`
trade'lerini kaçırdığı için /live kokpiti "9 trade · $0 PnL" gösteriyordu.
Çözüm: `data.polymarket_portfolio.compute_live_pnl` — Polymarket on-chain
`activity` feed'inden gerçek PnL hesabı (TRADE maliyeti + REDEEM payout).

Bu testler saf hesap fonksiyonunu pin'ler — gerçek dünya 9-trade senaryosu
(2026-05-18 production cache'inden) regresyon çapası olarak dahildir.
"""

from __future__ import annotations

import pytest

from data.polymarket_portfolio import compute_live_pnl

SINCE = 1_700_000_000  # sabit bot mainnet başlangıcı (test referansı)
NOW = SINCE + 1_000_000  # "şimdi" — tüm trade'lerden çok sonra


def _trade(cid: str, ts: int, price: float, size: float, usdc: float) -> dict:
    return {
        "type": "TRADE",
        "condition_id": cid,
        "timestamp": ts,
        "side": "BUY",
        "price": price,
        "size": size,
        "usdc_size": usdc,
    }


def _redeem(cid: str, ts: int, usdc: float) -> dict:
    return {
        "type": "REDEEM",
        "condition_id": cid,
        "timestamp": ts,
        "usdc_size": usdc,
    }


# ── Boş / boundary girdiler ──────────────────────────────────────────────


def test_empty_activity_all_zero():
    r = compute_live_pnl([], SINCE, now_epoch=NOW)
    assert r["trades"] == 0
    assert r["redeems"] == 0
    assert r["markets"] == 0
    assert r["win_markets"] == 0
    assert r["loss_markets"] == 0
    assert r["pending_markets"] == 0
    assert r["cost"] == 0.0
    assert r["payout"] == 0.0
    assert r["net_pnl"] == 0.0
    assert r["fee"] == 0.0
    assert r["roi_pct"] == 0.0  # cost=0 → bölme yok, 0.0


def test_no_exception_on_malformed_rows():
    """Eksik anahtar / None / string değerler — fonksiyon çökmemeli."""
    bad = [
        {"type": "TRADE"},  # condition_id yok
        {"type": "TRADE", "condition_id": "0xX", "timestamp": None},
        {"type": "TRADE", "condition_id": "0xX", "timestamp": "abc"},
        {"type": None, "condition_id": "0xX"},
        {},
    ]
    r = compute_live_pnl(bad, SINCE, now_epoch=NOW)
    assert isinstance(r, dict)
    assert "net_pnl" in r


# ── Tek kazanan market ───────────────────────────────────────────────────


def test_single_winning_market():
    act = [
        _trade("0xAAA", SINCE + 100, price=0.50, size=2.0, usdc=1.05),
        _redeem("0xAAA", SINCE + 200, usdc=2.0),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 1
    assert r["redeems"] == 1
    assert r["markets"] == 1
    assert r["win_markets"] == 1
    assert r["loss_markets"] == 0
    assert r["pending_markets"] == 0
    assert r["cost"] == pytest.approx(1.05)
    assert r["payout"] == pytest.approx(2.0)
    assert r["net_pnl"] == pytest.approx(0.95)
    # fee = usdc_size - price*size = 1.05 - 0.50*2.0 = 0.05
    assert r["fee"] == pytest.approx(0.05)
    assert r["roi_pct"] == pytest.approx(0.95 / 1.05 * 100, abs=0.01)


# ── Kaybeden market (eski trade, redeem yok) ─────────────────────────────


def test_losing_market_old_trade_no_redeem():
    act = [_trade("0xBBB", SINCE + 100, price=0.50, size=2.0, usdc=1.05)]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 1
    assert r["redeems"] == 0
    assert r["win_markets"] == 0
    assert r["loss_markets"] == 1  # eski + redeem yok → kayıp
    assert r["pending_markets"] == 0
    assert r["net_pnl"] == pytest.approx(-1.05)


# ── Pending market (yeni trade, henüz redeem yok) ────────────────────────


def test_pending_market_recent_trade_not_counted_as_loss():
    """Snapshot in-flight bir trade'i yakalarsa kayıp DEĞİL pending sayılır."""
    recent_ts = NOW - 60  # 900s grace içinde
    act = [_trade("0xCCC", recent_ts, price=0.50, size=2.0, usdc=1.05)]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["loss_markets"] == 0
    assert r["pending_markets"] == 1


def test_pending_becomes_loss_after_grace():
    """Grace penceresinden eski + redeem yok → gerçek kayıp."""
    old_ts = NOW - 1000  # 900s grace'ten eski
    act = [_trade("0xDDD", old_ts, price=0.50, size=2.0, usdc=1.05)]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["pending_markets"] == 0
    assert r["loss_markets"] == 1


def test_settle_grace_sec_is_configurable():
    ts = NOW - 300  # 300s önce
    act = [_trade("0xEEE", ts, price=0.5, size=2.0, usdc=1.05)]
    # grace=600 → pending; grace=100 → loss
    assert compute_live_pnl(act, SINCE, NOW, settle_grace_sec=600)["pending_markets"] == 1
    assert compute_live_pnl(act, SINCE, NOW, settle_grace_sec=100)["loss_markets"] == 1


# ── Bot-öncesi geçmiş eleme (since_epoch filtresi) ───────────────────────


def test_pre_bot_trade_excluded():
    """since_epoch öncesi TRADE — bot dönemine ait değil, elenir."""
    act = [
        _trade("0xPRE", SINCE - 5000, price=0.5, size=2.0, usdc=1.0),
        _redeem("0xPRE", SINCE - 4000, usdc=2.0),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 0
    assert r["redeems"] == 0
    assert r["net_pnl"] == 0.0


def test_pre_bot_market_redeemed_in_bot_era_not_counted():
    """Market-bazlı filtre çapası: bot-öncesi market'in redeem'i sınırı
    geçse bile PnL'e DAHİL EDİLMEMELİ (event-bazlı filtre PnL şişirir)."""
    act = [
        # bot-öncesi trade — bu market hiç bot tarafından trade edilmedi
        _trade("0xPHANTOM", SINCE - 5000, price=0.5, size=2.0, usdc=1.0),
        # redeem bot döneminde gelmiş — yine de sayılmamalı
        _redeem("0xPHANTOM", SINCE + 500, usdc=2.0),
        # gerçek bot trade'i — referans
        _trade("0xREAL", SINCE + 100, price=0.5, size=2.0, usdc=1.05),
        _redeem("0xREAL", SINCE + 200, usdc=2.0),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 1  # sadece 0xREAL
    assert r["redeems"] == 1  # sadece 0xREAL — phantom redeem elendi
    assert r["payout"] == pytest.approx(2.0)  # phantom $2.0 dahil DEĞİL
    assert r["net_pnl"] == pytest.approx(0.95)


# ── Aynı market'te birden fazla trade ────────────────────────────────────


def test_multiple_trades_same_market():
    act = [
        _trade("0xGGG", SINCE + 100, price=0.5, size=2.0, usdc=1.0),
        _trade("0xGGG", SINCE + 150, price=0.5, size=2.0, usdc=1.0),
        _redeem("0xGGG", SINCE + 300, usdc=2.5),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 2
    assert r["redeems"] == 1
    assert r["markets"] == 1  # tek conditionId
    assert r["win_markets"] == 1
    assert r["cost"] == pytest.approx(2.0)
    assert r["payout"] == pytest.approx(2.5)
    assert r["net_pnl"] == pytest.approx(0.5)


# ── Fee max(0,...) — negatife düşmez ─────────────────────────────────────


def test_fee_never_negative():
    """usdc_size < price*size olsa bile fee >= 0 (max guard)."""
    act = [_trade("0xHHH", SINCE + 100, price=0.9, size=2.0, usdc=1.0)]
    # price*size = 1.8 > usdc_size 1.0 → ham fark negatif
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["fee"] == 0.0


# ── Gerçek dünya 9-trade regresyon çapası (2026-05-18 production) ─────────

# (price, size, usdc_size) — production polymarket_portfolio_cache snapshot.
# Her trade'in price*size = $1.00 (bot $1 notional), usdc_size = $1 + taker
# fee. fees_v2 crypto modeli 0.07*(1-p) ile cent-cent doğrulandı.
_REAL_9 = [
    (0.86, 1.1628, 1.0098),
    (0.99, 1.0101, 1.0007),
    (0.46, 2.1739, 1.0378),
    (0.40, 2.5000, 1.0420),
    (0.95, 1.0526, 1.0035),
    (0.83, 1.2048, 1.0119),
    (0.88, 1.1364, 1.0084),
    (0.90, 1.1111, 1.0070),
    (0.75, 1.3333, 1.0175),
]


def test_real_world_9_trades_all_winners():
    """2026-05-18 production: 9 bot trade, 9/9 kazandı, net ≈ +$3.55."""
    act: list[dict] = []
    for i, (price, size, usdc) in enumerate(_REAL_9):
        cid = f"0xMKT{i}"
        act.append(_trade(cid, SINCE + i * 1000, price, size, usdc))
        # kazanan market: payout = kazanan share sayısı × $1
        act.append(_redeem(cid, SINCE + i * 1000 + 100, usdc=size))
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    assert r["trades"] == 9
    assert r["redeems"] == 9
    assert r["markets"] == 9
    assert r["win_markets"] == 9
    assert r["loss_markets"] == 0
    assert r["pending_markets"] == 0
    assert r["cost"] == pytest.approx(9.1386, abs=0.001)
    assert r["payout"] == pytest.approx(12.685, abs=0.001)
    assert r["net_pnl"] == pytest.approx(3.546, abs=0.01)
    # taker fee — 0.07*(1-p) modeline cent-cent denk
    assert r["fee"] == pytest.approx(0.1386, abs=0.001)
    assert r["roi_pct"] == pytest.approx(38.8, abs=0.5)


def test_real_world_fee_matches_crypto_taker_model():
    """fee = Σ max(0, usdc_size - price*size) == Σ 0.07*(1-p) (crypto model)."""
    act = [
        _trade(f"0xF{i}", SINCE + i * 1000, price, size, usdc)
        for i, (price, size, usdc) in enumerate(_REAL_9)
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    model_fee = sum(0.07 * (1 - price) for price, _, _ in _REAL_9)
    assert r["fee"] == pytest.approx(model_fee, abs=0.002)


# ── now_epoch=None → gerçek zaman (çökmez) ───────────────────────────────


def test_now_epoch_none_uses_real_time():
    """now_epoch verilmezse datetime.now() kullanılır — exception fırlatmaz."""
    act = [_trade("0xNOW", SINCE + 100, price=0.5, size=2.0, usdc=1.0)]
    r = compute_live_pnl(act, SINCE)  # now_epoch yok
    assert isinstance(r, dict)
    # SINCE çok eski → trade grace'ten eski → loss
    assert r["loss_markets"] == 1


# ── per_market işlem dökümü (2026-05-19 veri-zenginliği) ─────────────────


def test_per_market_detail():
    """per_market — her market için işlem dökümü (nerede/ne zaman/ne kadar)."""
    act = [
        _trade("0xWIN", SINCE + 100, price=0.5, size=2.0, usdc=1.05),
        _redeem("0xWIN", SINCE + 200, usdc=2.0),
        _trade("0xLOSS", SINCE + 300, price=0.8, size=1.25, usdc=1.02),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    pm = r["per_market"]
    assert len(pm) == 2
    # yeni → eski sıralı
    assert pm[0]["ts"] >= pm[1]["ts"]
    by_cid = {m["condition_id"]: m for m in pm}
    win = by_cid["0xWIN"]
    assert win["result"] == "win"
    assert win["entry_price"] == pytest.approx(0.5)
    assert win["cost"] == pytest.approx(1.05)
    assert win["payout"] == pytest.approx(2.0)
    assert win["net"] == pytest.approx(0.95)
    assert win["trades"] == 1
    loss = by_cid["0xLOSS"]
    assert loss["result"] == "loss"
    assert loss["payout"] == 0.0
    assert loss["net"] == pytest.approx(-1.02)


def test_per_market_carries_title_and_outcome():
    """per_market — title + outcome activity event'inden taşınır."""
    act = [
        {
            "type": "TRADE",
            "condition_id": "0xT",
            "timestamp": SINCE + 100,
            "title": "Bitcoin Up or Down - May 18, 2:55PM-3:00PM ET",
            "outcome": "Up",
            "price": 0.75,
            "size": 1.33,
            "usdc_size": 1.02,
        },
        _redeem("0xT", SINCE + 200, usdc=1.33),
    ]
    r = compute_live_pnl(act, SINCE, now_epoch=NOW)
    m = r["per_market"][0]
    assert "Bitcoin" in m["title"]
    assert m["outcome"] == "Up"
    assert m["result"] == "win"


def test_per_market_empty_when_no_trades():
    assert compute_live_pnl([], SINCE, now_epoch=NOW)["per_market"] == []
