# SOL / ETH Strategy Tasarımı — 2026-04-24

**Source:** T4.5 calibration (1082 trade) + T4.5-C per-strategy audit
**Target:** SOL ve ETH market'lerinde **slippage-aware**, edge-yüksek strategy'ler.
**Doctrine:** SOL/ETH'i kapatma — onlara özel parametre seti ile fırsat yakala.

---

## 1. Veri özeti — neden SOL/ETH özel?

| Asset | n | mean PnL ($) | mean slip% | p10 slip | Yorum |
|---|---|---|---|---|---|
| **SOL** | 54 | **+0.146** | **-10.75** | -23.56 | 🥇 EN KÂRLI ASSET, slippage'a rağmen edge yüksek |
| **ETH** | 72 | -0.115 | -6.63 | -18.26 | 🔸 net loss ama bireysel iyi strateji'ler var |
| BTC | 640 | -0.009 | -1.63 | -8.53 | high volume, küçük net loss |

**SOL'un EN KÂRLI olması şaşırtıcı** — düşük likidite + slip kötü ama **mean reversion fırsatları** çok büyük (Polymarket SOL/ETH market'lerinde info-asymmetry daha güçlü).

---

## 2. Mevcut SOL/ETH stratejileri (T4.5-C audit)

### SOL (54 trade, 5 strategy)

| ID | Label | Type | Status | n | wr% | totPnL | mPnL | mSlp% | Karar |
|---|---|---|---|---|---|---|---|---|---|
| `b991bc34` | AI_F_SOL_5m_up_0.51 | fusion | **ACTIVE** | 6 | 50% | **+5.74** | **+0.96** | -20.7 | 🎯 KEEP, izle |
| `c9333ea0` | SOL Contrarian Dip | contrarian | stopped | 39 | 35.9% | **+4.59** | +0.12 | -7.87 | 🟢 RESUME |
| `6bd0538b` | AI_F_SOL_5m_any_0.52 | fusion | stopped | 7 | 42.9% | -0.33 | -0.05 | -9.56 | borderline |
| `e6ebd39d` | AI_F_SOL_5m_any_0.51 | fusion | stopped | 1 | 0% | -1.07 | -1.07 | -64.3 | düşük sample, skip |
| `c224f425` | AI_F_SOL_5m_up_0.53 | fusion | stopped | 1 | 0% | -1.06 | -1.06 | -18.4 | düşük sample, skip |

### ETH (72 trade, 11 strategy)

| ID | Label | Type | Status | n | wr% | totPnL | mPnL | Karar |
|---|---|---|---|---|---|---|---|---|
| `20ffef08` | AI_ETH_5m_1112_sniper | sniper | **ACTIVE** | 2 | 50% | +2.23 | **+1.12** | 🎯 KEEP, izle |
| `55f5de13` | AI_F_ETH_5m_any_0.52 | fusion | stopped | 9 | 44% | +1.98 | +0.22 | 🟢 RESUME |
| `9869c1e4` | AI_ETH_15m_noon_UP_fus | fusion | stopped | 4 | 25% | -0.52 | -0.13 | borderline |
| `91b26127` | ETH Martingale DCA | martingale | stopped | 20 | 35% | -2.63 | -0.13 | DON'T resume |
| `64076dfc` | ETH Contrarian Dip | contrarian | stopped | 27 | 33% | -1.36 | -0.05 | borderline |
| Diğer 6 | various AI_F_ETH single-trade | fusion/sniper | stopped | 1-2 | 0% | -0.5 to -2.1 | düşük sample | skip |

---

## 3. Tasarım prensipleri (SOL/ETH-special)

### a) **Slippage absorber — daha geniş edge gate**
SOL/ETH'da slip mean -7 ile -10% arası. Edge'in slip'i yenmesi için sinyal eşiği BTC'den daha yüksek olmalı. BTC'de `odds_threshold=0.50` yeterli olabilir; SOL/ETH için **0.55 veya daha güçlü** sinyal beklemeli.

