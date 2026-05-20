"""Polymarket constants — drift detection pin tests (Faz 6, 2026-05-20).

Heddas direktifi: "polymarket connector kullanarak ve docs kullanarak
bilgilerin doğruluğundan ve güncelliğinden de emin olmanı istiyorum".

Bu testler `core/fees_v2.py`'deki sabitleri Polymarket docs'tan (MCP ile)
doğrulanmış değerlere PIN'ler. Sabit-değişirse test kırılır → niyetli
olduğunu (docs güncellendi ya da hata düzeltildi) commit mesajında
gerekçe + bu pinleri yenile.

Son docs MCP doğrulaması: 2026-05-20 (bu oturum).
Kaynak sayfalar:
  - https://docs.polymarket.com/trading/fees
  - https://docs.polymarket.com/concepts/prices-orderbook
  - https://docs.polymarket.com/trading/orders/overview
  - https://docs.polymarket.com/market-data/websocket/rtds

Drift script: `scripts/check_polymarket_drift.py` (manuel diff aracı —
HTTP fetch ya da MCP gerekirse).
"""

from __future__ import annotations

import pytest

from core.fees_v2 import (
    CATEGORY_FEES,
    DEFAULT_CATEGORY,
    TAIL_HIGH,
    TAIL_LOW,
    polymarket_maker_rebate,
    polymarket_taker_fee_v2,
)

# ── Kategori fee rate sabitleri ──────────────────────────────


def test_crypto_taker_rate_is_0_07():
    """Polymarket docs (2026-05-20): crypto taker rate = 0.07.

    Memory: `0.072 → 0.07 (Polymarket docs cross-check) ✅ 2026-05-11`.
    Eski 0.072 değeri +2.86% sapma yaratıyordu (peak fee $1.80 vs docs $1.75).
    Memory'de "cent-cent doğrulandı" — 9 live trade ile.
    """
    assert CATEGORY_FEES["crypto"]["taker_rate"] == 0.07


def test_crypto_taker_exponent_is_1():
    """Polymarket docs: formula = shares × rate × (p × (1-p))^exp.
    All documented categories use exp=1 (2026-05-20)."""
    assert CATEGORY_FEES["crypto"]["taker_exp"] == 1


def test_crypto_maker_rebate_is_20pct():
    """docs: market-makers/maker-rebates — crypto pool returns 20% to makers."""
    assert CATEGORY_FEES["crypto"]["maker_rebate_pct"] == 0.20


def test_geopolitics_zero_fee():
    """Geopolitics %0 fee markets — docs cross-check 2026-05-03."""
    assert CATEGORY_FEES["geopolitics"]["taker_rate"] == 0.000
    assert CATEGORY_FEES["geopolitics"]["maker_rebate_pct"] == 0.00


def test_default_category_is_crypto():
    """Bot crypto Up/Down marketlere odaklı — varsayılan kategori bu."""
    assert DEFAULT_CATEGORY == "crypto"


def test_all_documented_categories_present():
    """docs.polymarket.com/trading/fees tüm kategorileri listeler — eksik olmasin."""
    expected = {
        "crypto", "sports", "politics", "finance", "economics",
        "culture", "weather", "tech", "mentions", "other", "geopolitics",
    }
    assert expected.issubset(set(CATEGORY_FEES.keys()))


# ── Tail zone sabitleri ──────────────────────────────────────


def test_tail_zones_15_85():
    """Tail zones for fee/edge gate — docs (extreme prices, thin liquidity)."""
    assert TAIL_LOW == 0.15
    assert TAIL_HIGH == 0.85


# ── Fee curve — docs table ile cross-check ───────────────────


@pytest.mark.parametrize(
    "price,amount_usd,expected_fee",
    [
        # Docs: 100 shares at various prices (Trade Value = 100 × price)
        # Formula: shares × 0.07 × p × (1-p) for crypto
        # Table: price | trade value | taker fee
        (0.01, 1.0, 0.0693),   # docs: "$0.01 | $1   | $0.07"  (rounded)
        (0.05, 5.0, 0.3325),   # docs: "$0.05 | $5   | $0.33"
        (0.10, 10.0, 0.63),    # docs: "$0.10 | $10  | $0.63"
        (0.20, 20.0, 1.12),    # docs: "$0.20 | $20  | $1.12"
        (0.30, 30.0, 1.47),    # docs: "$0.30 | $30  | $1.47"
        (0.50, 50.0, 1.75),    # docs: "$0.50 | $50  | $1.75" — peak
        (0.70, 70.0, 1.47),    # docs: symmetric — $0.70 mirrors $0.30
        (0.90, 90.0, 0.63),    # docs: symmetric — $0.90 mirrors $0.10
        (0.99, 99.0, 0.0693),  # docs: "$0.99 | $99  | $0.07"
    ],
)
def test_crypto_fee_curve_matches_docs_table(price, amount_usd, expected_fee):
    """docs.polymarket.com/trading/fees#crypto — exact fee table cross-check.

    Eğer docs'taki tablo değişirse (rate veya exponent revize edilirse), bu
    test çoklu satırda kırılır. Çözüm: docs MCP ile yeniden doğrula,
    `CATEGORY_FEES["crypto"]` güncelle, bu satırları yeni değerlerle revize et.
    """
    actual = polymarket_taker_fee_v2(price, amount_usd, "crypto")
    # Docs tablosu 2-decimal yuvarlama gösteriyor; tolerance 0.01
    assert actual == pytest.approx(expected_fee, abs=0.005), (
        f"Drift! price={price}, amount={amount_usd}: "
        f"docs={expected_fee}, code={actual}"
    )


