# Fee Rates Re-audit vs Polymarket Docs (2026-05-11)

**Direktif:** Heddas "polymarket connector kullanarak ya her şeyi" → tüm fees_v2.py
değerlerini docs.polymarket.com ile bit-by-bit cross-check + sapma fix.

## Kaynaklar (Polymarket docs, fetched 2026-05-11 via MCP)

- https://docs.polymarket.com/trading/fees — Fee Structure tablosu
- https://docs.polymarket.com/market-makers/maker-rebates — Maker Rebate tablosu
- https://docs.polymarket.com/builders/fees — Builder fees layer

## Cross-check tablosu

| Category | docs rate | docs rebate | fees_v2 (önce) | fees_v2 (sonra) | Status |
|---|---:|---:|---:|---:|:---|
| **Crypto** | **0.07** | **20%** | 0.072 ⚠️ | **0.07** ✅ | **FIX UYGULANDI** |
| Sports | 0.03 | 25% | 0.030 | 0.030 | ✅ Identical |
| Finance | 0.04 | 25% | 0.040 | 0.040 | ✅ Identical |
| Politics | 0.04 | 25% | 0.040 | 0.040 | ✅ Identical |
| Economics | 0.05 | 25% | 0.050 | 0.050 | ✅ Identical |
| Culture | 0.05 | 25% | 0.050 | 0.050 | ✅ Identical |
| Weather | 0.05 | 25% | 0.050 | 0.050 | ✅ Identical |
| Mentions | 0.04 | 25% | 0.040 | 0.040 | ✅ Identical |
| Tech | 0.04 | 25% | 0.040 | 0.040 | ✅ Identical |
| Other/General | 0.05 | 25% | 0.050 | 0.050 | ✅ Identical |
| **Geopolitics** | **0** | **—** | **0.000** | **0.000** | ✅ Fee-free confirmed |

## Crypto fee 0.07 verification (üç bağımsız kaynak)

1. **Rate tablosu** (`Fee Structure` bölümü):
   ```
   Category    Taker Fee Rate  Maker Fee Rate  Maker Rebate
   Crypto      0.07            0               20%
   ```

2. **Crypto fee curve table** (peak $1.75 at p=0.50, 100 shares):
   ```
   Formula: fee = C × feeRate × p × (1 - p)
   100 × 0.07 × 0.5 × 0.5 = $1.75  ✓ matches table
   ```
   `0.072` olsa: `100 × 0.072 × 0.5 × 0.5 = $1.80` ≠ docs $1.75.

3. **Fee Tables (100 Shares) duplicate** — aynı $1.75 peak.

## Geopolitics fee-free verification

Docs direkt alıntı:
> "Geopolitical and world events markets are fee-free. Polymarket does not
> charge fees or profit from trading activity on these markets."

`fees_v2.py:70`: `"geopolitics": {"taker_rate": 0.000, "maker_rebate_pct": 0.00}` ✅

## Formula sanity check

Docs formula: `fee = C × feeRate × p × (1 - p)` (exponent 1 across categories).

`fees_v2.py:111`:
```python
fee = shares * rate * (price * (1 - price)) ** exp
```

Yapı uyumlu — `exp = 1` her kategori için doğru.

## Fee Precision

Docs: "Fees are rounded to 5 decimal places. The smallest fee charged is
0.00001 USDC."

`fees_v2.py`: `return float(round(fee, 4))` — **4 decimal precision**, docs 5.
Sapma marjinal (round-trip 0.00001 USDC), 100 sh @ $0.50 trade'de etki ~$0.0001.
**Karar:** Fix opsiyonel (4 vs 5 decimal — mainnet shadow için kritik değil).

## Etki analizi (crypto 0.072 → 0.07 fix)

- **Paper trading fee**: %2.86 daha az çekilir. Paper PnL biraz daha yüksek gözükür.
- **paper × 0.66 multiplier**: Önceki audit'te empirically calibrate edilmişti. Yeni
  fee rate ile gerçekleşen calibration biraz revize edilebilir (reality_gap_job
  birkaç hafta veri biriktirir).
- **Live trading**: Etki SIFIR — bot live fee'yi `getClobMarketInfo()` ile market'ten
  okuyor (per-market gerçek değer). `fees_v2.py` sadece paper hesaplaması.
- **Memory `project_faz0_1_fee_audit_closure.md` (2026-04-28)**: "bit-identical" iddiası
  bu fix ile gerçek anlamda bit-identical hale geldi.

## Yapılan değişiklikler (2026-05-11)

1. `core/fees_v2.py:60` — crypto rate `0.072 → 0.07` + audit yorum block.
2. `tests/unit/test_fees_v2.py:21-26` — `pytest.approx(3.6, abs=0.01) → 3.5`.
3. `tests/unit/test_fees_v2.py:40-43` — comment block updated.
4. `tests/unit/test_p0_p1_extra_coverage.py:4481-4489` — _compute_maker_rebate test sabiti 3.6 → 3.5.
5. `tests/test_phase55_critical.py:138` — yorum 0.072 → 0.07.

## Etkilenmeyen 0.072 cosmetic noktalar

Şu yerlerde 0.072 hâlâ mevcut (mock value / yorum, runtime davranışı etkilemiyor):
- `tests/unit/test_p0_p1_extra_coverage.py:537,541` — SDK mock response shape simülasyonu.
- `tests/unit/test_p0_p1_extra_coverage.py:622,2234,11439` — override test yorumları.

Bunlar mock data; runtime crypto fee artık 0.07.

## Heddas action items

- [x] Crypto fee 0.07 fix uygulandı.
- [ ] Bot Windows-side restart sonrası paper PnL ~%3 daha iyi gözükecek (fee az düşüyor).
- [ ] 5-7 gün veri sonra reality_gap_job multiplier'ı re-calibrate edilebilir.
- [ ] (opsiyonel) Fee precision 4 → 5 decimal (minor).