### b) **Maker yolu zorla** (T4.5-B knob'lar entegre)
T4.5-B sonrası `ADAPTIVE_MAKER_MAX_SIGNAL=0.65` + `ADAPTIVE_MAKER_MIN_MINS=0.5` aktif. SOL/ETH'da maker fill 0.2% rebate getirir + slip absorber olur. Maker hedef.

### c) **Stop-loss daha geniş**
BTC stop %3-5 yeterliyken SOL/ETH `stop_loss_percent=8.0` veya daha geniş. Slippage absorber. Aksi halde slip alone trade'i stop'a sürükler.

### d) **Take-profit aynı oranlı**
SL geniş ise TP de proportional yüksek. SL=8 ise TP=12-15.

### e) **15m timeframe tercih** (likidite zaman kazanıyor)
5m markets SOL/ETH'da çok riskli (zaten gördük). 15m daha güvenli — limit emirler maker yoluyla dolma şansı arttı.

### f) **min_volatility = 0** (filter kapat)
SOL/ETH zaten volatil; min_volatility filter set edersen sinyali kaybedersin.

### g) **max_entry_slippage = 0.10** (10% — geniş)
BTC %2-3 makul; SOL/ETH için 10% buffer ver, yoksa entry skip.

---

## 4. Önerilen yeni strategy spec'leri

### Spec A: SOL Contrarian Aggressive (mean-reversion fırsat odaklı)

```yaml
label: "SOL Contrarian Aggressive 15m"
asset: SOL
timeframe: 15m
direction: ANY
trade_amount: 1.0
odds_threshold: 0.55              # daha güçlü sinyal eşiği
strategy_type: contrarian
stop_loss_percent: 8.0            # slip absorber
take_profit_percent: 12.0         # 1.5x risk:reward
max_entry_slippage: 0.10          # 10% buffer
min_volatility: null              # filter yok
minutes_before_end: 1.0           # 1 dk önce kapat (15m markets)
max_executions_per_event: 1
max_losses_per_event: 1
```

**Veri desteği:** c9333ea0 SOL Contrarian Dip (stopped) 39 trade, +$4.59. Bu spec onu 15m + daha geniş tolerance ile yeniden inşa eder.

### Spec B: SOL Fusion Maker-First (b991bc34 doctrine)

```yaml
label: "SOL Fusion Maker 5m UP"
asset: SOL
timeframe: 5m
direction: UP
trade_amount: 1.0
odds_threshold: 0.51              # b991bc34 ile aynı (kanıtlı)
strategy_type: fusion
stop_loss_percent: 6.0
take_profit_percent: 10.0
max_entry_slippage: 0.10
minutes_before_end: 0.5
max_executions_per_event: 1
```

**Veri desteği:** b991bc34 (ACTIVE, +$0.96 mPnL × 6 trade). Daha fazla volume + maker yolu (T4.5-B knob aktif) = daha fazla rebate.

### Spec C: ETH Fusion Conservative (55f5de13 doctrine)

```yaml
label: "ETH Fusion 5m Any 0.52"
asset: ETH
timeframe: 5m
direction: ANY
trade_amount: 1.0
odds_threshold: 0.52              # 55f5de13 ile aynı
strategy_type: fusion
stop_loss_percent: 7.0            # ETH slip biraz daha kötü → biraz daha geniş
take_profit_percent: 11.0
max_entry_slippage: 0.08
minutes_before_end: 0.5
```

**Veri desteği:** 55f5de13 (stopped, 9 trade, +$1.98, mPnL +$0.22). Resume + maker.

### Spec D: ETH Sniper Strict (20ffef08 doctrine)

```yaml
label: "ETH Sniper 5m Strict"
asset: ETH
timeframe: 5m
direction: ANY
trade_amount: 1.0
odds_threshold: 0.55              # sniper genelde sıkı eşik
strategy_type: sniper
stop_loss_percent: 5.0            # sniper hızlı çıkar
take_profit_percent: 10.0
max_entry_slippage: 0.05          # daha sıkı (sniper edge slip toleransı düşük)
minutes_before_end: 0.5
```

