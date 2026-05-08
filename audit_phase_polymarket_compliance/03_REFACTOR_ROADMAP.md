# Refactor Roadmap — Sprint Planı

**Tarih:** 2026-04-30
**Bağ:** `docs/MASTER_PLAN_2026_04_30.md` §6 (30/60/90 takvim) + Audit `01_POLYMARKET_COMPLIANCE_AUDIT.md`

---

## Sprint 1 (Hafta 1-2) — P0 Heddas Yerel Apply ✅ Sandbox Hazır

**Hedef:** P0 sandbox apply → Heddas yerel doğrulama + entegrasyon

### Tasklar
- [x] L1-L11 audit raporları yazıldı
- [x] V2 SDK migration (P0.11) canlı doğrulandı
- [ ] P0.5 `core/allowance_preflight.py` engine.py boot wire (1h)
- [ ] P0.4 strategy pruning analiz koş + ENV toggle (2h)
- [ ] P0.6 walk-forward production run (DB'den 90g event, smoke) (3h)
- [ ] P0.7 `.env` FILL_SPREAD_COST=0.023 + IMPACT=0.025 + LATENCY_DRIFT=0.04 (15dk)
- [ ] P0.8 `core/portfolio_kill_switch.py` engine.py wire + whitelist (1h)
- [ ] P0.10 `telegram_bot/handlers/order_validator.py` `/buy` handler entegrasyon (1h)

**Toplam efor:** ~10 saat
**Risk:** V2 SDK Cloudflare 403 (cosmetic, fallback geçti)

---

## Sprint 2 (Hafta 3-4) — $20 Mainnet Mikro Test

**Hedef:** Paper vs live PnL sapması <%10 doğrula

### Tasklar
- [ ] $20 deposit Polymarket'e
- [ ] LIVE_ENABLED=true + MAX_ORDER_USD=10
- [ ] 14 gün shadow live trading
- [ ] Paper PnL vs Live PnL drift < %10 (T4.6-B threshold)
- [ ] Reconciliation loop (P1.4) implement + 24h test
- [ ] **Karar:** sapma <%10 → Sprint 3'e geç; ≥%10 → simülasyon fix önce

**Toplam efor:** 14 gün soak + ~6 saat fix
**Risk:** Paper-live drift fix gerek

---

## Sprint 3 (Ay 2 — Haziran) — P1 Paketi

**Hedef:** Production-quality bot (Linux deploy, refactor, %60 coverage)

### Tasklar
- [ ] **P1.1** Linux/Docker deployment (Dockerfile multi-stage + systemd)
- [ ] **P1.2** core/ → 3 modül refactor (signal_engine + execution_engine + risk_engine)
- [ ] **P1.3** Test coverage 21% → 60% (CI gate progressive ratchet)
- [ ] **P1.4** Reconciliation loop (5dk Polygon RPC CTF balanceOf vs DB)
- [ ] **P1.5** .env 100+ → 25 (whitelist + array consolidation)
- [ ] **P1.6** Taker/maker karar matrisi (Phase D Bulgu 10 + post-only GTC)
- [ ] **P1.6.1** Heartbeat 5s coroutine (P1.6 öncesi ZORUNLU)
- [ ] **P1.7** Structured logging (loguru/structlog) + secret scrubbing
- [ ] **P1.8** Executor abstraction (LiveExecutor + PaperExecutor common interface)
- [ ] **P1.9** ~~py-clob-client V2 migration~~ ✅ KAPALI 2026-04-30
- [ ] **P1.10** RTDS engine boot wire + sponsored Chainlink API key
- [ ] **P1.X** Cloudflare 403 polish (User-Agent override)

**Toplam efor:** 4 hafta × 30h = 120h
**$100 Promotion gate:** ≥200 trade, PnL ≥+%5, Sharpe>1, DD<%15, recon=0

---

## Sprint 4 (Ay 3 — Temmuz) — P2 SaaS Pivot Hazırlık

**Hedef:** Multi-user lisans + dashboard + ödeme

### Tasklar
- [ ] **P2.1** Multi-user + lisans (DB users, /redeem, 3-tier)
- [ ] **P2.2** Polymarket V2 error code mapping (Phase D Bulgu 11, 15+ kod)
- [ ] **P2.3** Status polling refinement (Phase D Bulgu 12, exp backoff)
- [ ] **P2.4** Web dashboard MVP (Streamlit/React, public PnL link)
- [ ] **P2.5** Stripe + Coingate ödeme entegrasyonu
- [ ] **P2.6** Affiliate program (%20 lifetime)

**Toplam efor:** 4 hafta × 30h = 120h
**$500 Promotion gate:** ≥1000 trade, 3 ay üstüste pozitif, Sharpe>1.2, PF>1.4

---

## Sprint 5-6 (Ay 4-6 — Ağustos-Ekim) — SaaS Lansman

**Hedef:** İlk 10 ödeme yapan müşteri, $500-1000 MRR

### Tasklar
- [ ] Pazarlama (Reddit, X, Discord, Telegram)
- [ ] 3 fiyat tier yayında (Starter $9 / Trader $29 / Pro $79)
- [ ] Yasal kontrol (TR vergi danışmanı, KVKK)
- [ ] İlk 10 ödeme yapan müşteri

---

## Sprint 7+ (Ay 7-12) — P3 Ölçek

- [ ] **P3.1** Multi-asset (Polymarket Geopolitics %0 fee)
- [ ] **P3.2** Multi-venue (Kalshi US-only)
- [ ] **P3.3** Public API (Pro tier)
- [ ] **P3.4** White-label lisans

**Hedef:** $3000+ MRR

---

## Karar Noktaları (Promotion Gates)

| Gate | Threshold | Yanlış olursa |
|---|---|---|
| Sprint 1 sonu | V2 smoke trade PASS | Cloudflare polish, V2 method ek rename |
| Sprint 2 ($20→$100) | paper-live <%10, Sharpe>1, DD<%15 | Paper engine fix, MAX_ORDER_USD=10 kalır |
| Sprint 3 ($100→$500) | Sharpe>1.2, PF>1.4, 3 ay üst üste pozitif | $100 cap, daha fazla mikro test |
| Sprint 4 (SaaS karar) | Bot uptime >%99.5, 3 beta kullanıcı | Sermaye yerine SaaS pivot tetikler |
| Sprint 5-6 | $500 MRR | Pazarlama re-evaluate, fiyat ayarı |
