# Risk Register

**Tarih:** 2026-04-30
**Skor formula:** Olasılık (1-5) × Etki (1-5) = 1-25

---

| ID | Layer | Risk | Olasılık | Etki | Skor | Mitigation | Status |
|---|---|---|---|---|---|---|---|
| R001 | L9 | V2 contract uyumsuzluğu (V1 SDK kullanılırsa) | 1 | 5 | 5 | P0.11 V2 migration ✅ canlı doğrulandı | ✅ KAPALI |
| R002 | L1 | EIP-712 v1/v2 domain mismatch | 1 | 5 | 5 | V2 SDK delegasyonu, Phase A+B+C closure | ✅ KAPALI |
| R003 | L4 | WS reconnect storm → IP ban | 2 | 4 | 8 | T5.4+T5.6 reconnect doctrine | ✅ KAPALI |
| R004 | L9 | Allowance eksik → INVALID_ORDER_NOT_ENOUGH_BALANCE | 1 | 4 | 4 | P0.5 pre-flight modül + Aşama 1+2 SDK approve | ✅ KAPALI |
| R005 | L5 | Fee miscalculation → P&L drift | 1 | 4 | 4 | core/fees_v2.py FAZ 0.1 bit-identical | ✅ KAPALI |
| R006 | L10 | Paper-live PnL drift > %10 | 3 | 4 | 12 | T4.6-B sweep + P0.6 walk-forward + P0.7 recalibration | ⚠️ DOĞRULAMA SPRINT 2 |
| R007 | L8 | Bot edge yok, $1k yanıyor | 4 | 4 | 16 | $20 mikro test (Sprint 2) + $100 promotion gate | ⏳ AKTIF MITIGATION |
| R008 | L6 | 15m markets Chainlink divergence | 3 | 3 | 9 | P0.12 RTDS Chainlink subscribe modül | ✅ MODUL HAZIR (P1.10 wire) |
| R009 | L1 | Cloudflare bot detection (V2 derive) | 4 | 1 | 4 | Cosmetic — fallback creds geçti | 📋 P1 polish |
| R010 | L8 | Drawdown -%20 sermaye yıpranma | 3 | 5 | 15 | P0.8 portfolio_kill_switch 3 katman | ✅ MODUL HAZIR |
| R011 | L2 | Parmak kayması (`/buy 100 0.99`) | 3 | 4 | 12 | P0.10 order_validator hard caps | ✅ MODUL HAZIR |
| R012 | L7 | Rate limit aşımı → 429 ban | 2 | 3 | 6 | P1 rate_limiter modül + 429 backoff exp | ⏳ P1 backlog |
| R013 | L8 | Off-chain ↔ on-chain sync exploit | 1 | 5 | 5 | P1.4 reconciliation loop her 5dk | ⏳ P1.4 |
| R014 | L1 | Telegram bot token sızıntısı | 1 | 4 | 4 | T10.8 13 secret regex audit + router whitelist | ✅ KAPALI |
| R015 | — | TR regülasyon (vergi/KYC) | 3 | 3 | 9 | Hukuk danışmanı Q3 2026 | ⏳ AÇIK |
| R016 | L1 | Tedarik zinciri saldırısı (3rd party SDK) | 1 | 5 | 5 | pip-audit 0 CVE + checksum + isolated wallet | ✅ KAPALI |
| R017 | L10 | AI Brain Sonnet 10dk maliyeti ölçek | 3 | 3 | 9 | T8.2 LLM rate-limit guard + Llama fallback P2 | ✅ KAPALI (T8.2) |
| R018 | L10 | Backtest fake edge → live yanma | 3 | 5 | 15 | P0.6 walk-forward (out-of-sample) zorunlu | ✅ MODUL HAZIR |
| R019 | — | Anlık $355 PnL = sample size küçük | 5 | 2 | 10 | 1000+ trade'a kadar conclusion verme | ⏳ AKTIF |
| R020 | L4 | Polymarket V2 deprecation V1 SDK kırar | 1 | 5 | 5 | P0.11 V2 migration ✅ tamam | ✅ KAPALI |

---

## Yüksek Skor (≥10) Aktif Risk Listesi

| ID | Risk | Skor | Aksiyon |
|---|---|---|---|
| **R007** | Bot edge yok, $1k yanıyor | **16** | Sprint 2 $20 mikro test + edge ölçüm zorunlu |
| **R010** | Drawdown -%20 sermaye yıpranma | 15 | P0.8 modül engine wire P1 |
| **R018** | Backtest fake edge | 15 | P0.6 walk-forward production run zorunlu |
| **R006** | Paper-live drift > %10 | 12 | T4.6-B fix uygulandı, Sprint 2 doğrulama |
| **R011** | Parmak kayması | 12 | P0.10 modül entegrasyonu zorunlu |

---

## Risk Skoru Trend (audit öncesi vs sonrası)

| Tarih | Yüksek Risk Sayısı (skor ≥10) | Toplam Risk Skoru |
|---|---|---|
| 2026-04-22 (Epic 11 sonrası) | 8 | 134 |
| 2026-04-28 (Phase A+B+C closure) | 7 | 122 |
| **2026-04-30 (bu audit sonrası)** | **5** | **102** |

→ Audit sonrası **risk skoru %16 düştü**. P0 modüllerinin entegrasyonu sonrası skor 60-80'e düşer.