**Veri desteği:** 20ffef08 (ACTIVE, 2 trade, +$1.12 mPnL — sample küçük ama promising). Volume artar → istatistiksel kanıt.

---

## 5. Aksiyon planı

### Sıra 1 (kolay — Telegram'dan resume)
```
/start_strategy c9333ea0      (SOL Contrarian Dip — kanıtlı +$4.59)
/start_strategy 55f5de13      (ETH Fusion 0.52 — kanıtlı +$1.98)
```

### Sıra 2 (yeni strategy — `/strategy_create` veya manuel SQL)
Spec A, B, C, D'yi uygula. Yeni 4 strategy = SOL/ETH için kümeleme. Mevcut 14 active'e eklenir → 18 active.

### Sıra 3 (24-48h gözlem)
- Maker A/B knob aktif (T4.5-B)
- Yeni regime_at_entry write aktif (T4.10)
- REST telemetry açık (T4.7)
- Akşam tekrar `audit_strategy_pnl.py --asset SOL` + `--asset ETH` → yeni strategy'lerin performansı

### Sıra 4 (data-driven karar)
Eğer Spec A,B,C,D 50+ trade biriktirip pozitif mPnL gösterirse → KEEP. Aksi halde pause + parametre tune.

---

## 6. Risk + dikkat

- **SOL/ETH likidite kötü** — paper trading'de gözüken edge'ler live'da farklı olabilir. Her strategy'i paper'da en az 30 trade test et.
- **Maker A/B sonrası slip pattern değişebilir** → eski calibration tablosu eski olur. T4.5 sonra haftalık tekrarla.
- **Spec A 15m timeframe** — bot için 15m markets var mı? Eğer 5m only ise spec A'yı 5m'e adapt et.
- **odds_threshold 0.55+** ile signal bulunamayabilir — `/diagnose` SIG_WEAK skip artarsa eşiği indir.

---

## 7. Uygulama yöntemleri

**A) Telegram /strategy_create:**
Bot'ta `/strategy_create` komutu varsa interactive (label, asset, timeframe seçim).
Veya `/strategies` → `+ New` butonu.

**B) Manuel SQL (advanced):**
`data_store/polypaper.db` → `INSERT INTO strategies (id, user_id, wallet_id, label, asset, timeframe, direction, trade_amount, odds_threshold, strategy_type, stop_loss_percent, take_profit_percent, ...) VALUES (?, ?, ?, ?, ?, ...)`
Sonra `/start_strategy <new_id>` ile aktive et.

---

## 8. Hızlı verify

Yeni strategy aktive sonrası:
1. `/active_strategies` — yeni 4 görünür mü, status active mi
2. 1-2 saat çalışsın → `/h` veya `/diagnose` ile cycle counts + skip pattern
3. 24h sonra `audit_strategy_pnl.py --top 30` → yeni strategy'ler nerede?

**Eğer yeni 4 strategy 24h içinde 0 trade üretirse:**
- `odds_threshold` çok yüksek → 0.50'ye indir
- `min_volatility` filter problem — null kontrol et
- `direction` çok dar — ANY yap

---

## 9. Kapanış notu

SOL ve ETH'i kapatma kararı **veriye karşı** olurdu (SOL en kârlı asset!). Doğru yaklaşım: **likidite-aware parametre seti** ile fırsatları yakala. Mevcut b991bc34 (SOL fusion ACTIVE) ve 20ffef08 (ETH sniper ACTIVE) zaten bunu kanıtlıyor — bu spec'ler onları **çoğaltıp + resume** ediyor.

T4.5-B maker A/B + T4.10 regime write data toplandıkça, SOL/ETH spec'lerin parametre tuning'i daha zenginleşir. Bu doc baseline; haftalık review.
