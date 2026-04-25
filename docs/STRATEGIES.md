# Stratejiler

PolyPaper Bot içinde aktif olarak çalışan 18+ stratejinin dokümantasyonu.

## Stratejik Katmanlar

Bot 3 stratejik katmanda organize edilmiş:

1. **Engine stratejileri** — Sabit algoritma, parametreleri HyperOpt ile optimize edilebilir.
2. **Classic plugin** — Algoritmasız, sadece kural bazlı (direction_filter + threshold + TP/SL). HyperOpt'a girmez (PARAM_SPACES yok).
3. **AI stratejileri** — AI Brain'in önerdiği parametre kombinasyonları. Cold-start için $1 ile başlar, 20+ trade sonrası Kelly-sized olurlar.

## Strateji Lifecycle (Phase 74b)

Her strateji 3 faz arasında otomatik ilerler:

| Faz | Trade Sayısı | Filtre Seviyesi | Özellik |
|---|---|---|---|
| `exploration` | 0-30 | Gevşek | Çoğu gate bypass, veri toplama |
| `evaluation` | 30-100 | Orta | Standart gate'ler + PnL gözetim |
| `proven` | 100+ | Sıkı | MIN_COMPOSITE, EDGE_ZONE, CONVICTION_MIN strict |

Geçiş kriteri: son N trade WR'si + cumulative PnL. `/lifecycle` komutu ile her strateji için mevcut faz görüntülenir.

## 5 Ghost-Class Doctrine (Epic 6 + T9.7)

UI panellerinde gösterilip de engine'de hiç çalışmayan veya tersine çalışan parametre/strateji "ghost" sayılır. Epic 6 T6.3 + Epic 9 T9.7'nin kapatma sonucu, bot'un panel-ile-davranış paritesi 5 sınıf altında doktrinize edildi:

1. **AI Brain Flags** — Canonical 6-flag set + `kelly_sizing` virtual flag. Boot-sync defect kapalı.
2. **Runtime ENV Knob** — Module-level import-time ENV'ler (örn. `MIN_ORDER_SHARES`, `ALLOWED_ZONES`) `/env_toggle` whitelist'ine **dahil edilmedi** — restart gerektirir, yalan söylemiyoruz.
3. **PNL_PAUSE Threshold** — T6.1'de module-top constant → `_get_pnl_pause_threshold()` helper, drift -3.0 → -8.0.
4. **Market Recorder Parity** — UI literal + AST valid_features + regex sibling-prop + engine init + stub sim — 11 test × 5 class.
5. **Kelly DB-persist** — Whitelist runtime-readiness guard ile DB'den okur, panel ↔ engine senkronizasyonu garantili.

## Aktif Engine Stratejileri (19 adet)

### 1. late_convergence
**Ne yapar:** Market kapanışa yakın (son 15-30dk) olasılık yakınsaması fırsatlarını yakalar. Becker calibration'dan en yüksek sinyal ağırlığı alır.

**Sinyal kaynakları:** Becker (0.25), Chainlink parity, spread, OB imbalance
**Önerilir:** Düşük volatilite ortamı, BTC/ETH

### 2. fusion_*  (fusion_btc_1h, fusion_eth_4h, fusion_sol_15m, …)
**Ne yapar:** Multi-timeframe + multi-asset sinyal birleşimi. Signal fusion ile 4+ kaynak bir arada değerlendirilir.

**Parametreler:** timeframe, asset, fusion weights
**Özel:** 29 granular apply, asset/tf bazında HyperOpt (Sprint 5)

### 3. fade_extreme
**Ne yapar:** 30-40c ve 60-70c zone'larında "aşırı tepki"yi fade eder (contrarian).
**Sinyaller:** RSI divergence + volume fade
**Uyarı:** FUSION 30-40c zone block (Phase 82c) nedeniyle bu zone'lar AI_F_* loss bucket'ta tutulur.

### 4. breakout
**Ne yapar:** Sıkışma sonrası BB squeeze çıkışlarını yakalar.
**Sinyaller:** BB width + volume expansion + MACD histogram
**Confluence:** K=3 minimum

### 5. mean_reversion
**Ne yapar:** Yerel ortalamaya dönüş. RSI oversold/overbought + range bound confirm.

### 6. penny_contract (Phase 70)
**Ne yapar:** 1-10c arası çok düşük fiyatlı kontratlarda pozitif EV yakalama. Küçük pozisyon, yüksek leverage oranı.
**Uyarı:** EV_MINIMUM strict. Sadece MCI > 0.40 geçerse.

### 7. bonding_yield (Phase 76)
**Ne yapar:** 90-99c kontratlarda tahvil-benzeri yield hesabı. Resolution'a 48 saat kala giriş, %1+ yield arar.
**Parametreler:** `BONDING_MIN_PRICE=0.90`, `BONDING_MAX_PRICE=0.99`, `BONDING_MIN_YIELD=0.01`
**Risk:** Aşağı hareket agresif likide eder.

### 8. three_way_arb (Phase 76)
**Ne yapar:** BTC up/down + ETH up/down + SOL up/down arasında statistical arbitrage.

### 9. copy_leader
**Ne yapar:** Polymarket'te kazanç oranı yüksek wallet'ların trade'lerini izler (bir tür on-chain copy-trading sinyali).

### 10. whale_signal (Phase 60)
**Ne yapar:** $100+ volume'lı trade'ler sonrası fiyat hareketini trade eder. `WHALE_MIN_VOLUME_USD=100`.

