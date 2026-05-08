# Changelog

Bu dosya, PolyPaper Bot'un önemli değişikliklerini kronolojik sırayla listeler.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) ·
Tarihler ISO formatında (YYYY-MM-DD).

---

## [Sprint 3 — Cleanup + V2 WSS Meta Events] — 2026-05-08

### Sadeleştirme

- Repo'dan **85 dosya** silindi (~9.5 MB):
  - 17 eski one-time commit batch
  - 18 `_archive/` snapshot klasörü (cleanup_phase57, cleanup_2026-04-09b, vb.)
  - 5 eski cleanup script
  - 14 `_commit_msg_*.txt` (zaten git log'da)
- README + CHANGELOG baştan aşağı yenilendi (modern, anlaşılır)
- 14 coverage runtime raporu `.gitignore`'a eklendi

### Düzeltildi

- `tests/unit/test_wave22_mega.py`: `SystemExit` catch eklendi
  (collector.main argparse → BaseException catch)
- `main_dashboard._get_paper_summary`: bot DB schema sync
  (`executions.pnl` + `strategies.status='started'`)

### Eklendi — Polymarket V2 WSS Meta Events

`data/websocket_client.py`'a yeni event handler:

| Event | Açıklama |
|---|---|
| `tick_size_change` | Market tick size değişiminde callback fire |
| `new_market` | Yeni market açılış event'i |
| `market_resolved` | UMA report event'i |

WSS subscribe çağrısı `custom_feature_enabled: true` flag'i ile zenginleştirildi
(Polymarket V2 spec uyumu).

---

## [Mod-First UX + V2 SDK Stack] — 2026-05-05/06

### Eklendi — Mod-First Dashboard

- `/start` artık **mod seçim ekranı** (PAPER vs LIVE)
- Paper-only / Live-only ayrı menüler, cross-mod geçiş butonları
- Üst dashboard'ta her iki modun özet bilgisi (bakiye, günlük PnL, allowance)

### Eklendi — Live Trade History + CSV Export

- `/lh`, `/livehistory` komutu
- Sayfalı trade listesi (5/sayfa) + per-trade detay ekran
- CSV export — 15 zengin alan: timestamp, market, side, size, price, USDC,
  condition_id, transaction_hash, polygonscan_url, ...
- PnL detay paneli: bugün + son 7 gün + win rate + best/worst trade

### Eklendi — Polymarket Live Trading Stack

- `approve_allowance()` — 3-contract approve (pUSD → CTF + CTF Exchange + Neg Risk)
  via Polymarket Relayer (gasless, $0 gas)
- `redeem_position()` — winning shares → pUSD via `CTF.redeemPositions` (gasless)
- `auto_redeem_job` — opsiyonel periodic job (5dk interval, idempotent)
- Live BUY/SELL UI: 4-ekran flow (TF → Asset → Amount → Confirm)
- SELL panel: PnL ile pozisyon listesi + 25/50/75/100% sat butonları
- Settled detection: 🏆 winner → Redeem button | ⚰️ loser → "değersiz" mesaj

### Eklendi — Polymarket Data API Coverage

- `fetch_activity()` — `/activity` endpoint (TRADE/REDEEM/SPLIT/MERGE)
- `fetch_closed_positions()` — `/closed-positions` endpoint
- `ActivityRow` + `ClosedPositionRow` typed dataclasses
- Snapshot job 6 paralel fetch (önceki 4'ten +2)

### Düzeltildi — Polymarket V2 SDK Breaking Changes

V2 cutover (2026-04-28) sonrası 4 dosyada:

- `bal["allowance"]` → `bal["allowances"]` dict per-spender
- `OrderArgs.builder_code` → V2'de OrderArgs içinde (V1'de options dict'te)
- `PartialCreateOrderOptions(tick_size, neg_risk)` → typed dataclass
- `MarketOrderArgs + create_and_post_market_order` → decimal precision auto

### Eklendi — Test Coverage Push

- **502 → 3,474 tests pass** (+591%)
- Coverage **21.2% → 43.7%** (+22.5 pt, 24-wave incremental)
- `tests/unit/conftest.py` shared fixtures (`db_stub`, `_AsyncCM`)
- Wave 22 mega: 130-modül parametrik import test
- Wave 23 integration env-gated (Windows aiosqlite incompatible)

### Eklendi — Yeni Core Modülleri

| Modül | Amaç |
|---|---|
| `core/heartbeat.py` | Polymarket post-only GTC öncesi 5sn heartbeat |
| `core/executor.py` | Paper/Live aynı emir path |
| `core/maker_taker_decision.py` | Spread-aware order routing |
| `core/reconciliation/onchain_sync.py` | Off-chain ↔ on-chain sync |
| `core/structured_logging.py` | JSON log + secret scrubbing |
| `core/uma_dispute.py` | UMA dispute window awareness |
| `core/allowance_preflight.py` | Boot-time allowance check |
| `core/portfolio_kill_switch.py` | Portfolio-level acil durdurma |

---

## [Polymarket V2 SDK Migration + Mainnet Hazırlığı] — 2026-04-28/30

### Değiştirildi

- `py-clob-client 0.34.6` → `py-clob-client-v2 1.0.0` (resmi V2 SDK)
- Polymarket 28 Nisan 2026 V2 cutover sonrası tüm endpoint'ler güncellendi
- EIP-712 domain version "2", V2 order struct (metadata + builder fields)

### Kaldırıldı

- **Becker calibrator** — Heddas direktifi tam silme (10 dosya, ~140 satır)
- **HyperOpt sistemi** — Heddas direktifi tam silme (Optuna TPE pipeline)
- AI Brain'in INSIGHT eylemi (LLM cost azaltma)
- Strategy Suggester gece job'u (paper-only manuel öneri yerine)

### Eklendi — Sprint 2 Mainnet Shadow Live

- 14 günlük shadow live test (1 USDC → 1$/trade, 3 strateji)
- 17 May decision gate
- 6 runtime guard (kill switch, PnL pause, rolling WR, staleness, ...)

---

## [Pre-Mainnet Gate Closure] — 2026-04-22/24

### Tamamlandı

- **T11.1** wallet_audit.py + secret_scan.py
- **T11.2** 6/6 live guard validation PASS
- **T11.3** 4/4 rollback dry-run PASS
- **T9.8-REG** Windows integration 52/52 PASS
- pip-audit 0 CVE, 13 secret regex × 3 scope = 0 match

### Düzeltildi (HIGH)

- DB backup atomic write — corrupt backup'ları engellendi
  (`dest.tmp` → atomic rename pattern)

---

## [Code Quality + Security] — 2026-04-20/22

### Refactor

- 56 dosya × 373 bare-except site (153 narrow + 220 documented `noqa`)
- Single fee oracle (`core/fees_v2.py`) — eski legacy + Becker fee dağınık silindi
- 5 ghost-class doctrine (UI ↔ engine parity 6-flag canonical)

### Security

- 3 CRIT callback auth gap fixed (admin gate via `_is_admin_call()`)
- Exception leak sweep (`_exc_render_policy.py` 4 handler entegre)
- Pre-commit secret scan hook

---

## [Older History (Phase 47–82e)]

Phase 47'den Phase 82e'ye kadar 80+ feature/refactor phase tamamlandı:

- WAL tuning + busy_timeout
- AI Brain (Claude Sonnet 10dk cycle)
- 18 engine stratejisi + Classic plugin
- 2-Agent (Optimist + Critic) AI
- Backtest v2 (replay engine, real L2 history)
- Walk-forward validation + overfit gate
- Monte Carlo Kelly sizing
- WebSocket reconnect doctrine
- Strategy lifecycle (exploration → evaluation → proven)
- Telegram dashboard hub + 30+ handler
- Watchdog + auto-restart

Detaylı liste artık [docs/PHASES.md](docs/PHASES.md) içinde.