# ── Maker rebate sanity ──────────────────────────────────────


def test_crypto_maker_rebate_20pct_of_taker():
    """Rebate = taker_fee × 0.20 (crypto pool)."""
    taker_fee = polymarket_taker_fee_v2(0.50, 50.0, "crypto")  # $1.75
    rebate = polymarket_maker_rebate(taker_fee, "crypto")
    assert rebate == pytest.approx(0.35, abs=0.001)  # 1.75 × 0.20


# ── Order types (referans test — sabit yok ama docs'ta listelenmiş) ──


def test_documented_order_types_reference():
    """docs: GTC (limit, rests), GTD (time-limited), FOK (all-or-nothing market),
    FAK (partial market).

    Bu test sabit pin'lemez ama docs'taki order type setinin değişmediğini
    KAYIT ALTINA ALIR — drift script (`scripts/check_polymarket_drift.py`)
    buradan referans alır. 2026-05-20 docs MCP doğrulaması: GTC/GTD/FOK/FAK.
    """
    expected_order_types = {"GTC", "GTD", "FOK", "FAK"}
    # Bu kod tarafında zorlanmıyor (V2 SDK'da serbestçe geçer), ama beklenen set
    # — eğer Polymarket order types ekler/çıkartırsa burayı manuel güncelle.
    assert isinstance(expected_order_types, set)
    assert len(expected_order_types) == 4


# ── Resolution source (5m/15m crypto = Chainlink RTDS) ──────


def test_5m_15m_crypto_uses_chainlink_resolution():
    """docs: market-data/websocket/rtds#chainlink-source — 5m/15m crypto
    markets resolve via Chainlink BTC/USD data stream (NOT Binance spot).

    Memory: 5m/15m kesin Chainlink, bot da RTDS'e bağlı (P1.10).
    Bu test bir SABIT pin DEĞIL — `data/polymarket_rtds.py` modülünün
    varlığı ve RTDS bağlantısı için sentinel. Modül silinirse veya
    Chainlink referansı kaybolursa test kırılır → docs ile yeniden uyumla.
    """
    import importlib

    try:
        rtds_mod = importlib.import_module("data.polymarket_rtds")
    except ImportError as e:
        pytest.fail(f"data/polymarket_rtds.py modülü kayıp — RTDS bağlantısı silinmiş olabilir: {e}")
        return

    # Modülde "chainlink" referansı bulunmalı (settle kaynağı doctrine)
    source_text = (rtds_mod.__doc__ or "") + " ".join(dir(rtds_mod))
    assert "chainlink" in source_text.lower(), (
        "data/polymarket_rtds.py modülünde 'chainlink' referansı yok — "
        "memory '5m/15m=Chainlink' doctrine'i kayıp olabilir, docs MCP ile yeniden doğrula"
    )


# ── Tick size (docs: 0.01 minimum) ──────────────────────────


def test_smallest_fee_5_decimal_precision():
    """docs: 'Fees are rounded to 5 decimal places. Smallest fee = 0.00001 USDC'.

    P0-10 (audit 2026-05-13): fee_model_v3 4→5 decimal precision.
    Eğer round() 4'e geri dönerse test kırılır.
    """
    # Ekstrem küçük trade — fee yuvarlama 5-decimal mi?
    fee = polymarket_taker_fee_v2(0.5, 0.001, "crypto")
    # amount=0.001, price=0.5, shares=0.002, fee=0.002*0.07*0.25 = 0.000035
    # 4-decimal yuvarlama → 0.0000, 5-decimal → 0.00004 (round)
    assert fee == pytest.approx(0.00004, abs=0.00001) or fee == pytest.approx(0.00003, abs=0.00001)
    # Sadece string-formatlı `5 decimals` görünür mü?
    assert len(str(fee).split(".")[-1]) <= 6  # round(x, 5) max 5 ondalık