### 11. calibration_arb
**Ne yapar:** Becker calibration surface ile market-implied probability arasındaki fark üzerinden arbitraj.

### 12. round_number_gravity (Phase 60)
**Ne yapar:** BTC $100K, $110K gibi yuvarlak rakamlara yaklaşan fiyatlar etrafındaki market inefficiency'yi yakalar.

### 13. cascade_detector (Phase 60)
**Ne yapar:** Liquidation cascade öncesi pozisyon alır. `CASCADE_VOLUME_MULT` + `CASCADE_PRICE_THRESHOLD` tetikleyicileri.

### 14. capital_velocity (Phase 60)
**Ne yapar:** Hızlı para dönüş hızı yakalar — kısa hold, yüksek frekans.

### 15. optimism_tax
**Ne yapar:** Aşırı iyimser (>70c) kontratlarda sistematik fade.

### 16. sub25c_becker
**Ne yapar:** <25c zone'da Becker calibration'ı öncelikli değerlendirerek value hunting.

### 17. lag_arb
**Ne yapar:** Farklı feed'ler arasındaki gecikmeyi arbitraj eder. Latency monitor'dan feed gecikmesi alır.

### 18. weekend_multiplier (Phase 60)
**Ne yapar:** Hafta sonu düşük likidite ortamında daha sıkı filter + daha büyük position size.

### 19. opening_breakout (2026-04-23)
**Ne yapar:** Polymarket cycle açılışından sonra ilk N dakikadaki breakout sinyalini yakalar. Backtest'te kârlı çıkmış live spec.

**Sinyaller:** Cycle-open price + ilk dakikalık range expansion + volume confirmation
**Parametreler:** `OPEN_WINDOW_MIN`, `BREAK_THRESHOLD_PCT`, `VOLUME_MIN_RATIO`
**Lifecycle:** exploration (yeni eklendi, henüz proven değil)
**Kaynak:** `f353060 feat(opening_breakout): backtest-karli strategy live spec + create script`

## Classic Plugin (Phase 82e Sprint 4.6)

Yeni bir strateji tipi. **Algoritma yok** — sadece:
- `direction_filter`: UP, DOWN, veya both
- `threshold`: min probability (örn: 0.65)
- `TP` / `SL`: Take-profit ve stop-loss hedefleri

**Tüm gate'leri bypass eder** (Phase 82e Sprint 5 HOTFIX v3):
```
CLASSIC_BYPASS_ALL_GATES=true  # 14-gate tek ENV flag ile devre dışı
```

Opt-in flag'ler:
- `CLASSIC_RESPECT_UNSELLABLE=false` — Phase 66 UNSELLABLE gate
- `CLASSIC_RESPECT_ZONES=false` — ALLOWED_ZONES filter

HyperOpt auto-skip eder (PARAM_SPACES'ta yok).

Kullanım: `/strategy_builder` komutu ile UI'den yapılandırılır.

## AI Stratejileri

AI Brain (Phase 69), 2-Agent sistemi ile yeni parametre kombinasyonları önerir:

1. **Optimist Agent** — "Bu stratejiyi daha agresif yapsak daha çok kazanır mıydık?"
2. **Critic Agent** — "Bu öneride hangi riskler/biases var?"
3. **Synthesis** — İki ajanın çıktısını birleştirir
4. `AI_AUTO_CONFIDENCE=0.70` üstü öneriler otomatik `$1/trade` ile aktive edilir
5. 20+ trade sonrası Kelly-sized olurlar

Öneri izleme: `/ai`, `/why <trade_id>`, `/report` komutları.

## HyperOpt & Tournament (Phase 67 + 82e)

**Optuna TPE ile parametre taraması:**
```
/hyperopt <strategy>          # Tek strateji
/hyperopt_all                 # 21→8 apply-filter (Sprint 4.5)
```

Pipeline:
1. `HyperOptPipeline.prime_windows_cache()` — pipeline'da tek seferlik (Sprint 2.5)
2. Per-trial discovery 300s timeout → 7s covering index sonrası (32x)
3. Overfit gate: train/test split > 0.60 WR ayrılığı varsa reddet
4. `is_overfit()` sign-aware (Phase 82e Sprint 5)
5. Apply: `hyperopt_results` (v14) → `_ALLOWED_PARAMS` whitelist

**AI Tournament:** Gecelik (00:00-04:00), `TOURNAMENT_TRIALS=50`, `TOURNAMENT_MIN_IMPROVEMENT=0.05` (5% üzerinde improvement varsa apply).

## Strateji Bakım Komutları

| Komut | Açıklama |
|---|---|
| `/strategies` | Tüm stratejilerin durumu |
| `/pause <strat>` | Geçici durdur |
| `/resume <strat>` | Devam ettir |
| `/streak_reset <strat>` | Loss streak sıfırla |
| `/lifecycle <strat>` | Mevcut faz (exploration/evaluation/proven) |
| `/report <strat>` | PnL, WR, Sharpe, Sortino |
| `/test_strategy <strat>` | Backtest üzerinde test (Phase 79) |
| `/experiment` | Sandbox test |

## Protected Stratejiler

Bazı stratejiler `protected` flag'i ile korunur — otomatik optimizer müdahale edemez, manuel dokunmadan değişmezler:
- `t` — Protected (project-level kural gereği)

Bu flag `config/strategies/*.yaml` içinde `protected: true` olarak tanımlanır.
